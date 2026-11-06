# MDM Syniti Analysis Orchestrator API Documentation

This document describes the REST API, Server-Sent Events (SSE) streaming protocol, error handling contracts, operational probes, and configuration settings for the MDM Syniti Analysis Orchestrator.

Under **Project Rules** (see [PROJECT_RULES.md](PROJECT_RULES.md)), execution works **strictly through the OpenAI Agents SDK and verified domain knowledge**. There is no offline mode, dry-run mode, or fallback answer generation.

---

## 1. Endpoint Summary

| Method | Path | Summary | Description | Access |
|---|---|---|---|---|
| `GET` | `/health` | Liveness Probe | Returns `200 OK` while web process is alive. | Public |
| `GET` | `/ready` | Readiness Probe | Checks vector indexes, cache, and LLM configuration (`503` if not ready). | Public |
| `GET` | `/metrics` | Prometheus Metrics | Plain-text Prometheus scrapable metrics. | Public / Admin |
| `POST` | `/api/v1/analyze` | Execute Analysis | Runs end-to-end analysis on SQL text. Returns `BusinessIntelligence`. | Authenticated |
| `POST` | `/api/v1/analyze/stream` | Stream Analysis | SSE stream emitting `progress`, `result`, `error`, `done`. | Authenticated |
| `GET` | `/api/v1/examples` | List Examples | Returns the 11 reference workbook queries (SQL text only). | Public |
| `GET` | `/api/v1/projects` | List Projects | Returns migration projects (Placeholder UI data). | Authenticated |
| `GET` | `/api/v1/projects/{id}` | Get Project | Returns specific migration project details. | Authenticated |
| `GET` | `/api/v1/activity` | List Activity | Returns recent execution activity history. | Authenticated |
| `GET` | `/api/v1/me` | Current User | Returns authenticated principal identity and roles. | Authenticated |
| `GET` | `/api/v1/llm/status` | LLM Status | Returns model, budget, and circuit breaker status (no keys exposed). | Public |
| `GET` | `/api/v1/cache/stats` | Cache Metrics | Multi-tier L1/L2/L3 cache hit/miss statistics. | `X-Admin-Key` |
| `DELETE` | `/api/v1/cache` | Clear Cache | Clears specified cache tier (`all`, `l1`, `l2`, `l3`). | `X-Admin-Key` |
| `POST` | `/api/v1/admin/llm/ping` | Probe LLM | 1-token live probe test (cached 60s, rate-limited). | `X-Admin-Key` |
| `GET` | `/api/v1/admin/learned` | List Learned | Returns pending, approved, and rejected learned SAP entries. | `X-Admin-Key` |
| `POST` | `/api/v1/admin/learned/{id}/approve` | Approve Entry | Approves learned SAP entry and writes audit trail. | `X-Admin-Key` |
| `POST` | `/api/v1/admin/learned/{id}/reject` | Reject Entry | Rejects learned SAP entry and writes audit trail. | `X-Admin-Key` |
| `GET` | `/api/v1/admin/glossary/proposals` | Glossary Proposals | Returns proposed new governance concepts and terms. | `X-Admin-Key` |

---

## 2. Error Response Envelope and Status Mapping

Every non-2xx error response returns a consistent JSON envelope:

```json
{
  "error": {
    "code": "ERROR_CODE",
    "message": "Human-readable sanitized error description.",
    "request_id": "89b52a12-87f5-4dc2-8f96-3c7d6e42b8e1",
    "retry_after_s": null,
    "details": [
      {
        "field": "sql",
        "issue": "Specific validation or syntax issue"
      }
    ]
  }
}
```

### Central Error Mapping Table

| Programmatic Code | HTTP Status | Cause / Scenario |
|---|---|---|
| `VALIDATION_ERROR` | `422 Unprocessable Content` | Schema validation error on request parameters or body. |
| `SQL_PARSE_ERROR` | `422 Unprocessable Content` | SQL statement contains fatal syntax errors rejected by parser. |
| `INPUT_REJECTED` | `422 Unprocessable Content` | Query violates security guardrails (prompt injection, PII, non-SQL). |
| `PAYLOAD_TOO_LARGE` | `413 Content Too Large` | Request entity length exceeds `MAX_BODY_BYTES`. |
| `RATE_LIMITED` | `429 Too Many Requests` | Request quota exceeded (includes `Retry-After` header). |
| `LLM_BUDGET_EXCEEDED`| `429 Too Many Requests` | Daily token or dollar usage budget has been exhausted. |
| `SYNTHESIS_FAILED` | `502 Bad Gateway` | Synthesizer LLM output failed schema validation after retries. |
| `LLM_NOT_CONFIGURED` | `503 Service Unavailable` | `OPENAI_API_KEY` is missing or contains placeholder text. |
| `LLM_AUTH_FAILED` | `503 Service Unavailable` | Provider returned HTTP 401/403 Unauthorized/Forbidden (server credential issue). Not retried. |
| `LLM_QUOTA_EXCEEDED` | `503 Service Unavailable` | Provider account billing quota, credit balance, or rate allowance exhausted. Not retried. |
| `LLM_MODEL_NOT_FOUND` | `503 Service Unavailable` | Configured AI model is not deployed or accessible on the AI gateway endpoint. Not retried. |
| `LLM_UNAVAILABLE` | `503 Service Unavailable` | Provider network failure, TLS/cert failure, 5xx server error, or circuit breaker is OPEN. |
| `INTERNAL_ERROR` | `500 Internal Server Error` | Unexpected server crash (details masked from client, logged server-side). |

---

## 3. Server-Sent Events (SSE) Streaming Protocol

The streaming endpoint `POST /api/v1/analyze/stream` emits W3C standard Server-Sent Events over HTTP with `Cache-Control: no-cache` and `X-Accel-Buffering: no`.

### Event Types

1. `progress`: Emitted at each pipeline transition:
   ```text
   event: progress
   data: {"stage":"guardrails","message":"Executing pre-flight security guardrails","elapsed_ms":12.4,"detail":{}}
   ```
2. `result`: Emitted upon successful synthesis:
   ```text
   event: result
   data: {"business_meaning":"...","technical_summary":"...","governance":{...},"meta":{...}}
   ```
3. `error`: Emitted if an error occurs after the stream has opened:
   ```text
   event: error
   data: {"error":{"code":"LLM_UNAVAILABLE","message":"...","request_id":"...","retry_after_s":null,"details":[]}}
   ```
4. `done`: Terminal signal closing the connection:
   ```text
   event: done
   data: {}
   ```
5. `: ping`: Comment heartbeat emitted every `SSE_HEARTBEAT_S` (15s) to prevent firewall/proxy timeouts.

### Connection Resiliency and Disconnect Shielding

When a client browser disconnects early, the ASGI server raises a disconnect signal. The background orchestrator run is **shielded** (`asyncio.shield`), allowing it to complete execution, populate L1/L2 caches, and save learned entries without waste.

---

## 4. Rate Limiting and Identity

Rate limiting uses an in-memory sliding window algorithm (or Redis in clustered mode):
- **Analyze Bucket**: Defaults to `20/minute` per client IP.
- **Default Bucket**: Defaults to `120/minute` per client IP.
- **Client IP Resolution**: Evaluates direct socket client IP. If direct IP matches `TRUSTED_PROXIES` (e.g. reverse proxy or load balancer), honors the leftmost untrusted IP from `X-Forwarded-For`.
- **Identity Dependency**: Pluggable via `get_principal() -> Principal` returning `{id, display_name, email, roles, initials}`. Currently provides a local admin placeholder until enterprise SSO is attached.

---

## 5. Administrative Authentication

All endpoints under `/api/v1/admin/*` require the `X-Admin-Key` header:
- When `ADMIN_API_KEY` is unset in configuration, admin endpoints return `404 Not Found`.
- Constant-time string comparison (`hmac.compare_digest`) prevents timing side-channels.
- An invalid key returns `403 Forbidden`.

---

## 6. Future Integration: API-Driven QuerySource

In Phase 4, the orchestrator established the asynchronous `QuerySource` protocol:
```python
class QuerySource(Protocol):
    def __aiter__(self) -> AsyncIterator[SqlRequest]: ...
```

In Phase 5, the FastAPI application accepts queries synchronously and via SSE streaming through `AnalyzeRequest`. To attach automated batch ingestion (e.g., scheduled SQL view scans, Syniti ETL webhook listeners, or Kafka topics):
1. Implement a class fulfilling `QuerySource` (e.g. `WebhookQuerySource` or `KafkaBatchSource`).
2. Pass the source to the orchestrator background task without altering the analysis pipeline, guardrails, or cache layers.

---

## 7. Environment Configuration Reference

| Environment Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `development` | Deployment environment (`development` or `production`). |
| `HOST` | `0.0.0.0` | Bind IP interface. |
| `PORT` | `8000` | Bind TCP port. |
| `CORS_ORIGINS` | `["http://localhost:3000"]` | Allowed CORS origins (JSON array or comma-separated). |
| `TRUSTED_HOSTS` | `["*"]` | Allowed Host header domains. |
| `TRUSTED_PROXIES` | `["127.0.0.1", "::1"]` | Proxies trusted to set `X-Forwarded-For`. |
| `MAX_BODY_BYTES` | `1048576` (1MB) | Maximum HTTP payload body size before `413` rejection. |
| `REQUEST_TIMEOUT_S` | `120.0` | Overall analysis timeout limit in seconds. |
| `SSE_HEARTBEAT_S` | `15.0` | Interval in seconds between SSE comment ping heartbeats. |
| `RATE_LIMIT_ANALYZE` | `20/minute` | Rate limit for `/api/v1/analyze` endpoints. |
| `RATE_LIMIT_DEFAULT` | `120/minute` | Rate limit for standard API endpoints. |
| `ADMIN_API_KEY` | `None` | Secret key for administrative endpoints. |
| `DOCS_ENABLED` | `True` (dev) / `False` (prod) | Enables Swagger UI at `/docs` and ReDoc at `/redoc`. |
| `METRICS_ENABLED` | `True` | Enables Prometheus scrape endpoint at `/metrics`. |
| `METRICS_REQUIRE_ADMIN` | `False` (dev) / `True` (prod)| Protects `/metrics` behind `X-Admin-Key`. |
| `SHUTDOWN_GRACE_S` | `30.0` | Maximum seconds to wait for in-flight requests on SIGTERM. |
| `LOG_JSON` | `False` (dev) / `True` (prod) | Outputs structured JSON logs in production. |
| `DEBUG_LOG_SQL` | `False` | When false, hashes SQL in log output for safety. |

