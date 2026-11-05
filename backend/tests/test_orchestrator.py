"""Unit tests for the end-to-end Orchestrator pipeline, progress events, and QuerySource."""

from __future__ import annotations

import asyncio

import pytest
from app.agents.settings import LLMSettings
from app.cache.backends import InMemoryBackend
from app.errors import LLMNotConfiguredError
from app.orchestrator.orchestrator import TextInputSource, analyze
from app.orchestrator.progress import ProgressEvent
from app.orchestrator.settings import OrchestratorSettings
from app.synthesizer.models import BusinessIntelligence

from tests.support.fakes import FakeModel


class TestOrchestratorPipeline:
    """Verifies end-to-end analysis orchestration, progress callbacks, and caching."""

    def test_full_pipeline_with_injected_model_and_cache_hit(self) -> None:
        async def _test() -> None:
            sql = "SELECT MARC.MATNR, MARA.MTART FROM [MM_MARC_ECC_EU_R1_MARC_S] MARC INNER JOIN [MM_MARA_ECC_EU_R1_MARA_S] MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 1"
            backend = InMemoryBackend()
            fake_model = FakeModel()
            settings = OrchestratorSettings()

            # First run: Cache miss, full execution
            res1 = await analyze(
                sql=sql,
                model=fake_model,
                cache_backend=backend,
                settings=settings,
            )
            assert isinstance(res1, BusinessIntelligence)
            assert res1.meta.cache_status == "miss"
            assert res1.meta.ai_generated is True
            assert res1.meta.validation.opportunity_sql_ok is True
            assert "total" in res1.meta.stage_timings_ms

            # Second run: Must hit L1 cache (zero runner calls)
            res2 = await analyze(
                sql=sql,
                model=fake_model,
                cache_backend=backend,
                settings=settings,
            )
            assert res2.meta.cache_status == "hit_final"
            assert res2.governance.rule == res1.governance.rule

        asyncio.run(_test())

    def test_missing_api_key_raises_llm_not_configured(self) -> None:
        async def _test() -> None:
            empty_settings = LLMSettings(openai_api_key=None)
            with pytest.raises(LLMNotConfiguredError) as exc_info:
                await analyze(
                    sql="SELECT [MATNR] FROM [MM_MARC_ECC_EU_R1_MARC_S]",
                    llm_settings=empty_settings,
                    model=None,
                )
            assert "LLM_NOT_CONFIGURED" in str(exc_info.value)

        asyncio.run(_test())

    def test_missing_api_key_raises_before_parsing_or_caching(self) -> None:
        async def _test() -> None:
            from unittest.mock import patch

            backend = InMemoryBackend()
            empty_settings = LLMSettings(openai_api_key=None)

            with (
                patch("app.parser.sql_parser.SqlParser.parse") as mock_parse,
                patch.object(backend, "get", wraps=backend.get) as mock_get,
            ):
                with pytest.raises(LLMNotConfiguredError) as exc_info:
                    await analyze(
                        sql="SELECT [MATNR] FROM [MM_MARC_ECC_EU_R1_MARC_S]",
                        llm_settings=empty_settings,
                        cache_backend=backend,
                        model=None,
                    )
                assert exc_info.value.code.value == "LLM_NOT_CONFIGURED"
                assert mock_parse.call_count == 0
                assert mock_get.call_count == 0

        asyncio.run(_test())

    def test_progress_events_emitted_in_order(self) -> None:
        async def _test() -> None:
            sql = "SELECT [MATNR] FROM [MM_MARC_ECC_EU_R1_MARC_S]"
            backend = InMemoryBackend()
            events: list[ProgressEvent] = []

            async def callback(evt: ProgressEvent) -> None:
                events.append(evt)

            await analyze(
                sql=sql,
                model=FakeModel(),
                cache_backend=backend,
                on_progress=callback,
            )

            stages = [e.stage for e in events]
            assert "guardrails" in stages
            assert "parsing" in stages
            assert "done" in stages
            assert stages[-1] == "done"

        asyncio.run(_test())

    def test_force_refresh_bypasses_cache(self) -> None:
        async def _test() -> None:
            sql = "SELECT [MATNR] FROM [MM_MARC_ECC_EU_R1_MARC_S] WHERE [LVORM] = 'X'"
            backend = InMemoryBackend()
            fake_model = FakeModel()
            settings = OrchestratorSettings()

            res1 = await analyze(
                sql=sql, model=fake_model, cache_backend=backend, settings=settings
            )
            assert res1.meta.cache_status == "miss"

            # Force refresh bypasses cached L1
            res2 = await analyze(
                sql=sql,
                force_refresh=True,
                model=fake_model,
                cache_backend=backend,
                settings=settings,
            )
            assert res2.meta.cache_status in ("miss", "refresh")

        asyncio.run(_test())

    def test_text_input_source_stream(self) -> None:
        async def _test() -> None:
            queries = [
                "SELECT [MATNR] FROM [MM_MARC_ECC_EU_R1_MARC_S]",
                "SELECT [MAKTX] FROM [MM_MAKT_ECC_EU_R1_MAKT_S]",
            ]
            source = TextInputSource(queries)
            results: list[str] = []
            async for req in source:
                results.append(req.sql)

            assert results == queries

        asyncio.run(_test())
