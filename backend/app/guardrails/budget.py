"""Token budget tracking, cost calculation, concurrency management, and rate limiting."""

from __future__ import annotations

import asyncio
import threading
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.guardrails.models import (
    GuardrailBlocked,
    GuardrailEvent,
    GuardrailReport,
    KeyValue,
    Verdict,
)
from app.guardrails.settings import GuardrailSettings, get_guardrail_settings


class TokenUsage(BaseModel):
    """Detailed token usage for an LLM call or aggregate run."""

    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = "gpt-4o-mini"
    estimated_cost_usd: float | None = None


class BudgetExceededError(GuardrailBlocked):
    """Raised when token or cost budget thresholds are exceeded."""


def calculate_cost_usd(
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    pricing_table: dict[str, dict[str, float]],
) -> float | None:
    """Calculates estimated cost in USD using pricing table per million tokens.

    Returns None if pricing table is empty or model pricing is not configured.
    """
    if not pricing_table or model not in pricing_table:
        return None

    pricing = pricing_table[model]
    input_rate = pricing.get("input_per_m")
    output_rate = pricing.get("output_per_m")
    if input_rate is None or output_rate is None:
        return None

    input_cost = (prompt_tokens / 1_000_000.0) * input_rate
    output_cost = (completion_tokens / 1_000_000.0) * output_rate
    return round(input_cost + output_cost, 6)


class BudgetTracker:
    """Thread-safe tracker for per-request and cumulative daily token usage."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current_date: date = datetime.now(timezone.utc).date()
        self._daily_prompt_tokens: int = 0
        self._daily_completion_tokens: int = 0
        self._daily_total_tokens: int = 0
        self._daily_cost_usd: float = 0.0

    def reset(self) -> None:
        """Resets all daily accumulated token counts and costs to zero."""
        with self._lock:
            self._daily_prompt_tokens = 0
            self._daily_completion_tokens = 0
            self._daily_total_tokens = 0
            self._daily_cost_usd = 0.0

    def _maybe_reset_daily(self) -> None:
        today = datetime.now(timezone.utc).date()
        if today != self._current_date:
            self._current_date = today
            self._daily_prompt_tokens = 0
            self._daily_completion_tokens = 0
            self._daily_total_tokens = 0
            self._daily_cost_usd = 0.0

    def check_request_budget(
        self, tokens: int, settings: GuardrailSettings | None = None, request_id: str = ""
    ) -> Verdict[bool]:
        """Checks if a request's token count exceeds the configured per-request budget."""
        cfg = settings or get_guardrail_settings()
        report = GuardrailReport()

        if tokens > cfg.per_request_token_budget:
            report.add_event(
                GuardrailEvent(
                    name="request_token_budget_exceeded",
                    stage="budget",
                    action="block",
                    severity="high",
                    message=f"Request tokens ({tokens}) exceeded budget ({cfg.per_request_token_budget})",
                    details=[
                        KeyValue(key="tokens", value=str(tokens)),
                        KeyValue(key="budget", value=str(cfg.per_request_token_budget)),
                    ],
                    request_id=request_id,
                )
            )
            return Verdict(allowed=False, value=False, report=report)

        return Verdict(allowed=True, value=True, report=report)

    def check_daily_budget(
        self,
        tokens_to_add: int = 0,
        settings: GuardrailSettings | None = None,
        request_id: str = "",
    ) -> Verdict[bool]:
        """Checks if adding tokens would exceed daily token budget."""
        cfg = settings or get_guardrail_settings()
        report = GuardrailReport()

        with self._lock:
            self._maybe_reset_daily()
            projected = self._daily_total_tokens + tokens_to_add
            if projected > cfg.daily_token_budget:
                report.add_event(
                    GuardrailEvent(
                        name="daily_token_budget_exceeded",
                        stage="budget",
                        action="block",
                        severity="critical",
                        message=f"Daily token budget ({cfg.daily_token_budget}) exceeded: current {self._daily_total_tokens}, attempted +{tokens_to_add}",
                        details=[
                            KeyValue(
                                key="current_daily_tokens", value=str(self._daily_total_tokens)
                            ),
                            KeyValue(key="attempted_tokens", value=str(tokens_to_add)),
                            KeyValue(key="daily_budget", value=str(cfg.daily_token_budget)),
                        ],
                        request_id=request_id,
                    )
                )
                return Verdict(allowed=False, value=False, report=report)

        return Verdict(allowed=True, value=True, report=report)

    def record_usage(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        model: str,
        settings: GuardrailSettings | None = None,
        request_id: str = "",
    ) -> tuple[TokenUsage, GuardrailReport]:
        """Records token usage, updates daily stats, calculates cost, and verifies budgets."""
        cfg = settings or get_guardrail_settings()
        total_tokens = prompt_tokens + completion_tokens
        cost = calculate_cost_usd(model, prompt_tokens, completion_tokens, cfg.pricing_table)

        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=model,
            estimated_cost_usd=cost,
        )

        report = GuardrailReport()

        # Check request budget
        req_verdict = self.check_request_budget(total_tokens, cfg, request_id)
        report.merge(req_verdict.report)

        with self._lock:
            self._maybe_reset_daily()
            self._daily_prompt_tokens += prompt_tokens
            self._daily_completion_tokens += completion_tokens
            self._daily_total_tokens += total_tokens
            if cost is not None:
                self._daily_cost_usd += cost

            if self._daily_total_tokens > cfg.daily_token_budget:
                report.add_event(
                    GuardrailEvent(
                        name="daily_token_budget_exceeded",
                        stage="budget",
                        action="block",
                        severity="critical",
                        message=f"Daily token budget ({cfg.daily_token_budget}) exceeded (current: {self._daily_total_tokens})",
                        details=[
                            KeyValue(key="daily_tokens", value=str(self._daily_total_tokens)),
                            KeyValue(key="daily_budget", value=str(cfg.daily_token_budget)),
                        ],
                        request_id=request_id,
                    )
                )

        if not report.blocked:
            cost_str = f"${cost:.6f}" if cost is not None else "pricing not configured"
            cost_val = f"{cost:.6f}" if cost is not None else "pricing not configured"
            report.add_event(
                GuardrailEvent(
                    name="token_usage_recorded",
                    stage="budget",
                    action="pass",
                    severity="info",
                    message=f"Tokens: {total_tokens} (prompt: {prompt_tokens}, completion: {completion_tokens}), est cost: {cost_str}",
                    details=[
                        KeyValue(key="prompt_tokens", value=str(prompt_tokens)),
                        KeyValue(key="completion_tokens", value=str(completion_tokens)),
                        KeyValue(key="total_tokens", value=str(total_tokens)),
                        KeyValue(key="estimated_cost_usd", value=cost_val),
                    ],
                    request_id=request_id,
                )
            )

        return usage, report

    def get_daily_stats(self) -> dict[str, Any]:
        """Returns current daily token consumption and cost."""
        with self._lock:
            self._maybe_reset_daily()
            return {
                "date": self._current_date.isoformat(),
                "prompt_tokens": self._daily_prompt_tokens,
                "completion_tokens": self._daily_completion_tokens,
                "total_tokens": self._daily_total_tokens,
                "cost_usd": round(self._daily_cost_usd, 6),
            }

    def reset_for_test(self) -> None:
        """Resets all metrics (for unit testing)."""
        with self._lock:
            self._current_date = datetime.now(timezone.utc).date()
            self._daily_prompt_tokens = 0
            self._daily_completion_tokens = 0
            self._daily_total_tokens = 0
            self._daily_cost_usd = 0.0


_GLOBAL_BUDGET_TRACKER = BudgetTracker()


def get_budget_tracker() -> BudgetTracker:
    """Returns the process-wide budget tracker."""
    return _GLOBAL_BUDGET_TRACKER


class ConcurrencyManager:
    """Limits concurrent pipeline or LLM executions using an asyncio Semaphore."""

    def __init__(self, max_concurrent: int | None = None) -> None:
        self._max_concurrent = max_concurrent or get_guardrail_settings().max_concurrent_runs
        self._semaphore: asyncio.Semaphore | None = None

    def _get_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
        return self._semaphore

    async def __aenter__(self) -> ConcurrencyManager:  # noqa: PYI034
        await self._get_semaphore().acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,  # noqa: PYI036
    ) -> None:
        self._get_semaphore().release()


class InMemoryRateLimiter:
    """Sliding-window rate limiter per client key."""

    def __init__(self, max_requests: int = 60, window_seconds: float = 60.0) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._history: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, client_key: str = "default") -> bool:
        """Checks if request is within allowed rate window."""
        now = datetime.now(timezone.utc).timestamp()
        cutoff = now - self.window_seconds
        with self._lock:
            timestamps = self._history.setdefault(client_key, [])
            # Prune old timestamps
            self._history[client_key] = [t for t in timestamps if t > cutoff]
            if len(self._history[client_key]) >= self.max_requests:
                return False
            self._history[client_key].append(now)
            return True
