"""Runner executing agent runs with tenacity retries, timeouts, and usage tracking."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import TYPE_CHECKING, TypeVar

import openai
from agents import RunConfig, Runner
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_random_exponential,
)

from app.agents.failure_cache import get_failure_cache
from app.agents.models import AgentErrorDetails, AgentOutcome, UsageInfo
from app.errors import (
    AppError,
    ErrorCode,
    LLMAuthError,
    LLMQuotaExceededError,
    LLMRateLimitedError,
    LLMUnavailableError,
)
from app.guardrails.budget import get_budget_tracker
from app.guardrails.circuit_breaker import CircuitBreakerOpenError, get_circuit_breaker

if TYPE_CHECKING:
    from agents import Agent

    from app.agents.models import AgentDeps
    from app.agents.settings import LLMSettings

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)

QUOTA_KEYWORDS = (
    "insufficient_quota",
    "quota",
    "account not active",
    "account_not_active",
    "account is not active",
    "billing",
    "credit balance",
    "payment method",
    "plan and billing",
    "exceeded your current quota",
)


def is_quota_error(exc: BaseException) -> bool:
    """Checks whether an exception indicates quota exhaustion or inactive billing."""
    msg = str(exc).lower()
    return any(kw in msg for kw in QUOTA_KEYWORDS)


def extract_retry_after(exc: BaseException) -> int | None:
    """Extracts integer Retry-After duration in seconds from exception headers if available."""
    resp = getattr(exc, "response", None)
    if resp is not None:
        headers = getattr(resp, "headers", {}) or {}
        val = headers.get("retry-after")
        if val and str(val).isdigit():
            return int(val)
    return None


def classify_provider_error(exc: BaseException) -> AppError:
    """Classifies provider exception into standardized AppError subclass.

    Rules:
      - 401 -> LLMAuthError (LLM_AUTH_FAILED)
      - 429 with insufficient_quota / "account not active" / billing wording -> LLMQuotaExceededError (LLM_QUOTA_EXCEEDED)
      - 429 rate limit -> LLMRateLimitedError (LLM_RATE_LIMITED)
      - timeouts, connection errors and 5xx -> LLMUnavailableError (LLM_UNAVAILABLE)
    """
    if isinstance(exc, AppError):
        return exc

    status_code = getattr(exc, "status_code", None)
    msg = str(exc).lower()

    # 1. 401 Unauthorized
    if (
        isinstance(exc, openai.AuthenticationError)
        or status_code == 401
        or ("401" in msg and "unauthorized" in msg)
    ):
        return LLMAuthError(
            message="Authentication failed with the LLM provider (HTTP 401). Please verify that your OPENAI_API_KEY is active and valid.",
            details="Provider returned 401 Unauthorized",
        )

    # 2. 429 Quota vs Rate Limit
    if isinstance(exc, openai.RateLimitError) or status_code == 429 or "429" in msg:
        if is_quota_error(exc):
            return LLMQuotaExceededError(
                message="LLM account quota or billing limit exceeded (HTTP 429). Please check your account usage and credits.",
                details="Provider returned 429 with quota or billing exhaustion",
            )
        return LLMRateLimitedError(
            message="LLM provider rate limit exceeded (HTTP 429). Retries exhausted. Please retry the request later.",
            details="Provider returned 429 rate limit",
        )

    # 3. Timeouts, connection errors, 5xx
    if isinstance(
        exc,
        (
            openai.APITimeoutError,
            asyncio.TimeoutError,
            openai.APIConnectionError,
            openai.InternalServerError,
        ),
    ) or (status_code is not None and status_code >= 500):
        return LLMUnavailableError(
            message="LLM service is currently unavailable (network timeout, connection error, or provider 5xx).",
            details=f"Provider failed with {type(exc).__name__}",
        )

    # Fallback to unavailable
    return LLMUnavailableError(
        message="LLM service encountered an unexpected provider error.",
        details=f"{type(exc).__name__}: {exc}",
    )


def is_transient_error(exc: BaseException) -> bool:
    """Identifies transient network or rate-limit errors eligible for retry."""
    # 401 is NOT transient (never retried)
    if isinstance(exc, openai.AuthenticationError) or getattr(exc, "status_code", None) == 401:
        return False

    # 429 quota/billing is NOT transient (must NOT be retried)
    if (
        isinstance(exc, openai.RateLimitError)
        or getattr(exc, "status_code", None) == 429
        or "429" in str(exc)
    ):
        return not is_quota_error(exc)

    if isinstance(
        exc,
        (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
            asyncio.TimeoutError,
        ),
    ):
        return True

    return bool(isinstance(exc, openai.APIStatusError) and exc.status_code >= 500)


def _extract_json_text(raw_text: str) -> str:
    """Extracts JSON substring, stripping markdown backticks if present."""
    trimmed = raw_text.strip()
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", trimmed)
    if match:
        return match.group(1).strip()
    return trimmed


async def run_agent(
    agent: Agent[AgentDeps],
    input_text: str,
    deps: AgentDeps,
    settings: LLMSettings,
    output_cls: type[T],
    prompt_version: str,
) -> AgentOutcome[T]:
    """Runs an individual Agent with tenacity retries, timeout, and usage collection.

    This function is the sole entry point in the application calling Runner.run.
    """
    start_time = time.perf_counter()
    attempts_count = 0
    total_usage = UsageInfo()
    model_name = settings.llm_model

    # Fail fast if within active 60s auth/quota failure window
    get_failure_cache().check_failure_window()

    logger.info(
        "Starting agent run: agent=%s, request_id=%s, model=%s, mode=%s",
        agent.name,
        deps.request_id,
        model_name,
        settings.structured_output_mode,
    )

    cb = get_circuit_breaker()
    try:
        cb.check_call_allowed(deps.request_id)
    except CircuitBreakerOpenError as exc:
        latency = (time.perf_counter() - start_time) * 1000
        return AgentOutcome[T](
            agent_name=agent.name,
            status="blocked",
            output=None,
            error=AgentErrorDetails(
                type="CircuitBreakerOpenError",
                message=str(exc),
                retryable=False,
                error_code="LLM_UNAVAILABLE",
            ),
            usage=total_usage,
            latency_ms=round(latency, 2),
            tool_calls=list(deps.run_trace.tool_calls),
            model=model_name,
            attempts=0,
            prompt_version=prompt_version,
        )

    retrying = AsyncRetrying(
        stop=stop_after_attempt(max(1, settings.llm_max_retries)),
        wait=wait_random_exponential(min=1, max=10),
        retry=retry_if_exception(is_transient_error),
        reraise=True,
    )

    try:
        run_res = None
        async for attempt in retrying:
            with attempt:
                attempts_count += 1
                run_config = RunConfig(
                    tracing_disabled=not settings.agents_tracing,
                )
                run_res = await asyncio.wait_for(
                    Runner.run(
                        agent,
                        input_text,
                        context=deps,
                        max_turns=settings.llm_max_turns,
                        run_config=run_config,
                    ),
                    timeout=settings.llm_timeout_s,
                )

        if run_res is None:
            raise RuntimeError("Agent run did not yield a result")

        # Accumulate token usage from raw model responses
        for resp in run_res.raw_responses:
            if resp.usage:
                total_usage.requests += getattr(resp.usage, "requests", 1) or 1
                total_usage.input_tokens += getattr(resp.usage, "input_tokens", 0) or 0
                total_usage.output_tokens += getattr(resp.usage, "output_tokens", 0) or 0
                total_usage.total_tokens += getattr(resp.usage, "total_tokens", 0) or 0

        # Process output based on structured output mode
        final_obj: T | None = None
        if settings.structured_output_mode == "native":
            raw_out = run_res.final_output
            if isinstance(raw_out, output_cls):
                final_obj = raw_out
            elif isinstance(raw_out, str):
                final_obj = output_cls.model_validate_json(_extract_json_text(raw_out))
            elif isinstance(raw_out, dict):
                final_obj = output_cls.model_validate(raw_out)
            else:
                raise ValueError(f"Unexpected output type from native mode: {type(raw_out)}")
        else:
            # json_prompt mode
            raw_str = str(run_res.final_output) if run_res.final_output is not None else ""
            json_text = _extract_json_text(raw_str)
            try:
                final_obj = output_cls.model_validate_json(json_text)
            except ValidationError as val_err:
                logger.warning("json_prompt validation failed, retrying once: %s", val_err)
                # Retry ONCE with validation error appended
                retry_input = (
                    f"{input_text}\n\n<validation_error>\n"
                    f"Your previous output was invalid JSON for the required schema:\n{val_err}\n"
                    "Output ONLY valid JSON matching the schema.\n</validation_error>"
                )
                attempts_count += 1
                retry_run = await asyncio.wait_for(
                    Runner.run(
                        agent,
                        retry_input,
                        context=deps,
                        max_turns=settings.llm_max_turns,
                    ),
                    timeout=settings.llm_timeout_s,
                )
                for resp in retry_run.raw_responses:
                    if resp.usage:
                        total_usage.requests += getattr(resp.usage, "requests", 1) or 1
                        total_usage.input_tokens += getattr(resp.usage, "input_tokens", 0) or 0
                        total_usage.output_tokens += getattr(resp.usage, "output_tokens", 0) or 0
                        total_usage.total_tokens += getattr(resp.usage, "total_tokens", 0) or 0

                raw_retry_str = (
                    str(retry_run.final_output) if retry_run.final_output is not None else ""
                )
                final_obj = output_cls.model_validate_json(_extract_json_text(raw_retry_str))

        latency = (time.perf_counter() - start_time) * 1000
        cb.record_success()
        get_failure_cache().clear()
        get_budget_tracker().record_usage(
            total_usage.input_tokens,
            total_usage.output_tokens,
            model_name,
            request_id=deps.request_id,
        )
        logger.info(
            "Agent run succeeded: agent=%s, request_id=%s, latency_ms=%.2f, tokens=%d",
            agent.name,
            deps.request_id,
            latency,
            total_usage.total_tokens,
        )

        return AgentOutcome[T](
            agent_name=agent.name,
            status="ok",
            output=final_obj,
            usage=total_usage,
            latency_ms=round(latency, 2),
            tool_calls=list(deps.run_trace.tool_calls),
            model=model_name,
            attempts=attempts_count,
            prompt_version=prompt_version,
        )

    except asyncio.TimeoutError as t_err:
        latency = (time.perf_counter() - start_time) * 1000
        cb.record_failure(t_err, request_id=deps.request_id)
        logger.error(
            "Agent run timed out: agent=%s, request_id=%s, latency_ms=%.2f",
            agent.name,
            deps.request_id,
            latency,
        )
        return AgentOutcome[T](
            agent_name=agent.name,
            status="timeout",
            output=None,
            error=AgentErrorDetails(
                type="TimeoutError",
                message=f"Agent timed out after {settings.llm_timeout_s}s",
                retryable=True,
                error_code="LLM_UNAVAILABLE",
            ),
            usage=total_usage,
            latency_ms=round(latency, 2),
            tool_calls=list(deps.run_trace.tool_calls),
            model=model_name,
            attempts=attempts_count,
            prompt_version=prompt_version,
        )

    except Exception as exc:  # noqa: BLE001
        latency = (time.perf_counter() - start_time) * 1000
        typed_err = classify_provider_error(exc)

        # Remember auth or quota failure for 60 seconds; do NOT count as breaker failure
        if typed_err.code in (ErrorCode.LLM_AUTH_FAILED, ErrorCode.LLM_QUOTA_EXCEEDED):
            get_failure_cache().record_failure(typed_err)
        else:
            cb.record_failure(exc, request_id=deps.request_id)

        retryable = is_transient_error(exc)
        exc_type_name = typed_err.__class__.__name__
        is_blocked = "tripwire" in exc_type_name.lower() or "guardrail" in exc_type_name.lower()
        logger.error(
            "Agent run failed: agent=%s, request_id=%s, error_code=%s, error=%s, retryable=%s, blocked=%s",
            agent.name,
            deps.request_id,
            typed_err.code.value,
            exc,
            retryable,
            is_blocked,
        )
        # Mask raw API keys in error message
        raw_msg = str(exc)
        sanitized_msg = typed_err.message if "sk-" in raw_msg else raw_msg

        return AgentOutcome[T](
            agent_name=agent.name,
            status="blocked" if is_blocked else "failed",
            output=None,
            error=AgentErrorDetails(
                type=exc_type_name,
                message=sanitized_msg,
                retryable=retryable and not is_blocked,
                error_code=typed_err.code.value,
                retry_after_s=extract_retry_after(exc)
                or (60 if typed_err.code == ErrorCode.LLM_RATE_LIMITED else None),
            ),
            usage=total_usage,
            latency_ms=round(latency, 2),
            tool_calls=list(deps.run_trace.tool_calls),
            model=model_name,
            attempts=attempts_count,
            prompt_version=prompt_version,
        )
