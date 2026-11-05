"""Unit tests for multi-tier cache coordination (L1, L2 SQL, L2 SAP) and cacheability rules."""

from __future__ import annotations

import asyncio

from app.cache.backends import InMemoryBackend
from app.cache.keys import (
    compute_knowledge_fingerprint,
    make_l2_sap_key,
)
from app.cache.layers import (
    is_cacheable,
    lookup_l1,
    lookup_l2_sap,
    write_l1,
    write_l2_sap,
)
from app.guardrails.models import GuardrailReport
from app.knowledge.store import KnowledgeStore
from app.synthesizer.models import (
    BusinessIntelligence,
    BusinessIntelligenceMeta,
    CacheDetail,
    PromptVersions,
    TokenUsageBreakdown,
    ValidationSummary,
)

from tests.support.fakes import CANNED_SAP_ANALYSIS, CANNED_SYNTHESIZER_OUTPUT


def _build_valid_bi() -> BusinessIntelligence:
    meta = BusinessIntelligenceMeta(
        request_id="req_test_123",
        cache_status="miss",
        cache_detail=CacheDetail(),
        model="gpt-4o-mini",
        prompt_versions=PromptVersions(sql="sql_v1", sap="sap_v1", synthesizer="synth_v1"),
        agent_timings_ms={"sql_agent": 10.0, "sap_agent": 15.0, "synthesizer": 20.0},
        stage_timings_ms={"total": 45.0},
        token_usage=TokenUsageBreakdown(total=100),
        degraded=False,
        degraded_reasons=[],
        warnings=[],
        guardrails=GuardrailReport(blocked=False),
        validation=ValidationSummary(
            opportunity_sql_ok=True,
            error_sql_ok=True,
            subset_ok=True,
            glossary_ok=True,
            matches_parser_baseline=True,
        ),
        ai_generated=True,
    )

    canned = CANNED_SYNTHESIZER_OUTPUT
    return BusinessIntelligence(
        business_meaning=canned.business_meaning,
        technical_summary=canned.technical_summary,
        affected_entities=canned.affected_entities,
        sap_module=canned.sap_module,
        query_type=canned.query_type,
        risk_summary=canned.risk_summary,
        final_learning=canned.final_learning,
        governance=canned.governance,
        meta=meta,
    )


class TestCacheLayers:
    """Tests L1, L2, integrity verification, and cacheability policy."""

    def test_is_cacheable_rules(self) -> None:
        bi = _build_valid_bi()
        assert is_cacheable(bi) is True

        # Degraded cannot be cached
        bi_deg = bi.model_copy(deep=True)
        bi_deg.meta.degraded = True
        assert is_cacheable(bi_deg) is False

        # Non-AI cannot be cached
        bi_non_ai = bi.model_copy(deep=True)
        bi_non_ai.meta.ai_generated = False
        assert is_cacheable(bi_non_ai) is False

        # Invalid opportunity SQL cannot be cached
        bi_bad_sql = bi.model_copy(deep=True)
        bi_bad_sql.meta.validation.opportunity_sql_ok = False
        assert is_cacheable(bi_bad_sql) is False

        # Blocked guardrails cannot be cached
        bi_blocked = bi.model_copy(deep=True)
        bi_blocked.meta.guardrails.blocked = True
        assert is_cacheable(bi_blocked) is False

    def test_l1_write_and_lookup(self) -> None:
        async def _test() -> None:
            backend = InMemoryBackend()
            bi = _build_valid_bi()
            key = "syniti:v1:l1:test_key"

            # Write
            assert await write_l1(key, bi, backend) is True

            # Lookup
            cached_bi = await lookup_l1(key, backend)
            assert cached_bi is not None
            assert cached_bi.meta.cache_status == "hit_final"
            assert cached_bi.governance.rule == bi.governance.rule

        asyncio.run(_test())

    def test_l1_integrity_corruption_treated_as_miss(self) -> None:
        async def _test() -> None:
            backend = InMemoryBackend()
            bi = _build_valid_bi()
            key = "syniti:v1:l1:corrupt_test"

            await write_l1(key, bi, backend)

            # Corrupt the raw payload JSON in the storage backend
            raw_json = await backend.get(key)
            assert raw_json is not None
            corrupted_json = raw_json.replace("MM_MARC_ECC_EU_R1_MARC_S", "HACKED_TABLE")
            await backend.set(key, corrupted_json)

            # Lookup must fail integrity check and return None
            lookup_result = await lookup_l1(key, backend)
            assert lookup_result is None

        asyncio.run(_test())

    def test_l2_sap_lookup_and_write(self) -> None:
        async def _test() -> None:
            backend = InMemoryBackend()
            sap_analysis = CANNED_SAP_ANALYSIS
            key = make_l2_sap_key(
                tables=["MARC", "MARA"],
                fields=["MATNR", "WERKS"],
                naming_facts=["MM_MARC_ECC_EU_R1_MARC_S"],
                sap_prompt_version="sap_v1",
                model="gpt-4o-mini",
                knowledge_fingerprint="fp123",
            )

            assert (
                await write_l2_sap(
                    key,
                    sap_analysis,
                    backend,
                    model="gpt-4o-mini",
                    prompt_version="sap_v1",
                )
                is True
            )
            retrieved = await lookup_l2_sap(key, backend)
            assert retrieved is not None
            assert retrieved.sap_module == "MM"

        asyncio.run(_test())

    def test_knowledge_fingerprint_excludes_learned(self) -> None:
        store = KnowledgeStore()
        fp1 = compute_knowledge_fingerprint(store)
        assert len(fp1) == 16
        # Recomputing gives deterministic identical fingerprint
        fp2 = compute_knowledge_fingerprint(store)
        assert fp1 == fp2

    def test_cache_keys_model_and_base_url_isolation(self) -> None:
        from app.cache.keys import make_l1_key, make_l2_sql_key

        # L1 keys differ by model
        k1 = make_l1_key(
            "SELECT 1", "vw_test", "gpt-4o-mini", "sql_v1", "sap_v1", "synth_v1", "fp1"
        )
        k2 = make_l1_key("SELECT 1", "vw_test", "astra-luna", "sql_v1", "sap_v1", "synth_v1", "fp1")
        assert k1 != k2

        # L1 keys differ by base_url
        k3 = make_l1_key(
            "SELECT 1",
            "vw_test",
            "gpt-4o-mini",
            "sql_v1",
            "sap_v1",
            "synth_v1",
            "fp1",
            base_url="https://api.openai.com/v1",
        )
        k4 = make_l1_key(
            "SELECT 1",
            "vw_test",
            "gpt-4o-mini",
            "sql_v1",
            "sap_v1",
            "synth_v1",
            "fp1",
            base_url="https://local-runner.internal/v1",
        )
        assert k1 != k3
        assert k3 != k4

        # L2 SQL keys differ by model and base_url
        sql_k1 = make_l2_sql_key("SELECT 1", "sql_v1", "gpt-4o-mini")
        sql_k2 = make_l2_sql_key("SELECT 1", "sql_v1", "astra-luna")
        sql_k3 = make_l2_sql_key(
            "SELECT 1", "sql_v1", "gpt-4o-mini", base_url="https://custom.ai/v1"
        )
        assert sql_k1 != sql_k2
        assert sql_k1 != sql_k3

        # L2 SAP keys differ by model and base_url
        sap_k1 = make_l2_sap_key(["MARA"], ["MATNR"], [], "sap_v1", "gpt-4o-mini", "fp1")
        sap_k2 = make_l2_sap_key(["MARA"], ["MATNR"], [], "sap_v1", "astra-luna", "fp1")
        sap_k3 = make_l2_sap_key(
            ["MARA"], ["MATNR"], [], "sap_v1", "gpt-4o-mini", "fp1", base_url="https://custom.ai/v1"
        )
        assert sap_k1 != sap_k2
        assert sap_k1 != sap_k3

    def test_model_switch_never_serves_cached_answers(self) -> None:
        async def _test() -> None:
            from app.cache.keys import make_l1_key

            backend = InMemoryBackend()
            bi = _build_valid_bi()

            key_gpt = make_l1_key(
                "SELECT 1", "vw_1", "gpt-4o-mini", "sql_v1", "sap_v1", "synth_v1", "fp1"
            )
            key_astra = make_l1_key(
                "SELECT 1", "vw_1", "astra-luna", "sql_v1", "sap_v1", "synth_v1", "fp1"
            )

            # Write under gpt-4o-mini
            await write_l1(key_gpt, bi, backend)

            # Query under gpt-4o-mini hits
            hit = await lookup_l1(key_gpt, backend)
            assert hit is not None

            # Query under astra-luna misses
            miss = await lookup_l1(key_astra, backend)
            assert miss is None

        asyncio.run(_test())
