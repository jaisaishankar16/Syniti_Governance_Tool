"""L3 Semantic similarity cache using literal-masked SQL embeddings and structural fingerprinting."""

from __future__ import annotations

import hashlib
import logging
import re
import threading
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.embeddings.embedder import Embedder, get_embedder
from app.embeddings.settings import EmbeddingSettings, get_embedding_settings
from app.knowledge.calibration import get_model_calibration

logger = logging.getLogger("app.cache.similarity")


def mask_literals_and_aliases(sql: str) -> str:
    """Masks string literals, numeric literals, and standardizes whitespace for semantic matching."""
    # Mask string literals: 'abc' -> '?'
    masked = re.sub(r"'(?:''|[^'])*'", "'?'", sql)
    # Mask numbers: 100 -> 0
    masked = re.sub(r"\b\d+\b", "0", masked)
    # Collapse multiple whitespaces
    masked = re.sub(r"\s+", " ", masked).strip().upper()
    return masked


def compute_structure_fingerprint(normalized_sql: str, tables: list[str]) -> str:
    """Computes a structural AST fingerprint insensitive to literal values and column order."""
    masked = mask_literals_and_aliases(normalized_sql)
    sorted_tables = sorted(t.strip().upper() for t in tables)
    raw = f"{','.join(sorted_tables)}|{masked}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class CachedQueryVector(BaseModel):
    """Metadata and embedding for a query indexed in the L3 similarity cache."""

    model_config = ConfigDict(extra="forbid")

    key: str
    masked_sql: str
    tables: list[str]
    structure_fingerprint: str
    analysis_payload: dict[str, Any]
    embedding: list[float] = Field(default_factory=list)


class SimilarityCacheIndex:
    """In-memory cosine similarity index namespaced by embedding model, dimensions, and base URL hash."""

    def __init__(
        self,
        embedding_settings: EmbeddingSettings | None = None,
        embedder: Embedder | None = None,
    ) -> None:
        self.embedding_settings = embedding_settings or get_embedding_settings()
        self.embedder = embedder or get_embedder(self.embedding_settings)
        # Namespaced storage: namespace -> (list[CachedQueryVector], np.ndarray | None)
        self._namespaces: dict[str, tuple[list[CachedQueryVector], np.ndarray | None]] = {}
        self._lock = threading.Lock()

    def _get_namespace(self) -> str:
        s = self.embedding_settings
        return f"{s.embedding_model}:{s.embedding_dimensions}:{s.base_url_hash}"

    def _get_active_storage(self) -> tuple[list[CachedQueryVector], np.ndarray | None]:
        ns = self._get_namespace()
        if ns not in self._namespaces:
            self._namespaces[ns] = ([], None)
        return self._namespaces[ns]

    def _encode(self, text: str) -> np.ndarray | None:
        try:
            vec = self.embedder.embed_query_sync(text)
            arr = np.array(vec, dtype=np.float32)
            norm = float(np.linalg.norm(arr))
            if norm > 0.0:
                arr = arr / norm
            return arr
        except Exception as exc:  # noqa: BLE001
            logger.warning("L3 embedding encoding failed: %s; skipping L3 similarity", exc)
            return None

    def add(
        self,
        key: str,
        normalized_sql: str,
        tables: list[str],
        analysis: Any,
    ) -> None:
        """Indexes a query execution in the similarity cache if calibrated."""
        s = self.embedding_settings
        calib = get_model_calibration(s.embedding_model, s.embedding_dimensions)
        if calib is None:
            logger.debug("Active embedding model is uncalibrated; skipping L3 caching")
            return

        masked = mask_literals_and_aliases(normalized_sql)
        vec = self._encode(masked)
        if vec is None:
            return

        fp = compute_structure_fingerprint(normalized_sql, tables)

        payload_dict: dict[str, Any]
        if hasattr(analysis, "model_dump"):
            payload_dict = analysis.model_dump()
        elif isinstance(analysis, dict):
            payload_dict = analysis
        else:
            payload_dict = {"raw": str(analysis)}

        entry = CachedQueryVector(
            key=key,
            masked_sql=masked,
            tables=sorted(t.strip().upper() for t in tables),
            structure_fingerprint=fp,
            analysis_payload=payload_dict,
            embedding=vec.tolist(),
        )

        with self._lock:
            entries, vectors = self._get_active_storage()
            existing_idx = next((i for i, e in enumerate(entries) if e.key == key), None)
            if existing_idx is not None:
                entries[existing_idx] = entry
                if vectors is not None:
                    vectors[existing_idx] = vec
            else:
                entries.append(entry)
                if vectors is None:
                    vectors = np.expand_dims(vec, axis=0)
                else:
                    vectors = np.vstack([vectors, np.expand_dims(vec, axis=0)])
            ns = self._get_namespace()
            self._namespaces[ns] = (entries, vectors)

    def find_similar(
        self,
        normalized_sql: str,
        tables: list[str],
        reuse_threshold: float | None = None,
        hint_threshold: float | None = None,
    ) -> tuple[Literal["similar_reused", "similar_hint", "miss"], dict[str, Any] | None, float]:
        """Performs cosine similarity search against cached queries.

        If uncalibrated, returns ("miss", None, 0.0) without error.
        """
        s = self.embedding_settings
        calib = get_model_calibration(s.embedding_model, s.embedding_dimensions)
        if calib is None:
            return "miss", None, 0.0

        eff_reuse_threshold = reuse_threshold if reuse_threshold is not None else calib.l3_reuse_threshold
        eff_hint_threshold = hint_threshold if hint_threshold is not None else calib.l3_hint_threshold

        with self._lock:
            entries, vectors = self._get_active_storage()
            if not entries or vectors is None:
                return "miss", None, 0.0

            masked = mask_literals_and_aliases(normalized_sql)
            query_vec = self._encode(masked)
            if query_vec is None:
                return "miss", None, 0.0

            scores = np.dot(vectors, query_vec)
            best_idx = int(np.argmax(scores))
            best_score = float(scores[best_idx])
            best_entry = entries[best_idx]

        target_tables = sorted(t.strip().upper() for t in tables)
        fp = compute_structure_fingerprint(normalized_sql, tables)

        # 1. Reuse evaluation (only when reuse threshold is configured)
        if eff_reuse_threshold is not None and best_score >= eff_reuse_threshold:
            if target_tables == best_entry.tables and fp == best_entry.structure_fingerprint:
                return "similar_reused", best_entry.analysis_payload, round(best_score, 4)
            if eff_hint_threshold is not None and best_score >= eff_hint_threshold:
                return "similar_hint", best_entry.analysis_payload, round(best_score, 4)

        # 2. Hint evaluation
        if eff_hint_threshold is not None and best_score >= eff_hint_threshold:
            return "similar_hint", best_entry.analysis_payload, round(best_score, 4)

        return "miss", None, round(best_score, 4)

    def clear(self) -> None:
        """Clears all indexed vectors across all namespaces."""
        with self._lock:
            self._namespaces.clear()


_GLOBAL_SIMILARITY_INDEX: SimilarityCacheIndex | None = None


def get_similarity_index(
    settings: EmbeddingSettings | None = None,
    embedder: Embedder | None = None,
) -> SimilarityCacheIndex:
    """Returns the singleton L3 similarity cache index."""
    global _GLOBAL_SIMILARITY_INDEX
    if _GLOBAL_SIMILARITY_INDEX is None:
        _GLOBAL_SIMILARITY_INDEX = SimilarityCacheIndex(
            embedding_settings=settings,
            embedder=embedder,
        )
    return _GLOBAL_SIMILARITY_INDEX


def reset_similarity_index() -> None:
    """Resets the singleton similarity index."""
    global _GLOBAL_SIMILARITY_INDEX
    _GLOBAL_SIMILARITY_INDEX = None
