# Project Rules

## Core Product Rule: No Offline Mode

> **The application works ONLY through the OpenAI Agents SDK and an OpenAI-compatible LLM (model from LLM_MODEL, default gpt-4o-mini; later astra-luna through env only). There is no offline mode, no demo mode, no dry-run, no canned or fake answers, no parse-only answers, and no answer assembled without the LLM. If the LLM cannot be used, the request fails with a clear typed error.**

---

## Architectural Guardrails & Enforcements

1. **Mandatory LLM Execution**:
   - Every business intelligence response must be assembled through the OpenAI Agents SDK and an OpenAI-compatible LLM.
   - Deterministic parsing (Phase 1) and knowledge retrievals (Phase 2) are auxiliary inputs to agent prompts, never standalone responses.
   - No mock, fake, dry-run, or canned outputs may ever be generated or imported in runtime application code (`backend/app/`).

2. **No Test Doubles in Production Code**:
   - `FakeModel`, canned agent responses, and mock runners reside strictly in `backend/tests/support/`.
   - Production modules under `backend/app/` are forbidden from importing from `tests/` or referencing test doubles.
   - Enforced by automated architecture test: `backend/tests/test_architecture.py`.

3. **Strict Typed Error Hierarchy**:
   If the LLM cannot be invoked or fails to produce a valid response, the application must immediately raise a typed exception (`backend/app/errors.py`) with a stable error code and sanitized user-facing message:
   - `LLM_NOT_CONFIGURED`: Missing, blank, or placeholder API key.
   - `LLM_AUTH_FAILED`: HTTP 401 unauthorized from the LLM provider.
   - `LLM_QUOTA_EXCEEDED`: HTTP 429 quota/billing exhaustion.
   - `LLM_RATE_LIMITED`: HTTP 429 rate limit exceeded after backoff retries.
   - `LLM_UNAVAILABLE`: Network failure, HTTP 5xx, timeout, or circuit breaker OPEN.
   - `LLM_BUDGET_EXCEEDED`: Daily token/cost budget cap reached.
   - `SYNTHESIS_FAILED`: Synthesizer failed or produced unparseable structure after retry.

4. **Sanitized User Diagnostics**:
   - Error messages must NEVER print, leak, or log API keys or provider internal traces.
   - Diagnostic tools (`--check-env`, `--ping`) provide safe environment inspection without full execution.

5. **Role of Baseline Opportunity Query**:
   - Deterministic query generation (`baseline_opportunity_query`) serves strictly as a **validation reference** and fewshot prompt benchmark.
   - It is **never** substituted in place of the LLM-generated opportunity query.
   - If an LLM-generated query fails SQL safety or table-subset validation after a retry, the field is left empty (`""`) with an explicit audit warning.

6. **Degraded Execution Policy**:
   - If **both** domain agents fail: the pipeline aborts immediately with `LLM_UNAVAILABLE` (no answer fabricated).
   - If **one** domain agent fails: the orchestrator may proceed to the Synthesizer LLM with `degraded=True` and an explanatory reason, allowing the LLM to synthesize available context.
   - If the **Synthesizer** fails: the pipeline aborts with `SYNTHESIS_FAILED` (no fallback text).

