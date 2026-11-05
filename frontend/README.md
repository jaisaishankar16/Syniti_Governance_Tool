# MDM Syniti Frontend

A production-grade React 18 single-page application for the Syniti Governance & Data Migration platform, built strictly according to `docs/PROJECT_RULES.md` and Phase 6 specifications.

---

## 1. Quickstart & PowerShell Commands

All commands assume a PowerShell terminal in the repository root or the `frontend` directory.

```powershell
# Navigate to frontend directory
cd frontend

# Install dependencies (Node 20+)
npm install

# Generate TypeScript types & limits from docs/openapi.json
npm run gen:api

# Verify schema freshness against docs/openapi.json
npm run check:api

# Start local Vite development server (proxies /api to http://127.0.0.1:8000)
npm run dev

# Run full Vitest suite (45 unit, integration, a11y, and architecture tests)
npm test

# Run ESLint (enforces react/no-danger = error, hooks, a11y)
npm run lint

# Run TypeScript strict type-check
npm run typecheck

# Verify Prettier formatting
npm run format:check

# Production build (type-checked, minified, split chunks)
npm run build

# Preview production build locally
npm run preview

# Run E2E smoke verification against running backend
npm run e2e
```

---

## 2. Environment Variables

Configure via `.env` or system environment variables:

| Variable | Default | Purpose |
| :--- | :--- | :--- |
| `VITE_API_BASE_URL` | `""` (same origin) | Base URL for API requests. When empty, uses relative origin so Vite proxy or Nginx proxies traffic seamlessly. |
| `VITE_API_PROXY_TARGET` | `http://127.0.0.1:8000` | Target backend host used by Vite's development proxy for `/api`, `/health`, and `/ready`. |
| `VITE_API_DOCS_URL` | *None* (optional) | When set, renders the "API reference" external link in the Navbar Configuration dropdown. |

---

## 3. Visual Parity Checklist

The design matches the reference application specification down to exact design tokens, typography, and layout structure:

- [x] **Page Background**: `#F8F9FB` applied globally to body and root layout container.
- [x] **Card Styling**: White background (`#FFFFFF`), 1px `#E5E7EB` border, 12px border radius (`rounded-xl`), and subtle elevation shadow (`shadow-xs`).
- [x] **Color Tokens & Brand Gradient**:
  - Primary Gradient: `linear-gradient(to right, #6D3FE0, #3B82F6)` applied to the main page title, "Analyze" button, and "View Project >" buttons.
  - "Active" Pill: `#6D4AE8` with solid white text (`bg-[#6D4AE8] text-white`).
  - Success Indicator: Green check tick (`#22C55E`) enclosed in a light-green circular badge (`bg-green-100`).
  - Status Badges: "Complete" in light green bg (`#DCFCE7`) + dark green text (`#15803D`); "Running" in amber; "Failed" in red; run-type chips in neutral gray.
  - Neutral Chips: Light gray background (`#F3F4F6`), `#374151` text.
- [x] **Typography & Hierarchy**:
  - Font Family: Self-hosted Inter variable font via `@fontsource-variable/inter` (no third-party CDN dependencies).
  - Headings: `#111827`, font-bold / font-extrabold.
  - Secondary Text: `#6B7280`.
  - Monospace Elements: Font-mono for SQL queries, token counts, request IDs, and chunk IDs.
- [x] **Icons**: High-clarity SVG icons from `lucide-react`.
- [x] **Accessibility & Motion**:
  - Focus Rings: Highly visible focus rings on all interactive elements (`focus-visible:ring-2 focus-visible:ring-[#6D3FE0] focus-visible:outline-none`).
  - Contrast: WCAG AA compliant text/background contrast ratios across all states.
  - Reduced Motion: `@media (prefers-reduced-motion: reduce)` removes animations, pulses, and transitions.
  - Screen Readers: Stepper updates announced via `aria-live="polite"`.
- [x] **Navbar**:
  - Sticky white header with `#E5E7EB` bottom border.
  - Left: Home icon + "Syniti Governance Tool" brand, followed by chevron separator and violet "Projects" breadcrumb.
  - Right: "Validations" tab with active violet underline (navigates to `/`), "Configuration" dropdown (triggering the live LLM Status modal dialog and API Reference link when configured), "Admin" link (navigates to `/admin`), and the authenticated user chip (initials circle + email from `GET /api/v1/me`).
  - Mobile: Accessible hamburger drawer with matching keyboard focus management.
- [x] **Home Page Sections (Order strictly maintained)**:
  1. **Header**: "Data Migration Projects" with violet-to-blue gradient title + subtitle "Manage your data migration and validation projects".
  2. **My Favorites Strip**: Star icon + heading + exact empty text *"No favorites yet — click a star beside a project, release, milestone, or data object to save one."* Persisted in `localStorage` under versioned key `syniti_favorites_v1`, validated with Zod, corrupted storage auto-repaired, ignores API `favorite_default` to preserve exact empty state.
  3. **All Projects**: Heading with debounced search input on the right; responsive grid (1, 2, 3 columns); cards with title, line-clamped description, star toggle (`aria-pressed`), "active" pill, green checkmark, releases/milestones counts, gradient "View Project >" button (routes to `/projects/:id`). Includes skeleton loaders, empty match state, and retryable error state.
  4. **Recent Activity**: Activity icon + subtitle; 2-column grid populated by `GET /api/v1/activity`; cards with status chip (Complete/Running/Failed), neutral run-type chip, table icon + table name, gray subtitle, and "View >" link (routes to `/activity/:id`).
  5. **Trial Section**:
     - Card description: *"Paste an MS SQL query over SAP tables. The analysis is produced by AI agents on the server."*
     - LLM status banner polled every 30s: amber warning if not configured, circuit open, or quota/auth errors; disables Analyze in those states.
     - Toolbar: "Load an example query" dropdown (`GET /api/v1/examples`), Options popover with "Bypass cache" checkbox, and character counter (`n / 50,000`).
     - Editor: CodeMirror 6 with MSSQL syntax highlighting, line numbers, monospace, resizable, min-height 260px, lazy-loaded with skeleton. Queries are never persisted to storage.
     - Buttons: Analyze (gradient, loading spinner, `Ctrl/Cmd+Enter` shortcut), Cancel (aborts in-flight stream), Clear.
     - Progress Stepper: Driven **only** by real SSE events (Checking input, Parsing SQL, Checking cache, SQL + SAP Agents, Synthesizing, Validating, Done) with elapsed timings.
     - Results Panel: Sticky badge strip (Cache status, query type, SAP module, Risk level with color+icon+text, Degraded badge with reasons tooltip), 5 Radix keyboard-accessible tabs:
       - **Summary**: Business narrative, technical summary, affected entity chips, risk items & recommendations, final learning.
       - **Governance**: Subject Area, Business Process, Dataset, Concept, Rule, Specification, Enforcement, Implementation, Opportunity Definition, Error Definition with "New" badges.
       - **Queries**: Opportunity and Error side-by-side read-only SQL editors with Copy button, validation badges, subset check badge, baseline benchmark badge.
       - **Run Details**: Model, prompt versions, cache resolution per agent, similarity score, stage and agent timing breakdowns, token usage, estimated cost, request ID copy button, and guardrail reports.
       - **Raw JSON**: Pretty-printed JSON with Copy and Download buttons.
     - Plain Text Rendering: All model-authored text rendered with `whitespace-pre-wrap` as literal text. `react/no-danger` is configured as a build error in ESLint.
- [x] **Route Handling**:
  - `/` Home Page.
  - `/admin` Admin Console (lazy-loaded chunk, in-memory `X-Admin-Key` session, cache eviction modal, LLM ping, learned SAP knowledge approval, glossary proposals).
  - `/projects/:id` and `/activity/:id`: `ScopeNotice` page explaining the view is outside the current build with a back link.
  - `*`: Custom 404 page.
  - App-wide Error Boundary displaying failure recovery with a Reload button.

---

## 4. Test Suite Summary

The test suite contains **45 automated tests** across **10 test files**, achieving 100% pass rate:

```
Test Files  10 passed (10)
Tests       45 passed (45)
Duration    ~6.5s
```

| Suite | File | Tests | Coverage Scope |
| :--- | :--- | :---: | :--- |
| **API Schemas** | `src/api/schemas.test.ts` | 4 | Zod runtime validations & TypeScript compile-time type agreement tests against generated OpenAPI types (`schema.gen.ts`). |
| **SSE Parser** | `src/api/sse.test.ts` | 9 | Chunk splits mid-event/mid-line, CRLF/LF normalization, heartbeat lines, multi-line data, error envelopes, done event, unknown event discard, malformed JSON recovery, abort controller signals, 45s idle timeout. |
| **API Client** | `src/api/client.test.ts` | 6 | `X-Request-ID` extraction, contract validation throwing `ContractError` on schema mismatch, error envelope mapping to `ApiError`, `Retry-After` header extraction, network timeout handling (130s analyze / 15s GETs), GET retry twice on network error only (POST never retried). |
| **Architecture** | `src/test/architecture.test.ts` | 2 | Verifies production source code (`src/` excluding `src/test/`) never imports test doubles, fixtures, or MSW, and never contains fake/mock identifiers. Asserts `react/no-danger: error` in ESLint config. |
| **Projects & Favs** | `src/features/projects/projects.test.tsx` | 5 | Exact empty favorites string matching design, toggling star updates `localStorage` (`syniti_favorites_v1`), corrupted storage self-healing, debounced project search filtering, API `favorite_default` ignored, error retry button. |
| **Navbar** | `src/components/Navbar.test.tsx` | 4 | Brand and violet breadcrumb, user chip initials + email from `/api/v1/me`, configuration dropdown opening LLM status modal, API docs link visibility. |
| **Trial Flow** | `src/features/trial/trialFlow.test.tsx` | 3 | Full analysis cycle via real SSE stream: example query loading, pipeline progress events, result tab population across all 5 tabs, XSS protection rendering malicious HTML/scripts strictly as literal text, served-from-cache announcement. |
| **Error UX** | `src/features/trial/errors.test.tsx` | 5 | Complete error matrix coverage (`LLM_NOT_CONFIGURED`, `LLM_AUTH_FAILED`, `LLM_QUOTA_EXCEEDED`, `LLM_RATE_LIMITED`, `RATE_LIMITED`, `CIRCUIT_BREAKER_OPEN`, `SQL_PARSE_ERROR`, etc.), offline banner when LLM unconfigured or circuit open, line/column parse indicator extraction, countdown timer enabling retry. |
| **Admin Console** | `src/features/admin/admin.test.tsx` | 4 | In-memory key gating, 401 handling, 404 when admin API disabled, cache stats rendering, cache purge confirmation dialog, learned knowledge approve/reject flow. |
| **Accessibility** | `src/test/a11y.test.tsx` | 3 | Automated WCAG accessibility audit via `jest-axe`, tab keyboard navigation via Arrow keys, ARIA roles, focus rings, screen reader alerts. |

---

## 5. Production Build & Bundle Breakdown

Built with Vite 5 and TypeScript strict compilation:

```
dist/index.html                                   0.62 kB │ gzip:   0.37 kB
dist/assets/index-n1CW3Qi1.css                   35.75 kB │ gzip:   6.93 kB
dist/assets/SqlEditor-7gGV1fTX.js                 1.01 kB │ gzip:   0.64 kB
dist/assets/AdminPage-BiesKU6l.js                20.66 kB │ gzip:   5.69 kB
dist/assets/icons-CdcjdzNs.js                    22.90 kB │ gzip:   6.28 kB
dist/assets/radix-ByZy7Oqm.js                   243.06 kB │ gzip:  78.41 kB
dist/assets/index-d2jwz7io.js                   248.20 kB │ gzip:  67.37 kB
dist/assets/codemirror-oYvD9dRt.js              444.99 kB │ gzip: 146.60 kB
```

### Chunking Strategy
- **CodeMirror SQL Editor (`codemirror-*.js`)**: Split into an isolated on-demand chunk lazy-loaded only when the Trial section or Query tabs mount.
- **Admin Console (`AdminPage-*.js`)**: Split into a lazy-loaded route chunk only loaded if the user visits `/admin`.
- **Radix UI Primitives (`radix-*.js`)**: Isolated chunk for tabs, popovers, dialogs, and tooltips.
- **Icons (`icons-*.js`)**: Shared `lucide-react` iconography.
- **Main App (`index-*.js`)**: React 18, React Router v6, TanStack Query v5, and Zod runtime validator.

---

## 6. Contract Differences: Prompt vs. Real Schemas

Inspected against `docs/openapi.json`, `backend/app/api/models.py`, `backend/app/api/errors.py`, `backend/app/orchestrator/progress.py`, and `backend/app/synthesizer/models.py`. In accordance with the system prompt, where the prompt and the real schema disagreed, the **real schema won**:

1. **Progress Event Stages**:
   - *Prompt*: Stated stages as "Checking input, Parsing SQL, Checking cache, SQL Agent + SAP Agent, Synthesizing, Validating, Done".
   - *Backend Schema (`backend/app/orchestrator/progress.py`)*: Uses machine keys: `"guardrails"`, `"parsing"`, `"cache_lookup"`, `"agents_running"`, `"synthesizing"`, `"validating"`, `"done"`, and `"error"`.
   - *Resolution*: The UI uses the real backend stage enum values for state transitions and maps them to human-readable labels matching the prompt requirements in the stepper view.
2. **Activity Item Status Casing**:
   - *Prompt*: Displayed title case `"Complete"`, `"Running"`, `"Failed"`.
   - *OpenAPI Schema (`ActivityItem.status`)*: Lowercase enum: `"complete" | "running" | "failed"`.
   - *Resolution*: Schema validation checks against lowercase; UI renders capitalized labels and badge chips.
3. **Error Response Envelope Definition**:
   - *Prompt*: Required parsing `ErrorEnvelope` (`{ error: { code, message, request_id, retry_after_s, details } }`).
   - *OpenAPI Schema (`docs/openapi.json`)*: The OpenAPI document exposes default FastAPI `HTTPValidationError` on several routes while the custom middleware and exceptions return `ErrorEnvelope`.
   - *Resolution*: Zod schemas and API client support both the structured `ErrorEnvelope` and standard error formats, with fallback handling for unparsed error payloads.
4. **Rate Limit Error Codes**:
   - *Prompt*: Listed `CLIENT_RATE_LIMITED`.
   - *Backend Code (`backend/app/api/errors.py`)*: Emits `RATE_LIMITED`, `LLM_RATE_LIMITED`, and `LLM_BUDGET_EXCEEDED`.
   - *Resolution*: `ERROR_CODE_MAP` in `errorMessages.ts` includes mappings for all backend codes (`RATE_LIMITED`, `LLM_RATE_LIMITED`, `LLM_BUDGET_EXCEEDED`) as well as `CLIENT_RATE_LIMITED`.
5. **Admin Cache Endpoints**:
   - *Prompt & OpenAPI*: `docs/openapi.json` defines `/api/v1/cache/stats` and `/api/v1/cache` (DELETE).
   - *Backend Code*: Also provides unlisted route aliases `/api/v1/admin/cache/stats` and `/api/v1/admin/cache`.
   - *Resolution*: The UI targets the canonical endpoints defined in OpenAPI (`/api/v1/cache/stats` and `/api/v1/cache`).
6. **Optional Backend Fields with Default Factories**:
   - In Pydantic models (`backend/app/synthesizer/models.py`, `backend/app/api/models.py`), fields such as `BusinessIntelligenceMeta.agent_timings_ms`, `token_usage`, `guardrails`, `Principal.roles`, and `Principal.initials` have default factories (`default_factory=list`, `default_factory=dict`). In `openapi.json`, these appear as optional fields.
   - *Resolution*: Zod schemas use `.optional().default(...)` so that TypeScript types agree bidirectionally with both the strict generated schema (`z.input<Schema>`) and the runtime output shape (`z.output<Schema>`).

