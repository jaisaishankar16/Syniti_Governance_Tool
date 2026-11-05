# Third-Party Licenses & Compatibility Summary

This document summarizes the licenses of all runtime and development dependencies utilized by the **MDM Syniti Governance & Migration Analysis Platform** (both backend and frontend).

All runtime dependencies use permissive open-source licenses (MIT, Apache 2.0, BSD-3-Clause, ISC, and SIL Open Font License 1.1). **No copyleft (GPL / AGPL / LGPL) dependencies are used in production runtime code.**

---

## 1. Backend Runtime Dependencies

| Package | Version | License | Compatibility / Usage Notes |
| :--- | :--- | :--- | :--- |
| **fastapi** | `0.162.0` | MIT | Permissive web framework for ASGI APIs |
| **starlette** | `0.45.3` | BSD-3-Clause | Underlying ASGI toolkit |
| **pydantic** | `2.12.5` | MIT | Data validation and settings management |
| **pydantic-settings** | `2.13.1` | MIT | Environment variable loading (.env) |
| **sqlglot** | `26.17.0` | MIT | SQL parsing, AST traversal, and transpilation |
| **openai** | `1.109.1` | Apache-2.0 | Official OpenAI Python API client |
| **openai-agents** (`agents`) | `0.8.2` | Apache-2.0 | Multi-agent coordination framework |
| **redis** | `7.4.0` | MIT | Redis cache backend client |
| **faiss-cpu** | `1.15.1` | MIT | Vector indexing for semantic knowledge retrieval |
| **sentence-transformers** | `5.3.0` | Apache-2.0 | Text embeddings for semantic search |
| **openpyxl** | `3.1.5` | MIT | Reading Excel `.xlsx` workbook test fixtures |
| **tiktoken** | `0.14.0` | MIT | Token counting for LLM context windows |
| **uvicorn** | `0.34.0` | BSD-3-Clause | Production ASGI web server |
| **httpx** | `0.28.1` | BSD-3-Clause | HTTP client for external integrations |

---

## 2. Frontend Runtime Dependencies

| Package | Version | License | Compatibility / Usage Notes |
| :--- | :--- | :--- | :--- |
| **react** | `18.3.1` | MIT | Core UI library |
| **react-dom** | `18.3.1` | MIT | DOM rendering for React |
| **react-router-dom** | `6.28.0` | MIT | Client-side routing |
| **@tanstack/react-query** | `5.62.8` | MIT | Async server state management and query caching |
| **zod** | `3.24.1` | MIT | Runtime schema validation for API responses |
| **lucide-react** | `0.468.0` | ISC | Permissive icon library |
| **@uiw/react-codemirror** | `4.23.6` | MIT | SQL code editor component |
| **@codemirror/lang-sql** | `6.8.0` | MIT | T-SQL syntax highlighting and editor support |
| **@radix-ui/react-dialog** | `1.1.2` | MIT | Accessible modal dialog primitives |
| **@radix-ui/react-dropdown-menu** | `2.1.2` | MIT | Accessible dropdown menu primitives |
| **@radix-ui/react-tabs** | `1.1.1` | MIT | Accessible tabs primitives |
| **@radix-ui/react-tooltip** | `1.1.4` | MIT | Accessible tooltip primitives |
| **sonner** | `1.7.1` | MIT | Toast notification system |
| **clsx** | `2.1.1` | MIT | Class name concatenation |
| **tailwind-merge** | `2.5.5` | MIT | Tailwind utility conflict resolution |
| **@fontsource-variable/inter** | `5.1.0` | OFL-1.1 | Self-hosted Inter font (SIL Open Font License) |

---

## 3. Dependency Security Audits

### Python Backend (`pip-audit`)
- Evaluated against PyPI and OSV advisory databases.
- Advisory note on transitive sub-dependency `filelock` and `pillow`. Both are development-pinned in `backend/requirements.lock.txt`.
- Zero vulnerabilities in core API, Agent, Synthesizer, or Guardrail modules.

### Node.js Frontend (`npm audit --omit=dev`)
- Runtime dependency scan confirms zero critical vulnerabilities.
- Advisory notices for `react-router` and `zod` are tied to upgrade paths to `react-router-dom` v7 and `zod` v4, while the project specification explicitly targets React Router v6 and Zod v3.

---

## 4. Compliance Verdict
The codebase complies with all enterprise software licensing guidelines. All third-party packages are licensed under permissive open-source licenses allowing commercial distribution, modification, and private deployment without viral copyleft obligations.

