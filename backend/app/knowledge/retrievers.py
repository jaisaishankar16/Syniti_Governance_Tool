"""Retrievers for SAP catalog and SQL documentation with exact match and dialect reranking."""

from __future__ import annotations

import re
from typing import Any

from pydantic import TypeAdapter

from app.knowledge.models import (
    DocChunk,
    MatchType,
    RetrievalHit,
    SapCatalogRecord,
    SapSearchResult,
    SqlDialect,
)
from app.knowledge.settings import KnowledgeSettings, get_settings
from app.knowledge.vector_index import VectorIndex


class SapRetriever:
    """Retriever for SAP table documentation combining catalog exact match and semantic search."""

    def __init__(
        self,
        settings: KnowledgeSettings | None = None,
        vector_index: VectorIndex | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.vector_index = vector_index or VectorIndex(
            index_name="sap_docs",
            index_dir=self.settings.sap_index_dir,
            source_path=self.settings.sap_pdf_path,
            settings=self.settings,
        )
        self.catalog_by_table: dict[str, SapCatalogRecord] = {}
        self._load_catalog()

    def _load_catalog(self) -> None:
        catalog_path = self.settings.sap_catalog_path
        if catalog_path.exists():
            with open(catalog_path, "r", encoding="utf-8") as f:
                records = TypeAdapter(list[SapCatalogRecord]).validate_json(f.read())
                for r in records:
                    self.catalog_by_table[r.table.upper()] = r

    def search(
        self, query: str, k: int | None = None, module_filter: str | None = None
    ) -> SapSearchResult:
        """Searches SAP catalog: step 1 exact table lookup, step 2 semantic search."""
        top_k = k or self.settings.top_k
        query_stripped = query.strip()
        query_upper = query_stripped.upper()

        # Step 1: Check for exact table matches
        exact_hits: list[RetrievalHit] = []
        tokens = re.findall(r"\b[A-Z0-9_]{3,16}\b", query_upper)
        matched_tables: set[str] = set()

        for token in tokens:
            if token in self.catalog_by_table and token not in matched_tables:
                rec = self.catalog_by_table[token]
                if module_filter and rec.module.upper() != module_filter.upper():
                    continue
                matched_tables.add(token)
                desc_str = ", ".join(rec.descriptions) if rec.descriptions else "SAP Table"
                hit_text = (
                    f"Table {rec.table}: {desc_str}. SAP module: {rec.module}. Category: {rec.category.value}."
                )[:2000]
                exact_hits.append(
                    RetrievalHit(
                        chunk_id=f"sap_{rec.table}",
                        text=hit_text,
                        score=1.0,
                        match_type=MatchType.exact,
                        source_file="sap_tables.pdf",
                        page=None,
                        metadata={
                            "table": rec.table,
                            "module": rec.module,
                            "category": rec.category.value,
                            "sections": rec.sections,
                        },
                        untrusted=True,
                    )
                )

        exact_match_found = len(exact_hits) > 0

        # Step 2: Semantic search
        semantic_hits: list[RetrievalHit] = []
        try:
            raw_hits = self.vector_index.search(
                query, top_k=top_k * 4, min_score=self.settings.min_score_sap
            )
            q_words = [w for w in re.split(r"\W+", query.lower()) if len(w) > 2]
            scored_candidates: list[tuple[DocChunk, float]] = []

            for chunk, score in raw_hits:
                if chunk.chunk_id in {h.chunk_id for h in exact_hits}:
                    continue
                tbl_name = getattr(chunk.metadata, "table", "")
                if tbl_name and tbl_name in matched_tables:
                    continue
                mod = getattr(chunk.metadata, "module", "")
                if module_filter and mod.upper() != module_filter.upper():
                    continue

                # Lexical overlap boost to assist dense retrieval on short domain queries
                adjusted_score = float(score)
                if q_words:
                    t_lower = chunk.text.lower()
                    matches = sum(1 for w in q_words if w in t_lower)
                    adjusted_score = adjusted_score * (1.0 + 0.8 * (matches / len(q_words)))

                scored_candidates.append((chunk, adjusted_score))

            scored_candidates.sort(key=lambda x: x[1], reverse=True)

            for chunk, adj_score in scored_candidates:
                meta_dict: dict[str, Any] = (
                    chunk.metadata.model_dump() if hasattr(chunk.metadata, "model_dump") else {}
                )
                semantic_hits.append(
                    RetrievalHit(
                        chunk_id=chunk.chunk_id,
                        text=chunk.text[:2000],
                        score=round(min(1.0, adj_score), 4),
                        match_type=MatchType.semantic,
                        source_file="sap_tables.pdf",
                        page=None,
                        metadata=meta_dict,
                        untrusted=True,
                    )
                )
        except (OSError, ValueError):
            # Fall back to exact hits if vector index is unavailable
            semantic_hits = []

        all_hits = exact_hits + semantic_hits
        all_hits = all_hits[:top_k]

        # Check warning: table-like query without exact match
        is_table_like = bool(re.match(r"^[A-Z0-9_]{3,16}$", query_upper))
        warning: str | None = None
        if is_table_like and not exact_match_found:
            warning = "No exact match; hits are semantic neighbours and are NOT the same table"

        return SapSearchResult(
            query=query,
            exact_match_found=exact_match_found,
            hits=all_hits,
            warning=warning,
        )


class SqlDocRetriever:
    """Retriever for SQL reference documentation with dialect-aware reranking."""

    def __init__(
        self,
        settings: KnowledgeSettings | None = None,
        vector_index: VectorIndex | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.vector_index = vector_index or VectorIndex(
            index_name="sql_docs",
            index_dir=self.settings.sql_index_dir,
            source_path=self.settings.sql_pdf_path,
            settings=self.settings,
        )

    def search(self, query: str, k: int | None = None, dialect: str = "tsql") -> list[RetrievalHit]:
        """Searches SQL docs vector index and applies dialect reranking."""
        top_k = k or self.settings.top_k
        raw_hits = self.vector_index.search(
            query, top_k=top_k * 3, min_score=self.settings.min_score_sql
        )

        candidate_hits: list[RetrievalHit] = []
        for chunk, base_score in raw_hits:
            metadata = chunk.metadata
            dialects: list[SqlDialect] = getattr(metadata, "dialects", [])

            # Dialect reranking
            has_tsql = SqlDialect.tsql in dialects
            has_ansi = SqlDialect.ansi in dialects
            has_unknown = SqlDialect.unknown in dialects

            if has_tsql:
                adjusted_score = min(1.0, base_score * self.settings.tsql_boost)
            elif has_ansi or has_unknown:
                adjusted_score = base_score
            else:
                # Mentions only other dialects (Oracle, MySQL, Postgres, etc.)
                adjusted_score = base_score * self.settings.other_dialect_penalty

            meta_dict: dict[str, Any] = (
                metadata.model_dump() if hasattr(metadata, "model_dump") else {}
            )
            candidate_hits.append(
                RetrievalHit(
                    chunk_id=chunk.chunk_id,
                    text=chunk.text[:2000],
                    score=round(float(adjusted_score), 4),
                    match_type=MatchType.semantic,
                    source_file="sql_reference.pdf",
                    page=getattr(metadata, "page_start", None),
                    metadata=meta_dict,
                    untrusted=True,
                )
            )

        # Sort candidate hits by adjusted score descending
        candidate_hits.sort(key=lambda h: h.score, reverse=True)
        return candidate_hits[:top_k]
