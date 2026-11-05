"""Typed tools registered with @function_tool for SAP and SQL agents."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from agents import FunctionTool, RunContextWrapper, function_tool
from pydantic import ValidationError

from app.agents.models import AgentDeps, SqlKeywordLookupResult, ToolCallRecord
from app.guardrails.settings import get_guardrail_settings
from app.guardrails.tool_policy import check_learned_write_allowed, validate_tool_arguments
from app.knowledge.models import (
    Evidence,
    LearnedSapEntry,
    RetrievalHit,
    SapLookupResult,
    SapSearchResult,
    SaveResult,
)

logger = logging.getLogger(__name__)


# Enable direct invocation of tools in unit tests
def _tool_call(self: Any, *args: Any, **kwargs: Any) -> Any:
    fn = getattr(self, "func", None)
    if callable(fn):
        return fn(*args, **kwargs)
    return None


FunctionTool.__call__ = _tool_call  # type: ignore[assignment]


# ==============================================================================
# SAP Agent Tools
# ==============================================================================


def _lookup_sap_knowledge(
    ctx: RunContextWrapper[AgentDeps],
    table: str,
    field: str | None = None,
) -> SapLookupResult:
    """Looks up standard SAP table or field metadata in prebuilt, learned, or catalog tables."""
    start_t = time.perf_counter()
    deps = ctx.context
    args_summary = f"table={table}, field={field}"

    guard_settings = get_guardrail_settings()
    arg_events = validate_tool_arguments(
        "lookup_sap_knowledge",
        {"table_name": table, "field_name": field},
        guard_settings,
        deps.request_id,
    )
    for ev in arg_events:
        if ev.action == "block":
            duration = (time.perf_counter() - start_t) * 1000
            deps.run_trace.tool_calls.append(
                ToolCallRecord(
                    name="lookup_sap_knowledge",
                    args_summary=args_summary,
                    duration_ms=round(duration, 2),
                    ok=False,
                    error=ev.message,
                )
            )
            query_str = f"{table}.{field}" if field else table
            return SapLookupResult(
                query=query_str,
                found=False,
                level="none",
                notes=f"Guardrail blocked: {ev.message}",
            )

    try:
        res = deps.store.lookup_sap(table, field)
        duration = (time.perf_counter() - start_t) * 1000
        deps.run_trace.tool_calls.append(
            ToolCallRecord(
                name="lookup_sap_knowledge",
                args_summary=args_summary,
                duration_ms=round(duration, 2),
                ok=True,
            )
        )
        return res
    except Exception as exc:  # noqa: BLE001
        duration = (time.perf_counter() - start_t) * 1000
        logger.warning("lookup_sap_knowledge failed: %s", exc)
        deps.run_trace.tool_calls.append(
            ToolCallRecord(
                name="lookup_sap_knowledge",
                args_summary=args_summary,
                duration_ms=round(duration, 2),
                ok=False,
                error=str(exc),
            )
        )
        query_str = f"{table}.{field}" if field else table
        return SapLookupResult(
            query=query_str,
            found=False,
            level="none",
            notes=f"Error executing lookup: {exc}",
        )


lookup_sap_knowledge = function_tool(_lookup_sap_knowledge)
lookup_sap_knowledge.func = _lookup_sap_knowledge  # type: ignore[attr-defined]


def _search_sap_catalog(
    ctx: RunContextWrapper[AgentDeps],
    query: str,
    k: int = 3,
) -> SapSearchResult:
    """Performs semantic search across the SAP Table Catalog index."""
    start_t = time.perf_counter()
    deps = ctx.context
    args_summary = f"query='{query}', k={k}"
    query_key = query.strip().lower()

    guard_settings = get_guardrail_settings()
    arg_events = validate_tool_arguments(
        "search_sap_catalog", {"query": query, "k": k}, guard_settings, deps.request_id
    )
    for ev in arg_events:
        if ev.action == "block":
            duration = (time.perf_counter() - start_t) * 1000
            deps.run_trace.tool_calls.append(
                ToolCallRecord(
                    name="search_sap_catalog",
                    args_summary=args_summary,
                    duration_ms=round(duration, 2),
                    ok=False,
                    error=ev.message,
                )
            )
            return SapSearchResult(
                query=query,
                exact_match_found=False,
                hits=[],
                warning=f"Guardrail blocked: {ev.message}",
            )
        elif ev.action == "sanitize" and ev.name == "tool_k_exceeded":
            k = 5

    # Enforce MAX_SEARCH_CALLS_PER_RUN
    if deps.run_trace.search_count >= deps.settings.max_search_calls_per_run:
        duration = (time.perf_counter() - start_t) * 1000
        warning = f"Search call limit exceeded for this run (max {deps.settings.max_search_calls_per_run})"
        deps.run_trace.tool_calls.append(
            ToolCallRecord(
                name="search_sap_catalog",
                args_summary=args_summary,
                duration_ms=round(duration, 2),
                ok=True,
                error=warning,
            )
        )
        return SapSearchResult(
            query=query,
            exact_match_found=False,
            hits=[],
            warning=warning,
        )

    # Dedupe identical queries within this run
    if query_key in deps.run_trace.cached_search_results:
        duration = (time.perf_counter() - start_t) * 1000
        cached = deps.run_trace.cached_search_results[query_key]
        if isinstance(cached, SapSearchResult):
            cached_copy = cached.model_copy()
            cached_copy.warning = "Query was already executed in this run (cached)"
            deps.run_trace.tool_calls.append(
                ToolCallRecord(
                    name="search_sap_catalog",
                    args_summary=f"{args_summary} (deduped)",
                    duration_ms=round(duration, 2),
                    ok=True,
                )
            )
            return cached_copy

    try:
        deps.run_trace.search_count += 1
        res = deps.sap_retriever.search(query, k=k)

        # Truncate hit text to max_retrieved_chars and register chunk_id
        max_chars = deps.settings.max_retrieved_chars
        for hit in res.hits:
            hit.text = hit.text[:max_chars]
            deps.run_trace.retrieved_chunks[hit.chunk_id] = hit

        deps.run_trace.cached_search_results[query_key] = res
        duration = (time.perf_counter() - start_t) * 1000
        deps.run_trace.tool_calls.append(
            ToolCallRecord(
                name="search_sap_catalog",
                args_summary=args_summary,
                duration_ms=round(duration, 2),
                ok=True,
            )
        )
        return res
    except Exception as exc:  # noqa: BLE001
        duration = (time.perf_counter() - start_t) * 1000
        logger.warning("search_sap_catalog failed: %s", exc)
        deps.run_trace.tool_calls.append(
            ToolCallRecord(
                name="search_sap_catalog",
                args_summary=args_summary,
                duration_ms=round(duration, 2),
                ok=False,
                error=str(exc),
            )
        )
        return SapSearchResult(
            query=query,
            exact_match_found=False,
            hits=[],
            warning=f"Search failed: {exc}",
        )


search_sap_catalog = function_tool(_search_sap_catalog)
search_sap_catalog.func = _search_sap_catalog  # type: ignore[attr-defined]


def _save_learned_sap_knowledge(
    ctx: RunContextWrapper[AgentDeps],
    chunk_id: str,
    table: str,
    field: str | None = None,
    description: str = "",
    business_meaning: str | None = None,
) -> SaveResult:
    """Saves verified SAP table knowledge derived from a retrieved chunk during this run."""
    start_t = time.perf_counter()
    deps = ctx.context
    args_summary = (
        f"chunk_id={chunk_id}, table={table}, field={field}, desc='{description[:30]}...'"
    )

    if not deps.settings.learning_enabled:
        duration = (time.perf_counter() - start_t) * 1000
        deps.run_trace.tool_calls.append(
            ToolCallRecord(
                name="save_learned_sap_knowledge",
                args_summary=args_summary,
                duration_ms=round(duration, 2),
                ok=True,
                error="Learning is disabled in settings",
            )
        )
        return SaveResult(
            saved=False,
            action="skipped",
            reason="Learning is disabled in settings",
        )

    # Rejects unknown chunk_ids: model NEVER supplies evidence text
    if chunk_id not in deps.run_trace.retrieved_chunks:
        duration = (time.perf_counter() - start_t) * 1000
        reason = f"Unknown chunk_id '{chunk_id}'. Must reference a chunk retrieved in this run."
        deps.run_trace.tool_calls.append(
            ToolCallRecord(
                name="save_learned_sap_knowledge",
                args_summary=args_summary,
                duration_ms=round(duration, 2),
                ok=False,
                error=reason,
            )
        )
        return SaveResult(saved=False, action="rejected", reason=reason)

    chunk = deps.run_trace.retrieved_chunks[chunk_id]

    # Guardrail policy check (write-cap, prior search, lexical grounding, expansion ratio)
    guard_settings = get_guardrail_settings()
    allowed, guard_events = check_learned_write_allowed(
        deps, table, description, chunk.text, guard_settings
    )
    if not allowed:
        duration = (time.perf_counter() - start_t) * 1000
        reason = (
            guard_events[0].message if guard_events else "Guardrail policy rejected learned write"
        )
        deps.run_trace.tool_calls.append(
            ToolCallRecord(
                name="save_learned_sap_knowledge",
                args_summary=args_summary,
                duration_ms=round(duration, 2),
                ok=False,
                error=reason,
            )
        )
        return SaveResult(saved=False, action="rejected", reason=reason)

    ev = Evidence(
        chunk_id=chunk.chunk_id,
        source_file=chunk.source_file,
        page=chunk.page,
        evidence_text=chunk.text,
        retrieval_score=chunk.score,
        match_type=chunk.match_type,
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    try:
        entry = LearnedSapEntry(
            table=table.strip().upper(),
            field=field.strip().upper() if field else None,
            description=description.strip(),
            business_meaning=business_meaning,
            evidence=ev,
            confidence=chunk.score,
            created_at=now_iso,
            last_seen_at=now_iso,
        )
    except ValidationError as val_err:
        duration = (time.perf_counter() - start_t) * 1000
        reason = f"Grounding validation rejected entry: {val_err}"
        deps.run_trace.tool_calls.append(
            ToolCallRecord(
                name="save_learned_sap_knowledge",
                args_summary=args_summary,
                duration_ms=round(duration, 2),
                ok=False,
                error=reason,
            )
        )
        return SaveResult(saved=False, action="rejected", reason=reason)

    save_res = deps.store.save_learned_sap(entry)
    duration = (time.perf_counter() - start_t) * 1000

    if save_res.saved:
        deps.run_trace.write_count += 1
        deps.run_trace.learned_saves.append(
            {
                "table": table,
                "field": field,
                "action": save_res.action,
                "chunk_id": chunk_id,
            }
        )

    deps.run_trace.tool_calls.append(
        ToolCallRecord(
            name="save_learned_sap_knowledge",
            args_summary=args_summary,
            duration_ms=round(duration, 2),
            ok=save_res.saved,
            error=None if save_res.saved else save_res.reason,
        )
    )
    return save_res


save_learned_sap_knowledge = function_tool(_save_learned_sap_knowledge)
save_learned_sap_knowledge.func = _save_learned_sap_knowledge  # type: ignore[attr-defined]


# ==============================================================================
# SQL Agent Tools
# ==============================================================================


def _lookup_sql_keyword(
    ctx: RunContextWrapper[AgentDeps],
    term: str,
) -> SqlKeywordLookupResult:
    """Looks up a SQL keyword, function, or operator in the SQL reference catalog."""
    start_t = time.perf_counter()
    deps = ctx.context
    args_summary = f"term='{term}'"

    guard_settings = get_guardrail_settings()
    arg_events = validate_tool_arguments(
        "lookup_sql_keyword", {"term": term}, guard_settings, deps.request_id
    )
    for ev in arg_events:
        if ev.action == "block":
            duration = (time.perf_counter() - start_t) * 1000
            deps.run_trace.tool_calls.append(
                ToolCallRecord(
                    name="lookup_sql_keyword",
                    args_summary=args_summary,
                    duration_ms=round(duration, 2),
                    ok=False,
                    error=ev.message,
                )
            )
            return SqlKeywordLookupResult(term=term, found=False)

    try:
        kw = deps.store.lookup_sql_keyword(term)
        duration = (time.perf_counter() - start_t) * 1000
        deps.run_trace.tool_calls.append(
            ToolCallRecord(
                name="lookup_sql_keyword",
                args_summary=args_summary,
                duration_ms=round(duration, 2),
                ok=True,
            )
        )
        if kw:
            return SqlKeywordLookupResult(
                term=term,
                found=True,
                keyword=kw.keyword,
                category=kw.category,
                technical_meaning=kw.technical_meaning,
                tsql_example=kw.tsql_example,
                performance_notes=kw.performance_notes,
                common_pitfalls=kw.common_pitfalls,
                source_chapter=kw.source_chapter,
                source=kw.source.value,
                verified=kw.verified,
            )
        return SqlKeywordLookupResult(term=term, found=False)
    except Exception as exc:  # noqa: BLE001
        duration = (time.perf_counter() - start_t) * 1000
        logger.warning("lookup_sql_keyword failed: %s", exc)
        deps.run_trace.tool_calls.append(
            ToolCallRecord(
                name="lookup_sql_keyword",
                args_summary=args_summary,
                duration_ms=round(duration, 2),
                ok=False,
                error=str(exc),
            )
        )
        return SqlKeywordLookupResult(term=term, found=False)


lookup_sql_keyword = function_tool(_lookup_sql_keyword)
lookup_sql_keyword.func = _lookup_sql_keyword  # type: ignore[attr-defined]


def _search_sql_docs(
    ctx: RunContextWrapper[AgentDeps],
    query: str,
    k: int = 3,
) -> list[RetrievalHit]:
    """Performs semantic search across the SQL documentation reference index."""
    start_t = time.perf_counter()
    deps = ctx.context
    args_summary = f"query='{query}', k={k}"
    query_key = query.strip().lower()

    guard_settings = get_guardrail_settings()
    arg_events = validate_tool_arguments(
        "search_sql_docs", {"query": query, "k": k}, guard_settings, deps.request_id
    )
    for ev in arg_events:
        if ev.action == "block":
            duration = (time.perf_counter() - start_t) * 1000
            deps.run_trace.tool_calls.append(
                ToolCallRecord(
                    name="search_sql_docs",
                    args_summary=args_summary,
                    duration_ms=round(duration, 2),
                    ok=False,
                    error=ev.message,
                )
            )
            return []
        elif ev.action == "sanitize" and ev.name == "tool_k_exceeded":
            k = 5

    if deps.run_trace.search_count >= deps.settings.max_search_calls_per_run:
        duration = (time.perf_counter() - start_t) * 1000
        deps.run_trace.tool_calls.append(
            ToolCallRecord(
                name="search_sql_docs",
                args_summary=args_summary,
                duration_ms=round(duration, 2),
                ok=True,
                error="Search limit exceeded",
            )
        )
        return []

    if query_key in deps.run_trace.cached_search_results:
        duration = (time.perf_counter() - start_t) * 1000
        cached = deps.run_trace.cached_search_results[query_key]
        if isinstance(cached, list):
            deps.run_trace.tool_calls.append(
                ToolCallRecord(
                    name="search_sql_docs",
                    args_summary=f"{args_summary} (deduped)",
                    duration_ms=round(duration, 2),
                    ok=True,
                )
            )
            return [h.model_copy() for h in cached]

    try:
        deps.run_trace.search_count += 1
        hits = deps.sql_retriever.search(query, k=k, dialect="tsql")

        max_chars = deps.settings.max_retrieved_chars
        for h in hits:
            h.text = h.text[:max_chars]
            deps.run_trace.retrieved_chunks[h.chunk_id] = h

        deps.run_trace.cached_search_results[query_key] = hits
        duration = (time.perf_counter() - start_t) * 1000
        deps.run_trace.tool_calls.append(
            ToolCallRecord(
                name="search_sql_docs",
                args_summary=args_summary,
                duration_ms=round(duration, 2),
                ok=True,
            )
        )
        return hits
    except Exception as exc:  # noqa: BLE001
        duration = (time.perf_counter() - start_t) * 1000
        logger.warning("search_sql_docs failed: %s", exc)
        deps.run_trace.tool_calls.append(
            ToolCallRecord(
                name="search_sql_docs",
                args_summary=args_summary,
                duration_ms=round(duration, 2),
                ok=False,
                error=str(exc),
            )
        )
        return []


search_sql_docs = function_tool(_search_sql_docs)
search_sql_docs.func = _search_sql_docs  # type: ignore[attr-defined]
