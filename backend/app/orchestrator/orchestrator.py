"""Central analysis orchestrator coordinating guardrails, parsing, multi-tier caching, parallel agents, and synthesis."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.agents.failure_cache import get_failure_cache
from app.agents.knowledge_pack import build_sap_pack, build_sql_pack
from app.agents.model_factory import build_model
from app.agents.models import (
    AgentDeps,
    AgentErrorDetails,
    AgentOutcome,
    SAPAnalysis,
    SQLAnalysis,
)
from app.agents.runner import run_agent
from app.agents.sap_agent import build_sap_agent_input, create_sap_agent
from app.agents.settings import ENV_FILE_PATH, LLMSettings, get_llm_settings
from app.agents.sql_agent import build_sql_agent_input, create_sql_agent
from app.cache.backends import CacheBackend, get_cache_backend
from app.cache.keys import (
    compute_knowledge_fingerprint,
    make_l1_key,
    make_l2_sap_key,
    make_l2_sql_key,
)
from app.cache.layers import (
    is_cacheable,
    lookup_l1,
    lookup_l2_sap,
    lookup_l2_sql,
    write_l1,
    write_l2_sap,
    write_l2_sql,
)
from app.cache.similarity import get_similarity_index
from app.cache.singleflight import get_single_flight
from app.errors import (
    LLMAuthError,
    LLMBudgetExceededError,
    LLMNotConfiguredError,
    LLMQuotaExceededError,
    LLMRateLimitedError,
    LLMUnavailableError,
)
from app.guardrails.agent_output import guardrail_sap_output, guardrail_sql_output
from app.guardrails.budget import get_budget_tracker
from app.guardrails.circuit_breaker import CircuitBreakerOpenError, get_circuit_breaker
from app.guardrails.pipeline import pre_flight_pipeline
from app.guardrails.settings import get_guardrail_settings
from app.knowledge.retrievers import SapRetriever, SqlDocRetriever
from app.knowledge.store import KnowledgeStore
from app.orchestrator.progress import ProgressCallback, emit_progress
from app.orchestrator.settings import OrchestratorSettings, get_orchestrator_settings
from app.parser.sql_parser import SqlParser
from app.synthesizer.models import (
    BusinessIntelligence,
    CacheDetail,
    PromptVersions,
    TokenUsageBreakdown,
)
from app.synthesizer.synthesizer import (
    _load_prompt_template as _load_synth_prompt_template,
)
from app.synthesizer.synthesizer import (
    synthesize,
)

if TYPE_CHECKING:
    from agents import Model

logger = logging.getLogger(__name__)


# ==============================================================================
# QuerySource Protocol
# ==============================================================================


class SqlRequest(BaseModel):
    """An individual SQL analysis request with optional source identifier and metadata."""

    model_config = ConfigDict(extra="forbid")

    sql: str
    source_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class QuerySource(Protocol):
    """Protocol for streaming SQL query requests into the orchestrator."""

    def __aiter__(self) -> AsyncIterator[SqlRequest]: ...


class TextInputSource:
    """Simple in-memory QuerySource over a list of SQL requests or strings."""

    def __init__(self, queries: Sequence[SqlRequest | str]) -> None:
        self.requests: list[SqlRequest] = []
        for q in queries:
            if isinstance(q, str):
                self.requests.append(SqlRequest(sql=q))
            else:
                self.requests.append(q)

    async def __aiter__(self) -> AsyncIterator[SqlRequest]:
        for req in self.requests:
            yield req


# ==============================================================================
# Orchestrator Main Entry Point
# ==============================================================================


async def analyze(
    sql: str,
    *,
    force_refresh: bool = False,
    request_id: str | None = None,
    on_progress: ProgressCallback | None = None,
    model: Model | str | None = None,
    settings: OrchestratorSettings | None = None,
    llm_settings: LLMSettings | None = None,
    cache_backend: CacheBackend | None = None,
) -> BusinessIntelligence:
    """End-to-end analysis orchestrator executing guardrails, caching, agents, and synthesis.

    Under Project Rules (see docs/PROJECT_RULES.md), the orchestrator works ONLY through the
    OpenAI Agents SDK and an OpenAI-compatible LLM. Answers are assembled strictly with the LLM.
    """
    start_time = time.perf_counter()
    orch_settings = settings or get_orchestrator_settings()
    active_llm_settings = llm_settings or get_llm_settings()
    guard_settings = get_guardrail_settings()
    req_id = request_id or str(uuid.uuid4())
    stage_timings: dict[str, float] = {}

    # 1. Check configuration FIRST before any parsing, retrieval, or cache lookup.
    # An active key is required unless an explicit custom Model instance (e.g. test double) was injected.
    is_custom_model_instance = model is not None and not isinstance(model, str)
    if not is_custom_model_instance and active_llm_settings.openai_api_key is None:
        raise LLMNotConfiguredError(
            message=(
                "OpenAI API key is not configured or is an invalid placeholder. "
                "Please configure OPENAI_API_KEY in your environment or root .env file. "
                "Run 'python -m app.agents.cli --check-env' to diagnose configuration."
            ),
            details=f"Looked in OS environment variables and root .env file ({ENV_FILE_PATH})",
        )

    # 1b. Check 60-second fail-fast window for recent auth or quota errors
    if not is_custom_model_instance:
        get_failure_cache().check_failure_window()

    # 2. Check Circuit Breaker & Budget
    cb = get_circuit_breaker()
    bt = get_budget_tracker()

    daily_check = bt.check_daily_budget(settings=guard_settings, request_id=req_id)
    if not daily_check.allowed:
        ev_msg = (
            daily_check.report.events[0].message
            if daily_check.report.events
            else "Daily budget exceeded"
        )
        logger.warning("Daily token budget exceeded: %s", ev_msg)
        raise LLMBudgetExceededError(
            message="Daily LLM usage budget limit has been reached for this deployment.",
            details=ev_msg,
        )

    try:
        cb.check_call_allowed(req_id)
    except CircuitBreakerOpenError as exc:
        logger.warning("Circuit breaker is OPEN: %s", exc)
        raise LLMUnavailableError(
            message="LLM service is currently unavailable because the circuit breaker is OPEN.",
            details=exc.message,
        ) from exc

    # Initialize backend & store
    backend = cache_backend or await get_cache_backend(
        orch_settings.cache_backend, orch_settings.redis_url
    )
    store = KnowledgeStore()
    sap_retriever = SapRetriever()
    sql_retriever = SqlDocRetriever()

    # 3. Pre-flight Guardrails
    t_stage = time.perf_counter()
    await emit_progress(
        on_progress, "guardrails", "Executing pre-flight security guardrails", start_time
    )
    sanitized = pre_flight_pipeline(sql, guard_settings, request_id=req_id)
    stage_timings["guardrails"] = round((time.perf_counter() - t_stage) * 1000, 2)

    # 4. Deterministic SQL Parsing & Naming Convention Analysis
    t_stage = time.perf_counter()
    await emit_progress(
        on_progress, "parsing", "Parsing T-SQL query and validating naming conventions", start_time
    )
    sql_parser = SqlParser()
    parsed = sql_parser.parse(sanitized.sanitized_sql)
    stage_timings["parsing"] = round((time.perf_counter() - t_stage) * 1000, 2)

    # 5. Resolve prompt versions & keys for L1 Cache lookup
    model_name = active_llm_settings.llm_model if model is None else str(model)
    k_fingerprint = compute_knowledge_fingerprint(store)
    active_base_url = str(active_llm_settings.openai_base_url or "")

    # Dummy model creation just to get version strings
    _, sql_p_ver = create_sql_agent("dummy", active_llm_settings)
    _, sap_p_ver = create_sap_agent("dummy", active_llm_settings)
    _, synth_p_ver = _load_synth_prompt_template()

    prompt_versions = PromptVersions(sql=sql_p_ver, sap=sap_p_ver, synthesizer=synth_p_ver)

    l1_key = make_l1_key(
        normalized_sql=parsed.normalized_sql,
        view_name=parsed.view_name,
        model=model_name,
        sql_prompt_version=sql_p_ver,
        sap_prompt_version=sap_p_ver,
        synth_prompt_version=synth_p_ver,
        knowledge_fingerprint=k_fingerprint,
        guardrail_policy_version=guard_settings.policy_version,
        base_url=active_base_url,
    )

    # Check L1 Cache
    t_stage = time.perf_counter()
    await emit_progress(on_progress, "cache_lookup", "Checking L1 final cache", start_time)
    if not force_refresh:
        l1_hit = await lookup_l1(l1_key, backend)
        if l1_hit is not None:
            stage_timings["cache_lookup"] = round((time.perf_counter() - t_stage) * 1000, 2)
            l1_hit.meta.request_id = req_id
            l1_hit.meta.stage_timings_ms = stage_timings
            await emit_progress(on_progress, "done", "L1 cache hit returned", start_time)
            return l1_hit

    # 6. Pipeline Execution with SingleFlight Coalescing
    single_flight = get_single_flight(backend)

    async def _run_pipeline() -> BusinessIntelligence:
        cache_detail = CacheDetail()
        token_breakdown = TokenUsageBreakdown()
        agent_timings: dict[str, float] = {}

        # 6a. L2 / L3 Lookups
        sql_analysis: SQLAnalysis | None = None
        sap_analysis: SAPAnalysis | None = None

        l2_sql_key = make_l2_sql_key(
            parsed.normalized_sql, sql_p_ver, model_name, base_url=active_base_url
        )
        table_names = [t.raw_name for t in parsed.tables]
        field_names = [c.column for c in parsed.columns]
        naming_facts = [f"{t.raw_name}:{t.naming.object_kind}" for t in parsed.tables if t.naming]
        l2_sap_key = make_l2_sap_key(
            table_names,
            field_names,
            naming_facts,
            sap_p_ver,
            model_name,
            k_fingerprint,
            base_url=active_base_url,
        )

        if not force_refresh:
            sql_analysis = await lookup_l2_sql(l2_sql_key, backend)
            if sql_analysis:
                cache_detail.sql_agent = "hit"

            sap_analysis = await lookup_l2_sap(l2_sap_key, backend)
            if sap_analysis:
                cache_detail.sap_agent = "hit"

        # L3 Similarity Cache lookup if both L2 missed
        sim_index = get_similarity_index()
        similarity_score: float | None = None
        if sql_analysis is None and not force_refresh:
            try:
                sim_action, sim_payload, score = sim_index.find_similar(
                    parsed.normalized_sql,
                    table_names,
                    reuse_threshold=orch_settings.similarity_reuse_threshold,
                    hint_threshold=orch_settings.similarity_hint_threshold,
                )
                similarity_score = score
                if sim_action == "similar_reused" and sim_payload:
                    try:
                        sql_analysis = SQLAnalysis.model_validate(sim_payload.get("sql", {}))
                        # Reconcile through Phase 3B agent output guardrail
                        sql_pack = build_sql_pack(
                            parsed, store, max_items=active_llm_settings.pack_max_items
                        )
                        sql_analysis, _, blocked = guardrail_sql_output(
                            sql_analysis,
                            parsed,
                            sql_pack,
                            sanitized.canary_token,
                            guard_settings,
                            req_id,
                        )
                        if not blocked:
                            cache_detail.sql_agent = "similar_reused"
                        else:
                            sql_analysis = None
                    except Exception:  # noqa: BLE001
                        sql_analysis = None
                elif sim_action == "similar_hint":
                    cache_detail.sql_agent = "similar_hint"
            except Exception as exc:  # noqa: BLE001
                logger.warning("L3 similarity lookup skipped: %s", exc)

        # 6b. Run only the needed agents in parallel
        need_sql = sql_analysis is None
        need_sap = sap_analysis is None

        if need_sql or need_sap:
            await emit_progress(
                on_progress,
                "agents_running",
                "Executing specialized agents in parallel",
                start_time,
            )
            t_agents = time.perf_counter()

            model_inst = model if model is not None else build_model(active_llm_settings)

            # Build dependencies
            sql_deps = AgentDeps(
                store=store,
                sap_retriever=sap_retriever,
                sql_retriever=sql_retriever,
                settings=active_llm_settings,
                request_id=req_id,
            )
            sap_deps = AgentDeps(
                store=store,
                sap_retriever=sap_retriever,
                sql_retriever=sql_retriever,
                settings=active_llm_settings,
                request_id=req_id,
            )

            # Build packs
            sql_pack = build_sql_pack(parsed, store, max_items=active_llm_settings.pack_max_items)
            sap_pack = build_sap_pack(parsed, store, max_items=active_llm_settings.pack_max_items)

            tasks: list[Any] = []
            if need_sql:
                sql_agent, _ = create_sql_agent(model_inst, active_llm_settings)
                sql_input = build_sql_agent_input(parsed, sql_pack)
                tasks.append(
                    run_agent(
                        sql_agent, sql_input, sql_deps, active_llm_settings, SQLAnalysis, sql_p_ver
                    )
                )
            if need_sap:
                sap_agent, _ = create_sap_agent(model_inst, active_llm_settings)
                sap_input = build_sap_agent_input(parsed, sap_pack)
                tasks.append(
                    run_agent(
                        sap_agent, sap_input, sap_deps, active_llm_settings, SAPAnalysis, sap_p_ver
                    )
                )

            raw_agent_results = await asyncio.gather(*tasks, return_exceptions=True)

            idx = 0
            if need_sql:
                sql_res = raw_agent_results[idx]
                idx += 1
                if (
                    isinstance(sql_res, AgentOutcome)
                    and sql_res.status == "ok"
                    and isinstance(sql_res.output, SQLAnalysis)
                ):
                    # Guardrail output reconciliation
                    sql_analysis, _, blk = guardrail_sql_output(
                        sql_res.output,
                        parsed,
                        sql_pack,
                        sanitized.canary_token,
                        guard_settings,
                        req_id,
                    )
                    if not blk:
                        token_breakdown.sql = sql_res.usage.total_tokens
                        agent_timings["sql_agent"] = sql_res.latency_ms
                        # Write L2 cache
                        await write_l2_sql(
                            l2_sql_key,
                            sql_analysis,
                            backend,
                            model_name,
                            sql_p_ver,
                            orch_settings.cache_ttl_l2_s,
                        )
                    else:
                        sql_analysis = None
                else:
                    sql_analysis = None
            else:
                cache_detail.sql_agent = (
                    "skipped" if cache_detail.sql_agent == "miss" else cache_detail.sql_agent
                )

            if need_sap:
                sap_res = raw_agent_results[idx]
                if (
                    isinstance(sap_res, AgentOutcome)
                    and sap_res.status == "ok"
                    and isinstance(sap_res.output, SAPAnalysis)
                ):
                    # Guardrail output reconciliation
                    sap_analysis, _, blk = guardrail_sap_output(
                        sap_res.output,
                        parsed,
                        sap_pack,
                        sanitized.canary_token,
                        guard_settings,
                        req_id,
                    )
                    if not blk:
                        token_breakdown.sap = sap_res.usage.total_tokens
                        agent_timings["sap_agent"] = sap_res.latency_ms
                        # Write L2 cache
                        await write_l2_sap(
                            l2_sap_key,
                            sap_analysis,
                            backend,
                            model_name,
                            sap_p_ver,
                            k_fingerprint,
                            orch_settings.cache_ttl_l2_s,
                        )
                    else:
                        sap_analysis = None
                else:
                    sap_analysis = None
            else:
                cache_detail.sap_agent = (
                    "skipped" if cache_detail.sap_agent == "miss" else cache_detail.sap_agent
                )

            stage_timings["agents_running"] = round((time.perf_counter() - t_agents) * 1000, 2)

        # Abort if BOTH domain agents failed (no response fabricated without LLM)
        if sql_analysis is None and sap_analysis is None:
            logger.error("Both domain agents (SQL and SAP) failed or produced no output")

            # Determine root-cause error with priority: auth > quota > rate limit > unavailable
            errors_produced: list[AgentErrorDetails] = []
            if need_sql and isinstance(sql_res, AgentOutcome) and sql_res.error:
                errors_produced.append(sql_res.error)
            if need_sap and isinstance(sap_res, AgentOutcome) and sap_res.error:
                errors_produced.append(sap_res.error)

            error_codes = {e.error_code for e in errors_produced if e.error_code}

            if "LLM_AUTH_FAILED" in error_codes:
                raise LLMAuthError()
            if "LLM_QUOTA_EXCEEDED" in error_codes:
                raise LLMQuotaExceededError()
            if "LLM_RATE_LIMITED" in error_codes:
                raise LLMRateLimitedError()
            raise LLMUnavailableError(
                message="Both specialized domain agents (SQL and SAP) failed to produce output.",
                details="No domain analysis could be completed by either agent",
            )

        # 6c. Synthesizer Stage
        t_synth = time.perf_counter()
        await emit_progress(
            on_progress,
            "synthesizing",
            "Synthesizing executive business intelligence and governance records",
            start_time,
        )

        synth_deps = AgentDeps(
            store=store,
            sap_retriever=sap_retriever,
            sql_retriever=sql_retriever,
            settings=active_llm_settings,
            request_id=req_id,
        )

        bi, _synth_report = await synthesize(
            parsed=parsed,
            sql_analysis=sql_analysis,
            sap_analysis=sap_analysis,
            deps=synth_deps,
            settings=active_llm_settings,
            model=model,
            request_id=req_id,
            canary_token=sanitized.canary_token,
            boundary_id=sanitized.boundary_id,
            prompt_versions=prompt_versions,
            cache_detail=cache_detail,
            agent_timings=agent_timings,
            token_usage=token_breakdown,
            degraded=(sql_analysis is None or sap_analysis is None),
        )
        stage_timings["synthesizing"] = round((time.perf_counter() - t_synth) * 1000, 2)

        # Embedding tokens and cost accounting
        from app.embeddings.settings import get_embedding_settings

        emb_settings = get_embedding_settings()
        sql_emb_tokens = (
            sql_deps.run_trace.embedding_tokens_count
            if ("sql_deps" in locals() and sql_deps.run_trace is not None)
            else 0
        )
        sap_emb_tokens = (
            sap_deps.run_trace.embedding_tokens_count
            if ("sap_deps" in locals() and sap_deps.run_trace is not None)
            else 0
        )
        total_emb_tokens = sql_emb_tokens + sap_emb_tokens
        bi.meta.token_usage.embedding = total_emb_tokens
        bi.meta.token_usage.total += total_emb_tokens

        if total_emb_tokens > 0 and emb_settings.embedding_price_per_m_tokens is not None:
            emb_cost = (total_emb_tokens / 1_000_000.0) * emb_settings.embedding_price_per_m_tokens
            bi.meta.estimated_cost_usd = round((bi.meta.estimated_cost_usd or 0.0) + emb_cost, 6)
            get_budget_tracker().record_usage(
                prompt_tokens=total_emb_tokens,
                completion_tokens=0,
                model=emb_settings.embedding_model,
                request_id=req_id,
            )

        # Update timings & similarity in meta
        bi.meta.similarity_score = similarity_score
        stage_timings["total"] = round((time.perf_counter() - start_time) * 1000, 2)
        bi.meta.stage_timings_ms = stage_timings

        # 6d. Write L1 and L3 caches if eligible
        if is_cacheable(bi):
            await write_l1(
                l1_key,
                bi,
                backend,
                ttl_seconds=orch_settings.cache_ttl_l1_s,
            )
            # Add to L3 similarity index
            sim_index.add(
                l1_key,
                parsed.normalized_sql,
                table_names,
                {"sql": sql_analysis, "sap": sap_analysis},
            )

        return bi

    # Execute with SingleFlight
    result, was_coalesced = await single_flight.execute(l1_key, _run_pipeline)
    if was_coalesced:
        result.meta.warnings.append("Request coalesced via SingleFlight concurrent lock")

    get_failure_cache().clear()
    await emit_progress(on_progress, "done", "Analysis pipeline completed successfully", start_time)
    return result
