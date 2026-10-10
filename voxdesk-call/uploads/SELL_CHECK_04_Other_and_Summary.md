# SELL CHECK 4 of 4 — Other checks and summary

Copy the whole block below with one click and paste it into the coding agent. It contains the mission, the check rules, the file tree with a `# comment` beside every file, the exact commands and the pass criteria.

```text
# ======================================================================================================================
# SELL CHECK 4 of 4 — OTHER CHECKS AND SUMMARY   (measurement prompt: run it, record the truth, do not fix product code)
# Series: VoxDesk x Retell sell-ready gap closure | repo root: voxdesk-call/ | companion to SELL PROMPT 1-10
# PREREQUISITE: CHECK 1-3 done (CHECK 3 may be SKIPPED only if Docker is unavailable - say so).
# MISSION: Everything else that can be wrong: Rust/Go/C++ services (including the Rust SFU that CI does not test),
#   protobuf contracts, security scans, observability and Helm config, SDK, production launch gate, load-test smoke,
#   repository hygiene and the opt-in real-provider suite. Then aggregate all four checks into ONE summary with the
#   failure backlog and the BEFORE numbers.
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
│       ├── check.yml              # [NEW] ci — manual (workflow_dispatch) + weekly: runs scripts/check_all.sh on
│       │                          #   ubuntu-latest with Docker, uploads reports/check as an artifact; does NOT replace
│       │                          #   ci.yml, polyglot.yml or security-scan.yml
│       ├── polyglot.yml           # [VERIFY] ci — read-only: jobs = contracts, media-plane, control-plane, signal-go,
│       │                          #   ops-go, dashboard-next. services/realtime/media-engine-rs has NO job (the Rust SFU is
│       │                          #   untested in CI): record this gap
│       ├── real-integrations.yml  # [VERIFY] ci — read-only: manual `pytest -m real_provider` with 13 provider credentials
│       │                          #   behind a GitHub environment; record the provider list
│       └── security-scan.yml      # [VERIFY] ci — read-only: sast, dependency-audit, secret-scan (gitleaks), weekly cron.
│                                  #   Record what it does not cover (images, Rust, Go)
├── infra/
│   └── helm                       # [KEEP] dir — Helm chart(s): `helm lint infra/helm/*` and, if kubeconform exists, `helm
│                                  #   template` piped to kubeconform -strict
├── loadtest/
│   ├── locustfile.py              # [KEEP] script — used read-only in step 7 against the CHECK 3 stack only
│   └── safety.py                  # [KEEP] module — guard: loopback targets always allowed, remote targets require
│                                  #   LOADTEST_ALLOW_REMOTE=1. Read it first; never set that variable in this prompt
├── observability/
│   ├── alerts.yml                 # [KEEP] config — Prometheus alert rules: `promtool check rules observability/alerts.yml`
│   │                              #   (SKIPPED if promtool missing)
│   └── prometheus.yml             # [KEEP] config — Prometheus config: `promtool check config observability/prometheus.yml`
├── reports/
│   └── check/
│       └── SUMMARY.md             # [NEW] doc — generated one-page truth, committed: per-area table, failure backlog,
│                                  #   BEFORE numbers; the baseline every SELL PROMPT is compared with
├── scripts/
│   ├── audit_dependencies.sh      # [KEEP] script — existing dependency audit: run and record
│   ├── baseline_numbers.py        # [NEW] script — writes reports/check/baseline_numbers.json and prints a table: routes
│   │                              #   (real vs template-clone, from routes.csv), clone modules, `Padding ... line N` lines,
│   │                              #   code lines of the three filler engine dirs, generated-tail files/lines in
│   │                              #   dashboard/src, code lines by area (app, tests, alembic, scripts, dashboard,
│   │                              #   dashboard-next, services) split REAL vs FAKE, tests collected, alembic heads, image
│   │                              #   sizes if present. This is the measured version of the readiness meter: re-run it after
│   │                              #   every SELL PROMPT
│   ├── check_all.sh               # [NEW] script — runs check_backend.sh, check_frontend.sh, check_docker.sh (only if
│   │                              #   Docker is available) and check_other.sh in that order; ALWAYS runs check_summary.py at
│   │                              #   the end, even if a step crashed
│   ├── check_other.sh             # [NEW] script — runs steps 1-10 below; every external tool is optional: a missing tool
│   │                              #   is recorded as SKIPPED(tool missing: <install hint>), never silently omitted; writes
│   │                              #   reports/check/other.json + other.md
│   ├── check_summary.py           # [NEW] script — aggregates reports/check/{backend,frontend,docker,other}.json into
│   │                              #   reports/check/SUMMARY.md: per area counts (PASS / FAIL-PRODUCT / FAIL-ENV / FLAKY /
│   │                              #   SKIPPED), failure backlog table (id, area, first error line, repro command), BEFORE
│   │                              #   numbers, blockers list; when a previous SUMMARY exists it also prints a regression diff
│   │                              #   (new failures, fixed failures)
│   ├── ops_preflight.py           # [KEEP] script — existing preflight: run read-only and record
│   ├── release_gate.py            # [KEEP] script — existing production launch gate: `python scripts/release_gate.py
│   │                              #   --json` (no network, no calls); record the verdict and every failing gate
│   ├── verify_cost_config.py      # [KEEP] script — existing cost-config verification: run and record
│   ├── verify_egress.py           # [KEEP] script — staging-only egress verification: read its guard; run only where it
│   │                              #   allows and record SKIPPED otherwise
│   ├── verify_observability.py    # [KEEP] script — existing observability verification: run read-only and record
│   └── verify_tls.py              # [KEEP] script — TLS verification: read its usage; run against the CHECK 3 caddy only if
│                                  #   a local tls-internal config exists, else SKIPPED
├── sdk/
│   └── client.py                  # [KEEP] sdk — Python SDK: `python -c "import sdk.client"` and record its public surface
│                                  #   (functions/classes)
├── services/
│   ├── realtime/
│   │   ├── gateway-go             # [KEEP] dir — Go 1.27 gateway: `go vet ./... && go test -race -count=1 ./...` (CI
│   │   │                          #   parity); record test counts
│   │   └── media-engine-rs        # [KEEP] dir — Rust SFU workspace (DTLS/SRTP/ICE crates, Cargo.lock, vendored dimpl):
│   │                              #   `cargo test --workspace --locked`, `cargo clippy`; NOT in CI today. Record the
│   │                              #   toolchain version used and per-crate test counts
│   ├── control-plane              # [KEEP] dir — Rust workspace (CI toolchain 1.90.0): `cargo fmt --all -- --check`, `cargo
│   │                              #   check --workspace`, `cargo test --workspace`, `cargo clippy --workspace --all-targets
│   │                              #   --all-features -- -D warnings`
│   ├── media-plane                # [KEEP] dir — C++17 (g++ -std=c++17 -Werror): `make test` builds and runs media_tests
│   │                              #   and audio_tests
│   ├── ops                        # [KEEP] dir — Go 1.27 ops tools: mirror the exact commands of the ops-go job in
│   │                              #   polyglot.yml
│   └── signal-go                  # [KEEP] dir — Go 1.27 signaling hub: mirror the exact commands of the signal-go job in
│                                  #   polyglot.yml
├── tests/
│   └── test_real_providers.py     # [KEEP] test — the real_provider suite (13 providers): run ONLY with
│                                  #   VOXDESK_REAL_INTEGRATION=1 and operator credentials; record per provider PASS / FAIL /
│                                  #   SKIPPED(no credential)
└── Makefile                       # [MODIFY] config — add `check-other` and `check-all` (calls scripts/check_all.sh); keep
                                   #   every other target unchanged

# ======================================================================================================================
# COMMANDS — run in this order from the repo root; record exit code + seconds for each
# ======================================================================================================================
# --- STEP 1: toolchains (record versions; missing = SKIPPED with install hint) ---
go version                                          # go.mod requires 1.27: if older, install 1.27 (GOTOOLCHAIN=auto) and record
rustc --version && cargo --version                  # CI uses 1.90.0 for control-plane
g++ --version && make --version && protoc --version
gitleaks version; promtool --version; helm version; trivy --version; hadolint --version    # optional tools

# --- STEP 2: polyglot services (mirror .github/workflows/ci.yml and polyglot.yml exactly) ---
(cd services/realtime/gateway-go && go vet ./... && go test -race -count=1 ./...)
(cd services/signal-go && go vet ./... && go test -race -count=1 ./...)
(cd services/ops && go vet ./... && go test -count=1 ./...)
(cd services/control-plane && cargo fmt --all -- --check && cargo check --workspace && cargo test --workspace && cargo clippy --workspace --all-targets --all-features -- -D warnings)
(cd services/realtime/media-engine-rs && cargo test --workspace --locked)        # NOT in CI: record test counts per crate
(cd services/media-plane && make test)
python scripts/verify_contracts.py && python -m pytest tests/test_contracts.py -q

# --- STEP 3: security ---
gitleaks detect --no-banner --redact                # working tree AND history (history includes the commit with 755 null-byte files)
bash scripts/audit_dependencies.sh
(cd dashboard && npm audit --omit=dev --audit-level=high); (cd dashboard-next && npm audit --omit=dev --audit-level=high)
cargo audit; govulncheck ./...                      # per Rust/Go workspace; SKIPPED if the tools are missing
python scripts/verify_egress.py; python scripts/verify_tls.py   # only where their guards allow

# --- STEP 4: observability + deployment config ---
promtool check rules observability/alerts.yml && promtool check config observability/prometheus.yml
python scripts/verify_observability.py && python scripts/verify_cost_config.py && python scripts/ops_preflight.py
helm lint infra/helm/*

# --- STEP 5: SDK ---
python -c "import sdk.client; print(sorted(n for n in dir(sdk.client) if not n.startswith('_')))"

# --- STEP 6: production launch gate (no network, no calls) ---
python scripts/release_gate.py --json | tee reports/check/release_gate.json

# --- STEP 7: load-test smoke against the CHECK 3 stack ONLY (loopback; safety.py enforces it) ---
locust -f loadtest/locustfile.py --headless -u 5 -r 1 -t 30s --host http://127.0.0.1:8000     # record requests, failures, p95; never set LOADTEST_ALLOW_REMOTE

# --- STEP 8: repository hygiene ---
git status --porcelain | wc -l; git count-objects -vH
grep -rIl $'\x00' --exclude-dir=.git --exclude-dir=node_modules . | head                 # null-byte files (the earlier corruption incident)
find . -path ./node_modules -prune -o -type f -size +5M -print | head -20                  # large files

# --- STEP 9: opt-in real providers (ONLY with operator credentials) ---
VOXDESK_REAL_INTEGRATION=1 python -m pytest -q -m real_provider                            # otherwise record all 13 providers as SKIPPED(no credential)

# --- STEP 10: BEFORE numbers + the one-page summary ---
python scripts/baseline_numbers.py                  # measured real-vs-fake code, clones, padding, generated tails, tests, heads
bash scripts/check_other.sh && python scripts/check_summary.py   # or simply: make check-all

# ======================================================================================================================
# PASS CRITERIA AND REPORT
# ======================================================================================================================
# PASS WHEN (record the real value for each, pass or fail): every service workspace test run recorded with counts
#   (media-engine-rs included) | security tools recorded (gitleaks findings listed with file + rule, redacted) |
#   observability/Helm checks recorded | release_gate verdict recorded with every failing gate | hygiene counts recorded |
#   baseline_numbers.json written | reports/check/SUMMARY.md written with the failure backlog and the blockers list.
# BLOCKER RULE: SELL PROMPT 1 may start once reports/check/SUMMARY.md exists. Failing PRODUCT tests do NOT block it
#   (they become the tracked backlog); an unusable ENV does (fix the environment, not the product, and re-run).
# REPORT reports/check/SUMMARY.md: (1) per-area table; (2) failure backlog; (3) BEFORE numbers; (4) blockers; (5) tools
#   missing and install hints; (6) the exact command to re-run everything: `make check-all`.

# NEXT: SELL PROMPT 1 of 10 - PART 0: LIABILITY REMOVAL & TRUTH RESET (SELL_PROMPT_01_...md), only after
#   reports/check/SUMMARY.md exists.
```
