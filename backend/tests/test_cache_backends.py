"""Unit tests for cache backends (InMemoryBackend, RedisBackend with fakeredis, and auto fallback)."""

from __future__ import annotations

import asyncio

import fakeredis.aioredis
from app.cache.backends import (
    InMemoryBackend,
    RedisBackend,
    get_cache_backend,
)


class TestInMemoryBackend:
    """Verifies in-memory backend operations, TTL expiration, and eviction."""

    def test_basic_crud(self) -> None:
        async def _test() -> None:
            backend = InMemoryBackend(max_entries=10)
            assert await backend.get("k1") is None

            # Set & Get
            assert await backend.set("k1", "val1") is True
            assert await backend.get("k1") == "val1"

            # Exists via scan
            keys = await backend.scan_prefix("k")
            assert "k1" in keys

            # Delete
            assert await backend.delete("k1") is True
            assert await backend.get("k1") is None
            assert await backend.delete("k1") is False

        asyncio.run(_test())

    def test_ttl_expiration(self) -> None:
        async def _test() -> None:
            backend = InMemoryBackend(max_entries=10)
            await backend.set("temp", "expiring", ttl_seconds=1)
            assert await backend.get("temp") == "expiring"

            # Immediate expiration
            await backend.set("immediate", "val", ttl_seconds=0)
            assert await backend.get("immediate") is None

        asyncio.run(_test())

    def test_max_entries_eviction(self) -> None:
        async def _test() -> None:
            backend = InMemoryBackend(max_entries=3)
            await backend.set("a", "1")
            await backend.set("b", "2")
            await backend.set("c", "3")
            assert len(backend._data) == 3

            # Adding 4th entry evicts oldest (a)
            await backend.set("d", "4")
            assert len(backend._data) == 3
            assert await backend.get("a") is None
            assert await backend.get("b") == "2"
            assert await backend.get("d") == "4"

        asyncio.run(_test())

    def test_clear_and_incr(self) -> None:
        async def _test() -> None:
            backend = InMemoryBackend()
            assert await backend.incr("counter") == 1
            assert await backend.incr("counter") == 2

            await backend.set("test:1", "a")
            await backend.set("test:2", "b")
            cleared = await backend.clear(prefix="test:")
            assert cleared == 2
            assert await backend.get("test:1") is None
            assert await backend.get("counter") == "2"

        asyncio.run(_test())

    def test_async_lock(self) -> None:
        async def _test() -> None:
            backend = InMemoryBackend()
            acquired = False
            async with backend.lock("my_lock"):
                acquired = True
            assert acquired is True

        asyncio.run(_test())


class TestRedisBackendWithFakeRedis:
    """Verifies RedisBackend using in-memory fakeredis client."""

    def test_fakeredis_crud_and_ttl(self) -> None:
        async def _test() -> None:
            fake_client = fakeredis.aioredis.FakeRedis(decode_responses=True)
            backend = RedisBackend(redis_client=fake_client)

            assert await backend.ping() is True

            # Set & Get
            assert await backend.set("r_key", "r_val", ttl_seconds=60) is True
            assert await backend.get("r_key") == "r_val"

            # Incr
            assert await backend.incr("r_cnt") == 1
            assert await backend.incr("r_cnt") == 2

            # Prefix scan
            keys = await backend.scan_prefix("r_")
            assert "r_key" in keys
            assert "r_cnt" in keys

            # Delete & Clear
            assert await backend.delete("r_key") is True
            assert await backend.get("r_key") is None

            cleared = await backend.clear(prefix="r_")
            assert cleared >= 1

        asyncio.run(_test())


class TestCacheBackendFactoryFallback:
    """Verifies get_cache_backend fallback to in-memory on unroutable redis port."""

    def test_auto_fallback_to_memory(self) -> None:
        async def _test() -> None:
            backend = await get_cache_backend(mode="auto", redis_url="redis://127.0.0.1:59999/0")
            assert isinstance(backend, InMemoryBackend)
            assert await backend.ping() is True

        asyncio.run(_test())
