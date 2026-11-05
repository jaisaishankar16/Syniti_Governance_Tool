"""Tests for SAP and SQL retrievers, exact vs semantic matching, and dialect reranking."""

import time

import pytest
from app.knowledge.models import MatchType, SqlDialect
from app.knowledge.retrievers import SapRetriever, SqlDocRetriever
from app.knowledge.store import KnowledgeStore


@pytest.fixture(scope="module")
def sap_retriever() -> SapRetriever:
    return SapRetriever()


@pytest.fixture(scope="module")
def sql_retriever() -> SqlDocRetriever:
    return SqlDocRetriever()


@pytest.fixture(scope="module")
def store() -> KnowledgeStore:
    return KnowledgeStore()


def test_sap_exact_match(sap_retriever: SapRetriever):
    """MARA query results in exact match with score 1.0."""
    result = sap_retriever.search("MARA")
    assert result.exact_match_found is True
    assert len(result.hits) > 0
    top_hit = result.hits[0]
    assert top_hit.match_type == MatchType.exact
    assert top_hit.score == 1.0
    assert top_hit.metadata.get("table") == "MARA"
    assert top_hit.untrusted is True
    assert len(top_hit.text) <= 2000


def test_sap_semantic_marc_in_top_3(sap_retriever: SapRetriever):
    """plant data for material returns MARC in top 3 hits."""
    result = sap_retriever.search("plant data for material", k=3)
    tables = [h.metadata.get("table") for h in result.hits]
    assert "MARC" in tables, f"Expected MARC in top 3 hits, got {tables}"


def test_sap_semantic_lfa1_in_top_3(sap_retriever: SapRetriever):
    """vendor master general returns LFA1 in top 3 hits."""
    result = sap_retriever.search("vendor master general", k=3)
    tables = [h.metadata.get("table") for h in result.hits]
    assert "LFA1" in tables, f"Expected LFA1 in top 3 hits, got {tables}"


def test_sap_but000_warning_and_prebuilt_fallback(
    sap_retriever: SapRetriever, store: KnowledgeStore
):
    """BUT000 is absent from catalog (exact_match_found=False with warning), but found in prebuilt store."""
    search_res = sap_retriever.search("BUT000")
    assert search_res.exact_match_found is False
    assert search_res.warning is not None
    assert (
        "No exact match; hits are semantic neighbours and are NOT the same table"
        in search_res.warning
    )

    # BUT000 must be found in prebuilt store
    store_res = store.lookup_sap("BUT000")
    assert store_res.found is True
    assert store_res.source == "prebuilt"
    assert store_res.table == "BUT000"


def test_mandt_resolves_common_fields(store: KnowledgeStore):
    """MANDT resolves through prebuilt common fields with client-handling notes."""
    result = store.lookup_sap("", "MANDT")
    assert result.found is True
    assert result.source == "prebuilt_common_fields"
    assert result.field == "MANDT"
    assert result.notes is not None
    assert "client" in result.notes.lower()


def test_sql_recursive_cte(sql_retriever: SqlDocRetriever):
    """recursive cte retrieves Common Table Expressions chapter."""
    hits = sql_retriever.search("recursive cte", k=3)
    assert len(hits) > 0
    top_hit = hits[0]
    assert "Common Table Expressions" in top_hit.metadata.get("chapter", "")
    assert top_hit.untrusted is True


def test_sql_row_number_partition_by(sql_retriever: SqlDocRetriever):
    """row_number partition by retrieves analytic / row number chapter."""
    hits = sql_retriever.search("row_number partition by", k=3)
    assert len(hits) > 0
    chapters = [h.metadata.get("chapter", "") for h in hits]
    assert any("Row number" in c or "Analytic" in c or "Window" in c for c in chapters)


def test_sql_not_exists(sql_retriever: SqlDocRetriever):
    """not exists retrieves EXISTS chapter."""
    hits = sql_retriever.search("not exists", k=3)
    assert len(hits) > 0
    assert any("EXISTS" in h.metadata.get("chapter", "") for h in hits)


def test_sql_isnull_tsql_rerank(sql_retriever: SqlDocRetriever):
    """isnull returns tsql-relevant chunks above other-dialect chunks."""
    hits = sql_retriever.search("isnull", k=5)
    assert len(hits) > 0
    # Any pure other-dialect hits (e.g. Oracle only) must not rank above tsql/ansi hits
    for h in hits:
        dialects = h.metadata.get("dialects", [])
        assert (
            SqlDialect.tsql in dialects
            or SqlDialect.ansi in dialects
            or SqlDialect.unknown in dialects
            or len(dialects) == 0
        )


def test_warm_search_latency(sap_retriever: SapRetriever, sql_retriever: SqlDocRetriever):
    """Warm search latency must be under 50 ms."""
    # Warm-up call
    sap_retriever.search("material", k=1)
    sql_retriever.search("join", k=1)

    t0 = time.perf_counter()
    sap_retriever.search("material master plant", k=3)
    t1 = time.perf_counter()
    latency_sap_ms = (t1 - t0) * 1000

    t2 = time.perf_counter()
    sql_retriever.search("select with join", k=3)
    t3 = time.perf_counter()
    latency_sql_ms = (t3 - t2) * 1000

    assert latency_sap_ms < 50.0, f"SAP search latency {latency_sap_ms:.2f}ms exceeds 50ms"
    assert latency_sql_ms < 50.0, f"SQL search latency {latency_sql_ms:.2f}ms exceeds 50ms"
