"""Builds FAISS vector indexes for SAP catalog and SQL documentation using OpenAI embeddings."""

from __future__ import annotations

import argparse
import sys

from pydantic import TypeAdapter

from app.embeddings.cache import get_disk_cache
from app.embeddings.embedder import estimate_tokens
from app.embeddings.settings import EmbeddingSettings, get_embedding_settings
from app.knowledge.models import (
    CatalogCategory,
    DocChunk,
    SapCatalogRecord,
    SapChunkMetadata,
)
from app.knowledge.sap_catalog_ingest import SapCatalogIngest
from app.knowledge.settings import KnowledgeSettings, get_settings
from app.knowledge.sql_docs_ingest import SqlDocsIngest
from app.knowledge.vector_index import VectorIndex


def build_sap_chunks(settings: KnowledgeSettings | None = None) -> list[DocChunk]:
    """Generates DocChunks from the SAP catalog."""
    settings = settings or get_settings()
    catalog_path = settings.sap_catalog_path

    if not catalog_path.exists():
        print(f"Catalog not found at {catalog_path}, running SapCatalogIngest...")
        ingestor = SapCatalogIngest(settings=settings)
        ingestor.ingest()

    with open(catalog_path, "r", encoding="utf-8") as f:
        tables = TypeAdapter(list[SapCatalogRecord]).validate_json(f.read())

    chunks: list[DocChunk] = []
    include_dd = settings.include_data_dictionary

    for rec in tables:
        # Exclude reports from index
        if rec.category == CatalogCategory.report:
            continue
        # Exclude data dictionary unless explicitly configured
        if rec.category == CatalogCategory.data_dictionary and not include_dd:
            continue

        desc_text = ", ".join(rec.descriptions) if rec.descriptions else "SAP Table"
        text = f"Table {rec.table}: {desc_text}. SAP module: {rec.module}. Category: {rec.category.value}."
        metadata = SapChunkMetadata(
            type="sap",
            table=rec.table,
            module=rec.module,
            category=rec.category,
            sections=rec.sections,
        )
        chunks.append(
            DocChunk(
                chunk_id=f"sap_{rec.table}",
                index_name="sap",
                text=text,
                metadata=metadata,
            )
        )
    return chunks


def build_sql_chunks(settings: KnowledgeSettings | None = None) -> list[DocChunk]:
    """Generates DocChunks from the SQL Reference PDF."""
    settings = settings or get_settings()
    ingestor = SqlDocsIngest(settings=settings)
    return ingestor.ingest()


def build_sap_index(
    settings: KnowledgeSettings | None = None,
    emb_settings: EmbeddingSettings | None = None,
    rebuild: bool = False,
) -> VectorIndex:
    """Builds or rebuilds the SAP vector index."""
    settings = settings or get_settings()
    emb_settings = emb_settings or get_embedding_settings()
    vec_idx = VectorIndex(
        index_name="sap_docs",
        index_dir=settings.sap_index_dir,
        source_path=settings.sap_pdf_path,
        settings=settings,
        embedding_settings=emb_settings,
        rebuild_builder=lambda: build_sap_chunks(settings),
    )

    stale, reason = vec_idx.is_stale()
    if not rebuild and not stale:
        print(f"[SAP Index] Already up to date at {settings.sap_index_dir}")
        return vec_idx

    print(f"[SAP Index] Building index (rebuild={rebuild}, reason='{reason}')...")
    chunks = build_sap_chunks(settings)
    vec_idx.build(chunks)
    print(f"[SAP Index] Successfully indexed {len(chunks)} chunks into {settings.sap_index_dir}")
    return vec_idx


def build_sql_index(
    settings: KnowledgeSettings | None = None,
    emb_settings: EmbeddingSettings | None = None,
    rebuild: bool = False,
) -> VectorIndex:
    """Builds or rebuilds the SQL documentation vector index."""
    settings = settings or get_settings()
    emb_settings = emb_settings or get_embedding_settings()
    vec_idx = VectorIndex(
        index_name="sql_docs",
        index_dir=settings.sql_index_dir,
        source_path=settings.sql_pdf_path,
        settings=settings,
        embedding_settings=emb_settings,
        rebuild_builder=lambda: build_sql_chunks(settings),
    )

    stale, reason = vec_idx.is_stale()
    if not rebuild and not stale:
        print(f"[SQL Index] Already up to date at {settings.sql_index_dir}")
        return vec_idx

    print(f"[SQL Index] Building index (rebuild={rebuild}, reason='{reason}')...")
    chunks = build_sql_chunks(settings)
    vec_idx.build(chunks)
    print(f"[SQL Index] Successfully indexed {len(chunks)} chunks into {settings.sql_index_dir}")
    return vec_idx


def main() -> None:
    parser = argparse.ArgumentParser(description="Build FAISS vector indexes with OpenAI embeddings.")
    parser.add_argument(
        "--target",
        choices=["sap", "sql", "all"],
        default="all",
        help="Which index to build (default: all)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Force rebuild even if index is fresh",
    )
    parser.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="Maximum allowable estimated cost in USD (hard stop if exceeded)",
    )
    args = parser.parse_args()

    emb_settings = get_embedding_settings()
    k_settings = get_settings()

    # Fail fast if no valid API key is configured
    if not emb_settings.is_configured:
        print("=" * 80)
        print("                     KNOWLEDGE VECTOR INDEX BUILDER")
        print("=" * 80)
        print("Error: No valid API key configured for embeddings.")
        print("Set OPENAI_API_KEY (or EMBEDDING_API_KEY) in .env before running build_index.")
        print("=" * 80)
        sys.exit(1)

    print("=" * 80)
    print("                     KNOWLEDGE VECTOR INDEX BUILDER")
    print("=" * 80)
    print(f"Embedding Model:    {emb_settings.embedding_model}")
    print(f"Dimensions:         {emb_settings.embedding_dimensions or 'Model Default'}")
    print(f"Provider Host:      {emb_settings.base_host}")
    print(f"Target:             {args.target}")
    print(f"Force Rebuild:      {args.rebuild}")
    print("=" * 80)

    # Collect chunks to evaluate estimated token counts and cost
    chunks_to_build: list[DocChunk] = []
    if args.target in ("sap", "all"):
        chunks_to_build.extend(build_sap_chunks(k_settings))
    if args.target in ("sql", "all"):
        chunks_to_build.extend(build_sql_chunks(k_settings))

    disk_cache = get_disk_cache()
    model_name = emb_settings.embedding_model
    dims = emb_settings.embedding_dimensions

    cached_chunks = 0
    uncached_tokens = 0
    total_tokens = 0

    for c in chunks_to_build:
        tok = estimate_tokens(c.text)
        total_tokens += tok
        if not args.rebuild and disk_cache.get(model_name, dims, c.text) is not None:
            cached_chunks += 1
        else:
            uncached_tokens += tok

    price_per_m = emb_settings.embedding_price_per_m_tokens or 0.02
    estimated_cost = (uncached_tokens / 1_000_000.0) * price_per_m

    print(f"Total Chunks:       {len(chunks_to_build)}")
    print(f"Cached Chunks:      {cached_chunks} (will skip provider calls)")
    print(f"Uncached Tokens:    ~{uncached_tokens:,} tokens (estimated)")
    print(f"Estimated Cost:     ${estimated_cost:.4f} USD (at ${price_per_m}/1M tokens)")

    if args.max_cost is not None and estimated_cost > args.max_cost:
        print(
            f"Error: Estimated cost (${estimated_cost:.4f}) exceeds --max-cost (${args.max_cost:.4f}). "
            "Aborting build."
        )
        sys.exit(1)

    print("-" * 80)

    if args.target in ("sap", "all"):
        build_sap_index(settings=k_settings, emb_settings=emb_settings, rebuild=args.rebuild)

    if args.target in ("sql", "all"):
        build_sql_index(settings=k_settings, emb_settings=emb_settings, rebuild=args.rebuild)

    print("=" * 80)
    print("Index build completed successfully.")
    print("=" * 80)


if __name__ == "__main__":
    main()
