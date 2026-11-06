# Operations & Troubleshooting Runbook

This operational runbook provides guidance for managing, maintaining, and troubleshooting the MDM Syniti Data Migration & Governance Agent pipeline in production and staging environments.

---

## 1. Architecture & Component Health

The application consists of:
1. **Frontend SPA:** React 18, Vite, TanStack Query v5, Tailwind CSS, CodeMirror (port `8080` in prod, `5173` in dev).
2. **Backend API:** FastAPI ASGI service running on Uvicorn (port `8000`).
3. **Cache & Locks:** Multi-tier cache (in-memory for single-node / local dev; Redis for production clusters).
4. **AI Inference:** OpenAI API (`gpt-4o-mini` or alternative OpenAI-compatible endpoints like `astra-luna`).

### Health & Readiness Endpoints
- **Liveness Probe:** `GET /health` -> `{ "status": "ok", "app_env": "production" }` (HTTP 200)
- **Readiness Probe:** `GET /ready` -> Checks Redis connectivity, FAISS indexes, and knowledge stores. Returns HTTP 200 or 503.
- **LLM Status:** `GET /api/v1/llm/status` -> Reports key configuration, active model, `last_error_code`, `last_error_at`, and provider health.
- **Prometheus Metrics:** `GET /metrics` -> Exposes latency histograms, token counters, and cache hit ratios.

---

## 2. Troubleshooting Guide

### Issue 1: Authentication Failure (HTTP 401 / `LLM_AUTH_FAILED`)
- **Symptoms:** API requests fail immediately with `LLM_AUTH_FAILED`. Frontend displays "Authentication failure communicating with the LLM provider."
- **Root Cause:** Missing, expired, or invalid API key (`OPENAI_API_KEY`).
- **Remediation Steps:**
  1. Inspect active key status without exposing secrets:
     ```powershell
     python -m app.agents.cli --check-env
     ```
  2. Verify if key passes a 1-token live ping:
     ```powershell
     python -m app.agents.cli --check-env --ping
     ```
  3. Update `.env` or set in process environment:
     ```powershell
     Set-Content -Path ".env" -Value 'OPENAI_API_KEY=sk-proj-yourActualKeyHere'
     ```
  4. The orchestrator failure cache automatically holds the failure for 60 seconds to avoid provider spam. Clear the failure cache or wait 60 seconds after updating the key.

### Issue 2: Quota Exceeded (HTTP 429 / `LLM_QUOTA_EXCEEDED`)
- **Symptoms:** Pipeline immediately fails with `LLM_QUOTA_EXCEEDED`. Message mentions "Your account is not active, please check your billing details" or "insufficient_quota".
- **Distinction:**
  - `LLM_QUOTA_EXCEEDED` is a **billing/credit exhaustion** issue. It is **NOT RETRIED** by runners.
  - `LLM_RATE_LIMITED` is a **transient concurrency spike**. It is retried automatically with exponential backoff.
- **Remediation Steps:**
  1. Check AI gateway or provider account dashboard to ensure active billing credits or token allocations are available.
  2. The failure cache stores `LLM_QUOTA_EXCEEDED` for 60 seconds, during which all incoming requests fail fast from memory without burning API calls.
  3. Once billing is replenished, restart the service or wait 60 seconds.

### Issue 3: Rate Limiting (HTTP 429 / `LLM_RATE_LIMITED`)
- **Symptoms:** High load results in occasional request slowdowns or `LLM_RATE_LIMITED`.
- **Mitigation:**
  - The pipeline automatically retries transient 429s up to `LLM_MAX_RETRIES` (default: 3) with jittered backoff.
  - Verify that SingleFlight coalescing is functioning (coalesces identical concurrent queries into 1 runner invocation).
  - Check `GET /metrics` for rate limit spikes.

### Issue 4: Circuit Breaker Open (HTTP 503 / `LLM_UNAVAILABLE`)
- **Symptoms:** Consecutive provider timeouts or 5xx server errors trip the circuit breaker. API returns `503 Service Unavailable`.
- **Behavior:**
  - If 5 consecutive failures occur within 60 seconds, the breaker enters `OPEN` state for 30 seconds (`recovery_timeout_s`).
  - While `OPEN`, calls fail fast without hitting the provider.
  - After 30 seconds, the breaker transitions to `HALF_OPEN` and lets 1 probe request through.
- **Remediation Steps:**
  1. Check AI gateway / provider health or endpoint status.
  2. Test provider ping: `python -m app.agents.cli --check-env --ping`.
  3. Breaker resets automatically once a probe request succeeds.

### Issue 5: Cache Corruption & Integrity Failures
- **Symptoms:** Warning logged: `"Cache integrity verification failed for L1 key ..."`
- **Behavior:**
  - Every cached entry contains an SHA-256 integrity hash covering its schema, model, prompt versions, and payload.
  - If a cached payload is corrupted or modified externally, the pipeline detects the checksum mismatch, logs a warning, treats it as a **cache miss**, and safely executes fresh analysis.
- **Clearing the Cache:**
  ```powershell
  # Clear cache via Orchestrator CLI:
  python -m app.orchestrator.cli --clear-cache

  # Or flush Redis directly (if using Redis backend):
  docker compose exec redis redis-cli -a syniti_dev_secret FLUSHDB
  ```

---

## 3. Maintenance Operations

### Pre-warming Knowledge & Embeddings
To pre-generate FAISS vector embeddings and cache SentenceTransformer models:
```powershell
python -m app.knowledge.build_index
```

### Inspecting Inputs & Token Usage (No LLM Call)
To inspect prompt formatting and verify token context without spending budget:
```powershell
python -m app.orchestrator.cli --inspect-inputs --example sheet1
```

### Checking Model Readiness for Astra-Luna
Before pointing traffic to an alternative inference endpoint:
```powershell
python -m app.agents.cli --check-model
```

