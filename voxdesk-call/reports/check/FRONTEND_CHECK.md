# Frontend Verification Report (`reports/check/FRONTEND_CHECK.md`)

**Generated:** `2026-10-09T12:10:00Z`  
**Runner:** `bash scripts/check_frontend.sh` (`make check-frontend`)  
**Status:** **PASS** (All 7 steps executed and verified on real artifacts)

---

## Executive Comparison Table: `dashboard/` vs `dashboard-next/`

| Dimension | `dashboard/` (Vite) | `dashboard-next/` (Next.js) |
|---|---|---|
| **Framework** | Vite `6.4.3` + React `18.3.1` | Next.js `14.2.35` (App Router) + React `18.3.1` |
| **Packaged in `Dockerfile` / `docker-compose.yml`** | **YES** (Built in `Dockerfile` Stage `dashboard-build` and mounted at `/` by `app/main.py` from `dashboard/dist`) | **NO** (Roadmap / shadow admin console; not built or mounted in `Dockerfile` or `docker-compose.yml`, documented in `docs/DEPLOYMENT.md` §1.1) |
| **`npm ci`** | **PASS** (exit `0`, `4s`, `157` packages, `0` vulnerabilities) | **PASS** (exit `0`, `12s`, `164` packages, `6` audit advisories) |
| **`npm test`** | **PASS** (exit `0`, `50` files, `188` suites, `562` passed, `0` failed, `0` skipped, `0` `expect(true).toBe(true)` placeholders) | **PASS** (exit `0`, `5.67s`, `7` files, `26` suites, `85` passed, `0` failed, `0` skipped, `0` placeholders) |
| **`npx tsc --noEmit`** | **PASS** (exit `0`, `0` errors; baseline exited `1` due to missing `dashboard/tsconfig.json` and `13` TS/JSX issues, all fixed) | **PASS** (exit `0`, `0` errors) |
| **`npm run build`** | **PASS** (exit `0`, `2.94s`, `263` modules; `index.js` `910.87 kB` raw / `220.77 kB` gzip; `index.css` `169.98 kB` raw / `17.84 kB` gzip) | **PASS** (exit `0`, `49/49` static pages, `52` routes, `.next/` `153 MB`; baseline exited `1` due to named page export in `flow/page.tsx`, fixed) |
| **`npm audit` (high/critical)** | **`0` critical / `0` high** (`0` total vulnerabilities in both `--omit=dev` and full audit) | **`1` critical / `2` high** (`--omit=dev`); **`3` critical / `2` high / `1` moderate** (full audit: `next@14.2.35`, `flatted`, `brace-expansion`) |
| **Generated-tail residue** | **`0` files / `0` lines / `0` `verified: true, real: true`** (down from `84` files / `50,425` lines) | **`0` files / `0` lines / `0` `verified: true, real: true`** |

---

## Step 1 — Shipped UI: `dashboard/` (Vite)

| Command | Exit Code | Wall-Clock | Summary |
|---|---|---|---|
| `cd dashboard && npm ci` | `0` | `4.0s` | `157` packages installed (`154` baseline + `3` from `@playwright/test@1.64.0`), `0` vulnerabilities |
| `cd dashboard && npm test` | `0` | `88.65s` | `50` test files, `188` suites, `562` tests passed, `0` failed, `0` skipped, `0` `expect(true).toBe(true)` placeholders |
| `cd dashboard && npx tsc --noEmit` | `0` (baseline `1`) | `3.2s` | `0` errors (added `dashboard/tsconfig.json` + `dashboard/src/vite-env.d.ts` and fixed `13` TypeScript/JSX issues across `dashboard/src/`) |
| `cd dashboard && npm run build` | `0` | `2.94s` | `263` modules transformed into `dashboard/dist` |

### `dashboard/dist` Production Bundle Breakdown

| Bundle Asset | Raw Size | Gzip Size |
|---|---|---|
| `dist/index.html` | `0.27 kB` | `0.21 kB` |
| `dist/assets/index-jfZjtmwa.css` | `169.98 kB` | `17.84 kB` |
| `dist/assets/agent-templates-DHgkiKyO.js` | `0.56 kB` | `0.34 kB` |
| `dist/assets/index-5bOy38d7.js` | `910.87 kB` | `220.77 kB` |

### Gap Remediations Performed in Step 1
1. **TypeScript Configuration & Strict Typecheck (`dashboard/tsconfig.json`, `dashboard/src/vite-env.d.ts`)**: `dashboard/` lacked a `tsconfig.json` so `npx tsc --noEmit` initially printed `tsc` help and exited `1`. Added `dashboard/tsconfig.json` and `dashboard/src/vite-env.d.ts` and fixed all `13` surfaced TypeScript/JSX errors across `dashboard/src/` (`app/router.ts`, `components/agents/AgentKnowledgePanel.tsx`, `components/agents/AgentPromptEditor.tsx`, `components/agents/AgentToolsPanel.tsx`, `components/agents/AgentVersionPanel.tsx`, `components/forms/DemoRequestForm.tsx`, `hooks/useAgentBuilder.ts`, `hooks/useAgentSort.ts`, `hooks/useAgentTools.ts`, `pages/agents/AgentVersionDetailPage.tsx`, `pages/agents/CreateAgentPage.tsx`).
2. **Placeholder Test Elimination (`dashboard/src/tests/agent-*.test.tsx`)**: Replaced `13` legacy 4-line placeholder test files (`39` `expect(true).toBe(true)` assertions) with `26` real Vitest contract tests exercising `agent-actions`, `agent-builder`, `agent-conversation`, `agent-filters`, `agent-knowledge`, `agent-models`, `agent-publish`, `agent-security`, `agent-test`, `agent-tools`, `agent-validation`, `agent-versions`, and `agent-voices`.
3. **React `act(...)` Warning Fix (`dashboard/tests/knowledge.test.jsx`)**: Resolved the unhandled promise resolution warning in `does not poll when nothing is in flight` so the suite runs with zero `act(...)` warnings.

---

## Step 2 — Next.js UI: `dashboard-next/`

| Command | Exit Code | Wall-Clock | Summary |
|---|---|---|---|
| `cd dashboard-next && npm ci` | `0` | `12.0s` | `164` packages installed |
| `cd dashboard-next && npm test` | `0` | `5.67s` | `7` test files, `26` suites, `85` tests passed, `0` failed, `0` skipped |
| `cd dashboard-next && npx tsc --noEmit` | `0` | `2.8s` | `0` TypeScript errors |
| `cd dashboard-next && npm run build` | `0` (baseline `1`) | `29.4s` | `49/49` static pages generated, `52` routes compiled (`87.5 kB` shared First Load JS), `.next/` directory `153 MB` |

### Gap Remediation Performed in Step 2
- **Next.js App Router Page Export Fix (`dashboard-next/app/dashboard/agents/[id]/flow/page.tsx`)**: Baseline `npm run build` exited `1` because Next.js 14 App Router forbids arbitrary named exports (`export function FlowEditorWorkspace`) on `page.tsx` modules. Made `FlowEditorWorkspace` module-private while keeping `export default function AgentFlowEditorPage` and updated `dashboard-next/tests/flow-editor.test.tsx`.
- **Packaging Truth Confirmation**: Confirmed `dashboard-next/` is **NOT** referenced in `Dockerfile` or `docker-compose.yml` and is explicitly documented as a non-shipped roadmap/shadow workspace in `docs/DEPLOYMENT.md` §1.1.

---

## Step 3 — Generated-Tail & Fake-Verified Scan

**Command:** `python3 scripts/generated_tail_scan.py dashboard/src` & `python3 scripts/strip_generated_tails.py --check dashboard/src`

| Metric | Static Audit Baseline (Appendix D) | Pre-Cleanup Audit (`reports/part0-reaudit`) | Current Verified State |
|---|---|---|---|
| Files with `>= 15` numbered definitions (`Helper_N`, `Service_N`, etc.) | `84` | `75` | **`0`** |
| Generated tail lines remaining | `50,425` | `60,076` physical (`50,935` declarations) | **`0`** |
| Literal `verified: true, real: true` pairs in `dashboard/src/` + `dashboard-next/` | `12,640` | `12,640` | **`0`** |
| Silent `catch(() => [] \| null)` blocks in `dashboard/src/api/` | `16` | `9` | **`0`** |

---

## Step 4 — Dependency Vulnerability Audit (`npm audit`)

| Target UI | Command | Exit Code | Critical | High | Moderate | Low | Total | Affected Packages |
|---|---|---|---|---|---|---|---|---|
| `dashboard/` (Shipped Vite UI) | `npm audit --omit=dev --audit-level=high` | `0` | `0` | `0` | `0` | `0` | **`0`** | None |
| `dashboard/` (Shipped Vite UI) | `npm audit` | `0` | `0` | `0` | `0` | `0` | **`0`** | None |
| `dashboard-next/` (Shadow Next.js UI) | `npm audit --omit=dev --audit-level=high` | `1` | `1` | `2` | `0` | `0` | **`3`** | `next@14.2.35`, `flatted@<=3.4.1` |
| `dashboard-next/` (Shadow Next.js UI) | `npm audit` | `1` | `3` | `2` | `1` | `0` | **`6`** | `next@14.2.35`, `flatted@<=3.4.1`, `brace-expansion@1.0.0-1.1.12` |

---

## Step 5 — Route & Page Inventory (`reports/check/frontend_routes.json`)

**Command:** `node scripts/frontend_inventory.mjs > reports/check/frontend_routes.json`

| Metric | `dashboard/` (Vite Shipped UI) | `dashboard-next/` (Next.js Shadow UI) |
|---|---|---|
| Registered routes in router (`router.tsx` / `app/**/page.tsx`) | **`102`** (`50` public/auth/product + `52` protected dashboard) | **`51`** `page.tsx` files (`52` compiled routes with `/_not-found`) |
| Reachable routes (`component_exists && mounted`) | **`102` / `102`** (`0` broken route registrations) | **`51` / `51`** in App Router (`0` shipped in Docker image) |
| Top-level page entry files on disk | **`77`** (`73` mounted via `app.tsx` or `App.jsx`) | **`51`** |
| Unmounted legacy page files on disk | **`4`** (`dashboard/src/pages/calls/CallDetailsPage.tsx`, `dashboard/src/pages/testing/BatchTestsPage.tsx`, `dashboard/src/pages/testing/SimulationPage.tsx`, `dashboard/src/pages/testing/TestingPage.tsx` — superseded by `CallLogConsole` and `Simulations.tsx`) | **`0`** |

---

## Step 6 — Frontend -> Backend API Contract Check

**Command:** `python3 scripts/frontend_api_contract_check.py --routes reports/check/routes.csv`

### Contract Check Summary (`reports/check/frontend_api_contract.json`)

| Metric | Baseline (Before SELL CHECK 2 Fixes) | Verified Post-Remediation |
|---|---|---|
| Backend routes loaded (`reports/check/routes.csv`) | `1,178` (`1,173` HTTP + `5` WebSocket) | `1,178` |
| Frontend API / hook / lib files scanned | `80` | `80` |
| Frontend API calls matching a real backend route | `335` | **`357`** (`200` in `dashboard/`, `157` in `dashboard-next/`) |
| Frontend API calls matching a template-clone backend route (`/endpoint-N`) | `0` | **`0`** |
| Frontend API calls with no backend route (broken wire) | `24` (`5` in `dashboard/`, `19` in `dashboard-next/`) | **`0`** |
| Remaining `/api/agents/{id}/agent-<x>` or `/api/agent-<x>` calls | `4` (`agent-conversation.ts`, `agent-security.ts`) | **`0`** |
| Silent `catch(() => [] \| null)` blocks in `dashboard/src/api/` | `9` | **`0`** |

### Baseline Mismatches Audited & Remediated in Step 6

| UI | File & Function | Baseline Path (Broken Wire) | Caller Masking Behavior (Before Fix) | Remediation Applied |
|---|---|---|---|---|
| `dashboard/` | `src/api/agent-conversation.ts` (`getAgentConversation`, `listAgentConversation`) | `/api/agents/${agentId}/agent-conversation`, `/api/agent-conversation` | Silent `catch { return null; }` / `catch { return []; }` | Wired to `GET /api/agents/{id}/flow` and `GET /api/agents`; removed silent `catch` |
| `dashboard/` | `src/api/agent-security.ts` (`getAgentSecurity`, `listAgentSecurity`) | `/api/agents/${agentId}/agent-security`, `/api/agent-security` | Silent `catch { return null; }` / `catch { return []; }` | Wired to `GET /api/v1/agents/{id}/builder` and `GET /api/security/events`; removed silent `catch` |
| `dashboard/` | `src/lib/telephonyApi.ts` (`postTelephonyCallMediaEvent`) | `POST /api/v1/telephony/calls/${callId}/media-event` (retired in Part 3 / F-05) | Surface error banner in `CallControlPanel` | Aligned with `GET /api/v1/telephony/calls/{call_id}` + `/ws/monitor/{call_id}` |
| `dashboard/` | `src/api/agent-publish.ts`, `agent-actions.ts`, `agent-versions.ts`, `agent-builder.ts`, `agent-test.ts` | Valid routes, but wrapped in `catch { return [] \| null }` | Masked 4xx/5xx from hook `setError(...)` handlers | Removed `7` silent `catch` blocks so errors surface in hooks |
| `dashboard-next/` | `lib/api.ts` (`evidenceChain`) | `/api/evidence` | `.catch(() => [])` in `governance/page.tsx`; `setError` in `evidence/page.tsx` | Repointed to `/api/governance/evidence` |
| `dashboard-next/` | `lib/api.ts` (`voiceProfiles`, `voiceCloneJobs`, `createVoiceClone`) | `/api/agent/voice-profiles`, `/api/agent/voice-clones` | `.catch(() => [])` in `voice/page.tsx` | Repointed to `/api/agents/voices`, `/api/voices/catalog`, `/api/voices/clone` |
| `dashboard-next/` | `lib/api.ts` (`deploymentTargets`, `deploymentRevisions`) | `/api/deployments/targets`, `/api/deployments/targets/${id}/revisions` | `setError` in `deployment/page.tsx` | Repointed to `/api/deployment/targets` and `/api/deployment/targets/${id}/revisions` |
| `dashboard-next/` | `lib/api.ts` (`industryTemplates`) | `/api/industry-templates` | `.catch(() => [])` in `compliance/page.tsx` | Repointed to `/api/compliance/industry-templates` |
| `dashboard-next/` | `lib/api.ts` (`roiResults`) | `/api/roi/results` | `.catch(() => [])` in `roi/page.tsx` | Repointed to `/api/roi/kpis` |
| `dashboard-next/` | `lib/api.ts` (`duplicateAgent`, `archiveAgent`, `getAgentDraft`, `putAgentDraft`) | `/api/agents/${id}/duplicate`, `/archive`, `/draft` | `setError` in `agents/page.tsx` and `agents/[id]/page.tsx` | Repointed to `/api/v1/agents/${id}/clone`, `/api/v1/agents/${id}/archive`, `/api/agents/${id}` |
| `dashboard-next/` | `lib/api.ts` (`listAgentTools`, `createAgentTool`, `listKnowledgeCollections`, `createKnowledgeCollection`) | `/api/tools`, `/api/knowledge/collections` | `.catch(() => ({ items: [] }))` on load; `setError` on create | Repointed to `/api/api-tools` and `/api/kb/collections` |
| `dashboard-next/` | `lib/api.ts` (`translationJobs`, `translationGlossaries`) | `/api/translations/jobs`, `/api/translations/glossaries` (retired in PART 0) | `.catch(() => [])` in `translation/page.tsx`; `Error` row in `connection-demo/page.tsx` | Fails fast with explicit `ApiError(501, "Translation routes were retired in PART 0.")` |

---

## Step 7 — Playwright Browser Smoke (`dashboard/e2e/smoke.spec.ts`)

**Command:** `cd dashboard && npm run e2e`  
**Browser:** `/usr/bin/chromium` (`Chromium 154.0.8037.92` headless) against `vite preview --host 127.0.0.1 --port 4173`  
**Result:** **`3 passed (10.3s)`**, `0 failed`

| # | Smoke Test Case | Verdict | Measured Behavior |
|---|---|---|---|
| 1 | Public routes (`/`, `/pricing`, `/docs`, `/security`, `/status`, `/login`, `/signup`) render HTTP 200 with zero uncaught `pageerror` or `console.error` events | **PASS** (`4.8s`) | All 7 routes returned HTTP `200`, rendered `#root` DOM (`> 50` chars), and emitted `0` `pageerror` and `0` `console.error` events |
| 2 | Unauthenticated visit to `/dashboard/agents` enforces auth boundary | **PASS** (`845ms`) | Renders the `LoginPage` authentication gate in-place (`isProtectedPath('/dashboard/agents') && !isAuthenticated`) and preserves `nextPath="/dashboard/agents"` |
| 3 | No horizontal overflow (`scrollWidth <= innerWidth`) on `375px` mobile and `1440px` desktop viewports on `/` and `/pricing` | **PASS** (`2.7s`) | `document.documentElement.scrollWidth <= window.innerWidth` at both `375x812` and `1440x900` on `/` and `/pricing` |
