"""Builds FAISS vector indexes for SAP catalog and SQL documentation."""

from __future__ import annotations

import argparse

from pydantic import TypeAdapter

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
    settings: KnowledgeSettings | None = None, rebuild: bool = False
) -> VectorIndex:
    """Builds or rebuilds the SAP vector index."""
    settings = settings or get_settings()
    vec_idx = VectorIndex(
        index_name="sap_docs",
        index_dir=settings.sap_index_dir,
        source_path=settings.sap_pdf_path,
        settings=settings,
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
    settings: KnowledgeSettings | None = None, rebuild: bool = False
) -> VectorIndex:
    """Builds or rebuilds the SQL documentation vector index."""
    settings = settings or get_settings()
    vec_idx = VectorIndex(
        index_name="sql_docs",
        index_dir=settings.sql_index_dir,
        source_path=settings.sql_pdf_path,
        settings=settings,
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
    parser = argparse.ArgumentParser(description="Build FAISS vector indexes for SAP and SQL docs.")
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
    args = parser.parse_args()

    settings = get_settings()

    if args.target in ("sap", "all"):
        build_sap_index(settings=settings, rebuild=args.rebuild)

    if args.target in ("sql", "all"):
        build_sql_index(settings=settings, rebuild=args.rebuild)


if __name__ == "__main__":
    main()
