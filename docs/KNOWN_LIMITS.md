# Known Limitations & System Boundaries

This document outlines the architectural boundaries, supported SQL subsets, dialect constraints, and capacity limits of the MDM Syniti Data Migration & Governance Agent pipeline.

---

## 1. Supported SQL Scope & Dialect Constraints

The pipeline is designed and optimized specifically for **Microsoft SQL Server (T-SQL / MSSQL)** data migration and data quality validation queries.

### Supported Constructs
- **Queries:** `SELECT`, `FROM`, `WHERE`, `GROUP BY`, `HAVING`, `ORDER BY`.
- **Joins:** `INNER JOIN`, `LEFT OUTER JOIN`, `RIGHT OUTER JOIN`, `FULL OUTER JOIN`, `CROSS JOIN`, `CROSS APPLY`, `OUTER APPLY`.
- **Expressions & Derivations:** `CASE ... WHEN ... THEN ... ELSE ... END`, string operations (`SUBSTRING`, `LTRIM`, `RTRIM`, `CHARINDEX`, `CONCAT`), null coalescing (`ISNULL`, `COALESCE`), arithmetic operations, and date functions (`DATEADD`, `DATEDIFF`).
- **Set Operations:** `UNION`, `UNION ALL`, `INTERSECT`, `EXCEPT`.
- **Views & CTEs:** `CREATE VIEW`, `CREATE OR ALTER VIEW`, Common Table Expressions (recursive and non-recursive CTEs), subqueries in `WHERE` and `FROM`.
- **Syniti Target Projections:** Rule view update projections matching the convention `<expression> AS <FIELD>NEW` or `<expression> AS <FIELD>_NEW`.

### Explicitly Out-of-Scope / Prohibited Constructs
- **Dynamic SQL:** Queries constructing and executing dynamic strings via `EXEC('...')` or `sp_executesql` cannot be parsed statically and will trigger guardrail warnings or parser degradation.
- **Procedural Constructs:** `WHILE` loops, `CURSOR` declarations (`OPEN`, `FETCH`, `DEALLOCATE`), and `GOTO` statements.
- **Data Definition & Destructive Statements:** `DROP`, `TRUNCATE`, `ALTER TABLE`, `GRANT`, `REVOKE` are strictly blocked by pre-flight and output SQL safety guardrails.
- **SELECT INTO #Temp:** `SELECT ... INTO #temp` is detected and blocked in generated output queries to guarantee stateless view-based execution.

---

## 2. Input Length & Token Limits

- **Maximum SQL Length:** 50,000 characters per analysis request. This limit is defined in `docs/openapi.json`, exported to `frontend/src/api/limits.ts`, and enforced at the FastAPI request validation layer. Queries exceeding 50,000 characters return HTTP 413 / `SQL_TOO_LARGE`.
- **Per-Request Token Budget:** Configured at 10,000 tokens per analysis run (`per_request_token_budget`). Queries exceeding this limit trigger budget breaker events.
- **Daily Token Budget:** Configured at 500,000 tokens per UTC calendar day (`daily_token_budget`). Once reached, non-cached requests are rejected until budget reset.

---

## 3. Cache Tier Boundaries: Single-Node vs Distributed Redis

The system supports two caching backends:

| Feature | In-Memory Backend (`InMemoryBackend`) | Redis Backend (`RedisBackend`) |
| :--- | :--- | :--- |
| **Target Environment** | Local development, single-node containers, offline test suites | Multi-instance production Kubernetes / ECS clusters |
| **Persistence** | Volatile (cleared when process restarts) | Persistent across process and container restarts |
| **Memory Management** | LRU eviction bounded by maximum key limit | Redis `maxmemory-policy` (e.g. `volatile-lru`) |
| **SingleFlight Coalescing** | In-process `asyncio.Future` locks | Distributed Redis key-lease mutexes |
| **Cache Invalidation** | Requires process restart or CLI clear | Distributed cache invalidation via Redis commands |

**Recommendation:** For high-availability, multi-replica deployments, always configure `CACHE_BACKEND=redis` and supply a valid `REDIS_URL`.

---

## 4. Multi-Agent Domain Assumptions

- **Staging Table Resolution:** Staging tables are mapped to SAP base tables using the Syniti Migrate naming rules configured in `knowledge/naming_conventions.json` (e.g., `MM_MARC_ECC_EU_R1_MARC_S` -> `MARC`). If non-standard custom naming conventions are introduced without updating `naming_conventions.json`, tables will be classified as `unknown` or `custom`.
- **Unverified Knowledge:** Tables or keywords not present in `knowledge/overrides/verified_entries.json` are flagged with `verified=False` and confidence `0.70`. Unverified items are visibly surfaced in the UI and marked with `[UNVERIFIED - Lower Confidence]` in agent prompts.
- **Embedding Model Calibration:** Vector similarity search and L3 semantic cache require a calibrated model entry in `knowledge/calibration/embedding_calibration.json`. When running with an uncalibrated embedding model, L3 semantic cache and hints degrade gracefully to disabled, and semantic search hits are marked `calibrated: false` to prevent uncalibrated learned knowledge write-backs. Standard exact catalog matching continues to function with 100% precision.

