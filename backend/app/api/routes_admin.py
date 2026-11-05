"""FastAPI routes for administrative management, cache controls, and learned knowledge curation."""

from __future__ import annotations

import logging
import time
from typing import Any, Literal

from app.agents.failure_cache import get_failure_cache
from app.agents.settings import get_llm_settings, is_valid_api_key
from app.api.deps import get_principal, get_settings, verify_admin_key
from app.api.models import AdminPingResponse, CacheClearResponse, Principal
from app.api.settings import ApiSettings
from app.cache.backends import get_cache_backend
from app.cache.stats import CacheStats, get_stats_tracker
from app.errors import LLMAuthError, LLMQuotaExceededError
from app.knowledge.models import LearnedSapEntry
from app.knowledge.store import KnowledgeStore
from fastapi import APIRouter, Depends, HTTPException, Query, status

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Administration"])

# Cache for 1-token live ping results (cached for 60 seconds)
_LAST_PING_RESULT: tuple[AdminPingResponse, float] | None = None


# ==============================================================================
# Cache Admin Endpoints
# ==============================================================================


@router.get(
    "/api/v1/cache/stats",
    response_model=CacheStats,
    summary="Get Cache Statistics",
    operation_id="get_cache_stats",
    dependencies=[Depends(verify_admin_key)],
)
@router.get(
    "/api/v1/admin/cache/stats",
    response_model=CacheStats,
    include_in_schema=False,
    dependencies=[Depends(verify_admin_key)],
)
async def get_cache_statistics(
    settings: ApiSettings = Depends(get_settings),
) -> CacheStats:
    """Returns detailed multi-tier cache statistics and hit/miss ratios."""
    backend = await get_cache_backend(settings.rate_limit_backend)
    tracker = get_stats_tracker()
    return await tracker.get_stats(backend)


@router.delete(
    "/api/v1/cache",
    response_model=CacheClearResponse,
    summary="Clear Cache",
    operation_id="clear_cache",
    dependencies=[Depends(verify_admin_key)],
)
@router.delete(
    "/api/v1/admin/cache",
    response_model=CacheClearResponse,
    include_in_schema=False,
    dependencies=[Depends(verify_admin_key)],
)
async def clear_cache(
    kind: Literal["all", "l1", "l2", "l3"] = Query(default="all"),
    settings: ApiSettings = Depends(get_settings),
) -> CacheClearResponse:
    """Clears cache storage across specified tiers (all, l1, l2, l3)."""
    backend = await get_cache_backend(settings.rate_limit_backend)
    prefix_map = {
        "all": "syniti:",
        "l1": "syniti:l1:",
        "l2": "syniti:l2:",
        "l3": "syniti:l3:",
    }
    prefix = prefix_map.get(kind, "syniti:")
    await backend.clear(prefix)
    return CacheClearResponse(cleared=True, kind=kind)


# ==============================================================================
# Live LLM Probe Endpoint
# ==============================================================================


@router.post(
    "/api/v1/admin/llm/ping",
    response_model=AdminPingResponse,
    summary="Ping LLM Provider",
    operation_id="ping_llm_provider",
    dependencies=[Depends(verify_admin_key)],
)
async def ping_llm_provider() -> AdminPingResponse:
    """Executes a 1-token live request to test credentials and connectivity (result cached 60s)."""
    global _LAST_PING_RESULT
    now = time.monotonic()

    # Return cached probe result if within 60s
    if _LAST_PING_RESULT is not None:
        cached_resp, cached_time = _LAST_PING_RESULT
        if now - cached_time < 60.0:
            return cached_resp

    llm_settings = get_llm_settings()
    raw_key = llm_settings.openai_api_key.get_secret_value() if llm_settings.openai_api_key else ""

    if not is_valid_api_key(raw_key):
        result = AdminPingResponse(
            status="401",
            latency_ms=0.0,
            message="No valid OpenAI API key is configured",
        )
        _LAST_PING_RESULT = (result, now)
        return result

    import openai

    client = openai.AsyncOpenAI(
        api_key=raw_key,
        base_url=llm_settings.openai_base_url,
        timeout=10.0,
    )

    t0 = time.perf_counter()
    try:
        await client.chat.completions.create(
            model=llm_settings.llm_model,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
        )
        latency = round((time.perf_counter() - t0) * 1000, 2)
        get_failure_cache().clear()
        result = AdminPingResponse(
            status="ok",
            latency_ms=latency,
            message="LLM provider connection active and responding",
        )
    except openai.AuthenticationError:
        latency = round((time.perf_counter() - t0) * 1000, 2)
        get_failure_cache().record_failure(LLMAuthError())
        result = AdminPingResponse(
            status="401",
            latency_ms=latency,
            message="Authentication failed: API key rejected by provider (HTTP 401)",
        )
    except openai.RateLimitError as exc:
        latency = round((time.perf_counter() - t0) * 1000, 2)
        is_quota = "quota" in str(exc).lower() or "billing" in str(exc).lower()
        if is_quota:
            get_failure_cache().record_failure(LLMQuotaExceededError())
        result = AdminPingResponse(
            status="429",
            latency_ms=latency,
            message=f"Rate limit or quota exhaustion (HTTP 429): {'Quota' if is_quota else 'Rate limit'}",
        )
    except Exception as exc:  # noqa: BLE001
        latency = round((time.perf_counter() - t0) * 1000, 2)
        result = AdminPingResponse(
            status="network_error",
            latency_ms=latency,
            message=f"Network or service error: {type(exc).__name__}",
        )

    _LAST_PING_RESULT = (result, now)
    return result


# ==============================================================================
# Learned Knowledge & Governance Curation
# ==============================================================================


@router.get(
    "/api/v1/admin/learned",
    response_model=list[LearnedSapEntry],
    summary="List Learned SAP Knowledge",
    operation_id="list_learned_sap",
    dependencies=[Depends(verify_admin_key)],
)
async def list_learned_sap(
    status_filter: Literal["pending", "approved", "rejected", "all"] = Query(
        default="all", alias="status"
    ),
) -> list[LearnedSapEntry]:
    """Lists learned SAP entries derived from grounded retrieval."""
    store = KnowledgeStore()
    filter_val = None if status_filter == "all" else status_filter
    return store.list_learned_sap(status=filter_val)


@router.post(
    "/api/v1/admin/learned/{entry_id}/approve",
    response_model=dict[str, Any],
    summary="Approve Learned SAP Entry",
    operation_id="approve_learned_sap",
    dependencies=[Depends(verify_admin_key)],
)
async def approve_learned_sap(
    entry_id: str,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Approves a learned SAP entry for official prebuilt integration and logs audit entry."""
    store = KnowledgeStore()
    success = store.set_learned_sap_status(
        entry_id=entry_id, status="approved", auditor=principal.email
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Learned SAP entry '{entry_id}' not found",
        )
    return {"id": entry_id, "status": "approved", "auditor": principal.email}


@router.post(
    "/api/v1/admin/learned/{entry_id}/reject",
    response_model=dict[str, Any],
    summary="Reject Learned SAP Entry",
    operation_id="reject_learned_sap",
    dependencies=[Depends(verify_admin_key)],
)
async def reject_learned_sap(
    entry_id: str,
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    """Rejects a learned SAP entry and logs audit entry."""
    store = KnowledgeStore()
    success = store.set_learned_sap_status(
        entry_id=entry_id, status="rejected", auditor=principal.email
    )
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Learned SAP entry '{entry_id}' not found",
        )
    return {"id": entry_id, "status": "rejected", "auditor": principal.email}


@router.get(
    "/api/v1/admin/glossary/proposals",
    response_model=list[dict[str, Any]],
    summary="List Governance Glossary Proposals",
    operation_id="list_glossary_proposals",
    dependencies=[Depends(verify_admin_key)],
)
async def list_glossary_proposals() -> list[dict[str, Any]]:
    """Returns proposed new subject areas, processes, and business concepts for review."""
    store = KnowledgeStore()
    return store.get_glossary_proposals()
