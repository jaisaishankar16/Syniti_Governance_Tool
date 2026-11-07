# MDM Syniti — Setup, Installation & Operations Guide

Comprehensive guide for configuring, installing, verifying, and running the **MDM Syniti Data Migration & Governance Agent Platform**.

---

## 1. System Overview & Architecture

MDM Syniti is an enterprise AI-assisted governance and data migration platform that inspects MS SQL queries over SAP ECC / S/4HANA target tables, performs semantic entity extraction and schema enrichment, and synthesizes structured business intelligence, governance specifications, and risk profiles.

### Architecture Stack
* **Frontend SPA:** React 18, Vite, TypeScript (strict), Tailwind CSS, TanStack Query v5, CodeMirror 6 (MSSQL), Radix UI primitives, `@fontsource-variable/inter`.
* **Backend API:** FastAPI (Python 3.10+), Uvicorn ASGI server with Windows selector loop compatibility, Pydantic v2 strict schemas, Server-Sent Events (SSE) streaming.
* **Orchestration & Agents:** Dual concurrent agent pipeline (SQL Agent + SAP Agent), OpenAI Agents SDK, semantic similarity caching (L1 memory, L2 persistent, L3 similarity reuse), single-flight request coalescing.
* **Knowledge Layer:** Prebuilt SAP table catalogs, common field taxonomies, SQL keyword semantics, and FAISS vector retrieval.
* **Data Storage:** Append-only JSON Lines execution timeline (`data/activity.jsonl`).

```
                                  +---------------------------------------+
                                  |   React 18 SPA (Vite / Nginx)         |
                                  |   Dev: Port 5173 | Prod: Port 8080    |
                                  +-------------------+-------------------+
                                                      |
                                   Reverse Proxy /    | REST & SSE Streams
                                   API Client         v
                                  +---------------------------------------+
                                  |   FastAPI Orchestrator (Port 8000)    |
                                  +-------------------+-------------------+
                                                      |
                      +-------------------------------+-------------------------------+
                      |                               |                               |
                      v                               v                               v
             +-----------------+             +-----------------+             +-----------------+
             | SQLglot Parser  |             | Dual LLM Agents |             | Multi-Tier      |
             | & Guardrails    |             | (SQL & SAP)     |             | Cache & Storage |
             +-----------------+             +-----------------+             +-----------------+
                      |                               |                               |
                      +-------------------------------+-------------------------------+
                                                      |
                                                      v
                                        +---------------------------+
                                        | Knowledge & FAISS Vector  |
                                        | Ingestion / data/activity |
                                        +---------------------------+
```

---

## 2. Prerequisites

Ensure your host machine meets the following runtime prerequisites:

| Requirement | Supported Version | Purpose |
| :--- | :--- | :--- |
| **Python** | `3.10.x` (or `>= 3.10`) | Core backend, AI agents, AST parsing, and knowledge ingestion. |
| **Node.js** | `>= 20.0.0` (LTS recommended) | Frontend build tools, Vite server, TypeScript compiler. |
| **npm** | `>= 10.0.0` | Frontend dependency package manager. |
| **Git** | Any modern version | Source control. |
| **Docker & Compose** | Docker v24+, Compose v2+ *(Optional)* | Containerized multi-service deployment. |
| **Redis** | `7.x` *(Optional)* | Distributed caching/locks. *Default development mode runs entirely in-memory with zero Redis required.* |

> [!NOTE]
> All paths in this repository are strictly repo-relative to the root (`D:\MDM_Syniti`). Do not hardcode absolute user paths.

---

## 3. Repository Directory Structure

```
D:\MDM_Syniti/
├── backend/                  # FastAPI backend source code
│   ├── app/
│   │   ├── agents/           # OpenAI agents, prompts, runners, CLI
│   │   ├── api/              # FastAPI routers, models, dependencies, SSE streams
│   │   ├── cache/            # Multi-tier cache backends (memory, redis, disk)
│   │   ├── guardrails/       # AST validation, token limits, SQL parser
│   │   ├── knowledge/        # Vector index retrieval, SAP catalog store
│   │   ├── orchestrator/     # Pipeline orchestrator, progress events, CLI
│   │   └── synthesizer/      # BI synthesis, governance models
│   ├── pyproject.toml        # Backend package configuration
│   ├── requirements.txt      # Production Python dependencies
│   ├── requirements-dev.txt  # Testing & linting dependencies
│   └── tests/                # Full offline test suite (289 tests)
├── frontend/                 # React 18 single-page application
│   ├── src/
│   │   ├── api/              # Fetch client, SSE parser, generated OpenAPI types
│   │   ├── app/              # Router, root layout, Home page
│   │   ├── components/       # Navbar, brand title, error boundaries
│   │   ├── features/         # Projects, Activity, Trial, Configuration, Admin
│   │   └── test/             # Vitest test suite, axe a11y, architecture tests
│   ├── package.json          # Frontend dependencies & scripts
│   └── vite.config.ts        # Vite configuration & backend proxy
├── knowledge/                # Prebuilt SAP catalogs, keywords & glossaries
├── data/                     # Execution activity timeline (activity.jsonl)
├── docs/                     # OpenAPI specifications, runbooks, architecture docs
├── env/                      # Configuration templates (.env.example)
├── scripts/                  # Knowledge validation & eval scripts
├── docker-compose.yml        # Multi-container production stack
└── setup.md                  # This setup documentation
```

---

## 4. Step-by-Step Installation

### Step 1: Environment Configuration (`.env`)

Copy the environment template from `env/.env.example` into a root-level `.env` file:

**Windows PowerShell:**
```powershell
Copy-Item env/.env.example .env
```

**Linux / macOS:**
```bash
cp env/.env.example .env
```

#### Key Settings Overview:

```ini
# LLM / AI Gateway Configuration
OPENAI_API_KEY=your-api-key-here            # Required for live agent execution (tests run offline without this)
LLM_MODEL=gpt-4o-mini                       # Target model (e.g. gpt-4o-mini, gpt-4o, or gateway-deployed model)
OPENAI_BASE_URL=                            # Optional custom AI gateway or enterprise proxy (used verbatim)
LLM_API_MODE=chat_completions               # 'chat_completions' (default for gateway) or 'responses'
STRUCTURED_OUTPUT_MODE=auto                 # 'auto' (auto-detects capabilities), 'native', or 'json_prompt'
TOOLS_MODE=auto                             # 'auto' (auto-detects tool calling), 'native', or 'prefetch'
LLM_CA_BUNDLE=                              # Optional corporate CA bundle path for TLS verification
LLM_EXTRA_HEADERS=                          # Optional JSON object of extra routing/auth headers

# User Principal Configuration
PLACEHOLDER_PRINCIPAL_NAME="Jai Shankar"    # Display name in navbar profile
PLACEHOLDER_PRINCIPAL_EMAIL="jai.shankar@example.com"

# Application Settings
APP_ENV=development                        # 'development' enables hot reload & debug logs
HOST=127.0.0.1                             # Backend binding address
PORT=8000                                  # Backend port
RATE_LIMIT_BACKEND=memory                  # 'memory' (default dev) or 'redis'
CACHE_BACKEND=memory                       # 'memory' (default dev) or 'redis'
DAILY_TOKEN_BUDGET=10000000                # Token budget guardrail
ACTIVITY_DATA_DIR=data                     # Location of activity.jsonl
```

---

### Step 2: Backend Setup (Python)

1. **Create the Python Virtual Environment:**

   **Windows PowerShell:**
   ```powershell
   python -m venv .venv
   ```

   **Linux / macOS:**
   ```bash
   python3 -m venv .venv
   ```

2. **Activate the Virtual Environment:**

   **Windows PowerShell:**
   ```powershell
   .venv\Scripts\Activate.ps1
   ```
   *(If script execution is disabled on Windows, run: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`)*

   **Windows Command Prompt (cmd):**
   ```cmd
   .venv\Scripts\activate.bat
   ```

   **Linux / macOS:**
   ```bash
   source .venv/bin/activate
   ```

3. **Install Dependencies:**
   ```powershell
   python -m pip install --upgrade pip setuptools wheel
   pip install -e backend
   pip install -r backend/requirements-dev.txt
   ```

4. **Set Python Path:**
   Always ensure `backend` is on your `PYTHONPATH` when running backend modules:

   **Windows PowerShell:**
   ```powershell
   $env:PYTHONPATH="backend"
   ```

   **Linux / macOS:**
   ```bash
   export PYTHONPATH="backend"
   ```

5. **Verify Backend Environment:**
   Run the environment diagnostic tool (safe: never leaks API keys):
   ```powershell
   python -m app.agents.cli --check-env
   ```

   *(Optional)* If you have an `OPENAI_API_KEY` configured and wish to test connectivity:
   ```powershell
   python -m app.agents.cli --check-env --ping
   ```

---

### Step 3: Knowledge Base Verification

Verify that all prebuilt SAP table catalogs, field maps, and governance glossaries are valid:

```powershell
python scripts/validate_knowledge.py
```
*Expected output: `INFO: All knowledge files validated successfully.`*

*(Optional)* Pre-generate vector embeddings for FAISS vector search using configured embeddings endpoint:
```powershell
python -m app.knowledge.build_index --target all
```

---

### Step 4: Frontend Setup (Node / React)

1. **Navigate to the frontend directory:**
   ```powershell
   cd frontend
   ```

2. **Install Node Packages:**
   ```powershell
   npm install
   ```

3. **Verify API Contract Freshness:**
   Ensure generated TypeScript schemas match `docs/openapi.json`:
   ```powershell
   npm run check:api
   ```
   *(If the backend schema changes, run `npm run gen:api` to update `src/api/schema.gen.ts` and `src/api/limits.ts`)*

4. **Return to repository root:**
   ```powershell
   cd ..
   ```

---

## 5. Running the Application (Development Mode)

Running the full application locally requires two terminal windows:

### Terminal 1: Backend Server (FastAPI + Uvicorn)

```powershell
# In repository root:
.venv\Scripts\Activate.ps1
$env:PYTHONPATH="backend"
python -m app.api.run
```

* The server starts on **`http://127.0.0.1:8000`** with automatic reload enabled.
* **Interactive Swagger UI:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
* **ReDoc Documentation:** [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)
* **Liveness Health Check:** [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)
* **Readiness Probe:** [http://127.0.0.1:8000/ready](http://127.0.0.1:8000/ready)

### Terminal 2: Frontend Dev Server (Vite)

```powershell
# In repository root:
cd frontend
npm run dev
```

* The Vite server starts on **`http://localhost:5173`**.
* The Vite proxy automatically routes calls to `/api/*`, `/health`, and `/ready` to `http://127.0.0.1:8000`.
* Open your browser and navigate to **`http://localhost:5173`**.

---

## 6. Running with Docker Compose (Containerized Production Stack)

To run the complete production-ready stack (FastAPI Backend, Nginx-served React SPA, and Redis cache):

```bash
# Build and start all services in the background:
docker compose up --build -d

# View container logs:
docker compose logs -f

# Check health of containers:
docker compose ps
```

### Deployed Services:
* **Frontend SPA:** `http://127.0.0.1:8080` (Nginx with Gzip compression and API reverse proxy)
* **Backend API:** `http://127.0.0.1:8000` (FastAPI production ASGI)
* **Redis Cache:** `127.0.0.1:6379` (Protected with password `syniti_dev_secret`)

To stop the containers:
```bash
docker compose down
```

---

## 7. Quality Verification & Testing Gates

All unit and integration tests run **100% offline with zero network calls and no API key required** (using test doubles and isolated storage fixtures).

### Backend Quality Suite

```powershell
# Activate venv and set python path:
.venv\Scripts\Activate.ps1
$env:PYTHONPATH="backend"

# 1. Run all Pytest tests (289 tests):
python -m pytest backend/tests

# 2. Run Ruff lint check:
python -m ruff check backend

# 3. Verify code formatting:
python -m ruff format backend --check

# 4. Run Mypy strict type checking:
python -m mypy backend/app
```

### Frontend Quality Suite

```powershell
cd frontend

# 1. Run Vitest suite (58 unit, integration, and a11y tests):
npm test

# 2. Run ESLint (enforces React hooks, a11y, and react/no-danger = error):
npm run lint

# 3. Strict TypeScript typecheck:
npm run typecheck

# 4. Prettier formatting check:
npm run format:check

# 5. Production build test:
npm run build
```

---

## 8. Application Features & Usage Walkthrough

### 1. Home Page Navigation (`/`)
* **Navbar:**
  * **Brand Title:** Bold violet-to-indigo gradient brand title (*Syniti Governance Tool*).
  * **Breadcrumb:** Active context tracker (`> Projects`).
  * **Validations Tab:** Active navigation tab.
  * **Configuration Dropdown:** Direct access to:
    * **SKP Connections** (`/configuration/skp-connections`): Manage Syniti Knowledge Platform endpoint URLs and connection testing.
    * **Refresh Metadata** (`/configuration/refresh-metadata`): Trigger live metadata extraction and SAP table sync with SSE progress bar.
    * **LLM Settings** (`/configuration/llm-settings`): Real-time read-only diagnostic metrics (model name, API mode, temperature, circuit breaker status, token budgets, zero secret leakage).
  * **Admin Pill:** Static visual badge designating administrative tier.
  * **Profile Avatar:** User initials circle and name (`Jai Shankar`) with dropdown access to the Admin console (`/admin`).

### 2. All Projects Section
* Heading with debounced search filter.
* **+ New Project Button:** Opens accessible modal dialog to create projects (integrated with backend).
* **Clean Empty State:** When no projects exist, displays exact text:
  `No projects yet - Click on New project to add`

### 3. Recent Activity Section
* **Clean Initial State:** Strictly empty upon fresh installation:
  `No Recent Activity yet - execute to see the latest details`
* **Real Execution Tracking:** Pytest and test suites run in isolated temp environments and never pollute `data/activity.jsonl`. When you execute an actual query from the Trial section, the execution is appended to `data/activity.jsonl` and immediately renders as a card featuring:
  * Status badge (`Complete`, `Degraded`, or `Failed`).
  * Run type chip (`TRIAL_STREAM` or `BATCH`).
  * Target SAP table/view name.
  * Human-readable subtitle and execution timestamp.

### 4. Trial SQL Analysis Section
* **SQL Editor:** CodeMirror 6 with Microsoft SQL Server syntax highlighting, line numbers, and character limit counter (`/ 50,000`).
* **Example Queries:** Quick-load predefined real-world SAP validation queries from dropdown.
* **Bypass Cache Toggle:** Force fresh analysis bypassing L1/L2 cache layers.
* **Run Analysis (Ctrl+Enter):** Streams real-time pipeline stages via SSE:
  1. Guardrails validation & SQL parsing (SQLglot AST).
  2. Multi-tier cache inspection.
  3. Concurrent SQL Agent + SAP Agent execution.
  4. Governance synthesis & schema validation.
* **Results Panel:**
  * **Summary Tab:** Business narrative, technical summary, affected SAP entities, and risk factors.
  * **Governance Tab:** Syniti methodology mapping (Subject Area, Business Process, Rule Specification, Enforcement).
  * **Queries Tab:** Side-by-side Opportunity vs. Error queries with subset verification badges.
  * **Run Details Tab:** Model name, prompt versions, cache hit details, agent execution timings, token counts, and cost breakdown.
  * **Raw JSON Tab:** Full validated `BusinessIntelligence` schema output with one-click copy.

---

## 9. Troubleshooting & FAQ

### Q1: The Home Page shows "Cannot reach the server. Check that the backend is running."
* **Cause:** The Vite dev server is running, but the backend is not running on port 8000.
* **Resolution:** Open a second terminal, activate the virtual environment, and start the backend:
  ```powershell
  .venv\Scripts\Activate.ps1
  $env:PYTHONPATH="backend"
  python -m app.api.run
  ```
  Then click **Retry** on the frontend banner.

### Q2: How do I test with an alternative LLM (e.g. `astra-luna` or local model)?
* **Resolution:** Edit `.env` without modifying any Python code:
  ```ini
  LLM_MODEL=astra-luna
  OPENAI_BASE_URL=https://your-custom-llm-host/v1
  LLM_API_MODE=native
  STRUCTURED_OUTPUT_MODE=native
  ```
  Test model readiness using the diagnostic probe:
  ```powershell
  python -m app.agents.cli --check-model
  ```

### Q3: Why is Recent Activity empty?
* **Resolution:** As per `docs/PROJECT_RULES.md`, the platform ships zero canned, fake, or mock data. Recent Activity is populated exclusively when real queries are analyzed. Run any query in the **Trial SQL** section, and it will immediately appear under Recent Activity.

### Q4: How do I reset or clear the Activity history?
* **Resolution:** To return to a completely clean state:
  ```powershell
  # Windows PowerShell:
  Clear-Content -Path "data/activity.jsonl"
  ```

### Q5: Windows PowerShell says `Activate.ps1 cannot be loaded because running scripts is disabled`.
* **Resolution:** Run the following command in PowerShell before activating:
  ```powershell
  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
  ```

