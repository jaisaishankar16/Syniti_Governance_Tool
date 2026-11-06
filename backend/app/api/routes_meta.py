"""FastAPI routes for metadata, UI placeholder repositories, LLM status, and health probes."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.agents.failure_cache import get_failure_cache
from app.agents.sap_agent import create_sap_agent
from app.agents.settings import get_llm_settings, is_valid_api_key
from app.agents.sql_agent import create_sql_agent
from app.api.deps import (
    get_activity_repo,
    get_principal,
    get_project_repo,
    get_settings,
    verify_admin_key,
)
from app.api.errors import ErrorEnvelope
from app.api.metrics import get_metrics_text
from app.api.models import (
    ActivityItem,
    ExampleQueryItem,
    HealthResponse,
    LLMBudgetStatus,
    LlmSettingsView,
    LLMStatusResponse,
    MetadataRefreshRequest,
    Principal,
    Project,
    ProjectCreateRequest,
    ReadyResponse,
)
from app.api.repositories import ActivityRepository, ProjectRepository
from app.api.settings import ApiSettings
from app.cache.backends import get_cache_backend
from app.guardrails.budget import get_budget_tracker
from app.guardrails.circuit_breaker import get_circuit_breaker
from app.guardrails.settings import get_guardrail_settings
from app.knowledge.settings import get_settings as get_knowledge_settings
from app.knowledge.vector_index import VectorIndex
from app.orchestrator.settings import get_orchestrator_settings
from app.synthesizer.synthesizer import _load_prompt_template
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["Metadata & Operations"])

EXAMPLES_FIXTURE_PATH = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "examples.json"


def _load_workbook_examples() -> list[ExampleQueryItem]:
    """Loads 11 workbook queries, returning ONLY identifier, title, and SQL text."""
    if not EXAMPLES_FIXTURE_PATH.exists():
        return []

    with open(EXAMPLES_FIXTURE_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    items: list[ExampleQueryItem] = []
    for item in data:
        ex_id = str(item.get("id", item.get("source_sheet", "example")))
        sql = str(item.get("input_sql", "")).strip()

        # Derive title from CREATE VIEW statement or fallback to rule name
        match = re.search(r"CREATE\s+VIEW\s+(?:dbo\.)?([A-Za-z0-9_]+)", sql, re.IGNORECASE)
        if match:
            title = match.group(1)
        else:
            title = f"Query {ex_id}"

        items.append(ExampleQueryItem(id=ex_id, title=title, sql=sql))

    return items


# ==============================================================================
# Operational & Health Probes (Root level)
# ==============================================================================


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Liveness Probe",
    operation_id="health_check",
    tags=["Operations"],
)
async def health_check() -> HealthResponse:
    """Liveness probe. Always returns HTTP 200 while the web process is running."""
    return HealthResponse(
        status="ok",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Readiness Probe",
    operation_id="readiness_check",
    tags=["Operations"],
    responses={
        200: {"description": "All subsystems healthy and ready for traffic."},
        503: {"description": "One or more subsystems not ready."},
    },
)
async def readiness_check(
    response: Response,
    settings: ApiSettings = Depends(get_settings),
) -> ReadyResponse:
    """Readiness probe verifying vector indexes, cache connectivity, and LLM configuration.

    Returns HTTP 503 if any check fails (including missing or unconfigured LLM API key).
    """
    k_settings = get_knowledge_settings()

    # Check 1: Vector indexes built and not stale
    sap_idx = VectorIndex("sap", k_settings.sap_index_dir, k_settings.sap_pdf_path, k_settings)
    sql_idx = VectorIndex("sql", k_settings.sql_index_dir, k_settings.sql_pdf_path, k_settings)
    indexes_ok = sap_idx.is_built() and sql_idx.is_built()

    # Check 2: Cache backend connectivity
    cache = await get_cache_backend(settings.rate_limit_backend)
    cache_ok = await cache.ping()

    # Check 3: LLM Configuration
    llm_settings = get_llm_settings()
    raw_key = llm_settings.openai_api_key.get_secret_value() if llm_settings.openai_api_key else ""
    llm_ok = is_valid_api_key(raw_key)

    checks = {
        "indexes_loaded": indexes_ok,
        "cache_healthy": cache_ok,
        "llm_configured": llm_ok,
    }

    all_ready = all(checks.values())
    if not all_ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadyResponse(status="not_ready", checks=checks)

    return ReadyResponse(status="ready", checks=checks)


@router.get(
    "/metrics",
    summary="Prometheus Metrics",
    operation_id="get_prometheus_metrics",
    tags=["Operations"],
    response_class=PlainTextResponse,
)
async def get_metrics(
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    settings: ApiSettings = Depends(get_settings),
) -> PlainTextResponse:
    """Exports Prometheus text-formatted application metrics."""
    if not settings.metrics_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Metrics export is disabled",
        )

    if settings.metrics_require_admin:
        await verify_admin_key(x_admin_key=x_admin_key, settings=settings)

    metrics_bytes = get_metrics_text()
    return PlainTextResponse(
        content=metrics_bytes.decode("utf-8"),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )


# ==============================================================================
# API v1 Metadata Endpoints
# ==============================================================================


@router.get(
    "/api/v1/examples",
    response_model=list[ExampleQueryItem],
    summary="List Curated Examples",
    operation_id="list_examples",
    tags=["Metadata"],
)
async def list_examples() -> list[ExampleQueryItem]:
    """Returns curated SQL examples from the reference workbook for dropdown selection."""
    return _load_workbook_examples()


@router.get(
    "/api/v1/me",
    response_model=Principal,
    summary="Get User Profile",
    operation_id="get_current_user",
    tags=["Identity"],
)
async def get_current_user(
    principal: Principal = Depends(get_principal),
) -> Principal:
    """Returns current user profile for application navbar and UI shell."""
    return principal


@router.get(
    "/api/v1/projects",
    response_model=list[Project],
    summary="List Migration Projects",
    operation_id="list_projects",
    tags=["Projects"],
)
async def list_projects(
    repo: ProjectRepository = Depends(get_project_repo),
) -> list[Project]:
    """Returns configured enterprise migration projects."""
    return await repo.list_projects()


@router.post(
    "/api/v1/projects",
    response_model=Project,
    summary="Create Migration Project (Pending)",
    operation_id="create_project",
    tags=["Projects"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    responses={501: {"model": ErrorEnvelope, "description": "Backend integration pending."}},
)
async def create_project(
    request: Request,
    body: ProjectCreateRequest,
) -> Any:
    """Creates a migration project. Currently returns 501 Not Implemented."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Project creation is not available yet (backend integration pending)",
    )


@router.post(
    "/api/v1/metadata/refresh/stream",
    summary="Refresh Metadata Stream (Pending)",
    operation_id="refresh_metadata_stream",
    tags=["Metadata"],
    status_code=status.HTTP_501_NOT_IMPLEMENTED,
    responses={501: {"model": ErrorEnvelope, "description": "Backend integration pending."}},
)
async def refresh_metadata_stream(
    request: Request,
    body: MetadataRefreshRequest,
) -> Any:
    """Streams metadata refresh logs and progress. Currently returns 501 Not Implemented."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Metadata refresh is not available on this server yet (backend integration pending)",
    )


@router.get(
    "/api/v1/projects/{project_id}",
    response_model=Project,
    summary="Get Migration Project",
    operation_id="get_project_by_id",
    tags=["Projects"],
)
async def get_project(
    project_id: str,
    repo: ProjectRepository = Depends(get_project_repo),
) -> Project:
    """Returns single migration project by ID (PLACEHOLDER UI DATA)."""
    proj = await repo.get_project(project_id)
    if proj is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project '{project_id}' not found",
        )
    return proj


@router.get(
    "/api/v1/activity",
    response_model=list[ActivityItem],
    summary="List Recent Activity",
    operation_id="list_recent_activity",
    tags=["Projects"],
)
async def list_activity(
    limit: int = Query(default=20, ge=1, le=100),
    repo: ActivityRepository = Depends(get_activity_repo),
) -> list[ActivityItem]:
    """Returns recent analysis activity history (PLACEHOLDER UI DATA)."""
    return await repo.list_activity(limit=limit)


@router.get(
    "/api/v1/llm/status",
    response_model=LLMStatusResponse,
    summary="Get LLM Subsystem Status",
    operation_id="get_llm_status",
    tags=["Metadata"],
)
async def get_llm_status() -> LLMStatusResponse:
    """Returns status of LLM subsystem without contacting provider or leaking API keys."""
    llm_settings = get_llm_settings()
    raw_key = llm_settings.openai_api_key.get_secret_value() if llm_settings.openai_api_key else ""
    configured = is_valid_api_key(raw_key)

    cb = get_circuit_breaker()
    bt = get_budget_tracker()
    daily_stats = bt.get_daily_stats()

    from app.guardrails.settings import get_guardrail_settings

    guard_settings = get_guardrail_settings()
    last_err_code, last_err_at = get_failure_cache().get_status()

    return LLMStatusResponse(
        configured=configured,
        model=llm_settings.llm_model,
        api_mode=llm_settings.llm_api_mode,
        base_url_set=bool(llm_settings.openai_base_url),
        circuit_state=cb.state.value,
        budget=LLMBudgetStatus(
            used_tokens=daily_stats.get("total_tokens", 0),
            daily_limit=guard_settings.daily_token_budget,
        ),
        last_error_code=last_err_code,
        last_error_at=last_err_at,
    )


@router.get(
    "/api/v1/llm/settings",
    response_model=LlmSettingsView,
    summary="Get LLM Subsystem Diagnostics & Settings",
    operation_id="get_llm_settings_diagnostics",
    tags=["Metadata"],
)
async def get_llm_settings_view() -> LlmSettingsView:
    """Returns read-only diagnostics and configuration settings of the LLM pipeline."""
    llm_settings = get_llm_settings()
    k_settings = get_knowledge_settings()
    guard_settings = get_guardrail_settings()
    orch_settings = get_orchestrator_settings()
    budget_tracker = get_budget_tracker()
    daily_stats = budget_tracker.get_daily_stats()

    # Sanitize base URL to scheme+host only
    raw_url = str(llm_settings.openai_base_url or "").strip()
    if raw_url:
        p = urlparse(raw_url)
        clean_base_url = f"{p.scheme}://{p.netloc}" if p.netloc else "default"
    else:
        clean_base_url = "default"

    raw_key = llm_settings.openai_api_key.get_secret_value() if llm_settings.openai_api_key else ""
    key_configured = is_valid_api_key(raw_key)

    from app.agents.capabilities import load_capabilities, resolve_effective_modes
    from app.agents.client_factory import get_base_host

    eff_struct, eff_tools, temp_supported = resolve_effective_modes(llm_settings)
    caps = load_capabilities(llm_settings.llm_model, llm_settings.openai_base_url)
    caps_probe_ts = caps.get("probed_at") if caps else None
    base_host = get_base_host(llm_settings.openai_base_url)
    provider_type = "gateway" if llm_settings.openai_base_url else "direct"

    # Prompt versions
    _, sql_v = create_sql_agent("dummy", llm_settings)
    _, sap_v = create_sap_agent("dummy", llm_settings)
    _, synth_v = _load_prompt_template()

    # Index status
    sap_idx = VectorIndex("sap", k_settings.sap_index_dir, k_settings.sap_pdf_path, k_settings)
    sql_idx = VectorIndex("sql", k_settings.sql_index_dir, k_settings.sql_pdf_path, k_settings)
    v_path = getattr(k_settings, "verified_entries_path", None)

    last_err_code, last_err_at = get_failure_cache().get_status()

    return LlmSettingsView(
        model=llm_settings.llm_model,
        api_mode=llm_settings.llm_api_mode,
        structured_output_mode=eff_struct,
        tools_mode=eff_tools,
        temperature=llm_settings.llm_temperature,
        temperature_supported=temp_supported,
        max_turns=llm_settings.llm_max_turns,
        timeout_s=llm_settings.llm_timeout_s,
        max_retries=llm_settings.llm_max_retries,
        provider="OpenAI-compatible",
        provider_type=provider_type,
        base_url=clean_base_url,
        base_host=base_host,
        api_key_configured=key_configured,
        capabilities_probe_timestamp=caps_probe_ts,
        tracing="disabled (forced)",
        learning_enabled=llm_settings.learning_enabled and eff_tools != "prefetch",
        tracing_enabled=False,
        token_budgets={
            "max_request_tokens": guard_settings.per_request_token_budget,
            "daily_token_budget": guard_settings.daily_token_budget,
            "daily_tokens_used": daily_stats.get("total_tokens", 0),
            "daily_cost_usd": daily_stats.get("cost_usd", 0.0),
        },
        circuit_state=get_circuit_breaker().state.value,
        last_error_code=last_err_code,
        last_error_at=last_err_at,
        prompt_versions={
            "sql_agent": sql_v,
            "sap_agent": sap_v,
            "synthesizer": synth_v,
        },
        embedding_model=k_settings.embedding_model,
        knowledge_index_status={
            "sap_index_built": sap_idx.is_built(),
            "sql_index_built": sql_idx.is_built(),
            "verified_entries_present": bool(v_path and v_path.exists()),
        },
        cache_backend={
            "type": orch_settings.cache_backend,
            "l1_ttl_s": orch_settings.cache_ttl_l1_s,
            "l2_ttl_s": orch_settings.cache_ttl_l2_s,
            "similarity_threshold": orch_settings.similarity_reuse_threshold,
        },
        guardrail_modes={
            "max_sql_length": guard_settings.max_sql_chars,
            "concurrency_limit": guard_settings.max_concurrent_runs,
            "require_subset_containment": True,
            "risk_floor_elevation": True,
        },
        pricing_configured=bool(guard_settings.pricing_table),
    )
