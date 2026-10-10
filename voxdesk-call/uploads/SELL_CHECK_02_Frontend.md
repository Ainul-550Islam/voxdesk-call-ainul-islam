# SELL CHECK 2 of 4 — Frontend check and test

Copy the whole block below with one click and paste it into the coding agent. It contains the mission, the check rules, the file tree with a `# comment` beside every file, the exact commands and the pass criteria.

```text
# ======================================================================================================================
# SELL CHECK 2 of 4 — FRONTEND CHECK AND TEST   (measurement prompt: run it, record the truth, do not fix product code)
# Series: VoxDesk x Retell sell-ready gap closure | repo root: voxdesk-call/ | companion to SELL PROMPT 1-10
# PREREQUISITE: CHECK 1 done (reports/check/routes.csv exists; without it the API-mismatch step is SKIPPED and recorded
#   as such).
# MISSION: Establish the truth about both frontends - dashboard/ (Vite, the SHIPPED UI) and dashboard-next/ (Next.js,
#   roadmap/shadow): install, typecheck, unit tests, build, audit, route inventory, frontend-vs-backend API mismatches and
#   a headless browser smoke. Record the BEFORE numbers of the generated-tail cleanup.
# RUN ORDER: CHECK 1 (backend) -> CHECK 2 (frontend) -> CHECK 3 (docker run) -> CHECK 4 (other + summary) -> then SELL
#   PROMPT 1. After EVERY SELL PROMPT re-run `make check-all` and compare with the baseline in reports/check/SUMMARY.md:
#   any new failure is a regression the prompt must fix before it is accepted.
# CHECK RULES (apply to every step)
#   C1  This is a MEASUREMENT prompt. Do NOT change product code, existing tests, migrations, compose files or docs
#       to make anything pass. Add only the harness files marked [NEW]/[MODIFY] in the tree.
#   C2  Record every failure verbatim: command, exit code, last 40 output lines. Never write "environment issue"
#       without proof (show the missing tool, key, port or RAM).
#   C3  Triage every failure as PRODUCT (real defect) | ENV (missing tool/key/port/RAM) | FLAKY (passes on rerun:
#       say how many reruns) | SKIPPED (not run: say why). SKIPPED is never counted as passed.
#   C4  No real phone calls, charges, bookings or CRM mutations. Tests marked real_provider run ONLY if
#       VOXDESK_REAL_INTEGRATION=1 AND the operator supplied credentials; otherwise report SKIPPED.
#   C5  Be memory-safe. The ~4,048-test suite was killed (exit 137 / -9) in a previous environment: run in chunks,
#       one process per chunk, retry an OOM-killed chunk with half the size and record the event.
#   C6  Never print or commit secrets. Generated env files are git-ignored and written with mode 0600; reports show
#       variable NAMES only.
#   C7  Every number in a report comes from a command you ran in THIS session; paste the command next to the number.
#       No estimates, no "should pass".
#   C8  Output the COMPLETE content of every script/test you create. Never write "...", "rest unchanged" or "omitted
#       for brevity".
#   C9  Write reports/check/<AREA>.md (human) and reports/check/<AREA>.json (a list of {step, command, exit_code,
#       duration_s, status, counts, class}). status = PASS | FAIL | SKIPPED; class = PRODUCT | ENV | FLAKY | "".
#   C10 Tags in the tree: [NEW] create | [MODIFY] change only what is described | [KEEP] existing file: run/read it,
#       do not edit | [VERIFY] read-only inspection, record findings.
# COMMENT FORMAT: # [TAG] kind — what the file contains / what to do with it. Commands below are plain lines; lines
#   starting with # are explanations.
# ======================================================================================================================

voxdesk-call/
├── .github/
│   └── workflows/
│       ├── ci.yml                      # [VERIFY] ci — read-only: frontend job runs `npm ci`, `npm test`, `npm run build`
│       │                               #   and `npm audit --omit=dev --audit-level=high` in dashboard/ only; record that tsc,
│       │                               #   e2e and dashboard-next are not covered there (dashboard-next is covered by
│       │                               #   polyglot.yml)
│       └── polyglot.yml                # [VERIFY] ci — read-only: dashboard-next job = npm ci, npm test, npx tsc --noEmit,
│                                       #   npm run build. Record drift versus this prompt
├── dashboard/
│   ├── e2e/
│   │   └── smoke.spec.ts               # [NEW] test — loads / and every PUBLIC route from frontend_routes.json; asserts
│   │                                   #   HTTP 200, a non-empty main landmark or h1, zero uncaught page errors and zero
│   │                                   #   console.error; API calls are intercepted with a fixed mock so the test needs no
│   │                                   #   backend
│   ├── src/
│   │   └── app/
│   │       ├── app.tsx                 # [KEEP] module — imports of the routed pages: read it, do not edit
│   │       └── router.tsx              # [KEEP] module — route table of the shipped dashboard: read it, do not edit;
│   │                                   #   frontend_inventory.mjs parses it
│   ├── package.json                    # [MODIFY] config — add devDependency @playwright/test and the script `e2e` =
│   │                                   #   "playwright test"; change nothing else (existing scripts: dev, build, preview,
│   │                                   #   test, test:watch)
│   └── playwright.config.ts            # [NEW] config — Playwright (chromium, headless): baseURL http://127.0.0.1:4173,
│                                       #   webServer = `npm run build && npm run preview -- --port 4173`, retries 1,
│                                       #   screenshots on failure into reports/check/screens/
├── dashboard-next/
│   └── lib/
│       └── api.ts                      # [KEEP] module — API client of the Next.js dashboard: read it, do not edit; its
│                                       #   endpoints are checked against the backend route inventory
├── docs/
│   └── DEPLOYMENT.md                   # [VERIFY] doc — section 10 documents an accepted dev-only Vitest advisory: when
│                                       #   reading npm audit output, record whether the advisory list changed
├── scripts/
│   ├── check_frontend.sh               # [NEW] script — runs steps 1-7 for dashboard/ (Vite, SHIPPED) and dashboard-next/
│   │                                   #   (Next.js, roadmap/shadow, not shipped) and records exit codes and durations; never
│   │                                   #   stops at the first failure; writes reports/check/frontend.json + frontend.md
│   ├── frontend_api_contract_check.py  # [NEW] script — extracts "/api/..." and other backend path literals from
│   │                                   #   dashboard/src/api/*.ts, hooks, and dashboard-next/lib/api.ts; compares them with
│   │                                   #   reports/check/routes.csv from CHECK 1; reports frontend calls that have no backend
│   │                                   #   route and calls that are served only by template-clone routes
│   ├── frontend_inventory.mjs          # [NEW] script — node script: parses dashboard/src/app/router.tsx + app.tsx and
│   │                                   #   lists dashboard-next/app/**/page.tsx; writes reports/check/frontend_routes.json
│   │                                   #   (path, component file, code-line count, generated-tail count, routed? flag) and
│   │                                   #   flags pages whose real code is < 40 lines (stubs) and pages that exist but are not
│   │                                   #   routed
│   └── generated_tail_scan.py          # [NEW] script — read-only counter for the BEFORE state: files with >=15 numbered
│                                       #   definitions sharing a stem (`<stem>_real_N`, `<STEM>_CONST_N`), total generated
│                                       #   lines, number of `verified: true, real: true` literals; must reproduce the audit
│                                       #   numbers (84 files / 50,425 generated lines) and report any difference
└── Makefile                            # [MODIFY] config — add ONE target `check-frontend` that runs
                                        #   scripts/check_frontend.sh; keep the others unchanged

# ======================================================================================================================
# COMMANDS — run in this order from the repo root; record exit code + seconds for each
# ======================================================================================================================
# --- STEP 1: shipped dashboard (Vite + React) ---
node --version                                      # CI uses Node 20
cd dashboard
npm ci
npm test                                            # vitest run: record files / tests / failures
npx tsc --noEmit                                    # NOT in CI today; record errors (a previous log shows TS5058 "path does not exist" from another machine)
npm run build                                       # record seconds, dist size, 5 largest chunks
npm audit --omit=dev --audit-level=high             # CI parity
cd ..

# --- STEP 2: Next.js dashboard (roadmap, not shipped in docker-compose.prod.yml) ---
cd dashboard-next && npm ci && npm test && npx tsc --noEmit && npm run build && cd ..   # polyglot.yml parity; record each result separately

# --- STEP 3: BEFORE numbers for the generated-tail cleanup (SELL PROMPT 1) ---
python scripts/generated_tail_scan.py               # expect 84 files / 50,425 generated lines; record any difference
grep -rn "verified: true, real: true" dashboard/src | wc -l

# --- STEP 4: route inventory ---
node scripts/frontend_inventory.mjs                 # routed vs unrouted pages, stub pages (< 40 real lines), generated-tail counts per page

# --- STEP 5: frontend calls vs backend routes (needs CHECK 1 output) ---
python scripts/frontend_api_contract_check.py --routes reports/check/routes.csv

# --- STEP 6: headless smoke of the SHIPPED dashboard ---
cd dashboard && npx playwright install --with-deps chromium && npm run e2e && cd ..

# --- STEP 7: report ---
bash scripts/check_frontend.sh                      # re-runs everything above, writes reports/check/frontend.md + frontend.json

# ======================================================================================================================
# PASS CRITERIA AND REPORT
# ======================================================================================================================
# PASS WHEN (record the real value for each, pass or fail): both frontends install | vitest counts recorded for each |
#   tsc result recorded for each (errors listed) | both build (dist/.next size recorded) | npm audit result recorded |
#   generated-tail numbers recorded and compared with the audit (84 files / 50,425 lines) | route inventory written |
#   frontend-vs-backend mismatches listed | Playwright smoke passes for every public route (failures triaged per C3).
# REPORT reports/check/FRONTEND_CHECK.md: environment, step table, per-frontend test counts, build sizes, tsc errors,
#   audit output, route inventory summary (routed / unrouted / stub pages), API mismatch table, e2e result with
#   screenshots path.

# NEXT: CHECK 3 - DOCKER RUN CHECK (SELL_CHECK_03_Docker_Run.md).
```
