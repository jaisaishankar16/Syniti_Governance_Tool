"""FastAPI routes for executing synchronous and streaming SQL analysis."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from app.agents.failure_cache import get_failure_cache
from app.agents.settings import get_llm_settings, is_valid_api_key
from app.api.deps import get_activity_repo, get_orchestrator, get_settings
from app.api.errors import (
    ErrorDetail,
    ErrorEnvelope,
    ErrorEnvelopeBody,
    get_request_id,
)
from app.api.metrics import (
    ANALYZE_STAGE_DURATION_SECONDS,
    ANALYZE_TOTAL,
    LLM_TOKENS_TOTAL,
)
from app.api.models import ActivityItem, AnalyzeRequest
from app.api.repositories import ActivityRepository
from app.api.settings import ApiSettings
from app.api.sse import SseEvent, sse_event_generator
from app.errors import AppError, LLMNotConfiguredError
from app.guardrails.models import GuardrailBlocked
from app.orchestrator.orchestrator import analyze as default_analyze
from app.orchestrator.progress import ProgressEvent
from app.synthesizer.models import BusinessIntelligence
from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Analysis"])


def _check_llm_configured() -> None:
    """Verifies LLM configuration before initiating analysis or streaming.

    Under Project Rules (see docs/PROJECT_RULES.md), requests cannot be assembled
    without the LLM; fails fast before any parsing or caching work.
    """
    settings = get_llm_settings()
    raw_key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
    if not is_valid_api_key(raw_key):
        raise LLMNotConfiguredError(
            message=(
                "OpenAI API key is not configured or is an invalid placeholder. "
                "Please configure OPENAI_API_KEY in your environment or root .env file. "
                "Run 'python -m app.agents.cli --check-env' to diagnose configuration."
            ),
            details="Looked in OS environment variables and root .env file",
        )
    get_failure_cache().check_failure_window()


def _format_error_payload(exc: Exception, request_id: str) -> dict[str, Any]:
    """Serializes exception to dictionary matching ErrorEnvelope schema for SSE error events."""
    if isinstance(exc, AppError):
        code = exc.code.value
        msg = exc.message
        retry = 60 if "RATE_LIMIT" in code or "BUDGET" in code else None
        details: list[ErrorDetail] = [ErrorDetail(issue=exc.details)] if exc.details else []
    elif isinstance(exc, GuardrailBlocked):
        code = "INPUT_REJECTED"
        msg = "Input query was rejected by enterprise security guardrails."
        retry = None
        details = (
            [
                ErrorDetail(field=e.stage, issue=e.name)
                for e in exc.report.events
                if e.action == "block"
            ]
            if exc.report
            else []
        )
    else:
        code = "INTERNAL_ERROR"
        msg = "An internal error occurred during synthesis."
        retry = None
        details = []

    return ErrorEnvelope(
        error=ErrorEnvelopeBody(
            code=code,
            message=msg,
            request_id=request_id,
            retry_after_s=retry,
            details=details,
        )
    ).model_dump()


def _extract_table_name(sql: str, bi: BusinessIntelligence | None = None) -> str:
    """Extracts target view or table name without retaining full SQL."""
    m = re.search(r"CREATE\s+VIEW\s+(?:dbo\.)?([A-Za-z0-9_]+)", sql, re.IGNORECASE)
    if m:
        return m.group(1)
    if bi and bi.affected_entities:
        return bi.affected_entities[0]
    return "Query"


def _derive_subtitle(bi: BusinessIntelligence | None = None, error_code: str | None = None) -> str:
    """Derives short context subtitle."""
    if error_code:
        return error_code
    if bi:
        if bi.sap_module and bi.business_meaning:
            return f"{bi.sap_module} - {bi.business_meaning[:45].strip()}"
        if bi.sap_module:
            return bi.sap_module
        if bi.technical_summary:
            return bi.technical_summary[:45].strip()
    return "Analysis Execution"


async def _record_activity_outcome(
    repo: ActivityRepository | None,
    request_id: str,
    sql: str,
    bi: BusinessIntelligence | None = None,
    error_code: str | None = None,
    duration_ms: float = 0.0,
) -> None:
    """Safely records execution activity outcome in the background (swallows and logs errors)."""
    if not repo:
        return
    try:
        if bi:
            status_val: Literal["complete", "degraded", "failed"] = (
                "degraded" if bi.meta.degraded else "complete"
            )
            run_type = (
                "Trial Execution (cached)"
                if bi.meta.cache_status == "hit_final"
                else "Trial Execution"
            )
            cache_st = bi.meta.cache_status
            dur = bi.meta.stage_timings_ms.get("total", duration_ms)
            sub = _derive_subtitle(bi=bi)
            tbl = _extract_table_name(sql, bi=bi)
        else:
            status_val = "failed"
            run_type = "Trial Execution"
            cache_st = "miss"
            dur = duration_ms
            sub = _derive_subtitle(error_code=error_code)
            tbl = _extract_table_name(sql)

        item = ActivityItem(
            id=f"act-{uuid4().hex[:12]}",
            request_id=request_id,
            status=status_val,
            run_type=run_type,
            table_name=tbl,
            subtitle=sub,
            cache_status=cache_st,
            duration_ms=round(dur, 2),
            finished_at=datetime.now(timezone.utc).isoformat(),
        )
        await repo.record_activity(item)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to record activity entry for %s (non-fatal): %s", request_id, exc)


@router.post(
    "/analyze",
    response_model=BusinessIntelligence,
    summary="Analyze SQL Query",
    operation_id="analyze_sql_query",
    status_code=status.HTTP_200_OK,
)
async def analyze_sql(
    request: Request,
    body: AnalyzeRequest,
    settings: ApiSettings = Depends(get_settings),
    orchestrator_fn: Any = Depends(get_orchestrator),
    activity_repo: ActivityRepository = Depends(get_activity_repo),
) -> BusinessIntelligence:
    """Executes full analysis pipeline across security guardrails, parsing, multi-tier cache, agents, and synthesizer.

    Returns authoritative BusinessIntelligence and Data Governance metadata.
    """
    req_id = get_request_id(request)

    # 1. Fail fast if LLM is not configured before ANY parsing or cache lookup
    _check_llm_configured()

    force_refresh = body.force_refresh and settings.allow_force_refresh
    fn = orchestrator_fn or default_analyze

    start_time = asyncio.get_event_loop().time()
    try:
        bi: BusinessIntelligence = await asyncio.wait_for(
            fn(sql=body.sql, force_refresh=force_refresh, request_id=req_id),
            timeout=settings.request_timeout_s,
        )
        elapsed_ms = (asyncio.get_event_loop().time() - start_time) * 1000.0
        await _record_activity_outcome(
            activity_repo, req_id, body.sql, bi=bi, duration_ms=elapsed_ms
        )
    except asyncio.TimeoutError as exc:
        elapsed_ms = (asyncio.get_event_loop().time() - start_time) * 1000.0
        await _record_activity_outcome(
            activity_repo, req_id, body.sql, error_code="TIMEOUT", duration_ms=elapsed_ms
        )
        raise AppError(
            "Analysis execution timed out", details=f"Exceeded {settings.request_timeout_s}s limit"
        ) from exc
    except Exception as exc:
        elapsed_ms = (asyncio.get_event_loop().time() - start_time) * 1000.0
        err_code = getattr(exc, "code", None)
        val = getattr(err_code, "value", None)
        err_code_str = str(val or err_code or "ERROR")
        await _record_activity_outcome(
            activity_repo, req_id, body.sql, error_code=err_code_str, duration_ms=elapsed_ms
        )
        raise

    # Metrics instrumentation
    ANALYZE_TOTAL.labels(
        cache_status=bi.meta.cache_status,
        degraded=str(bi.meta.degraded).lower(),
        outcome="success",
    ).inc()

    if bi.meta.stage_timings_ms:
        for stage, duration_ms in bi.meta.stage_timings_ms.items():
            if stage != "total":
                ANALYZE_STAGE_DURATION_SECONDS.labels(stage=stage).observe(duration_ms / 1000.0)

    if bi.meta.token_usage:
        LLM_TOKENS_TOTAL.labels(agent="sql", kind="total").inc(bi.meta.token_usage.sql)
        LLM_TOKENS_TOTAL.labels(agent="sap", kind="total").inc(bi.meta.token_usage.sap)
        LLM_TOKENS_TOTAL.labels(agent="synthesizer", kind="total").inc(
            bi.meta.token_usage.synthesizer
        )

    return bi


@router.post(
    "/analyze/stream",
    summary="Stream SQL Analysis Progress",
    operation_id="stream_sql_analysis",
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "content": {"text/event-stream": {}},
            "description": "Server-Sent Events stream emitting progress, result, and done events.",
        }
    },
)
async def analyze_sql_stream(
    request: Request,
    body: AnalyzeRequest,
    settings: ApiSettings = Depends(get_settings),
    orchestrator_fn: Any = Depends(get_orchestrator),
    activity_repo: ActivityRepository = Depends(get_activity_repo),
) -> StreamingResponse:
    """Streams pipeline progress, intermediate events, and final result over Server-Sent Events (SSE).

    Client disconnection stops writing without cancelling the shielded background execution.
    """
    req_id = get_request_id(request)

    # Errors prior to stream starting return standard HTTP status responses
    _check_llm_configured()

    force_refresh = body.force_refresh and settings.allow_force_refresh
    fn = orchestrator_fn or default_analyze

    queue: asyncio.Queue[SseEvent | str | None] = asyncio.Queue()

    async def _on_progress(evt: ProgressEvent) -> None:
        await queue.put(SseEvent(event="progress", data=evt.model_dump_json()))

    async def _background_runner() -> None:
        start_time = asyncio.get_event_loop().time()
        try:
            bi: BusinessIntelligence = await fn(
                sql=body.sql,
                force_refresh=force_refresh,
                request_id=req_id,
                on_progress=_on_progress,
            )
            elapsed_ms = (asyncio.get_event_loop().time() - start_time) * 1000.0
            await _record_activity_outcome(
                activity_repo, req_id, body.sql, bi=bi, duration_ms=elapsed_ms
            )
            # Emit result only if valid
            await queue.put(SseEvent(event="result", data=bi.model_dump_json()))
            await queue.put(SseEvent(event="done", data="{}"))
        except Exception as exc:  # noqa: BLE001
            elapsed_ms = (asyncio.get_event_loop().time() - start_time) * 1000.0
            err_data = _format_error_payload(exc, req_id)
            err_code_str = err_data.get("error", {}).get("code", "ERROR")
            await _record_activity_outcome(
                activity_repo, req_id, body.sql, error_code=err_code_str, duration_ms=elapsed_ms
            )
            await queue.put(SseEvent(event="error", data=json.dumps(err_data)))
            await queue.put(SseEvent(event="done", data="{}"))
        finally:
            await queue.put(None)

    # Launch runner as shielded task so client disconnect does not cancel cache generation
    runner_task = asyncio.create_task(_background_runner())
    shielded_future = asyncio.shield(runner_task)

    return StreamingResponse(
        sse_event_generator(
            shielded_future,
            queue,
            heartbeat_interval_s=settings.sse_heartbeat_s,
            request_id=req_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
            "X-Request-ID": req_id,
        },
    )
