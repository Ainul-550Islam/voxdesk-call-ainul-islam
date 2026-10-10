# SELL CHECK 1 of 4 — Backend check and test

Copy the whole block below with one click and paste it into the coding agent. It contains the mission, the check rules, the file tree with a `# comment` beside every file, the exact commands and the pass criteria.

```text
# ======================================================================================================================
# SELL CHECK 1 of 4 — BACKEND CHECK AND TEST   (measurement prompt: run it, record the truth, do not fix product code)
# Series: VoxDesk x Retell sell-ready gap closure | repo root: voxdesk-call/ | companion to SELL PROMPT 1-10
# PREREQUISITE: None. Run this BEFORE SELL PROMPT 1 and re-run it after every SELL PROMPT (regression gate).
# MISSION: Establish the truth about the Python backend in a clean environment: it installs, imports, boots, migrates
#   (SQLite in tests, real Postgres for the migration round trip), passes lint and runs its ~4,048-test suite in
#   memory-safe chunks. Record real numbers and a triaged failure backlog; these are the BEFORE numbers for SELL PROMPT 1.
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
│       └── ci.yml                # [VERIFY] ci — read-only: backend job = postgres:16-alpine service; pip install -r
│                                 #   requirements.txt + pytest pytest-asyncio aiosqlite httpx ruff; `ruff check app scripts
│                                 #   tests`; alembic upgrade head -> downgrade base -> upgrade head; `pytest -q -m "not
│                                 #   real_provider"`. Record any drift between CI and this prompt
├── alembic/
│   └── versions/                 # [KEEP] dir — shipped state: single head 0045_request_idempotency_receipts. Verify
│                                 #   `alembic heads` prints exactly one head, the upgrade/downgrade round trip works on real
│                                 #   Postgres, and record the `alembic check` (model drift) output
├── reports/
│   └── check/
│       └── .gitkeep              # [NEW] config — keeps the output directory in git
├── scripts/
│   ├── audit_except_handlers.py  # [KEEP] script — existing audit of swallowed exceptions; run read-only and record the
│   │                             #   finding count
│   ├── audit_missing_files.py    # [KEEP] script — existing audit that created stub files in a previous run: run it ONLY on
│   │                             #   a clean tree, compare `git status` before and after, revert anything it creates and
│   │                             #   record that as a finding
│   ├── boot_probe.py             # [NEW] script — starts uvicorn as a subprocess with a minimal development environment
│   │                             #   (SQLite file DB, no providers), polls /health, /health/ready, /health/dependencies and
│   │                             #   /openapi.json (status, bytes, seconds), measures startup seconds and peak RSS (psutil),
│   │                             #   then terminates the process cleanly
│   ├── check_backend.sh          # [NEW] script — orchestrates steps 1-7 below in order; never stops at the first failure
│   │                             #   (runs with `set +e`, traps errors, ALWAYS writes the report); writes
│   │                             #   reports/check/backend.json + backend.md; exit code = number of PRODUCT failures (capped
│   │                             #   at 125)
│   ├── entrypoint.sh             # [KEEP] script — container entrypoint (migrate, then uvicorn): read for the exact startup
│   │                             #   sequence; exercised in CHECK 3
│   ├── migrate.py                # [KEEP] script — advisory-lock wrapper around `alembic upgrade head` used by
│   │                             #   scripts/entrypoint.sh: run it against the throwaway Postgres
│   ├── pytest_chunks.py          # [NEW] script — memory-safe chunk runner: discovers test files via `pytest --collect-only
│   │                             #   -q`, groups them into chunks (--size N files), runs each chunk as `python -m pytest -q
│   │                             #   -p no:cacheprovider --junitxml=reports/check/junit/<chunk>.xml` in a FRESH subprocess
│   │                             #   with a timeout; retries an OOM-killed chunk (exit 137 / -9) with half the size and
│   │                             #   records the event; reruns failed tests once to classify FLAKY vs PRODUCT; aggregates
│   │                             #   junit xml into reports/check/pytest_summary.json (collected / passed / failed / error /
│   │                             #   skipped / xfail + per-file table)
│   ├── route_inventory.py        # [NEW] script — imports app.main and dumps every route (method, path, endpoint module +
│   │                             #   function, tags) to reports/check/routes.csv; flags /endpoint-N paths and modules
│   │                             #   containing banner strings ("NO SKIP FULL CODE", "1050+ lines"); prints totals by
│   │                             #   top-level path prefix and real-vs-clone counts (BEFORE numbers for SELL PROMPT 1);
│   │                             #   read-only
│   ├── verify_contracts.py       # [KEEP] script — existing `make contracts-check`: compiles protobuf contracts (needs
│   │                             #   protoc: apt-get install protobuf-compiler) and cross-checks enums/tenancy; run and
│   │                             #   record the output
│   └── verify_dependencies.py    # [KEEP] script — existing `make deps-check`: every pinned dependency imports and
│                                 #   version-matches; run and record the output
├── tests/
│   ├── smoke/
│   │   ├── __init__.py           # [NEW] package — test package (repo convention: every tests/<dir>/ has __init__.py)
│   │   └── test_app_boot.py      # [NEW] test — imports app.main, builds the OpenAPI schema, asserts operationIds are
│   │                             #   unique (RECORDS duplicates instead of failing BEFORE the cleanup), /health returns
│   │                             #   {"status":"ok"}, writes the route count to reports/check/boot_numbers.json; must not
│   │                             #   modify any existing test
│   └── conftest.py               # [VERIFY] test — tests run on in-memory SQLite (sqlite+aiosqlite,
│                                 #   Base.metadata.create_all). List every test that needs real Postgres semantics (FOR
│                                 #   UPDATE SKIP LOCKED, advisory locks, RLS, JSONB operators): SQLite cannot prove them.
│                                 #   Record the list; do not change the fixture in this prompt
├── .gitignore                    # [MODIFY] config — ignore reports/check/*.json, *.xml, *.csv, *.log, *.txt and
│                                 #   reports/check/junit/, but keep reports/check/.gitkeep and the *.md reports
├── Makefile                      # [MODIFY] config — add ONE target `check-backend` that runs scripts/check_backend.sh;
│                                 #   keep every existing target (up, down, logs, migrate, revision, seed, test, lint,
│                                 #   deps-check, contracts-check, dev, worker, clean) unchanged
└── pytest.ini                    # [VERIFY] config — markers today: unit, integration, real_provider (needs
                                  #   VOXDESK_REAL_INTEGRATION=1); asyncio_mode = auto. Record how many tests carry each
                                  #   marker

# ======================================================================================================================
# COMMANDS — run in this order from the repo root; record exit code + seconds for each
# ======================================================================================================================
# --- STEP 1: environment (Python 3.12, same as Dockerfile and CI) ---
python3 --version                                   # expect 3.12.x; record
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt                     # record duration, failures, resolver conflicts (pipecat-ai[...]==0.0.94 pulls onnxruntime etc.)
pip install pytest pytest-asyncio aiosqlite httpx ruff pytest-timeout psutil coverage   # CI set + harness helpers
pip check                                           # dependency conflicts
python scripts/verify_dependencies.py               # = make deps-check

# --- STEP 2: static checks ---
python -m compileall -q app scripts tests
ruff check app scripts tests                        # CI parity; record the finding count per rule
python scripts/verify_contracts.py                  # = make contracts-check
python scripts/audit_except_handlers.py

# --- STEP 3: import + boot ---
python -c "from app.main import app; print('routes:', len(app.routes))"   # record; expect thousands before PART 0 (clone routes)
python scripts/route_inventory.py                   # writes reports/check/routes.csv; record real vs clone route counts
python scripts/boot_probe.py                        # startup seconds, peak RSS, /health, /health/ready, /health/dependencies, /openapi.json size

# --- STEP 4: migrations on REAL Postgres 16 (CI parity) ---
docker run -d --name vox-pg -e POSTGRES_USER=voxdesk -e POSTGRES_PASSWORD=voxdesk-ci -e POSTGRES_DB=voxdesk_ci -p 5432:5432 postgres:16-alpine
export DATABASE_URL=postgresql+asyncpg://voxdesk:voxdesk-ci@localhost:5432/voxdesk_ci
alembic heads                                       # exactly ONE head
alembic upgrade head && alembic downgrade base && alembic upgrade head
alembic check                                       # model-vs-migration drift; record the diff if any
python scripts/migrate.py                           # the advisory-lock wrapper the container uses
docker rm -f vox-pg

# --- STEP 5: tests (memory-safe) ---
python -m pytest --collect-only -q | tail -5        # expect ~4,048 items; ANY collection error is a PRODUCT finding
python scripts/pytest_chunks.py --size 120 --timeout 300 --junit reports/check/junit
python -m pytest -q -m "not real_provider"          # CI parity: run only if RAM >= 8 GB, otherwise rely on the chunk runner and say so
for i in 1 2 3 4 5; do python -m pytest -q tests/campaign/test_concurrency.py || break; done   # claim-exactly-once guard: flakiness check
VOXDESK_REAL_INTEGRATION=1 python -m pytest -q -m real_provider   # ONLY if the operator supplied credentials; otherwise record SKIPPED
coverage run -m pytest -q -m "not real_provider" -x --maxfail=50 && coverage report --skip-empty > reports/check/coverage.txt   # optional, only if memory allows

# --- STEP 6: BEFORE numbers for SELL PROMPT 1 ---
grep -rEn "Padding .* line [0-9]+" app services | wc -l            # padding marker lines
grep -l '"/endpoint-0"' app/api/*.py | wc -l                       # template-clone modules (expect 95)

# --- STEP 7: report ---
bash scripts/check_backend.sh                       # the orchestrator re-runs everything above and writes reports/check/backend.md + backend.json

# ======================================================================================================================
# PASS CRITERIA AND REPORT
# ======================================================================================================================
# PASS WHEN (record the real value for each, pass or fail): pip install + pip check clean | verify_dependencies passes |
#   compileall passes | ruff finding count recorded (0 = clean) | app imports and /health answers | alembic heads = 1 and
#   the Postgres round trip succeeds | tests: collected / passed / failed / error / skipped / flaky recorded, every
#   failure triaged (C3), OOM events recorded | BEFORE numbers recorded.
# REPORT reports/check/BACKEND_CHECK.md: (1) environment versions; (2) step table (step | command | exit | seconds |
#   status | class); (3) test summary + per-file table; (4) failure backlog (test id | class | first error line |
#   reproduction command); (5) SQLite-only-unprovable test list; (6) BEFORE numbers; (7) open questions.

# NEXT: CHECK 2 - FRONTEND CHECK AND TEST (SELL_CHECK_02_Frontend.md).
```
