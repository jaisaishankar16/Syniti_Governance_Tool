"""Deterministic hashed bag-of-words fake embedder for offline unit testing."""

from __future__ import annotations

import hashlib
import re

import numpy as np
from app.embeddings.models import EmbeddingResult, EmbeddingUsage


class FakeEmbedder:
    """Test double implementing Embedder protocol with deterministic, normalized vectors.

    Produces realistic normalized vectors where lexical overlap yields positive cosine similarity.
    Requires ZERO network access.
    """

    def __init__(
        self,
        dimensions: int = 1536,
        model: str = "text-embedding-3-small",
        simulate_error: Exception | None = None,
    ) -> None:
        self._dimensions = dimensions
        self._model = model
        self.simulate_error = simulate_error
        self.call_count = 0
        self.embedded_texts: list[str] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def _encode_text(self, text: str) -> list[float]:
        vec = np.zeros(self._dimensions, dtype=np.float32)
        tokens = re.findall(r"\w+", text.lower())
        if not tokens:
            vec[0] = 1.0
            return vec.tolist()

        for t in tokens:
            h = int(hashlib.md5(t.encode("utf-8")).hexdigest(), 16)
            idx1 = h % self._dimensions
            sign1 = 1.0 if (h >> 16) % 2 == 0 else -1.0
            vec[idx1] += sign1

            idx2 = (h >> 8) % self._dimensions
            sign2 = 1.0 if (h >> 24) % 2 == 0 else -1.0
            vec[idx2] += sign2 * 0.5

        norm = float(np.linalg.norm(vec))
        if norm > 0.0:
            vec = vec / norm
        else:
            vec[0] = 1.0
        return vec.tolist()

    async def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        self.call_count += 1
        if self.simulate_error is not None:
            raise self.simulate_error

        self.embedded_texts.extend(texts)
        vectors = [self._encode_text(t) for t in texts]
        return EmbeddingResult(
            vectors=vectors,
            model=self._model,
            dimensions=self._dimensions,
            usage=EmbeddingUsage(prompt_tokens=len(texts) * 10, total_tokens=len(texts) * 10),
            request_count=1,
            truncated_count=0,
            latency_ms=1.0,
        )

    async def embed_query(self, text: str) -> list[float]:
        self.call_count += 1
        if self.simulate_error is not None:
            raise self.simulate_error

        self.embedded_texts.append(text)
        return self._encode_text(text)

    def embed_documents_sync(self, texts: list[str]) -> EmbeddingResult:
        self.call_count += 1
        if self.simulate_error is not None:
            raise self.simulate_error

        self.embedded_texts.extend(texts)
        vectors = [self._encode_text(t) for t in texts]
        return EmbeddingResult(
            vectors=vectors,
            model=self._model,
            dimensions=self._dimensions,
            usage=EmbeddingUsage(prompt_tokens=len(texts) * 10, total_tokens=len(texts) * 10),
            request_count=1,
            truncated_count=0,
            latency_ms=1.0,
        )

    def embed_query_sync(self, text: str) -> list[float]:
        self.call_count += 1
        if self.simulate_error is not None:
            raise self.simulate_error

        self.embedded_texts.append(text)
        return self._encode_text(text)
