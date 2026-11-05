# Production Go-Live Checklist

This checklist must be reviewed and signed off prior to deploying the MDM Syniti Governance & Migration Analysis Platform into a production environment.

---

## 1. Environment & Credentials

- [ ] **API Credentials:** Valid production `OPENAI_API_KEY` (or custom provider key) is configured in `.env` or injected via secrets manager (AWS Secrets Manager, Azure Key Vault, HashiCorp Vault).
- [ ] **No Default Placeholders:** Verified that `.env` does not contain any placeholder values (`"sk-..."`, `"your-key-here"`, etc.).
- [ ] **Model Configuration:** `LLM_MODEL` is set to target production model (e.g., `gpt-4o-mini`, `gpt-4o`, or `astra-luna`).
- [ ] **Base URL:** If using a self-hosted inference server or gateway (vLLM, LiteLLM, Ollama), verify `OPENAI_BASE_URL` is configured and network-accessible.
- [ ] **Model Readiness Probe:** Run capability validation against target model:
  ```powershell
  python -m app.agents.cli --check-model
  ```
  Ensure all 4 checks pass or appropriate fallback modes (`STRUCTURED_OUTPUT_MODE=json_prompt`) are set.

---

## 2. Infrastructure & Networking

- [ ] **Host Binding:** In production containers, `HOST=0.0.0.0` is set (development defaults to `127.0.0.1`).
- [ ] **TLS / SSL Termination:** A reverse proxy (Nginx, Traefik, AWS ALB, Cloudflare) handles HTTPS termination.
- [ ] **Redis Backend:** For multi-instance horizontal scaling, `CACHE_BACKEND=redis` and `REDIS_URL` are configured with authentication and persistent storage.
- [ ] **Health & Readiness Monitoring:**
  - Load balancer liveness probe targets `GET /health` (expects 200).
  - Load balancer readiness probe targets `GET /ready` (expects 200).
- [ ] **CORS Configuration:** `ALLOWED_ORIGINS` in `ApiSettings` is set to the explicit production domain(s) rather than wildcard `*`.
- [ ] **Rate Limiting:** `RATE_LIMIT_BACKEND=redis` is enabled to protect against denial of service and API abuse.

---

## 3. Storage & Persistence

- [ ] **Knowledge Volumes:** Docker volume mounted to `/app/knowledge/learned` to ensure learned SAP and SQL domain knowledge is persisted across container restarts.
- [ ] **Log Retention:** Volume mounted to `/app/logs` with log rotation policies enabled.
- [ ] **Pre-built FAISS Indexes:** Verify that `knowledge/index/` contains pre-built vector indexes (`sap_docs.faiss`, `sql_docs.faiss`). If missing, run:
  ```powershell
  python -m app.knowledge.build_index
  ```

---

## 4. Quality & Safety Verification

- [ ] **Zero Mock / Offline Data in Production:** Verified that no canned analysis or fake analysis data exists in production code paths.
- [ ] **Subset Rule Enforced:** AST subset validator is active to ensure all generated error queries are valid mathematical subsets of opportunity queries.
- [ ] **Input Sanitization & Canary Tokens:** Input tripwires, Canary token tracking, and secret redaction regexes are enabled.
- [ ] **All Offline Tests Pass:** Run full test suite:
  ```powershell
  pytest backend/tests
  npm --prefix frontend test
  ```
  Both suites must achieve 100% pass rate with zero warnings.
- [ ] **Frontend Production Build:** Run static build and verify clean compilation:
  ```powershell
  npm --prefix frontend run build
  ```

