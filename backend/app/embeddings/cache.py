"""In-memory and on-disk embedding caches."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.embeddings.settings import EmbeddingSettings

logger = logging.getLogger("app.embeddings.cache")


def find_repo_root() -> Path:
    """Finds repository root anchoring on backend/app/embeddings/cache.py."""
    return Path(__file__).resolve().parents[3]


REPO_ROOT = find_repo_root()
DEFAULT_DISK_CACHE_DIR = REPO_ROOT / "data" / "embedding_cache"


class QueryEmbeddingCache:
    """Thread-safe LRU cache for in-process query embeddings."""

    def __init__(self, maxsize: int = 2000) -> None:
        self.maxsize = maxsize
        self._cache: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def _make_key(
        self,
        model: str,
        dimensions: int | None,
        base_url_hash: str,
        text: str,
    ) -> str:
        raw = f"{model}:{dimensions}:{base_url_hash}:{text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def get(
        self,
        model: str,
        dimensions: int | None,
        base_url_hash: str,
        text: str,
    ) -> list[float] | None:
        key = self._make_key(model, dimensions, base_url_hash, text)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self.hits += 1
                return self._cache[key]
            self.misses += 1
            return None

    def put(
        self,
        model: str,
        dimensions: int | None,
        base_url_hash: str,
        text: str,
        vector: list[float],
    ) -> None:
        key = self._make_key(model, dimensions, base_url_hash, text)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self._cache[key] = vector
            else:
                self._cache[key] = vector
                if len(self._cache) > self.maxsize:
                    self._cache.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self.hits = 0
            self.misses = 0


class DocumentEmbeddingDiskCache:
    """On-disk cache for document chunk embeddings keyed by sha256(model + dimensions + text).

    Stored under data/embedding_cache/ so index rebuilds do not re-embed unchanged chunks.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir or DEFAULT_DISK_CACHE_DIR
        self._lock = threading.Lock()

    def _make_key(self, model: str, dimensions: int | None, text: str) -> str:
        raw = f"{model}:{dimensions}:{text}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _get_path(self, key: str) -> Path:
        prefix = key[:2]
        return self.cache_dir / prefix / f"{key}.json"

    def get(self, model: str, dimensions: int | None, text: str) -> list[float] | None:
        key = self._make_key(model, dimensions, text)
        path = self._get_path(key)
        if not path.is_file():
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed reading document embedding cache at %s: %s", path, exc)
            return None
        return None

    def put(
        self,
        model: str,
        dimensions: int | None,
        text: str,
        vector: list[float],
    ) -> None:
        key = self._make_key(model, dimensions, text)
        path = self._get_path(key)
        try:
            with self._lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp_path = path.with_suffix(".tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(vector, f)
                tmp_path.replace(path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed writing document embedding cache to %s: %s", path, exc)


_GLOBAL_QUERY_CACHE: QueryEmbeddingCache | None = None
_GLOBAL_DISK_CACHE: DocumentEmbeddingDiskCache | None = None


def get_query_cache(settings: EmbeddingSettings | None = None) -> QueryEmbeddingCache:
    """Returns singleton instance of QueryEmbeddingCache."""
    global _GLOBAL_QUERY_CACHE
    if _GLOBAL_QUERY_CACHE is None:
        from app.embeddings.settings import get_embedding_settings

        s = settings or get_embedding_settings()
        _GLOBAL_QUERY_CACHE = QueryEmbeddingCache(maxsize=s.embedding_query_cache_size)
    return _GLOBAL_QUERY_CACHE


def get_disk_cache(cache_dir: Path | None = None) -> DocumentEmbeddingDiskCache:
    """Returns singleton instance of DocumentEmbeddingDiskCache."""
    global _GLOBAL_DISK_CACHE
    if _GLOBAL_DISK_CACHE is None:
        _GLOBAL_DISK_CACHE = DocumentEmbeddingDiskCache(cache_dir=cache_dir)
    return _GLOBAL_DISK_CACHE
