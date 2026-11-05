# Audit of Earlier Fix-Ups (Phase 7A Initial Baseline)

This document records the initial verification audit performed before applying any Phase 7A fix-ups, as required by Section 1 of Phase 7A.

---

## 1. Audit Summary Table

| Requirement / Fix-Up Area | Scope & Key Files | Evidence (File + Test Name) | Status | Findings / Gaps Identified |
|---|---|---|:---:|---|
| **Phase 2.1: Prebuilt Verification & Overrides** | • `knowledge/sap_tables_prebuilt.json`<br>• `knowledge/sap_common_fields.json`<br>• `knowledge/sql_keywords.json`<br>• `knowledge/overrides/verified_entries.json`<br>• `knowledge/REVIEW.md`<br>• `backend/app/knowledge/store.py`<br>• `backend/app/knowledge/models.py`<br>• `backend/app/agents/packs.py`<br>• `backend/app/agents/prompts.py` | `backend/tests/test_knowledge_store.py::test_grounded_save_and_immediate_lookup`<br>`backend/tests/test_knowledge_store.py::test_prebuilt_catalog_immutable_and_cannot_overwrite` | **FAIL (Incomplete)** | 1. `/knowledge/overrides/verified_entries.json` does not exist.<br>2. Entries in `sap_tables_prebuilt.json` and `sql_keywords.json` are marked `verified: true` by default instead of `verified: false` unless listed in `verified_entries.json`.<br>3. `knowledge/REVIEW.md` states zero pending reviews instead of listing unverified tables, common fields, and keywords with counts.<br>4. `SapLookupResult` in `models.py` and store lookups in `store.py` do not expose a `verified` boolean field.<br>5. Knowledge packs (`packs.py`) and agent prompts (`prompts.py`) do not annotate unverified entries with lower confidence tags. |
| **Phase 3B: Safety Guardrails Comprehensive Coverage** | • `backend/app/guardrails/sql_safety.py`<br>• `backend/app/guardrails/injection.py`<br>• `backend/app/guardrails/redaction.py`<br>• `backend/app/guardrails/input_checks.py`<br>• `backend/app/guardrails/tool_policy.py`<br>• `backend/app/guardrails/agent_output.py`<br>• `backend/app/guardrails/final_output.py`<br>• `backend/app/guardrails/budget.py`<br>• `backend/app/guardrails/circuit_breaker.py`<br>• `backend/app/guardrails/logging_filter.py`<br>• `backend/app/guardrails/pipeline.py` | `backend/tests/test_guardrails_input.py` (11 tests)<br>`backend/tests/test_guardrails_redteam.py` (16 tests)<br>`backend/tests/test_guardrails_agent_output.py` (3 tests)<br>`backend/tests/test_guardrails_final_output.py` (11 tests)<br>`backend/tests/test_guardrails_budget_breaker.py` (6 tests)<br>`backend/tests/test_guardrails_fuzz.py` (1 test) | **PASS** | Complete implementation. Every item in Phase 3B specification (SQL read-only enforcement, DDL/DML rejection, comment quarantine, injection detection, PII/secret scrubbing, tool call rate limiting, hallucination checking, subset rule validation, token budget, circuit breaker) has a dedicated module and passing unit tests. |
| **Phase 5.1: Root-Cause Classification & Fail-Fast** | • `backend/app/agents/runner.py`<br>• `backend/app/agents/failure_cache.py`<br>• `backend/app/api/routes_analyze.py`<br>• `backend/app/api/routes_meta.py`<br>• `backend/app/orchestrator/orchestrator.py` | `backend/tests/test_api_root_cause_errors.py` (11 tests)<br>`backend/tests/test_api_sse_stream.py` (6 tests)<br>`backend/tests/test_api_analyze_and_cache.py` (2 tests)<br>`backend/tests/test_cache_layers.py` (5 tests) | **PASS** | Provider failures classified once (401 -> `LLM_AUTH_FAILED`; 429 quota -> `LLM_QUOTA_EXCEEDED` and non-retryable; 429 rate limit -> `LLM_RATE_LIMITED` with backoff; timeouts/5xx -> `LLM_UNAVAILABLE`). Memory failure cache enforces 60s fail-fast window answering without provider calls; `/api/v1/llm/status` exposes `last_error_code` and `last_error_at`. SSE stream and caching verified. |
| **Phase 6.1: React Frontend & Contract Coverage** | • `frontend/index.html`<br>• `frontend/src/` (app, api, components, features, hooks, lib, styles, test)<br>• `frontend/Dockerfile`<br>• `frontend/nginx.conf` | `frontend/src/api/schemas.test.ts` (4 tests)<br>`frontend/src/api/sse.test.ts` (9 tests)<br>`frontend/src/api/client.test.ts` (6 tests)<br>`frontend/src/test/architecture.test.ts` (2 tests)<br>`frontend/src/features/projects/projects.test.tsx` (5 tests)<br>`frontend/src/components/Navbar.test.tsx` (4 tests)<br>`frontend/src/features/trial/trialFlow.test.tsx` (3 tests)<br>`frontend/src/features/trial/errors.test.tsx` (5 tests)<br>`frontend/src/features/admin/admin.test.tsx` (4 tests)<br>`frontend/src/test/a11y.test.tsx` (3 tests) | **PASS** | 45 tests across 10 suites passing. UI strictly implements no demo/mock data in production code; all analysis live from SSE; plain text rendering (`whitespace-pre-wrap`); CodeMirror lazy loaded; complete error code coverage and accessible focus rings. |

---

## 2. Detailed Gap Analysis & Action Plan for Phase 2.1

### Gap 2.1.1: Missing `/knowledge/overrides/verified_entries.json`
- **Current State**: File does not exist.
- **Action**: Create `/knowledge/overrides/verified_entries.json` specifying verified tables (e.g. `MARA`, `MARC`, `MARD`, `MAKT`, `MBEW`, `MVKE`, `KNA1`, `KNB1`, `KNVV`, `LFA1`, `LFB1`, `LFM1`, `VBAK`, `VBAP`, `EKKO`, `EKPO`, `BKPF`, `BSEG`), verified fields (`MANDT`, `SPRAS`, `MATNR`, `WERKS`, `KUNNR`, `LIFNR`, `BUKRS`, `BELNR`), and verified SQL keywords.

### Gap 2.1.2: Prebuilt JSONs Missing Verification Defaults
- **Current State**: Entries in `sap_tables_prebuilt.json` and `sql_keywords.json` have `verified: true`.
- **Action**: Update `sap_tables_prebuilt.json`, `sap_common_fields.json`, and `sql_keywords.json` so entries reflect `verified: false` unless explicitly listed in `verified_entries.json`.

### Gap 2.1.3: Store Lookups Missing `verified` Attribute
- **Current State**: `SapLookupResult` in `backend/app/knowledge/models.py` has no `verified` field; `store.py` does not propagate `verified` to lookup responses.
- **Action**: Add `verified: bool = False` to `SapLookupResult` and `SqlKeyword` / lookup models; load `verified_entries.json` in `KnowledgeStore` and set `verified` accordingly with lower confidence (0.7 vs 1.0) for unverified entries.

### Gap 2.1.4: Knowledge Packs & Agent Prompts Missing Lower-Confidence Annotations
- **Current State**: `backend/app/agents/packs.py` and `backend/app/agents/prompts.py` do not distinguish unverified items.
- **Action**: Add `[UNVERIFIED - Lower Confidence]` annotations in knowledge pack formatting and instruct agent prompts to handle unverified entries with lower confidence.

### Gap 2.1.5: Outdated `knowledge/REVIEW.md`
- **Current State**: Claims zero pending reviews.
- **Action**: Populate `knowledge/REVIEW.md` with the exact list and counts of unverified tables, fields, and keywords pending domain review.

