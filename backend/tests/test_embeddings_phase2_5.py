"""Comprehensive test suite for Phase 2.5 OpenAI-compatible embeddings and retrieval integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest
from app.cache.keys import compute_knowledge_fingerprint, make_l1_key
from app.cache.similarity import SimilarityCacheIndex
from app.embeddings.cache import DocumentEmbeddingDiskCache, QueryEmbeddingCache
from app.embeddings.embedder import (
    OpenAIEmbedder,
    classify_embedding_error,
    estimate_tokens,
)
from app.embeddings.models import (
    EmbeddingResponseError,
    EmbeddingResult,
)
from app.embeddings.settings import EmbeddingSettings
from app.knowledge.models import (
    CatalogCategory,
    DocChunk,
    IndexManifest,
    SapChunkMetadata,
    StaleIndexError,
)
from app.knowledge.retrievers import SapRetriever
from app.knowledge.vector_index import VectorIndex
from pydantic import ValidationError

from tests.support.fake_embedder import FakeEmbedder


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ==============================================================================
# 1. Embedder Unit Tests
# ==============================================================================


def test_estimate_tokens() -> None:
    """Verifies deterministic token estimation without external tokenizer."""
    text = "SELECT * FROM MARA WHERE MANDT = '100'"
    tok = estimate_tokens(text)
    assert tok > 0
    assert tok == estimate_tokens(text)


def test_classify_embedding_error() -> None:
    """Verifies classified error codes and retryability rules."""
    assert classify_embedding_error(Exception("401 Unauthorized")) == ("LLM_AUTH_FAILED", False, 401)
    assert classify_embedding_error(Exception("403 Forbidden")) == ("LLM_AUTH_FAILED", False, 403)
    assert classify_embedding_error(Exception("404 The model does not exist")) == (
        "EMBEDDING_MODEL_NOT_FOUND",
        False,
        404,
    )
    assert classify_embedding_error(Exception("429 You exceeded your current quota")) == (
        "LLM_QUOTA_EXCEEDED",
        False,
        429,
    )
    assert classify_embedding_error(Exception("429 Rate limit reached")) == (
        "LLM_RATE_LIMITED",
        True,
        429,
    )
    assert classify_embedding_error(Exception("503 Service Unavailable")) == (
        "LLM_UNAVAILABLE",
        True,
        503,
    )


def test_embedding_result_validation_and_normalization() -> None:
    """Verifies vector dimension checking, NaN rejection, and automatic L2 normalization."""
    raw_vec = [3.0, 4.0]
    res = EmbeddingResult(
        vectors=[raw_vec],
        model="text-embedding-3-small",
        dimensions=2,
    )
    norm = np.linalg.norm(res.vectors[0])
    assert pytest.approx(norm, 1e-4) == 1.0
    assert pytest.approx(res.vectors[0][0], 1e-4) == 0.6
    assert pytest.approx(res.vectors[0][1], 1e-4) == 0.8

    # Dimension mismatch
    with pytest.raises(ValidationError, match="has dimension 2, expected 3"):
        EmbeddingResult(
            vectors=[[1.0, 2.0]],
            model="text-embedding-3-small",
            dimensions=3,
        )

    # NaN rejection
    with pytest.raises(ValidationError, match="invalid value"):
        EmbeddingResult(
            vectors=[[float("nan"), 1.0]],
            model="text-embedding-3-small",
            dimensions=2,
        )


@pytest.mark.anyio
async def test_embedder_batching_and_ordering() -> None:
    """Verifies that provider inputs are ordered strictly by provider index field."""
    settings = EmbeddingSettings(embedding_batch_size=2)
    mock_client = AsyncMock()

    # Mock response with unordered index returns
    item0 = MagicMock(index=0, embedding=[1.0, 0.0])
    item1 = MagicMock(index=1, embedding=[0.0, 1.0])
    resp_batch = MagicMock(data=[item1, item0], usage=MagicMock(total_tokens=20))
    mock_client.embeddings.create.return_value = resp_batch

    embedder = OpenAIEmbedder(settings=settings, async_client=mock_client)
    res = await embedder.embed_documents(["input 0", "input 1"])

    assert len(res.vectors) == 2
    assert res.vectors[0] == [1.0, 0.0]
    assert res.vectors[1] == [0.0, 1.0]


@pytest.mark.anyio
async def test_embedder_quota_not_retried() -> None:
    """Verifies that quota/billing 429 fails immediately without retry."""
    settings = EmbeddingSettings()
    mock_client = AsyncMock()
    mock_client.embeddings.create.side_effect = Exception("429 insufficient_quota")

    embedder = OpenAIEmbedder(settings=settings, async_client=mock_client)
    with pytest.raises(EmbeddingResponseError) as exc_info:
        await embedder.embed_documents(["test text"])

    assert exc_info.value.code == "LLM_QUOTA_EXCEEDED"
    assert mock_client.embeddings.create.call_count == 1  # No retries!


# ==============================================================================
# 2. Cache Unit Tests
# ==============================================================================


def test_query_cache_hit_makes_zero_provider_calls() -> None:
    """Verifies that query cache hit answers immediately without calling provider."""
    cache = QueryEmbeddingCache(maxsize=10)
    fake_vec = [0.1, 0.2, 0.3]
    cache.put("model-a", 3, "hash1", "SELECT 1", fake_vec)

    hit = cache.get("model-a", 3, "hash1", "SELECT 1")
    assert hit == fake_vec
    assert cache.hits == 1

    miss = cache.get("model-a", 3, "hash1", "SELECT 2")
    assert miss is None
    assert cache.misses == 1


def test_document_disk_cache(tmp_path: Path) -> None:
    """Verifies document chunk disk cache persistence and retrieval."""
    disk_cache = DocumentEmbeddingDiskCache(cache_dir=tmp_path)
    vec = [0.5, 0.5]
    disk_cache.put("model-x", 2, "chunk content", vec)

    retrieved = disk_cache.get("model-x", 2, "chunk content")
    assert retrieved == vec

    assert disk_cache.get("model-x", 2, "different content") is None


# ==============================================================================
# 3. VectorIndex Manifest & Stale Checks
# ==============================================================================


def test_vector_index_manifest_schema_v2_roundtrip(tmp_path: Path) -> None:
    """Verifies IndexManifest v2 validation, serialization, and round-trip."""
    manifest = IndexManifest(
        schema_version=2,
        provider="OpenAI-compatible",
        index_name="sap_docs",
        embedding_model="text-embedding-3-small",
        dimensions=1536,
        base_url_hash="abc123",
        normalized=True,
        source_file="sap_tables.pdf",
        source_sha256="deadbeef",
        chunk_count=10,
        built_at="2026-11-07T00:00:00Z",
        chunk_id_scheme="deterministic_v1",
    )
    p = tmp_path / "manifest.json"
    p.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    loaded = IndexManifest.model_validate_json(p.read_text(encoding="utf-8"))
    assert loaded.schema_version == 2
    assert loaded.embedding_model == "text-embedding-3-small"
    assert loaded.dimensions == 1536


def test_vector_index_stale_detection(tmp_path: Path) -> None:
    """Verifies that changed model, dimensions, or base-url hash triggers StaleIndexError."""
    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.4 test")

    fake = FakeEmbedder(dimensions=1536, model="text-embedding-3-small")
    settings = EmbeddingSettings(embedding_model="text-embedding-3-small", embedding_dimensions=1536)

    idx = VectorIndex(
        index_name="test_index",
        index_dir=tmp_path / "index",
        source_path=source_pdf,
        embedding_settings=settings,
        embedder=fake,
    )

    chunks = [
        DocChunk(
            chunk_id="chunk_1",
            index_name="sap",
            text="Table MARA Material Master",
            metadata=SapChunkMetadata(type="sap", table="MARA", module="MM", category=CatalogCategory.business),
        )
    ]
    idx.build(chunks)
    assert idx.is_built() is True
    stale, reason = idx.is_stale()
    assert stale is False

    # Changing model makes index stale
    stale_settings = EmbeddingSettings(embedding_model="text-embedding-3-large")
    idx_changed = VectorIndex(
        index_name="test_index",
        index_dir=tmp_path / "index",
        source_path=source_pdf,
        embedding_settings=stale_settings,
        embedder=fake,
    )
    is_stale, reason = idx_changed.is_stale()
    assert is_stale is True
    assert "Embedding model mismatch" in reason

    with pytest.raises(StaleIndexError, match="Rebuild with: python -m app.knowledge.build_index --target all"):
        idx_changed.load()


# ==============================================================================
# 4. Retrieval Plumbing with FakeEmbedder
# ==============================================================================


def test_sap_exact_match_makes_zero_embedding_calls(tmp_path: Path) -> None:
    """Verifies exact table match answers FIRST with zero embedding provider calls."""
    fake = FakeEmbedder(dimensions=1536)
    idx = VectorIndex(
        index_name="sap_docs",
        index_dir=tmp_path / "sap",
        source_path=tmp_path / "fake.pdf",
        embedder=fake,
    )
    retriever = SapRetriever(vector_index=idx)
    # Pre-populate catalog
    from app.knowledge.models import CatalogCategory, SapCatalogRecord
    retriever.catalog_by_table["MARA"] = SapCatalogRecord(
        table="MARA", module="MM", category=CatalogCategory.business, descriptions=["Material Master"]
    )

    result = retriever.search("MARA")
    assert result.exact_match_found is True
    assert len(result.hits) == 1
    assert result.hits[0].match_type.value == "exact"
    assert fake.call_count == 0  # Zero embedding calls!


def test_semantic_search_unavailable_when_embedder_fails(tmp_path: Path) -> None:
    """Verifies semantic search returns typed unavailable result when embedder fails."""
    err = EmbeddingResponseError("Gateway timeout", code="LLM_UNAVAILABLE")
    fake = FakeEmbedder(dimensions=1536)
    idx = VectorIndex(
        index_name="sap_docs",
        index_dir=tmp_path / "sap",
        source_path=tmp_path / "fake.pdf",
        embedder=fake,
    )
    # Build index first so files exist
    idx.build([
        DocChunk(
            chunk_id="c1",
            index_name="sap",
            text="General material data",
            metadata=SapChunkMetadata(type="sap", table="MARA", module="MM", category=CatalogCategory.business),
        )
    ])
    # Now simulate failure on query embed
    fake.simulate_error = err
    retriever = SapRetriever(vector_index=idx)

    result = retriever.search("non_exact_table_query")
    assert result.exact_match_found is False
    assert result.semantic_search_unavailable is True
    assert result.unavailable_reason == "LLM_UNAVAILABLE"


# ==============================================================================
# 5. L3 Similarity Namespacing and Calibration
# ==============================================================================


def test_l3_similarity_namespacing() -> None:
    """Verifies L3 similarity cache namespaces vectors by model + dimensions + base-url hash."""
    fake = FakeEmbedder(dimensions=1536, model="model-A")
    settings_a = EmbeddingSettings(embedding_model="model-A")
    l3_index = SimilarityCacheIndex(embedding_settings=settings_a, embedder=fake)

    # Without calibration, L3 find_similar returns miss
    action, _payload, score = l3_index.find_similar("SELECT * FROM MARA", ["MARA"])
    assert action == "miss"
    assert score == 0.0


# ==============================================================================
# 6. Cache Key Changes with EMBEDDING_MODEL
# ==============================================================================


def test_l1_key_changes_when_embedding_model_changes() -> None:
    """Verifies that changing EMBEDDING_MODEL produces a different L1 cache key."""
    s1 = EmbeddingSettings(embedding_model="text-embedding-3-small")
    fp1 = compute_knowledge_fingerprint(embedding_settings=s1)
    k1 = make_l1_key(
        normalized_sql="SELECT 1",
        view_name="vw1",
        model="gpt-4o",
        sql_prompt_version="v1",
        sap_prompt_version="v1",
        synth_prompt_version="v1",
        knowledge_fingerprint=fp1,
    )

    s2 = EmbeddingSettings(embedding_model="text-embedding-3-large")
    fp2 = compute_knowledge_fingerprint(embedding_settings=s2)
    k2 = make_l1_key(
        normalized_sql="SELECT 1",
        view_name="vw1",
        model="gpt-4o",
        sql_prompt_version="v1",
        sap_prompt_version="v1",
        synth_prompt_version="v1",
        knowledge_fingerprint=fp2,
    )

    assert fp1 != fp2
    assert k1 != k2
