# Requirement Traceability Matrix

This document maps all original project requirements to their corresponding backend/frontend implementation modules and automated test coverage.

All tests operate using test doubles and offline fixtures—**no live network or API keys required**.

---

## 1. Traceability Matrix

| Requirement Area | Detailed Requirement | Code Implementation | Verification / Automated Tests | Status |
| :--- | :--- | :--- | :--- | :--- |
| **1. UI Navbar & Structure** | Sticky top navigation bar, brand logo, Projects breadcrumb, navigation links, Configuration dropdown with LLM status modal, user chip from `/me`. | `frontend/src/components/layout/Navbar.tsx`<br>`frontend/src/features/admin/LlmStatusDialog.tsx` | `frontend/src/test/Navbar.test.tsx`<br>`frontend/src/test/LlmStatusDialog.test.tsx` | **PASS** |
| **2. Home Page Sections** | Gradient header, "My Favorites" persistent strip, "All Projects" grid with responsive cards, "Recent Activity" table, "Trial SQL" interactive workspace. | `frontend/src/features/projects/ProjectsSection.tsx`<br>`frontend/src/features/activity/ActivitySection.tsx`<br>`frontend/src/features/trial/TrialSection.tsx` | `frontend/src/test/HomeSections.test.tsx`<br>`frontend/src/test/TrialSection.test.tsx` | **PASS** |
| **3. Project Cards & Activity** | Project cards with active pills, completion chips, metadata rows, and star toggles; Recent Activity table with status pills, relative timestamps, and pagination. | `frontend/src/features/projects/ProjectCard.tsx`<br>`frontend/src/features/activity/ActivityTable.tsx` | `frontend/src/test/ProjectCard.test.tsx`<br>`frontend/src/test/ActivityTable.test.tsx` | **PASS** |
| **4. Trial SQL Input & Stream** | T-SQL syntax-highlighted editor (@uiw/react-codemirror + mssql), Analyze button with active spinner, SSE live stage progress, 130s timeout, cancel button. | `frontend/src/features/trial/TrialSection.tsx`<br>`frontend/src/features/trial/ProgressStepper.tsx`<br>`frontend/src/api/sse.ts` | `frontend/src/test/TrialSection.test.tsx`<br>`frontend/src/test/sse.test.ts`<br>`backend/tests/test_api_sse_stream.py` | **PASS** |
| **5. SQL AST Parsing** | sqlglot-based deterministic parser, CTE unfolding, subquery extraction, staging table resolution (`object_kind`, `system_id`), column lineage, risk hints. | `backend/app/parser/sql_parser.py`<br>`backend/app/parser/naming.py`<br>`backend/app/parser/risk.py` | `backend/tests/test_deterministic.py`<br>`backend/tests/test_naming.py`<br>`backend/tests/test_risk.py` | **PASS** |
| **6. Concurrent Domain Agents** | Two specialized agents (SQLSemanticsAgent and SAPDomainAgent) executing concurrently in parallel; total latency ~ max(t_sql, t_sap), not sum. | `backend/app/agents/parallel.py`<br>`backend/app/agents/runner.py`<br>`backend/app/agents/sql_agent.py`<br>`backend/app/agents/sap_agent.py` | `backend/tests/test_agents_runner_and_parallel.py`<br>`backend/tests/test_traceability_behaviors.py::TestTraceabilityBehaviors::test_both_agents_run_concurrently` | **PASS** |
| **7. SQL Knowledge Base** | Prebuilt SQL keywords, constructs, and anti-patterns with verified status flags and overrides. | `knowledge/sql_keywords.json`<br>`knowledge/overrides/verified_entries.json`<br>`backend/app/knowledge/store.py` | `backend/tests/test_knowledge_store.py`<br>`backend/tests/test_knowledge_sql_ingest.py` | **PASS** |
| **8. SAP Knowledge & FAISS** | Prebuilt SAP tables/fields JSON, FAISS vector index, semantic catalog retriever, verified status, grounded write-back to `/knowledge/learned/`. | `knowledge/sap_tables_prebuilt.json`<br>`knowledge/sap_common_fields.json`<br>`backend/app/knowledge/vector_index.py`<br>`backend/app/agents/tools.py` | `backend/tests/test_knowledge_retrieval.py`<br>`backend/tests/test_agents_tools.py`<br>`backend/tests/test_traceability_behaviors.py::TestTraceabilityBehaviors::test_sap_retrieval_and_learned_knowledge_persistence` | **PASS** |
| **9. Synthesizer Output Schema** | Complete BusinessIntelligence model containing business_meaning, technical_summary, affected_entities, sap_module, query_type, risk_summary, final_learning, and governance fields (rule, specification, opportunity_query, error_query, driving_table, etc.). | `backend/app/synthesizer/models.py`<br>`backend/app/synthesizer/synthesizer.py` | `backend/tests/test_synthesizer.py`<br>`backend/tests/test_orchestrator.py` | **PASS** |
| **10. Multi-Tier Cache Coordination** | L1 Final Answer cache, L2 Agent Output cache, L3 Semantic Similarity cache. Repeat query makes ZERO runner calls; similar query reuses cached agent output. | `backend/app/cache/layers.py`<br>`backend/app/cache/keys.py`<br>`backend/app/cache/similarity.py`<br>`backend/app/cache/singleflight.py` | `backend/tests/test_cache_layers.py`<br>`backend/tests/test_cache_similarity.py`<br>`backend/tests/test_singleflight.py`<br>`backend/tests/test_traceability_behaviors.py::test_identical_repeat_makes_zero_runner_calls`<br>`backend/tests/test_traceability_behaviors.py::test_similar_query_reuses_cached_agent_output` | **PASS** |
| **11. Model Switch Readiness** | OpenAI Agents SDK with `gpt-4o-mini`, switchable to `astra-luna` or custom OpenAI-compatible endpoints with zero code edits. Cache isolation by model + base_url hash. | `backend/app/agents/settings.py`<br>`backend/app/agents/model_factory.py`<br>`backend/app/cache/keys.py`<br>`backend/app/agents/cli.py` (`--check-model`) | `backend/tests/test_cache_layers.py::test_cache_keys_model_and_base_url_isolation`<br>`backend/tests/test_traceability_behaviors.py::test_env_only_model_switch_changes_configuration` | **PASS** |
| **12. Guardrails & Safety** | Comprehensive input tripwires, canary token tracking, output AST validation, SQL safety (comments, DDL, table whitelist), subset rule enforcement, budget & circuit breaker. | `backend/app/guardrails/pipeline.py`<br>`backend/app/guardrails/agent_output.py`<br>`backend/app/guardrails/final_output.py`<br>`backend/app/guardrails/sql_safety.py`<br>`backend/app/guardrails/budget.py`<br>`backend/app/guardrails/circuit_breaker.py` | `backend/tests/test_guardrails_input.py`<br>`backend/tests/test_guardrails_agent_output.py`<br>`backend/tests/test_guardrails_final_output.py`<br>`backend/tests/test_guardrails_budget_breaker.py`<br>`backend/tests/test_guardrails_fuzz.py`<br>`backend/tests/test_guardrails_redteam.py`<br>`backend/tests/test_traceability_behaviors.py::test_error_query_is_always_subset_of_opportunity_query` | **PASS** |
| **13. Pydantic Everywhere** | Strict schemas, BaseModel everywhere, typed envelopes, contract enforcement, zero untyped dict returns in API. | `backend/app/api/models.py`<br>`backend/app/synthesizer/models.py`<br>`backend/app/agents/models.py`<br>`backend/app/guardrails/models.py` | `backend/tests/test_agents_settings_and_models.py`<br>`backend/tests/test_api_lifespan_and_openapi.py`<br>`backend/tests/test_architecture.py` | **PASS** |
| **14. No Offline / Demo Mode** | Zero mock or canned analysis payloads shipped in production code; live requests strictly require configured LLM; fails fast when not configured. | `backend/app/orchestrator/orchestrator.py`<br>`backend/app/agents/runner.py`<br>`docs/PROJECT_RULES.md` | `backend/tests/test_orchestrator.py::test_missing_api_key_raises_llm_not_configured`<br>`backend/tests/test_architecture.py` | **PASS** |

---

## 2. Dedicated Behavioral Guarantees Verification

The 6 critical behaviors mandated in Phase 7A are tested in `backend/tests/test_traceability_behaviors.py`:

```
============================= test session starts =============================
collected 6 items

backend\tests\test_traceability_behaviors.py ......                      [100%]

============================= 6 passed in 15.15s ==============================
```

1. **Both agents run concurrently**: `test_both_agents_run_concurrently` validates that two agents with 0.4s sleep complete in 0.43s (< 0.65s), proving `asyncio.gather` concurrent execution.
2. **Identical repeat makes zero runner calls**: `test_identical_repeat_makes_zero_runner_calls` spies on `run_agent` and asserts 0 runner calls on repeat L1 cache hits.
3. **Similar query reuses cached agent output**: `test_similar_query_reuses_cached_agent_output` confirms that modifying only literal values results in an L2 SAP hit and L3 semantic similarity reuse.
4. **SAP retrieval tool & learned persistence**: `test_sap_retrieval_and_learned_knowledge_persistence` tests grounding, write-back to `/knowledge/learned/`, and reload by subsequent store instances.
5. **Error query is always subset of opportunity query**: `test_error_query_is_always_subset_of_opportunity_query` validates AST table, join, and predicate subset preservation.
6. **Zero-code model switch**: `test_env_only_model_switch_changes_configuration` proves that configuring `LLM_MODEL`, `OPENAI_BASE_URL`, and `LLM_API_MODE` changes active components and isolates cache namespaces without code modifications.

