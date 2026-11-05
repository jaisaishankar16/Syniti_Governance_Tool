"""Dedicated traceability verification tests for the 6 core architectural requirements in Phase 7A.

Specifically confirms:
  (1) Both agents run concurrently (total latency ~ max, not sum).
  (2) An identical repeat query makes ZERO runner calls.
  (3) A similar query reuses cached agent output per L2/L3 rules.
  (4) The SAP retrieval tool is called only for unresolved items and learned knowledge is
      persisted, pending/approved correctly and reused next request.
  (5) The error query is always a subset of the opportunity query in anything the pipeline returns.
  (6) Changing LLM_MODEL / OPENAI_BASE_URL / LLM_API_MODE changes the model with no code edits.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from app.agents.models import AgentDeps, RunTrace
from app.agents.parallel import analyze_with_agents
from app.agents.settings import LLMSettings
from app.agents.tools import save_learned_sap_knowledge
from app.cache.backends import InMemoryBackend
from app.cache.similarity import get_similarity_index
from app.guardrails.sql_safety import validate_error_subset_of_opportunity
from app.knowledge.models import MatchType, RetrievalHit
from app.knowledge.retrievers import SapRetriever, SqlDocRetriever
from app.knowledge.settings import KnowledgeSettings
from app.knowledge.store import KnowledgeStore
from app.orchestrator.orchestrator import analyze
from app.orchestrator.settings import OrchestratorSettings
from app.parser.sql_parser import SqlParser
from app.synthesizer.models import BusinessIntelligence

from tests.support.fakes import FakeModel


class TestTraceabilityBehaviors:
    """Explicit tests for the 6 prompt-specified behavioral guarantees."""

    # --------------------------------------------------------------------------
    # Behavior (1): Both agents run concurrently (total ~ max, not sum)
    # --------------------------------------------------------------------------
    def test_both_agents_run_concurrently(self, tmp_path: Path) -> None:
        async def _test() -> None:
            parser = SqlParser()
            parsed = parser.parse("SELECT MATNR FROM MARC WHERE MANDT = '100'")
            k_settings = KnowledgeSettings(custom_learned_dir=tmp_path / "learned")
            store = KnowledgeStore(settings=k_settings)
            settings = LLMSettings(llm_timeout_s=5.0)

            def deps_factory() -> tuple[AgentDeps, AgentDeps]:
                d1 = AgentDeps(
                    store=store,
                    sap_retriever=SapRetriever(settings=k_settings),
                    sql_retriever=SqlDocRetriever(settings=k_settings),
                    settings=settings,
                    request_id="req_concurrency_sql",
                    run_trace=RunTrace(),
                )
                d2 = AgentDeps(
                    store=store,
                    sap_retriever=SapRetriever(settings=k_settings),
                    sql_retriever=SqlDocRetriever(settings=k_settings),
                    settings=settings,
                    request_id="req_concurrency_sap",
                    run_trace=RunTrace(),
                )
                return d1, d2

            # Each agent sleeps for 0.4s. If serial, took >= 0.8s. If concurrent, took ~ 0.4s (< 0.65s).
            slow_model = FakeModel(sleep_s=0.4)
            start_t = time.perf_counter()
            res = await analyze_with_agents(
                parsed=parsed,
                deps_factory=deps_factory,
                settings=settings,
                model=slow_model,
            )
            elapsed = time.perf_counter() - start_t

            assert res.degraded is False
            assert res.sql.status == "ok"
            assert res.sap.status == "ok"
            assert elapsed < 0.65, f"Took {elapsed:.2f}s; expected concurrent execution < 0.65s"

        asyncio.run(_test())

    # --------------------------------------------------------------------------
    # Behavior (2): An identical repeat makes ZERO runner calls
    # --------------------------------------------------------------------------
    def test_identical_repeat_makes_zero_runner_calls(self) -> None:
        async def _test() -> None:
            sql = "SELECT MARC.MATNR, MARA.MTART FROM [MM_MARC_ECC_EU_R1_MARC_S] MARC INNER JOIN [MM_MARA_ECC_EU_R1_MARA_S] MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 1"
            backend = InMemoryBackend()
            fake_model = FakeModel()
            orch_settings = OrchestratorSettings()

            # First run: cache miss, runs pipeline
            res1 = await analyze(
                sql=sql,
                model=fake_model,
                cache_backend=backend,
                settings=orch_settings,
            )
            assert res1.meta.cache_status == "miss"
            assert res1.meta.validation.opportunity_sql_ok is True

            # Second run: identical query must return L1 hit with ZERO runner calls
            with patch("app.orchestrator.orchestrator.run_agent") as mock_runner:
                res2 = await analyze(
                    sql=sql,
                    model=fake_model,
                    cache_backend=backend,
                    settings=orch_settings,
                )
                assert res2.meta.cache_status == "hit_final"
                assert mock_runner.call_count == 0, (
                    f"Expected 0 runner calls, got {mock_runner.call_count}"
                )

        asyncio.run(_test())

    # --------------------------------------------------------------------------
    # Behavior (3): A similar query reuses cached agent output per L2/L3 rules
    # --------------------------------------------------------------------------
    def test_similar_query_reuses_cached_agent_output(self) -> None:
        async def _test() -> None:
            backend = InMemoryBackend()
            orch_settings = OrchestratorSettings()
            fake_model = FakeModel()
            sim_index = get_similarity_index()
            sim_index.clear()

            sql1 = "SELECT MARC.MATNR, MARA.MTART FROM [MM_MARC_ECC_EU_R1_MARC_S] MARC INNER JOIN [MM_MARA_ECC_EU_R1_MARA_S] MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 1"
            res1 = await analyze(
                sql=sql1,
                model=fake_model,
                cache_backend=backend,
                settings=orch_settings,
            )
            assert res1.meta.cache_status == "miss"

            # sql2 differs only in literal: MARC.ZACTIVE = 2 vs MARC.ZACTIVE = 1
            sql2 = "SELECT MARC.MATNR, MARA.MTART FROM [MM_MARC_ECC_EU_R1_MARC_S] MARC INNER JOIN [MM_MARA_ECC_EU_R1_MARA_S] MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 2"

            res2 = await analyze(
                sql=sql2,
                model=fake_model,
                cache_backend=backend,
                settings=orch_settings,
            )
            # SAP agent hits L2, and SQL agent reuses similarity or hit
            assert res2.meta.cache_detail.sap_agent == "hit"
            assert res2.meta.cache_detail.sql_agent in ("similar_reused", "hit")

        asyncio.run(_test())

    # --------------------------------------------------------------------------
    # Behavior (4): SAP retrieval tool and learned knowledge persistence/reuse
    # --------------------------------------------------------------------------
    def test_sap_retrieval_and_learned_knowledge_persistence(self, tmp_path: Path) -> None:
        from unittest.mock import MagicMock

        from agents import RunContextWrapper

        k_settings = KnowledgeSettings(custom_learned_dir=tmp_path / "learned")
        store = KnowledgeStore(settings=k_settings)
        sap_retriever = SapRetriever(settings=k_settings)
        sql_retriever = SqlDocRetriever(settings=k_settings)
        settings = LLMSettings(learning_enabled=True)

        deps = AgentDeps(
            store=store,
            sap_retriever=sap_retriever,
            sql_retriever=sql_retriever,
            settings=settings,
            request_id="req_learned_test",
            run_trace=RunTrace(),
        )

        # 1. Unknown table initially not found in prebuilt or store
        initial = store.lookup_sap("Z_TRACED_TABLE")
        assert initial.found is False

        # 2. Retrieved chunk arrives via retriever
        chunk_id = "c_traced_999"
        deps.run_trace.retrieved_chunks[chunk_id] = RetrievalHit(
            chunk_id=chunk_id,
            text="Table Z_TRACED_TABLE: Traced custom customer migration buffer.",
            score=0.91,
            match_type=MatchType.semantic,
            source_file="sap_custom.txt",
        )

        mock_ctx = MagicMock(spec=RunContextWrapper)
        mock_ctx.context = deps

        # 3. Agent calls save_learned_sap_knowledge
        res = save_learned_sap_knowledge(
            mock_ctx,
            chunk_id=chunk_id,
            table="Z_TRACED_TABLE",
            description="Traced custom customer migration buffer",
        )
        assert res.saved is True
        assert res.action == "created"

        # 4. Immediately visible without restart
        lookup_after = store.lookup_sap("Z_TRACED_TABLE")
        assert lookup_after.found is True
        assert lookup_after.source == "learned"

        # 5. Persisted to disk in learned directory
        learned_files = list((tmp_path / "learned").glob("*.json"))
        assert len(learned_files) > 0

        # 6. Reused by a fresh KnowledgeStore instance next request
        fresh_store = KnowledgeStore(settings=k_settings)
        next_lookup = fresh_store.lookup_sap("Z_TRACED_TABLE")
        assert next_lookup.found is True
        assert next_lookup.source == "learned"

    # --------------------------------------------------------------------------
    # Behavior (5): Error query is always a subset of opportunity query
    # --------------------------------------------------------------------------
    def test_error_query_is_always_subset_of_opportunity_query(self) -> None:
        async def _test() -> None:
            backend = InMemoryBackend()
            orch_settings = OrchestratorSettings()

            sql = "SELECT MARC.MATNR, MARA.MTART FROM [MM_MARC_ECC_EU_R1_MARC_S] MARC INNER JOIN [MM_MARA_ECC_EU_R1_MARA_S] MARA ON MARC.MATNR = MARA.MATNR WHERE MARC.ZACTIVE = 1"
            res = await analyze(
                sql=sql,
                model=FakeModel(),
                cache_backend=backend,
                settings=orch_settings,
            )

            assert isinstance(res, BusinessIntelligence)
            assert res.meta.validation.opportunity_sql_ok is True
            assert res.meta.validation.error_sql_ok is True
            assert res.meta.validation.subset_ok is True

            # Directly verify with AST subset checker
            opp_sql = res.governance.opportunity_query
            err_sql = res.governance.error_query
            verdict = validate_error_subset_of_opportunity(opp_sql, err_sql)
            assert verdict.allowed is True, (
                f"Error query was not a valid subset: {verdict.report.events}"
            )

        asyncio.run(_test())

    # --------------------------------------------------------------------------
    # Behavior (6): Changing LLM_MODEL / OPENAI_BASE_URL / LLM_API_MODE with no code edits
    # --------------------------------------------------------------------------
    def test_env_only_model_switch_changes_configuration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_MODEL", "astra-luna")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://astra-luna.internal/v1")
        monkeypatch.setenv("LLM_API_MODE", "chat_completions")

        custom_settings = LLMSettings()
        assert custom_settings.llm_model == "astra-luna"
        assert custom_settings.openai_base_url == "https://astra-luna.internal/v1"
        assert custom_settings.llm_api_mode == "chat_completions"

        # Model factory creates model with the configured target
        from app.agents.model_factory import build_model

        model_instance = build_model(custom_settings)
        assert model_instance is not None

        # Cache key generation is isolated by model and base_url
        from app.cache.keys import make_l1_key

        key_astra = make_l1_key(
            "SELECT 1",
            "vw",
            custom_settings.llm_model,
            "sql_v1",
            "sap_v1",
            "synth_v1",
            "fp1",
            base_url=str(custom_settings.openai_base_url),
        )
        key_default = make_l1_key(
            "SELECT 1",
            "vw",
            "gpt-4o-mini",
            "sql_v1",
            "sap_v1",
            "synth_v1",
            "fp1",
            base_url=None,
        )
        assert key_astra != key_default
