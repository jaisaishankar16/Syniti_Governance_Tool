"""Cache backend protocol and implementations for Redis and in-memory LRU storage."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol, runtime_checkable

import redis.asyncio as aioredis
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


@runtime_checkable
class CacheBackend(Protocol):
    """Protocol defining the asynchronous cache backend interface."""

    async def get(self, key: str) -> str | None:
        """Retrieves raw string value by key, or None if expired or missing."""
        ...

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        """Stores a key-value pair with an optional time-to-live in seconds."""
        ...

    async def delete(self, key: str) -> bool:
        """Deletes a key from storage."""
        ...

    async def scan_prefix(self, prefix: str) -> list[str]:
        """Returns all keys matching the given prefix pattern."""
        ...

    async def incr(self, key: str) -> int:
        """Increments an integer counter, returning the new value."""
        ...

    async def ping(self) -> bool:
        """Returns True if the backend service is healthy and reachable."""
        ...

    async def clear(self, prefix: str | None = None) -> int:
        """Deletes all keys matching prefix, or all keys if prefix is None."""
        ...

    def lock(self, key: str, timeout_seconds: float = 10.0) -> AbstractAsyncContextManager[Any]:
        """Returns an async context manager acquiring a distributed or process lock."""
        ...


class InMemoryBackend:
    """Thread-safe and async-safe in-memory cache backend with per-key TTLs and locking."""

    def __init__(self, max_entries: int = 5000) -> None:
        self.max_entries = max_entries
        self._data: dict[str, tuple[str, float | None]] = {}  # key -> (value, expiry_timestamp)
        self._lock = asyncio.Lock()
        self._key_locks: dict[str, asyncio.Lock] = {}

    def _prune_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, (_, exp) in self._data.items() if exp is not None and exp <= now]
        for k in expired:
            self._data.pop(k, None)

    async def get(self, key: str) -> str | None:
        async with self._lock:
            self._prune_expired()
            if key not in self._data:
                return None
            val, exp = self._data[key]
            if exp is not None and exp <= time.monotonic():
                self._data.pop(key, None)
                return None
            return val

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        async with self._lock:
            self._prune_expired()
            # Enforce size cap
            if len(self._data) >= self.max_entries and key not in self._data:
                # Remove oldest entry
                oldest_k = next(iter(self._data))
                self._data.pop(oldest_k, None)

            exp = (time.monotonic() + ttl_seconds) if ttl_seconds is not None else None
            self._data[key] = (value, exp)
            return True

    async def delete(self, key: str) -> bool:
        async with self._lock:
            return bool(self._data.pop(key, None))

    async def scan_prefix(self, prefix: str) -> list[str]:
        async with self._lock:
            self._prune_expired()
            return [k for k in self._data if k.startswith(prefix)]

    async def incr(self, key: str) -> int:
        async with self._lock:
            self._prune_expired()
            val, exp = self._data.get(key, ("0", None))
            try:
                new_val = int(val) + 1
            except ValueError:
                new_val = 1
            self._data[key] = (str(new_val), exp)
            return new_val

    async def ping(self) -> bool:
        return True

    async def clear(self, prefix: str | None = None) -> int:
        async with self._lock:
            if prefix is None:
                count = len(self._data)
                self._data.clear()
                return count
            keys = [k for k in self._data if k.startswith(prefix)]
            for k in keys:
                self._data.pop(k, None)
            return len(keys)

    @asynccontextmanager
    async def lock(self, key: str, timeout_seconds: float = 10.0) -> AsyncIterator[None]:
        lock_key = f"lock:{key}"
        async with self._lock:
            if lock_key not in self._key_locks:
                self._key_locks[lock_key] = asyncio.Lock()
            klock = self._key_locks[lock_key]

        try:
            await asyncio.wait_for(klock.acquire(), timeout=timeout_seconds)
            try:
                yield
            finally:
                klock.release()
        except asyncio.TimeoutError:
            raise TimeoutError(f"Could not acquire lock for {key} within {timeout_seconds}s")


class RedisBackend:
    """Production Redis cache backend using redis.asyncio with graceful degradation."""

    def __init__(
        self,
        redis_client: aioredis.Redis | None = None,
        redis_url: str = "redis://localhost:6379/0",
        socket_timeout: float = 1.0,
        socket_connect_timeout: float = 1.0,
    ) -> None:
        if redis_client is not None:
            self._client = redis_client
        else:
            self._client = aioredis.from_url(
                redis_url,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_connect_timeout,
                decode_responses=True,
            )

    async def get(self, key: str) -> str | None:
        try:
            val = await self._client.get(key)
            return str(val) if val is not None else None
        except RedisError as exc:
            logger.warning("Redis get error for key '%s': %s (treated as cache miss)", key, exc)
            return None

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        try:
            if ttl_seconds is not None:
                await self._client.set(key, value, ex=ttl_seconds)
            else:
                await self._client.set(key, value)
            return True
        except RedisError as exc:
            logger.warning("Redis set error for key '%s': %s", key, exc)
            return False

    async def delete(self, key: str) -> bool:
        try:
            count = await self._client.delete(key)
            return count > 0
        except RedisError as exc:
            logger.warning("Redis delete error for key '%s': %s", key, exc)
            return False

    async def scan_prefix(self, prefix: str) -> list[str]:
        try:
            keys: list[str] = []
            pattern = f"{prefix}*"
            cur: Any = b"0"
            while cur:
                cur, batch = await self._client.scan(cursor=cur, match=pattern, count=100)
                for k in batch:
                    keys.append(k if isinstance(k, str) else k.decode("utf-8"))
                if str(cur) in ("0", b"0"):
                    break
            return keys
        except RedisError as exc:
            logger.warning("Redis scan error for prefix '%s': %s", prefix, exc)
            return []

    async def incr(self, key: str) -> int:
        try:
            return await self._client.incr(key)
        except RedisError as exc:
            logger.warning("Redis incr error for key '%s': %s", key, exc)
            return 1

    async def ping(self) -> bool:
        try:
            return await self._client.ping()
        except RedisError:
            return False

    async def clear(self, prefix: str | None = None) -> int:
        try:
            keys = await self.scan_prefix(prefix or "")
            if keys:
                return await self._client.delete(*keys)
            return 0
        except RedisError as exc:
            logger.warning("Redis clear error: %s", exc)
            return 0

    @asynccontextmanager
    async def lock(self, key: str, timeout_seconds: float = 10.0) -> AsyncIterator[Any]:
        lock_key = f"lock:{key}"
        lock_obj = self._client.lock(
            lock_key, timeout=timeout_seconds, blocking_timeout=timeout_seconds
        )
        try:
            acquired = await lock_obj.acquire()
            if not acquired:
                raise TimeoutError(f"Could not acquire Redis lock for {key}")
            try:
                yield lock_obj
            finally:
                await lock_obj.release()
        except RedisError as exc:
            logger.warning(
                "Redis lock error for key '%s': %s (proceeding without distributed lock)", key, exc
            )
            yield None


_GLOBAL_MEMORY_BACKEND = InMemoryBackend()


async def get_cache_backend(
    mode: str = "auto",
    redis_url: str = "redis://localhost:6379/0",
) -> CacheBackend:
    """Factory selecting the appropriate cache backend based on configuration and availability.

    Modes:
      - 'memory': Always in-memory
      - 'redis': Always Redis (raises or warns on failure)
      - 'auto': Attempts to connect to Redis; falls back to in-memory if unreachable.
    """
    if mode.lower() == "memory":
        return _GLOBAL_MEMORY_BACKEND

    if mode.lower() in ("redis", "auto"):
        try:
            r_backend = RedisBackend(
                redis_url=redis_url, socket_timeout=0.5, socket_connect_timeout=0.5
            )
            is_alive = await asyncio.wait_for(r_backend.ping(), timeout=0.6)
            if is_alive:
                return r_backend
            if mode.lower() == "redis":
                logger.error("Redis ping failed in mode='redis'; falling back to memory")
                return _GLOBAL_MEMORY_BACKEND
            logger.info("Redis ping failed in mode='auto'; using in-memory backend")
            return _GLOBAL_MEMORY_BACKEND
        except (RedisError, OSError, asyncio.TimeoutError) as exc:
            logger.info("Redis unavailable (%s); using in-memory backend", exc)
            return _GLOBAL_MEMORY_BACKEND

    return _GLOBAL_MEMORY_BACKEND
