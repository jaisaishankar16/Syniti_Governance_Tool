"""Unit tests for typed agent tools registered with @function_tool."""
# mypy: disable-error-code="operator"

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from agents import RunContextWrapper
from app.agents.models import AgentDeps, RunTrace
from app.agents.settings import LLMSettings
from app.agents.tools import (
    lookup_sap_knowledge,
    lookup_sql_keyword,
    save_learned_sap_knowledge,
    search_sap_catalog,
    search_sql_docs,
)
from app.knowledge.models import MatchType, RetrievalHit
from app.knowledge.retrievers import SapRetriever, SqlDocRetriever
from app.knowledge.settings import KnowledgeSettings
from app.knowledge.store import KnowledgeStore


@pytest.fixture
def test_deps(tmp_path: Path) -> AgentDeps:
    """Fixture providing isolated AgentDeps."""
    k_settings = KnowledgeSettings(
        custom_learned_dir=tmp_path / "learned",
    )
    store = KnowledgeStore(settings=k_settings)
    sap_retriever = SapRetriever(settings=k_settings)
    sql_retriever = SqlDocRetriever(settings=k_settings)
    settings = LLMSettings(
        max_search_calls_per_run=2,
        max_retrieved_chars=50,
        learning_enabled=True,
    )
    deps = AgentDeps(
        store=store,
        sap_retriever=sap_retriever,
        sql_retriever=sql_retriever,
        settings=settings,
        request_id="test_req_123",
        run_trace=RunTrace(),
    )
    return deps


def _wrap(deps: AgentDeps) -> RunContextWrapper[AgentDeps]:
    """Helper creating RunContextWrapper for direct tool invocation."""
    mock_wrapper = MagicMock(spec=RunContextWrapper)
    mock_wrapper.context = deps
    return mock_wrapper


def test_search_cap_and_deduplication(test_deps: AgentDeps):
    """Enforces MAX_SEARCH_CALLS_PER_RUN and dedupes identical queries."""
    wrapped = _wrap(test_deps)

    # 1. First search -> ok, increment search_count
    res1 = search_sap_catalog(wrapped, "vendor general", k=2)
    assert test_deps.run_trace.search_count == 1
    assert res1.warning is None or "limit" not in res1.warning

    # 2. Duplicate search -> deduped, search_count remains 1
    res2 = search_sap_catalog(wrapped, "vendor general", k=2)
    assert test_deps.run_trace.search_count == 1
    assert res2.warning is not None
    assert "cached" in res2.warning.lower()

    # 3. Second unique search -> reaches cap of 2
    _ = search_sap_catalog(wrapped, "plant data", k=2)
    assert test_deps.run_trace.search_count == 2

    # 4. Third search exceeds cap -> returns empty hits with limit warning
    res4 = search_sap_catalog(wrapped, "material sales", k=2)
    assert test_deps.run_trace.search_count == 2
    assert len(res4.hits) == 0
    assert res4.warning is not None
    assert "limit exceeded" in res4.warning.lower()


def test_hit_text_truncated(test_deps: AgentDeps):
    """Retrieved hit text is truncated to MAX_RETRIEVED_CHARS (50 in test)."""
    wrapped = _wrap(test_deps)
    res = search_sap_catalog(wrapped, "MARA", k=2)
    assert len(res.hits) > 0
    for h in res.hits:
        assert len(h.text) <= test_deps.settings.max_retrieved_chars


def test_save_tool_rejects_unknown_chunk_id(test_deps: AgentDeps):
    """save_learned_sap_knowledge rejects chunk_id not present in run_trace registry."""
    wrapped = _wrap(test_deps)
    res = save_learned_sap_knowledge(
        wrapped,
        chunk_id="chunk_not_in_registry",
        table="Z_NEW_TABLE",
        description="A brand new table",
    )
    assert res.saved is False
    assert res.action == "rejected"
    assert "Unknown chunk_id" in res.reason


def test_save_tool_grounded_success_and_immediate_visibility(test_deps: AgentDeps):
    """save_learned_sap_knowledge succeeds with registered chunk and is visible to store.lookup_sap."""
    wrapped = _wrap(test_deps)

    # Register a valid retrieved chunk
    chunk_id = "test_chunk_mara_01"
    hit = RetrievalHit(
        chunk_id=chunk_id,
        text="Table Z_CUSTOM_LEARNED stores custom migration master records.",
        score=0.92,
        match_type=MatchType.semantic,
        source_file="sap_tables.pdf",
    )
    test_deps.run_trace.retrieved_chunks[chunk_id] = hit

    res = save_learned_sap_knowledge(
        wrapped,
        chunk_id=chunk_id,
        table="Z_CUSTOM_LEARNED",
        description="Custom migration master records table",
    )
    assert res.saved is True
    assert res.action == "created"

    # Immediately visible without restart
    lookup = test_deps.store.lookup_sap("Z_CUSTOM_LEARNED")
    assert lookup.found is True
    assert lookup.source == "learned"
    assert lookup.confidence == 0.92


def test_save_tool_fabricated_description_rejected(test_deps: AgentDeps):
    """Fabricating a description for a table not grounded in the chunk text fails Pydantic validator."""
    wrapped = _wrap(test_deps)

    # Register chunk that ONLY mentions plant data
    chunk_id = "test_chunk_plant"
    hit = RetrievalHit(
        chunk_id=chunk_id,
        text="Table MARC: Plant Data for Material. SAP module: MM.",
        score=0.85,
        match_type=MatchType.semantic,
        source_file="sap_tables.pdf",
    )
    test_deps.run_trace.retrieved_chunks[chunk_id] = hit

    # Attempt to save completely fabricated table and description not in chunk
    res = save_learned_sap_knowledge(
        wrapped,
        chunk_id=chunk_id,
        table="Z_UNMENTIONED_CUSTOMER_TABLE",
        description="Customer account balances for FI",
    )
    assert res.saved is False
    assert res.action == "rejected"
    assert "validation" in res.reason.lower() or "rejected" in res.reason.lower()


def test_prebuilt_entries_never_overwritten(test_deps: AgentDeps):
    """Attempts to save over prebuilt tables (e.g. MARA) are rejected."""
    wrapped = _wrap(test_deps)
    chunk_id = "c_mara"
    hit = RetrievalHit(
        chunk_id=chunk_id,
        text="Table MARA: General Material Data. SAP module MM.",
        score=0.95,
        match_type=MatchType.exact,
        source_file="sap_tables.pdf",
    )
    test_deps.run_trace.retrieved_chunks[chunk_id] = hit

    res = save_learned_sap_knowledge(
        wrapped,
        chunk_id=chunk_id,
        table="MARA",
        description="Attempt to overwrite prebuilt table",
    )
    assert res.saved is False
    assert res.action == "rejected"
    assert "already defined in prebuilt" in res.reason


def test_tools_never_raise_unhandled_exceptions(test_deps: AgentDeps):
    """Tools return typed error objects rather than raising exceptions into the model."""
    wrapped = _wrap(test_deps)

    # Force an error by breaking the store lookup
    test_deps.store.lookup_sap = MagicMock(side_effect=RuntimeError("Simulated DB failure"))  # type: ignore[method-assign]
    sap_res = lookup_sap_knowledge(wrapped, "MARA")
    assert sap_res.found is False
    assert "Error" in (sap_res.notes or "")

    test_deps.store.lookup_sql_keyword = MagicMock(
        side_effect=RuntimeError("Simulated SQL failure")
    )  # type: ignore[method-assign]
    sql_res = lookup_sql_keyword(wrapped, "SELECT")
    assert sql_res.found is False

    test_deps.sql_retriever.search = MagicMock(side_effect=RuntimeError("Index corrupt"))  # type: ignore[method-assign]
    hits = search_sql_docs(wrapped, "query")
    assert hits == []
