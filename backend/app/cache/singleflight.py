"""SingleFlight coalescing mechanism preventing thundering-herd on identical concurrent requests."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from app.cache.backends import CacheBackend

T = TypeVar("T")
logger = logging.getLogger(__name__)


class SingleFlight:
    """Coalesces concurrent identical in-flight requests to run exactly once."""

    def __init__(self, backend: CacheBackend | None = None) -> None:
        self.backend = backend
        self._calls: dict[str, tuple[asyncio.Future[Any], int]] = {}
        self._lock = asyncio.Lock()

    async def execute(
        self,
        key: str,
        work_fn: Callable[[], Coroutine[Any, Any, T]],
    ) -> tuple[T, bool]:
        """Executes work_fn for key. If already in flight, awaits the existing execution.

        Returns:
          (result, is_coalesced): is_coalesced is True if this invocation waited on another execution.
        """
        async with self._lock:
            if key in self._calls:
                future, waiters = self._calls[key]
                self._calls[key] = (future, waiters + 1)
                is_coalesced = True
            else:
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._calls[key] = (future, 0)
                is_coalesced = False

        if is_coalesced:
            # Wait for leader to finish
            result = await future
            return result, True

        # Leader executes work
        try:
            # If backend supports locking, use lock for multi-process safety
            if self.backend is not None:
                async with self.backend.lock(key, timeout_seconds=15.0):
                    res = await work_fn()
            else:
                res = await work_fn()

            future.set_result(res)
            return res, False

        except BaseException as exc:
            async with self._lock:
                _, waiters = self._calls.get(key, (future, 0))
            if waiters > 0:
                future.set_exception(exc)
            else:
                # Cancel future if no callers are awaiting to avoid unretrieved future warning
                future.cancel()
            raise

        finally:
            async with self._lock:
                self._calls.pop(key, None)


_GLOBAL_SINGLE_FLIGHT = SingleFlight()


def get_single_flight(backend: CacheBackend | None = None) -> SingleFlight:
    """Returns singleton SingleFlight instance."""
    if backend is not None:
        _GLOBAL_SINGLE_FLIGHT.backend = backend
    return _GLOBAL_SINGLE_FLIGHT
