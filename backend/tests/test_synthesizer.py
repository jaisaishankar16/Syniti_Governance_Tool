"""Unit tests for the Phase 4 Synthesizer Agent, validation, fewshot selection, and retry fallback."""

from __future__ import annotations

import asyncio
import json

import pytest
from app.agents.models import AgentDeps
from app.agents.settings import LLMSettings
from app.errors import SynthesisFailedError
from app.knowledge.retrievers import SapRetriever, SqlDocRetriever
from app.knowledge.store import KnowledgeStore
from app.parser.sql_parser import SqlParser
from app.synthesizer.fewshot import (
    ALLOWED_FEWSHOT_SHEETS,
    format_fewshot_for_prompt,
    load_allowed_fewshot_examples,
)
from app.synthesizer.models import (
    BusinessIntelligence,
    CacheDetail,
    PromptVersions,
    SynthesizerRawOutput,
)
from app.synthesizer.synthesizer import (
    synthesize,
)
from pydantic import ValidationError

from tests.support.fakes import CANNED_SYNTHESIZER_OUTPUT, FakeModel


class TestFewShotIsolation:
    """Verifies strict isolation of few-shot examples (only Sheet1, Sheet5, Sheet7, Sheet8)."""

    def test_only_allowed_sheets_loaded(self) -> None:
        examples = load_allowed_fewshot_examples()
        assert len(examples) == 4
        sheet_ids = {ex.sheet_id.lower() for ex in examples}
        assert sheet_ids == ALLOWED_FEWSHOT_SHEETS

        # Held-out sheets must never be in the few-shot examples
        forbidden = {"sheet2", "sheet3", "sheet4", "sheet6", "sheet9", "sheet10", "sheet11"}
        for f in forbidden:
            assert f not in sheet_ids

    def test_format_fewshot_for_prompt(self) -> None:
        text = format_fewshot_for_prompt()
        assert "Sheet1" in text
        assert "Sheet5" in text
        assert "Sheet7" in text
        assert "Sheet8" in text
        assert "Sheet2" not in text
        assert "Sheet3" not in text


class TestSynthesizerModels:
    """Tests schema validation and extra='forbid' constraint on Synthesizer models."""

    def test_synthesizer_raw_output_extra_forbidden(self) -> None:
        valid_data = {
            "business_meaning": "Meaningful business description",
            "technical_summary": "Technical join summary",
            "affected_entities": ["MARC"],
            "sap_module": "MM",
            "query_type": "Data Quality Filter",
            "risk_summary": {
                "level": "low",
                "items": ["No critical risk"],
                "recommendations": ["Document query"],
            },
            "final_learning": None,
            "governance": {
                "subject_area": "Supply Chain",
                "business_process": "Inventory Management",
                "dataset": "Material Master",
                "business_concept": {
                    "term": "Active Plant Status",
                    "field": "LVORM",
                    "description": "Plant deletion flag",
                },
                "rule": "Flag deleted plant items",
                "specification": "Filter where LVORM is X",
                "enforcement": "Mandatory",
                "implementation": "SQL View",
                "opportunity_definition": "All plant materials",
                "error_definition": "Deleted plant materials",
                "opportunity_query": "SELECT [MATNR] FROM [MM_MARC_ECC_EU_R1_MARC_S]",
                "error_query": "SELECT [MATNR] FROM [MM_MARC_ECC_EU_R1_MARC_S] WHERE [LVORM] = 'X'",
            },
        }
        raw = SynthesizerRawOutput.model_validate(valid_data)
        assert raw.sap_module == "MM"

        # Extra fields should raise ValidationError
        with pytest.raises(ValidationError):
            SynthesizerRawOutput.model_validate({**valid_data, "unauthorized_extra": 123})


class TestSynthesizerExecution:
    """Tests synthesizer execution with fake model, retry on invalid SQL, and risk floor reconciliation."""

    def test_synthesize_success_with_fake_model(self) -> None:
        async def _test() -> None:
            parser = SqlParser()
            sql = "SELECT MARC.MATNR, MARA.MTART FROM [MM_MARC_ECC_EU_R1_MARC_S] MARC INNER JOIN [MM_MARA_ECC_EU_R1_MARA_S] MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 1"
            parsed = parser.parse(sql)

            store = KnowledgeStore()
            sap_ret = SapRetriever()
            sql_ret = SqlDocRetriever()
            settings = LLMSettings()

            deps = AgentDeps(
                store=store,
                sap_retriever=sap_ret,
                sql_retriever=sql_ret,
                settings=settings,
                request_id="req-test-synth",
            )

            fake_model = FakeModel()
            cache_detail = CacheDetail(sql_agent="miss", sap_agent="miss")
            prompt_versions = PromptVersions(sql="v1", sap="v1", synthesizer="v1")

            bi, _ = await synthesize(
                parsed=parsed,
                sql_analysis=None,
                sap_analysis=None,
                deps=deps,
                model=fake_model,
                settings=settings,
                request_id="req-test-synth",
                canary_token="canary-12345",
                boundary_id="b-12345",
                prompt_versions=prompt_versions,
                cache_detail=cache_detail,
            )

            assert isinstance(bi, BusinessIntelligence)
            assert bi.meta.request_id == "req-test-synth"
            assert bi.meta.validation.opportunity_sql_ok is True
            assert bi.meta.validation.error_sql_ok is True
            assert bi.meta.validation.subset_ok is True
            assert bi.meta.ai_generated is True

        asyncio.run(_test())

    def test_synthesize_falls_back_on_unsafe_sql(self) -> None:
        async def _test() -> None:
            parser = SqlParser()
            sql = "SELECT [MATNR] FROM [MM_MARC_ECC_EU_R1_MARC_S]"
            parsed = parser.parse(sql)

            store = KnowledgeStore()
            sap_ret = SapRetriever()
            sql_ret = SqlDocRetriever()
            settings = LLMSettings()

            deps = AgentDeps(
                store=store,
                sap_retriever=sap_ret,
                sql_retriever=sql_ret,
                settings=settings,
                request_id="req-fallback",
            )

            bad_output = CANNED_SYNTHESIZER_OUTPUT.model_dump()
            bad_output["governance"]["opportunity_query"] = "DROP TABLE [MM_MARC_ECC_EU_R1_MARC_S]"

            fake_model = FakeModel(canned_response=json.dumps(bad_output))

            cache_detail = CacheDetail(sql_agent="miss", sap_agent="miss")
            prompt_versions = PromptVersions(sql="v1", sap="v1", synthesizer="v1")

            bi, _ = await synthesize(
                parsed=parsed,
                sql_analysis=None,
                sap_analysis=None,
                deps=deps,
                model=fake_model,
                settings=settings,
                request_id="req-fallback",
                canary_token="canary-12345",
                boundary_id="b-12345",
                prompt_versions=prompt_versions,
                cache_detail=cache_detail,
            )

            # Under Project Rules (No Offline Mode), unsafe opportunity queries are cleared with warning (no baseline substitution)
            assert bi.meta.validation.opportunity_sql_ok is False
            assert bi.governance.opportunity_query == ""
            assert bi.meta.degraded is True
            assert bi.meta.validation.matches_parser_baseline is False
            assert any("cleared" in w for w in bi.meta.warnings)

        asyncio.run(_test())

    def test_synthesizer_failure_raises_synthesis_failed_error(self) -> None:
        async def _test() -> None:
            parser = SqlParser()
            parsed = parser.parse("SELECT [MATNR] FROM [MM_MARC_ECC_EU_R1_MARC_S]")
            deps = AgentDeps(
                store=KnowledgeStore(),
                sap_retriever=SapRetriever(),
                sql_retriever=SqlDocRetriever(),
                settings=LLMSettings(),
                request_id="req-failure",
            )
            failing_model = FakeModel(error_to_raise=RuntimeError("LLM crashed"))
            with pytest.raises(SynthesisFailedError):
                await synthesize(
                    parsed=parsed,
                    sql_analysis=None,
                    sap_analysis=None,
                    deps=deps,
                    model=failing_model,
                    settings=LLMSettings(),
                    request_id="req-failure",
                    canary_token="canary-123",
                    boundary_id="b-123",
                    prompt_versions=PromptVersions(sql="v1", sap="v1", synthesizer="v1"),
                    cache_detail=CacheDetail(),
                )

        asyncio.run(_test())

    def test_risk_floor_elevation_enforced(self) -> None:
        async def _test() -> None:
            parser = SqlParser()
            sql = "SELECT * FROM [MM_MARC_ECC_EU_R1_MARC_S]"
            parsed = parser.parse(sql)

            store = KnowledgeStore()
            deps = AgentDeps(
                store=store,
                sap_retriever=SapRetriever(),
                sql_retriever=SqlDocRetriever(),
                settings=LLMSettings(),
                request_id="req-risk-floor",
            )

            fake_model = FakeModel()
            cache_detail = CacheDetail(sql_agent="miss", sap_agent="miss")
            prompt_versions = PromptVersions(sql="v1", sap="v1", synthesizer="v1")

            bi, _ = await synthesize(
                parsed=parsed,
                sql_analysis=None,
                sap_analysis=None,
                deps=deps,
                model=fake_model,
                settings=LLMSettings(),
                request_id="req-risk-floor",
                canary_token="canary-12345",
                boundary_id="b-12345",
                prompt_versions=prompt_versions,
                cache_detail=cache_detail,
            )

            severities = ["low", "medium", "high", "critical"]
            assert severities.index(bi.risk_summary.level) >= severities.index("medium")

        asyncio.run(_test())
