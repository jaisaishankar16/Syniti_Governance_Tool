"""Tests for multi-dialect SQL reference PDF ingestion and chunking."""

import pytest
from app.knowledge.models import DocChunk, SqlDialect
from app.knowledge.settings import get_settings
from app.knowledge.sql_docs_ingest import SqlDocsIngest, estimate_tokens


@pytest.fixture(scope="module")
def sql_chunks() -> list[DocChunk]:
    settings = get_settings()
    ingestor = SqlDocsIngest(settings=settings)
    return ingestor.ingest()


def test_sql_chunks_generated(sql_chunks: list[DocChunk]):
    """SQL PDF must produce structured DocChunks."""
    assert len(sql_chunks) >= 300
    for chunk in sql_chunks:
        assert chunk.index_name == "sql"
        assert chunk.chunk_id.startswith("sql_chunk_")
        assert "Chapter:" in chunk.text
        assert "Section:" in chunk.text


def test_footers_stripped(sql_chunks: list[DocChunk]):
    """Riptutorial footers must be completely stripped."""
    for chunk in sql_chunks:
        assert "https://riptutorial.com/" not in chunk.text


def test_non_content_chapters_skipped(sql_chunks: list[DocChunk]):
    """TOC, About, Credits, and Chapter 17 (Example Databases and Tables) must be omitted."""
    chapters = {chunk.metadata.chapter for chunk in sql_chunks}  # type: ignore[union-attr]
    assert "About" not in chapters
    assert "Credits" not in chapters
    assert "Example Databases and Tables" not in chapters


def test_chunk_token_limit(sql_chunks: list[DocChunk]):
    """No chunk may exceed the hard max token limit of 900 tokens."""
    for chunk in sql_chunks:
        tokens = estimate_tokens(chunk.text)
        assert tokens <= 900, f"Chunk {chunk.chunk_id} exceeds 900 tokens ({tokens})"


def test_dialect_detection(sql_chunks: list[DocChunk]):
    """Dialect detection accurately identifies T-SQL, Oracle, MySQL, Postgres, and ANSI."""
    all_dialects = set()
    for chunk in sql_chunks:
        for d in chunk.metadata.dialects:  # type: ignore[union-attr]
            all_dialects.add(d)
    assert SqlDialect.tsql in all_dialects
    assert SqlDialect.oracle in all_dialects
    assert SqlDialect.mysql in all_dialects
    assert SqlDialect.postgres in all_dialects
