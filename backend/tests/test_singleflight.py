"""Unit tests for SingleFlight concurrent request coalescing."""

from __future__ import annotations

import asyncio

from app.cache.singleflight import SingleFlight


class TestSingleFlight:
    """Verifies that concurrent identical requests coalesce into a single execution."""

    def test_20_concurrent_requests_execute_once(self) -> None:
        async def _test() -> None:
            sf = SingleFlight()
            execution_count = 0

            async def expensive_work() -> str:
                nonlocal execution_count
                execution_count += 1
                await asyncio.sleep(0.05)
                return "computed_result"

            # Launch 20 concurrent tasks requesting the same key
            tasks = [
                asyncio.create_task(sf.execute("query_key_1", expensive_work)) for _ in range(20)
            ]
            results = await asyncio.gather(*tasks)

            # Assert exactly 1 leader executed
            assert execution_count == 1
            assert len(results) == 20

            # All 20 results must match
            for res, _ in results:
                assert res == "computed_result"

            # Exactly 1 leader was not coalesced, and 19 were coalesced
            coalesced_flags = [c for _, c in results]
            assert coalesced_flags.count(False) == 1
            assert coalesced_flags.count(True) == 19

        asyncio.run(_test())

    def test_exception_propagates_to_all_waiters(self) -> None:
        async def _test() -> None:
            sf = SingleFlight()

            async def failing_work() -> str:
                await asyncio.sleep(0.02)
                raise ValueError("Worker failed deliberately")

            tasks = [asyncio.create_task(sf.execute("failing_key", failing_work)) for _ in range(5)]

            results = await asyncio.gather(*tasks, return_exceptions=True)
            assert len(results) == 5
            for res in results:
                assert isinstance(res, ValueError)
                assert "Worker failed deliberately" in str(res)

        asyncio.run(_test())
