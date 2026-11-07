"""FastAPI application factory, lifespan management, and router registration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

from app.agents.settings import get_llm_settings, is_valid_api_key
from app.api.errors import (
    app_error_exception_handler,
    guardrail_blocked_exception_handler,
    request_validation_exception_handler,
    sql_parse_exception_handler,
    starlette_http_exception_handler,
    unhandled_exception_handler,
)
from app.api.middleware import ProductionMiddleware, get_rate_limiter
from app.api.repositories import (
    DefaultProjectRepository,
    JsonLinesActivityRepository,
)
from app.api.routes_admin import router as admin_router
from app.api.routes_analyze import router as analyze_router
from app.api.routes_meta import router as meta_router
from app.api.settings import ApiSettings, get_api_settings
from app.cache.backends import get_cache_backend
from app.errors import AppError
from app.guardrails.budget import get_budget_tracker
from app.guardrails.circuit_breaker import get_circuit_breaker
from app.guardrails.logging_filter import GuardrailLoggingFilter
from app.guardrails.models import GuardrailBlocked
from app.knowledge.settings import get_settings as get_knowledge_settings
from app.knowledge.store import KnowledgeStore
from app.knowledge.vector_index import VectorIndex
from app.orchestrator.orchestrator import analyze as default_analyze
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlglot.errors import ParseError
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


def _configure_logging(settings: ApiSettings) -> None:
    """Configures application logging with security filters and optional JSON format."""
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Attach secret & SQL masking filter
    sec_filter = GuardrailLoggingFilter(debug_log_sql=settings.debug_log_sql)
    for handler in root_logger.handlers:
        handler.addFilter(sec_filter)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Manages application startup warmups, resource initialization, and graceful shutdown."""
    settings: ApiSettings = app.state.settings
    _configure_logging(settings)

    logger.info("Initializing Syniti Orchestrator API (environment: %s)", settings.app_env)

    # 1. Log LLM configured status WITHOUT failing if missing
    llm_settings = get_llm_settings()
    raw_key = llm_settings.openai_api_key.get_secret_value() if llm_settings.openai_api_key else ""
    llm_configured = is_valid_api_key(raw_key)
    logger.info(
        "LLM Subsystem Status: configured=%s, model=%s, api_mode=%s",
        llm_configured,
        llm_settings.llm_model,
        llm_settings.llm_api_mode,
    )

    # 2. Warm Knowledge Store & FAISS Vector Indexes
    t_warm = time.perf_counter()
    k_settings = get_knowledge_settings()
    store = KnowledgeStore(k_settings)
    app.state.knowledge_store = store

    # Check vector index manifests for logging (startup never fails if missing/stale)
    sap_idx = VectorIndex("sap_docs", k_settings.sap_index_dir, k_settings.sap_pdf_path, k_settings)
    sql_idx = VectorIndex("sql_docs", k_settings.sql_index_dir, k_settings.sql_pdf_path, k_settings)

    for idx in (sap_idx, sql_idx):
        stale, reason = idx.is_stale()
        if stale:
            logger.warning(
                "Knowledge vector index '%s' is missing or stale (%s). "
                "Semantic retrieval disabled until built with 'python -m app.knowledge.build_index --target all'.",
                idx.index_name,
                reason,
            )

    logger.info(
        "Knowledge store initialized in %.2fms",
        (time.perf_counter() - t_warm) * 1000,
    )

    # 3. Initialize Cache Backend (with in-memory fallback on Redis failure)
    cache_backend = await get_cache_backend(settings.rate_limit_backend)
    app.state.cache_backend = cache_backend
    logger.info("Cache backend initialized: %s", type(cache_backend).__name__)

    # 4. Singletons in state
    app.state.budget_tracker = get_budget_tracker()
    app.state.circuit_breaker = get_circuit_breaker()
    app.state.rate_limiter = get_rate_limiter()
    app.state.active_analyses = 0
    app.state.accepting_analyses = True

    # Repositories for UI shell
    if not hasattr(app.state, "project_repo") or app.state.project_repo is None:
        app.state.project_repo = DefaultProjectRepository()
    if not hasattr(app.state, "activity_repo") or app.state.activity_repo is None:
        app.state.activity_repo = JsonLinesActivityRepository(
            file_path=settings.activity_data_dir / "activity.jsonl"
        )

    yield

    # ==========================================================================
    # Graceful Shutdown
    # ==========================================================================
    logger.info(
        "Shutdown initiated: waiting for in-flight requests (grace: %.1fs)...",
        settings.shutdown_grace_s,
    )
    app.state.accepting_analyses = False

    t_start_shutdown = time.perf_counter()
    while app.state.active_analyses > 0:
        if (time.perf_counter() - t_start_shutdown) > settings.shutdown_grace_s:
            logger.warning(
                "Grace period expired with %d active analyses still running",
                app.state.active_analyses,
            )
            break
        await asyncio.sleep(0.5)

    logger.info("Shutdown completed cleanly.")


def create_app(
    settings: ApiSettings | None = None,
    orchestrator_factory: Callable[..., Any] | None = None,
    skip_index_check: bool = False,
) -> FastAPI:
    """Creates and configures a FastAPI application instance."""
    app_settings = settings or get_api_settings()

    docs_url = "/docs" if app_settings.docs_enabled else None
    redoc_url = "/redoc" if app_settings.docs_enabled else None
    openapi_url = "/openapi.json" if app_settings.docs_enabled else None

    app = FastAPI(
        title="MDM Syniti Analysis Orchestrator API",
        description=(
            "Enterprise SAP Migration & Data Governance SQL Analysis Engine.\n"
            "Under Project Rules (see docs/PROJECT_RULES.md), all queries are parsed and synthesized "
            "exclusively through the OpenAI Agents SDK and verified domain knowledge."
        ),
        version="1.0.0",
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )

    app.state.settings = app_settings
    app.state.skip_index_check = skip_index_check
    app.state.orchestrator = orchestrator_factory or default_analyze

    # Register Exception Handlers
    app.add_exception_handler(RequestValidationError, request_validation_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(ParseError, sql_parse_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(GuardrailBlocked, guardrail_blocked_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(AppError, app_error_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, starlette_http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_exception_handler)

    # Register Routers
    app.include_router(meta_router)
    app.include_router(analyze_router)
    app.include_router(admin_router)

    # Register Middleware
    # 1. CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID", "Retry-After"],
    )

    # 2. Production Middleware (Request ID, Logging, Security Headers, Host, Body Limit, Rate Limit, Metrics)
    app.add_middleware(ProductionMiddleware, settings=app_settings)

    return app
