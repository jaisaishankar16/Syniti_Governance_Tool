# Guardrails Safety & Security Architecture

## 1. Overview & Threat Model

The **MDM Syniti Guardrails Layer** provides deterministic, policy-driven protection against adversarial prompt injections, credential leaks, data poisoning, resource exhaustion, and unsafe SQL generation. 

Unlike systems relying entirely on LLM-as-a-judge (which is non-deterministic, slow, and susceptible to the same vulnerabilities as the agent), this guardrail subsystem executes **deterministic verification first**:
- AST-level validation (via `sqlglot`)
- Strict lexical grounding
- Parser source-of-truth reconciliation
- Cryptographic canary tokens and per-request boundary isolation

### OWASP Top 10 for LLMs (2025) Coverage Mapping

| OWASP Risk | Description | Syniti Guardrail Defense | Module |
| :--- | :--- | :--- | :--- |
| **LLM01: Prompt Injection** | Direct or indirect injection in comments, literals, or table identifiers | Weighted regex scanner, delimiter hardening (`<sql_data id="...">`), tag escaping, comment quarantine | `injection.py`, `input_checks.py` |
| **LLM02: Sensitive Information Disclosure** | Accidental leakage of API keys, DB passwords, connection strings, PII, or system prompts | Automated secret masking, stable redaction tokens (`<REDACTED_SECRET_n>`), Luhn CC validator, random canary token leak detector | `redaction.py`, `logging_filter.py`, `agent_output.py` |
| **LLM03: Supply Chain Vulnerabilities** | Poisoned learned knowledge or untrusted schema references | Write-caps (max 3/run), prior-search requirement, lexical evidence grounding (minimum overlap requirement), human approval gates | `tool_policy.py` |
| **LLM04: Data & Model Poisoning** | Unauthorized writes to learned knowledge store | AST verification, exact table/field catalog validation, evidence chunk provenance verification | `tool_policy.py`, `sap_catalog.py` |
| **LLM05: Improper Output Handling** | Unsafe SQL execution, DDL injection, multi-statement attacks | Read-only single statement validation, disallowed commands (`DROP`, `INSERT`, `EXEC`, etc.), `SELECT ... INTO` prohibition | `sql_safety.py` |
| **LLM06: Excessive Agency** | Agent invoking unpermitted tools or arbitrary table modifications | Strict tool allowlist per agent (`ALLOWED_AGENT_TOOLS`), regex identifier validation, `max_writes_per_run` | `tool_policy.py` |
| **LLM07: System Prompt Leakage** | Exfiltration of agent system prompt instructions | Unique per-request canary tokens (`CANARY_<hex>_<hex>`); any agent output containing canary is immediately blocked | `injection.py`, `agent_output.py` |
| **LLM08: Vector & Embedding Weaknesses** | Excessive search queries or semantic hijacking | Per-run search caps (`max_search_calls_per_run`), query length limit, query injection scanning, top-k capping | `tool_policy.py` |
| **LLM09: Misinformation / Hallucination** | Invented tables, fields, roles, or incorrect view types | Parser ground-truth reconciliation: drops invented tables/fields, forces statement_type/view_name from AST | `agent_output.py`, `final_output.py` |
| **LLM10: Unbounded Consumption** | Resource exhaustion, runaway loops, API billing denial of service | Per-request token budget, cumulative daily token budget, circuit breaker (transient 5xx/429 tracking), concurrency semaphore | `budget.py`, `circuit_breaker.py` |

---

## 2. Guardrails Architecture & Pipeline Stages

```
Raw SQL Input
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ Pre-Flight Input Pipeline (Deterministic)              │
│  ├─ Charset check (Control, zero-width, bidi stripped)  │
│  ├─ Limits check (Length, statements, token, nesting)  │
│  ├─ Looks-like-SQL check (Rejects plain prose/code)    │
│  ├─ Secret redaction (Replaces credentials with tokens)│
│  ├─ PII handling (Email, phone, SSN, Luhn CC)         │
│  ├─ Injection scanning (Comments, literals, IDs)       │
│  └─ Boundary ID & Canary Token Generation              │
└────────────────────────────────────────────────────────┘
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ Deterministic SQL Parser (AST Source of Truth)        │
└────────────────────────────────────────────────────────┘
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ Agent Execution & Tool Policy Enforcement              │
│  ├─ Tool allowlist per agent                           │
│  ├─ Identifier & query argument safety                 │
│  ├─ Learned-knowledge write checks (Prior search,      │
│  │   lexical grounding, length expansion ratio)        │
│  ├─ Token budget & concurrency management              │
│  └─ Circuit breaker (Fast-fail on transient outages)   │
└────────────────────────────────────────────────────────┘
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ Post-Flight Reconciliation & Sanitization              │
│  ├─ Canary leak & secret leakage check                 │
│  ├─ Drop hallucinated tables and fields                │
│  ├─ Enforce parser table roles, statement type, view   │
│  └─ Text sanitization (Strip HTML, scripts, URLs)      │
└────────────────────────────────────────────────────────┘
      │
      ▼
┌────────────────────────────────────────────────────────┐
│ Final Output & Synthesis Validation                    │
│  ├─ Read-only single-statement SELECT validation       │
│  ├─ Opportunity vs. Error Query subset verification    │
│  ├─ Governance glossary term conformance               │
│  ├─ Literal grounding warning                          │
│  └─ Risk score consistency floor enforcement           │
└────────────────────────────────────────────────────────┘
```

---

## 3. Configuration Reference

All settings are configured via `pydantic-settings` with environment variable prefix `GUARD_` (or direct threshold names) loaded from the repository `.env` file or process environment.

| Setting | Type | Default | Operational Modes / Description |
| :--- | :--- | :--- | :--- |
| `GUARD_MODE_INPUT_SIZE` | `Literal` | `"enforce"` | `"off"`, `"warn"`, `"enforce"` |
| `GUARD_MODE_LOOKS_LIKE_SQL` | `Literal` | `"enforce"` | `"off"`, `"warn"`, `"enforce"` |
| `GUARD_MODE_SECRETS` | `Literal` | `"enforce"` | `"off"`, `"warn"`, `"enforce"` |
| `GUARD_MODE_PII` | `Literal` | `"warn"` | `"off"`, `"warn"`, `"redact"`, `"block"` |
| `GUARD_MODE_INJECTION` | `Literal` | `"sanitize"` | `"off"`, `"warn"`, `"sanitize"`, `"block"` |
| `GUARD_MODE_TOOL_POLICY` | `Literal` | `"enforce"` | `"off"`, `"warn"`, `"enforce"` |
| `GUARD_MODE_AGENT_OUTPUT` | `Literal` | `"enforce"` | `"off"`, `"warn"`, `"enforce"` |
| `GUARD_MODE_FINAL_OUTPUT` | `Literal` | `"enforce"` | `"off"`, `"warn"`, `"enforce"` |
| `MAX_SQL_CHARS` | `int` | `50,000` | Maximum length of input SQL query |
| `MAX_STATEMENTS` | `int` | `10` | Maximum batch statements |
| `MAX_NESTING` | `int` | `15` | Maximum parenthesis nesting depth |
| `MAX_TOKEN_LEN` | `int` | `256` | Maximum single token length |
| `MAX_FIELD_CHARS` | `int` | `5,000` | Maximum output narrative field length |
| `MAX_TOOL_QUERY_CHARS`| `int` | `256` | Maximum length of tool search queries |
| `MAX_WRITES_PER_RUN` | `int` | `3` | Maximum learned knowledge writes per run |
| `PER_REQUEST_TOKEN_BUDGET`| `int` | `50,000` | Maximum token ceiling for a single request |
| `DAILY_TOKEN_BUDGET` | `int` | `1,000,000` | Process-wide daily token ceiling |
| `MAX_CONCURRENT_RUNS` | `int` | `10` | Concurrency limit via asyncio Semaphore |
| `CIRCUIT_FAILURE_THRESHOLD`| `int` | `3` | Consecutive transient errors before tripping OPEN |
| `CIRCUIT_RESET_S` | `float` | `30.0` | Seconds to wait in OPEN before HALF_OPEN trial |
| `DEBUG_LOG_SQL` | `bool` | `False` | When False, logs replace raw SQL with `[SQL sha256=... len=...]` |
| `LEARNED_REQUIRES_APPROVAL`| `bool`| `True` | Requires key presence in `approved_learned_keys.txt` |

---

## 4. Known Limits & Trade-offs

1. **Parser Superiority over LLM-as-a-Judge**:
   LLMs are notoriously prone to false negatives when evaluating subtle SQL nuances (e.g., recursive CTE termination, subtle join cartesian risks, or subset predicate matching). Our guardrails delegate structural validation entirely to deterministic AST parsing via `sqlglot`.
2. **Literal Grounding Warnings vs. Blocks**:
   Constants like `1`, `'100'`, or `0` are standard in SQL filters and client isolation. To avoid blocking legitimate queries, ungrounded literals generate `warn` events rather than hard blocks.
3. **Redaction Mapping in Memory Only**:
   Redacted secret placeholders `<REDACTED_SECRET_n>` are mapped only in transient memory during pre-flight. The mapping is never passed to agents or saved in logs, preventing reverse reconstruction of credentials.
4. **Transient vs. Non-Transient Circuit Breakers**:
   Only 5xx server errors, rate limits (429), and network timeouts count toward circuit breaker trips. Guardrail blocks or bad client queries (400) do not trip the circuit breaker.

