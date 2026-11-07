"""OpenAI-compatible embedder implementation with batching, validation, and error classification."""

from __future__ import annotations

import asyncio
import math
import random
import time
from typing import TYPE_CHECKING, Any, Protocol

from app.embeddings.cache import get_query_cache
from app.embeddings.client import create_embedding_async_client, create_embedding_sync_client
from app.embeddings.models import (
    EmbeddingResponseError,
    EmbeddingResult,
    EmbeddingUsage,
)
from app.embeddings.settings import EmbeddingSettings, get_embedding_settings

if TYPE_CHECKING:
    from openai import AsyncOpenAI, OpenAI

SAFE_CHAR_CAP = 24000  # ~6,800 tokens, well below 8,191 OpenAI limit
MAX_BATCH_TOKENS = 64000  # Cap total tokens in a single request batch


def estimate_tokens(text: str) -> int:
    """Estimates token count deterministically without external tokenizers or network calls."""
    return max(1, math.ceil(len(text) / 3.5))


def classify_embedding_error(exc: Exception) -> tuple[str, bool, int | None]:
    """Classifies provider exception into standard error code, retryability, and HTTP status."""
    err_str = str(exc).lower()

    # 1. 401 / 403 Authentication failure
    if "401" in err_str or "unauthorized" in err_str or "invalid_api_key" in err_str or "invalid api key" in err_str:
        return "LLM_AUTH_FAILED", False, 401
    if "403" in err_str or "forbidden" in err_str:
        return "LLM_AUTH_FAILED", False, 403

    # 2. 404 Model Not Found
    if "404" in err_str or "model_not_found" in err_str or "model not found" in err_str or "does not exist" in err_str:
        return "EMBEDDING_MODEL_NOT_FOUND", False, 404

    # 3. 429 Quota / Billing Exceeded (Do NOT retry)
    if (
        "quota" in err_str
        or "billing" in err_str
        or "insufficient_quota" in err_str
        or "account not active" in err_str
        or "exceeded your current quota" in err_str
    ):
        return "LLM_QUOTA_EXCEEDED", False, 429

    # 4. 429 Rate limit (Retryable)
    if "429" in err_str or "rate_limit" in err_str or "rate limit" in err_str:
        return "LLM_RATE_LIMITED", True, 429

    # 5. Network / Timeout / 5xx (Retryable)
    if "timeout" in err_str or "timed out" in err_str:
        return "LLM_UNAVAILABLE", True, 504
    if any(code in err_str for code in ("500", "502", "503", "504", "bad gateway", "connection error")):
        return "LLM_UNAVAILABLE", True, 503

    return "EMBEDDING_UNAVAILABLE", True, None


class Embedder(Protocol):
    """Protocol defining the embedding interface."""

    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int | None: ...

    async def embed_documents(self, texts: list[str]) -> EmbeddingResult: ...

    async def embed_query(self, text: str) -> list[float]: ...

    def embed_documents_sync(self, texts: list[str]) -> EmbeddingResult: ...

    def embed_query_sync(self, text: str) -> list[float]: ...


class OpenAIEmbedder:
    """Production embedder communicating with OpenAI or OpenAI-compatible AI gateway."""

    def __init__(
        self,
        settings: EmbeddingSettings | None = None,
        async_client: AsyncOpenAI | None = None,
        sync_client: OpenAI | None = None,
    ) -> None:
        self.settings = settings or get_embedding_settings()
        self._async_client = async_client
        self._sync_client = sync_client
        self._semaphore = asyncio.Semaphore(self.settings.embedding_max_concurrency)

    @property
    def model(self) -> str:
        return self.settings.embedding_model

    @property
    def dimensions(self) -> int | None:
        return self.settings.embedding_dimensions

    def _get_async_client(self) -> AsyncOpenAI:
        if self._async_client is None:
            self._async_client = create_embedding_async_client(self.settings)
        return self._async_client

    def _get_sync_client(self) -> OpenAI:
        if self._sync_client is None:
            self._sync_client = create_embedding_sync_client(self.settings)
        return self._sync_client

    def _prepare_inputs(self, texts: list[str]) -> tuple[list[str], int]:
        """Truncates texts exceeding safe character cap deterministically."""
        cleaned: list[str] = []
        truncated_count = 0
        for t in texts:
            if len(t) > SAFE_CHAR_CAP:
                cleaned.append(t[:SAFE_CHAR_CAP])
                truncated_count += 1
            else:
                cleaned.append(t)
        return cleaned, truncated_count

    def _partition_batches(self, texts: list[str]) -> list[list[str]]:
        """Partitions input texts into batches constrained by count and token estimate."""
        batches: list[list[str]] = []
        cur_batch: list[str] = []
        cur_tokens = 0

        for t in texts:
            t_tok = estimate_tokens(t)
            if cur_batch and (
                len(cur_batch) >= self.settings.embedding_batch_size
                or (cur_tokens + t_tok > MAX_BATCH_TOKENS)
            ):
                batches.append(cur_batch)
                cur_batch = [t]
                cur_tokens = t_tok
            else:
                cur_batch.append(t)
                cur_tokens += t_tok

        if cur_batch:
            batches.append(cur_batch)
        return batches

    async def _embed_single_batch_with_retry(
        self,
        batch: list[str],
        max_retries: int = 3,
    ) -> tuple[list[list[float]], int]:
        """Calls provider with exponential backoff for transient errors."""
        client = self._get_async_client()
        call_kwargs: dict[str, Any] = {
            "model": self.settings.embedding_model,
            "input": batch,
            "encoding_format": "float",
        }
        if self.settings.embedding_dimensions is not None:
            call_kwargs["dimensions"] = self.settings.embedding_dimensions

        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._semaphore:
                    resp = await client.embeddings.create(**call_kwargs)

                # Validate count
                data = resp.data
                if len(data) != len(batch):
                    raise EmbeddingResponseError(
                        f"Provider returned {len(data)} vectors for {len(batch)} inputs",
                        code="EMBEDDING_VALIDATION_ERROR",
                    )

                # Sort by index
                data_sorted = sorted(data, key=lambda item: item.index)
                raw_vectors = [item.embedding for item in data_sorted]

                tokens_used = 0
                if resp.usage:
                    tokens_used = resp.usage.total_tokens or resp.usage.prompt_tokens or 0

                return raw_vectors, tokens_used

            except EmbeddingResponseError:
                raise
            except Exception as exc:
                code, retryable, status_c = classify_embedding_error(exc)
                if not retryable or attempt > max_retries:
                    raise EmbeddingResponseError(
                        f"Embedding call failed: {exc}",
                        code=code,
                        status_code=status_c,
                        retryable=retryable,
                    ) from exc

                # Parse Retry-After or calculate exponential backoff + jitter
                delay = (0.5 * (2 ** (attempt - 1))) + (random.uniform(0.1, 0.5))
                await asyncio.sleep(delay)

    async def embed_documents(self, texts: list[str]) -> EmbeddingResult:
        """Embeds a list of document strings, returning validated normalized vectors."""
        if not texts:
            return EmbeddingResult(
                vectors=[],
                model=self.settings.embedding_model,
                dimensions=self.settings.embedding_dimensions or 0,
                usage=EmbeddingUsage(),
                request_count=0,
                truncated_count=0,
                latency_ms=0.0,
            )

        start_time = time.perf_counter()
        prepared_texts, truncated_count = self._prepare_inputs(texts)
        batches = self._partition_batches(prepared_texts)

        all_vectors: list[list[float]] = []
        total_tokens = 0
        request_count = len(batches)

        # Execute batches concurrently within semaphore
        tasks = [self._embed_single_batch_with_retry(b) for b in batches]
        results = await asyncio.gather(*tasks)

        for batch_vecs, b_tokens in results:
            all_vectors.extend(batch_vecs)
            total_tokens += b_tokens

        if not all_vectors:
            actual_dim = self.settings.embedding_dimensions or 0
        else:
            actual_dim = len(all_vectors[0])

        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        usage = EmbeddingUsage(prompt_tokens=total_tokens, total_tokens=total_tokens)

        return EmbeddingResult(
            vectors=all_vectors,
            model=self.settings.embedding_model,
            dimensions=actual_dim,
            usage=usage,
            request_count=request_count,
            truncated_count=truncated_count,
            latency_ms=latency_ms,
        )

    async def embed_query(self, text: str) -> list[float]:
        """Embeds a single query string, utilizing the in-process query LRU cache."""
        cache = get_query_cache(self.settings)
        cached = cache.get(
            model=self.settings.embedding_model,
            dimensions=self.settings.embedding_dimensions,
            base_url_hash=self.settings.base_url_hash,
            text=text,
        )
        if cached is not None:
            return cached

        res = await self.embed_documents([text])
        if not res.vectors:
            raise EmbeddingResponseError("Embedder returned no vectors for query", code="EMBEDDING_UNAVAILABLE")

        vec = res.vectors[0]
        cache.put(
            model=self.settings.embedding_model,
            dimensions=self.settings.embedding_dimensions,
            base_url_hash=self.settings.base_url_hash,
            text=text,
            vector=vec,
        )
        return vec

    def embed_documents_sync(self, texts: list[str]) -> EmbeddingResult:
        """Synchronous wrapper for scripts (e.g. build_index, calibration)."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(asyncio.run, self.embed_documents(texts)).result()
            return loop.run_until_complete(self.embed_documents(texts))
        except RuntimeError:
            return asyncio.run(self.embed_documents(texts))

    def embed_query_sync(self, text: str) -> list[float]:
        """Synchronous query embedding wrapper."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(asyncio.run, self.embed_query(text)).result()
            return loop.run_until_complete(self.embed_query(text))
        except RuntimeError:
            return asyncio.run(self.embed_query(text))


_GLOBAL_EMBEDDER: Embedder | None = None


def get_embedder(settings: EmbeddingSettings | None = None) -> Embedder:
    """Returns singleton instance of Embedder."""
    global _GLOBAL_EMBEDDER
    if _GLOBAL_EMBEDDER is None:
        s = settings or get_embedding_settings()
        _GLOBAL_EMBEDDER = OpenAIEmbedder(settings=s)
    return _GLOBAL_EMBEDDER


def set_global_embedder(embedder: Embedder | None) -> None:
    """Sets or resets the global embedder instance (used in tests)."""
    global _GLOBAL_EMBEDDER
    _GLOBAL_EMBEDDER = embedder
