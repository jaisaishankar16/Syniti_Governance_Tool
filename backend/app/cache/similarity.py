"""L3 Semantic similarity cache using literal-masked SQL embeddings and structural fingerprinting."""

from __future__ import annotations

import hashlib
import re
import threading
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from app.knowledge.settings import get_settings
from app.knowledge.vector_index import get_embedding_model


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
    """In-memory cosine similarity index for cached query analyses."""

    def __init__(self, model_name: str | None = None) -> None:
        cfg = get_settings()
        self.model_name = model_name or cfg.embedding_model
        self._entries: list[CachedQueryVector] = []
        self._vectors: np.ndarray | None = None
        self._lock = threading.Lock()

    def _encode(self, text: str) -> np.ndarray:
        model = get_embedding_model(self.model_name)
        emb = model.encode([text], normalize_embeddings=True)
        return np.asarray(emb[0], dtype=np.float32)

    def add(
        self,
        key: str,
        normalized_sql: str,
        tables: list[str],
        analysis: Any,
    ) -> None:
        """Indexes a query execution in the similarity cache."""
        masked = mask_literals_and_aliases(normalized_sql)
        vec = self._encode(masked)
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
            # Check if key already exists
            existing_idx = next((i for i, e in enumerate(self._entries) if e.key == key), None)
            if existing_idx is not None:
                self._entries[existing_idx] = entry
                if self._vectors is not None:
                    self._vectors[existing_idx] = vec
            else:
                self._entries.append(entry)
                if self._vectors is None:
                    self._vectors = np.expand_dims(vec, axis=0)
                else:
                    self._vectors = np.vstack([self._vectors, np.expand_dims(vec, axis=0)])

    def find_similar(
        self,
        normalized_sql: str,
        tables: list[str],
        reuse_threshold: float = 0.97,
        hint_threshold: float = 0.85,
    ) -> tuple[Literal["similar_reused", "similar_hint", "miss"], dict[str, Any] | None, float]:
        """Performs cosine similarity search against cached queries.

        Returns:
          ("similar_reused", payload, score): if cosine >= reuse_threshold, tables match, and structure matches.
          ("similar_hint", payload, score): if cosine >= hint_threshold (passed to agent as untrusted hint).
          ("miss", None, score): if below hint_threshold or no candidates exist.
        """
        with self._lock:
            if not self._entries or self._vectors is None:
                return "miss", None, 0.0

            masked = mask_literals_and_aliases(normalized_sql)
            query_vec = self._encode(masked)

            # Cosine similarity (both vectors are L2 normalized)
            scores = np.dot(self._vectors, query_vec)
            best_idx = int(np.argmax(scores))
            best_score = float(scores[best_idx])
            best_entry = self._entries[best_idx]

        target_tables = sorted(t.strip().upper() for t in tables)
        fp = compute_structure_fingerprint(normalized_sql, tables)

        # 1. Reuse evaluation
        if best_score >= reuse_threshold:
            # Must have identical SAP table set and structural fingerprint
            if target_tables == best_entry.tables and fp == best_entry.structure_fingerprint:
                return "similar_reused", best_entry.analysis_payload, round(best_score, 4)

            # If score is high but structure or tables differ, downgrade to hint
            return "similar_hint", best_entry.analysis_payload, round(best_score, 4)

        # 2. Hint evaluation
        if best_score >= hint_threshold:
            return "similar_hint", best_entry.analysis_payload, round(best_score, 4)

        return "miss", None, round(best_score, 4)

    def clear(self) -> None:
        """Clears all indexed vectors."""
        with self._lock:
            self._entries.clear()
            self._vectors = None


_GLOBAL_SIMILARITY_INDEX = SimilarityCacheIndex()


def get_similarity_index() -> SimilarityCacheIndex:
    """Returns the singleton L3 similarity cache index."""
    return _GLOBAL_SIMILARITY_INDEX
