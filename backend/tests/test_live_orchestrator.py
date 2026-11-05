"""Live end-to-end orchestrator tests against real OpenAI-compatible LLM endpoint.

These tests execute full analysis workflows including guardrails, multi-tier caching,
domain agents, and business intelligence synthesis using real LLM calls.

Activated ONLY when environment variable RUN_LIVE_LLM=1 is set.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from app.agents.settings import get_llm_settings, is_valid_api_key
from app.cache.backends import InMemoryBackend
from app.orchestrator.orchestrator import analyze
from app.orchestrator.settings import OrchestratorSettings
from app.synthesizer.models import BusinessIntelligence

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_LLM") != "1",
    reason="Requires active OPENAI_API_KEY and RUN_LIVE_LLM=1",
)


def _load_fixtures() -> list[dict]:
    fixture_path = Path(__file__).resolve().parent / "fixtures" / "examples.json"
    if not fixture_path.exists():
        pytest.skip(f"Fixture file {fixture_path} not found")
    with open(fixture_path, "r", encoding="utf-8") as f:
        return json.load(f)


class TestLiveOrchestrator:
    """Verifies live LLM execution, synthesis quality, and cache hit persistence."""

    @pytest.fixture(autouse=True)
    def _check_key(self) -> None:
        settings = get_llm_settings()
        raw_key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
        if not is_valid_api_key(raw_key):
            pytest.skip(
                "RUN_LIVE_LLM=1 set but no valid OPENAI_API_KEY configured in environment or .env"
            )

    def test_live_sheet1_twice_verifies_l1_cache_hit(self) -> None:
        """Runs Sheet1 twice and asserts the second run hits L1 cache with zero LLM calls."""

        async def _test() -> None:
            examples = _load_fixtures()
            sheet1_ex = next(ex for ex in examples if ex.get("id") == "Sheet1")
            sql = sheet1_ex["input_sql"]

            backend = InMemoryBackend()
            orch_settings = OrchestratorSettings()

            # First run: miss
            res1 = await analyze(
                sql=sql,
                cache_backend=backend,
                settings=orch_settings,
                force_refresh=True,
            )
            assert isinstance(res1, BusinessIntelligence)
            assert res1.meta.cache_status in ("miss", "refresh")
            assert res1.meta.ai_generated is True
            assert res1.governance.rule != ""
            assert res1.governance.subject_area != ""

            # Second run: must hit L1 cache (zero agent and synthesizer calls)
            res2 = await analyze(
                sql=sql,
                cache_backend=backend,
                settings=orch_settings,
                force_refresh=False,
            )
            assert isinstance(res2, BusinessIntelligence)
            assert res2.meta.cache_status == "hit_final"
            assert res2.governance.rule == res1.governance.rule
            assert (
                res2.meta.token_usage.total == 0
                or res2.meta.token_usage.total == res1.meta.token_usage.total
            )

        asyncio.run(_test())

    def test_live_workbook_examples(self) -> None:
        """Executes live analysis on all 11 workbook examples from examples.json."""

        async def _test() -> None:
            examples = _load_fixtures()
            backend = InMemoryBackend()
            orch_settings = OrchestratorSettings()

            for ex in examples:
                ex_id = ex.get("id", "example")
                sql = ex["input_sql"]

                res = await analyze(
                    sql=sql,
                    cache_backend=backend,
                    settings=orch_settings,
                    force_refresh=False,
                )

                assert isinstance(res, BusinessIntelligence), f"Failed for {ex_id}"
                assert res.governance.rule != "", f"Empty rule for {ex_id}"
                assert res.meta.ai_generated is True, f"ai_generated false for {ex_id}"
                assert res.meta.cache_status in ("miss", "hit_final", "partial"), (
                    f"Invalid cache_status for {ex_id}"
                )

        asyncio.run(_test())
