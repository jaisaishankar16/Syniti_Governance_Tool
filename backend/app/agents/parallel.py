"""Parallel execution of SQLSemanticsAgent and SAPDomainAgent."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.agents.knowledge_pack import build_sap_pack, build_sql_pack
from app.agents.model_factory import build_model
from app.agents.models import (
    AgentErrorDetails,
    AgentOutcome,
    AgentsResult,
    SAPAnalysis,
    SQLAnalysis,
)
from app.agents.runner import run_agent
from app.agents.sap_agent import build_sap_agent_input, create_sap_agent
from app.agents.settings import LLMSettings, get_llm_settings
from app.agents.sql_agent import build_sql_agent_input, create_sql_agent
from app.guardrails.agent_output import guardrail_sap_output, guardrail_sql_output
from app.guardrails.models import GuardrailReport
from app.guardrails.settings import get_guardrail_settings

if TYPE_CHECKING:
    from agents import Model

    from app.agents.models import AgentDeps
    from app.parser.models import ParsedQuery

logger = logging.getLogger(__name__)


async def analyze_with_agents(
    parsed: ParsedQuery,
    deps_factory: Callable[[], tuple[AgentDeps, AgentDeps]],
    settings: LLMSettings | None = None,
    model: Model | str | None = None,
    request_id: str | None = None,
    canary_token: str = "",
) -> AgentsResult:
    """Builds knowledge packs and runs SQL and SAP agents in parallel.

    If one agent fails or times out, the other's result is preserved with degraded=True.
    If both fail, returns degraded=True with two failed outcomes without raising.
    Agent outputs are reconciled with parser ground truth and sanitized by guardrails.
    """
    start_time = time.perf_counter()
    active_settings = settings or get_llm_settings()
    req_id = request_id or str(uuid.uuid4())

    sql_deps, sap_deps = deps_factory()
    sql_deps.request_id = req_id
    sap_deps.request_id = req_id

    # 1. Deterministic knowledge packs
    sql_pack = build_sql_pack(parsed, sql_deps.store, max_items=active_settings.pack_max_items)
    sap_pack = build_sap_pack(parsed, sap_deps.store, max_items=active_settings.pack_max_items)

    # 2. Agent input builders
    sql_input = build_sql_agent_input(parsed, sql_pack)
    sap_input = build_sap_agent_input(parsed, sap_pack)

    from app.agents.capabilities import resolve_effective_modes

    _, effective_tools, _ = resolve_effective_modes(active_settings)
    if effective_tools == "prefetch":
        logger.warning(
            "Running agents in prefetch mode: dynamic tool calling and write-back disabled."
        )
        prefetched_sql = []
        for u_sql in sql_pack.unresolved:
            hits_sql = sql_deps.sql_retriever.search(u_sql.construct, k=2)
            if hits_sql:
                prefetched_sql.append(
                    {"item": u_sql.construct, "hits": [h.text[:400] for h in hits_sql]}
                )
        if prefetched_sql:
            sql_input += f"\n\n<prefetched_knowledge>\n{json.dumps(prefetched_sql, indent=2)}\n</prefetched_knowledge>"

        prefetched_sap = []
        for u_sap in sap_pack.unresolved:
            res_sap = sap_deps.sap_retriever.search(u_sap.table, k=2)
            if res_sap.hits:
                prefetched_sap.append(
                    {"item": u_sap.table, "hits": [h.text[:400] for h in res_sap.hits]}
                )
        if prefetched_sap:
            sap_input += f"\n\n<prefetched_knowledge>\n{json.dumps(prefetched_sap, indent=2)}\n</prefetched_knowledge>"

    # 3. Model & Agent creation
    model_instance = model if model is not None else build_model(active_settings)
    sql_agent, sql_prompt_version = create_sql_agent(model_instance, active_settings)
    sap_agent, sap_prompt_version = create_sap_agent(model_instance, active_settings)

    # 4. Concurrent execution
    logger.info("Executing SQL and SAP agents in parallel (request_id=%s)", req_id)
    raw_results = await asyncio.gather(
        run_agent(
            sql_agent,
            sql_input,
            sql_deps,
            active_settings,
            SQLAnalysis,
            sql_prompt_version,
        ),
        run_agent(
            sap_agent,
            sap_input,
            sap_deps,
            active_settings,
            SAPAnalysis,
            sap_prompt_version,
        ),
        return_exceptions=True,
    )

    sql_res = raw_results[0]
    sap_res = raw_results[1]

    # Handle SQL outcome
    sql_outcome: AgentOutcome[SQLAnalysis]
    if isinstance(sql_res, BaseException):
        logger.error("Unhandled exception in SQL agent: %s", sql_res)
        sql_outcome = AgentOutcome[SQLAnalysis](
            agent_name="SQLSemanticsAgent",
            status="failed",
            output=None,
            error=AgentErrorDetails(
                type=type(sql_res).__name__,
                message=str(sql_res),
                retryable=False,
            ),
            model=active_settings.llm_model,
            prompt_version=sql_prompt_version,
        )
    else:
        sql_outcome = sql_res

    # Handle SAP outcome
    sap_outcome: AgentOutcome[SAPAnalysis]
    if isinstance(sap_res, BaseException):
        logger.error("Unhandled exception in SAP agent: %s", sap_res)
        sap_outcome = AgentOutcome[SAPAnalysis](
            agent_name="SAPDomainAgent",
            status="failed",
            output=None,
            error=AgentErrorDetails(
                type=type(sap_res).__name__,
                message=str(sap_res),
                retryable=False,
            ),
            model=active_settings.llm_model,
            prompt_version=sap_prompt_version,
        )
    else:
        sap_outcome = sap_res

    # 5. Post-flight guardrail reconciliation
    guard_settings = get_guardrail_settings()
    guard_report = GuardrailReport()

    if sql_outcome.status == "ok" and sql_outcome.output is not None:
        rec_sql, sql_events, sql_blocked = guardrail_sql_output(
            sql_outcome.output,
            parsed,
            sql_pack,
            canary_token=canary_token,
            settings=guard_settings,
            request_id=req_id,
        )
        for ev in sql_events:
            guard_report.add_event(ev)
        if sql_blocked:
            sql_outcome.status = "blocked"
            sql_outcome.output = None
        else:
            sql_outcome.output = rec_sql

    if sap_outcome.status == "ok" and sap_outcome.output is not None:
        rec_sap, sap_events, sap_blocked = guardrail_sap_output(
            sap_outcome.output,
            parsed,
            sap_pack,
            canary_token=canary_token,
            settings=guard_settings,
            request_id=req_id,
        )
        for ev in sap_events:
            guard_report.add_event(ev)
        if sap_blocked:
            sap_outcome.status = "blocked"
            sap_outcome.output = None
        else:
            sap_outcome.output = rec_sap

    total_latency = (time.perf_counter() - start_time) * 1000
    degraded = (sql_outcome.status != "ok") or (sap_outcome.status != "ok")

    logger.info(
        "Parallel analysis finished: degraded=%s, latency_ms=%.2f, sql_status=%s, sap_status=%s, guard_events=%d",
        degraded,
        total_latency,
        sql_outcome.status,
        sap_outcome.status,
        len(guard_report.events),
    )

    return AgentsResult(
        sql=sql_outcome,
        sap=sap_outcome,
        degraded=degraded,
        total_latency_ms=round(total_latency, 2),
        request_id=req_id,
        guardrails=guard_report,
    )
