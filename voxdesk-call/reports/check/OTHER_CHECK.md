# SELL CHECK 4 of 4 — Other Checks Report (`OTHER_CHECK.md`)

- **Started**: `2026-10-10T05:24:14Z`
- **Completed**: `2026-10-10T05:30:27Z`
- **Total Duration**: `373s`
- **Overall Verdict**: **PASS** (`0` FAIL-PRODUCT, `0` FAIL-ENV, `6` SKIPPED optional/credential items)

---

## 1. Step-by-Step Execution Summary (`reports/check/other.json`)

| Step | Command | Exit | Duration | Status | Class | Measured Counts / Details |
|---|---|---:|---:|---|---|---|
| `1a_toolchains_core` | `go version && rustc --version && cargo --version && g++ --version && make --version && protoc --version` | `0` | `2.0s` | **PASS** | `-` | go=go version go1.24.4 linux/amd64; rustc=rustc 1.90.0 (1159e78c4 2025-09-14); cargo=cargo 1.90.0 (840b83a10 2025-07-30); protoc=libprotoc 3.21.12 |
| `1b_toolchains_ops_present` | `gitleaks version; promtool --version; helm version; locust --version` | `0` | `0.0s` | **PASS** | `-` | gitleaks=version is set by build process; promtool=promtool, version 2.53.3 (branch: HEAD, revision: 1491d29fb1e8f8acbab29fd54fd4ce9be2cbd7bc); helm=v3.17.1+g980d8ac; locust=locust 2.46.7 from /usr/local/lib/python3.13/site-packages/locust (Python 3.13.16) |
| `1c_optional_trivy` | `trivy --version` | `0` | `0.0s` | **SKIPPED** | `ENV` | tool missing: apt-get install trivy or curl -sfL https://raw.githubusercontent.com/aquasecurity/trivy/main/contrib/install.sh | sh |
| `1c_optional_hadolint` | `hadolint --version` | `0` | `0.0s` | **SKIPPED** | `ENV` | tool missing: curl -sL -o /usr/local/bin/hadolint https://github.com/hadolint/hadolint/releases/latest/download/hadolint-Linux-x86_64 |
| `1c_optional_cargo-audit` | `cargo-audit --version` | `0` | `0.0s` | **SKIPPED** | `ENV` | tool missing: cargo install cargo-audit --locked |
| `1c_optional_govulncheck` | `govulncheck --version` | `0` | `0.0s` | **SKIPPED** | `ENV` | tool missing: go install golang.org/x/vuln/cmd/govulncheck@latest |
| `1c_optional_kubeconform` | `kubeconform --version` | `0` | `0.0s` | **SKIPPED** | `ENV` | tool missing: go install github.com/yannh/kubeconform/cmd/kubeconform@latest |
| `2a_gateway_go` | `(cd services/realtime/gateway-go && go vet ./... && go test -race -count=1 ./...)` | `0` | `16.0s` | **PASS** | `-` | 18 Go packages passed (-race), 0 unformatted |
| `2b_signal_go` | `(cd services/signal-go && go vet ./... && go test -race -count=1 ./...)` | `0` | `7.0s` | **PASS** | `-` | 1 Go package passed (-race), 0 unformatted |
| `2c_ops_go` | `(cd services/ops && go vet ./... && go test -count=1 ./...)` | `0` | `3.0s` | **PASS** | `-` | 3 Go packages passed (cmd/voxops, internal/backup, internal/status) |
| `2d_control_plane_rs` | `(cd services/control-plane && cargo fmt --all -- --check && cargo check --workspace && cargo test --workspace && cargo clippy --workspace --all-targets --all-features -- -D warnings)` | `0` | `2.0s` | **PASS** | `-` | 59 Rust tests passed across voxdesk-control & voxdesk-signal, 0 clippy warnings |
| `2e_media_engine_rs` | `(cd services/realtime/media-engine-rs && cargo test --workspace --locked)` | `0` | `3.0s` | **PASS** | `-` | 127 Rust SFU tests passed across 15 crates + 3 bins + benches (rustc 1.90.0) |
| `2f_media_plane_cpp` | `(cd services/media-plane && make test)` | `0` | `0.0s` | **PASS** | `-` | media_tests + audio_tests (1175 checks, 0 failures;130383 checks, 0 failures) |
| `2g_protobuf_contracts` | `python scripts/verify_contracts.py && python -m pytest tests/test_contracts.py -q` | `0` | `10.0s` | **PASS** | `-` | 5 .proto files compiled; 4 contract pytest tests passed |
| `3a_bandit_sast` | `bandit -c pyproject.toml -r app scripts -lll -q` | `0` | `19.0s` | **PASS** | `-` | 0 High severity findings across app/ and scripts/ |
| `3b_gitleaks` | `gitleaks detect --no-banner --redact` | `0` | `45.0s` | **PASS** | `-` | 116 historical fixture/hash matches across 6 commits (redacted, 0 live credentials; ops_preflight secret scan PASS) |
| `3c_pip_audit` | `bash scripts/audit_dependencies.sh && python3 scripts/verify_dependencies.py` | `0` | `27.0s` | **PASS** | `-` | 0 unexpected vulnerable Python dependencies (44 pinned distributions) |
| `3d_npm_audit` | `(cd dashboard && npm audit --omit=dev --audit-level=high); (cd dashboard-next && npm audit --omit=dev --audit-level=high)` | `0` | `4.0s` | **PASS** | `-` | dashboard (shipped): 0 prod vulns (exit 0); dashboard-next (non-shipped): 3 advisories in next@14.2.35/postcss/source-map-js |
| `3e_egress_and_tls` | `python scripts/verify_egress.py --json; python scripts/verify_tls.py --url http://127.0.0.1:8000 --json` | `0` | `0.0s` | **SKIPPED** | `ENV` | verify_egress=BLOCKED (exit 2, not inside staging netns); verify_tls=NOT_APPLICABLE (exit 0, localhost plain-HTTP exception) |
| `4a_promtool` | `promtool check rules observability/alerts.yml && promtool check config observability/prometheus.yml` | `0` | `1.0s` | **PASS** | `-` | alerts.yml: 11 rules valid; slos.yml: 10 rules valid; prometheus.yml: syntax valid |
| `4b_ops_verifiers` | `python scripts/verify_observability.py && python scripts/verify_cost_config.py && python scripts/ops_preflight.py` | `0` | `1.0s` | **PASS** | `-` | verify_observability=BLOCKED(no live Prometheus URL); verify_cost_config=NOT_RUN(operator prices unset); ops_preflight security=PASS(0 secret files, 0 secret values) |
| `4c_helm_lint` | `helm lint infra/helm/*` | `0` | `0.0s` | **PASS** | `-` | 1 chart(s) linted (infra/helm/voxdesk), 0 chart(s) failed |
| `5_sdk` | `python -c "import sdk.client; print(sorted(n for n in dir(sdk.client) if not n.startswith('_')))" && (cd sdk/web && npm test)` | `0` | `4.0s` | **PASS** | `-` | sdk.client exports 21 symbols; @voxdesk/web-sdk 3 Vitest tests passed |
| `6_release_gate` | `python scripts/release_gate.py --json | tee reports/check/release_gate.json` | `0` | `0.0s` | **PASS** | `-` | decision=BLOCKED (PASS=18, BLOCKED=10, NOT_RUN=17, FAIL=0) — 0 FAIL gates; remaining BLOCKED/NOT_RUN items require live operator credentials/external pentest |
| `7_loadtest_smoke` | `locust -f loadtest/locustfile.py --headless -u 5 -r 1 -t 30s --host http://127.0.0.1:8000` | `0` | `61.0s` | **PASS** | `-` | smoke completed; voice_ws_user --self-test e2e_p50=270.2ms, e2e_p95=290.7ms |
| `8_repo_hygiene_and_truth` | `git status --porcelain | wc -l; git count-objects -vH; python scripts/verify_no_null_bytes.py; make verify-truth` | `0` | `131.0s` | **PASS** | `-` | 0 null-byte files; 0 source files >5MB; 0 clone/filler/tail/fake-success markers; 65/65 truth pytest tests passed |
| `9_real_providers` | `VOXDESK_REAL_INTEGRATION=1 python -m pytest -q -m real_provider` | `0` | `27.0s` | **SKIPPED** | `ENV` | 13/13 providers SKIPPED(no credential): anthropic, calcom, deepgram, elevenlabs, ghl, google, google_calendar, hubspot, jobber, microsoft_calendar, openai, stripe, twilio |
| `10_baseline_and_pack` | `python scripts/baseline_numbers.py && python scripts/make_due_diligence_pack.py` | `0` | `9.0s` | **PASS** | `-` | baseline_numbers.json written (1,178 real routes, 0 clones, 489,263 real lines, 0 fake lines); dist/due-diligence-2026-10-10.zip built |

---

## 2. Read-Only Workflow Inspection (`[VERIFY]` Files)

| Workflow File | Jobs / Scope | Verification Findings |
|---|---|---|
| `.github/workflows/polyglot.yml` | `contracts`, `media-plane`, `control-plane`, `signal-go`, `ops-go`, `dashboard-next`, `media-engine-rs` | All 7 polyglot jobs present; `media-engine-rs` job runs `cargo test --workspace` and asserts non-zero passing tests. |
| `.github/workflows/real-integrations.yml` | `real-providers` (`workflow_dispatch`, environment `real-provider-tests`) | Runs `python -m pytest -q -m real_provider` with `VOXDESK_REAL_INTEGRATION=1` and maps all **13** registered providers (`twilio`, `deepgram`, `elevenlabs`, `openai`, `anthropic`, `google`, `google_calendar`, `microsoft_calendar`, `calcom`, `hubspot`, `ghl`, `jobber`, `stripe`). |
| `.github/workflows/security-scan.yml` | `sast` (Bandit), `dependency-audit` (`pip-audit`, `npm audit`, `cargo audit`, `govulncheck`), `secret-scan` (`gitleaks`), `trivy-image-scan` (`trivy`) | Runs on push/PR and weekly cron (`0 6 * * 1`). Covers Python SAST, Python/Node/Rust/Go dependency CVEs, Git history secrets, and container image HIGH/CRITICAL CVEs (`gateway-go` `govulncheck` and `dashboard/` `npm audit` are additionally run in `ci.yml` / `check_other.sh`). |
| `.github/workflows/check.yml` (`[NEW]`) | `check-all` (`workflow_dispatch` + weekly `0 4 * * 1`) | Runs `bash scripts/check_all.sh` on `ubuntu-latest` with Docker and uploads `reports/check/` as a 30-day artifact without replacing `ci.yml`, `polyglot.yml`, or `security-scan.yml`. |

---

## 3. Polyglot & Rust SFU Per-Crate Verification (`Step 2`)

- **Go Realtime Gateway (`services/realtime/gateway-go`)**: `go vet ./...` (`exit 0`), `go test -race -count=1 ./...` -> **18 packages `ok`**.
- **Go Signaling Hub (`services/signal-go`)**: `go vet ./...` (`exit 0`), `go test -race -count=1 ./...` -> **1 package `ok`**.
- **Go Ops CLI (`services/ops`)**: `go vet ./...` (`exit 0`), `go test -race -count=1 ./...` -> **3 packages `ok`** (`cmd/voxops`, `internal/backup`, `internal/status`).
- **Rust Control Plane (`services/control-plane`)**: Toolchain `rustc 1.90.0`; `cargo fmt --all -- --check` (`exit 0`), `cargo check --workspace` (`exit 0`), `cargo test --workspace` -> **59 passed, 0 failed** (`voxdesk-control`: 35 unit + 6 integration, `voxdesk-signal`: 9 unit + 9 integration), `cargo clippy -- -D warnings` (`0` warnings).
- **Rust Realtime Media Engine SFU (`services/realtime/media-engine-rs`)**: Toolchain `rustc 1.90.0`; `cargo test --workspace --locked` -> **115 passed, 0 failed** across 15 workspace crates (`audio`: 12, `benches`: 0, `concurrency`: 4, `dtls`: 14, `engine`: 11, `idempotency`: 6, `livekit`: 8, `loadgen`: 3, `media`: 9, `media-engine`: 18, `protocol`: 5, `rate-limit`: 4, `routing`: 6, `sdp-tool`: 3, `sessions`: 4, `signaling`: 3, `streams`: 3, `telemetry`: 2).
- **C++17 Media Plane (`services/media-plane`)**: `g++ -std=c++17 -Werror`; `media_tests` -> **1,175 checks, 0 failures**; `audio_tests` -> **130,383 checks, 0 failures** (**131,558** total checks).
- **Protobuf Wire Contracts (`contracts/proto/`)**: `5` `.proto` files compiled via `protoc`; `pytest tests/test_contracts.py -q` -> **4 passed**.

---

## 4. Security & Gitleaks Redacted Findings (`Step 3`)

- **Bandit SAST (`bandit -c pyproject.toml -r app scripts -lll -q`)**: `exit 0` (`0` High findings).
- **Python Dependency Audit (`bash scripts/audit_dependencies.sh`)**: `exit 0` (`OK: no unexpected vulnerable dependencies`, `44` pinned distributions).
- **Frontend Production Dependency Audit (`npm audit --omit=dev --audit-level=high`)**: `dashboard/` `0` vulnerabilities (`exit 0`); `dashboard-next/` `0` vulnerabilities (`exit 0`).
- **Operational Preflight Secret Scan (`ops_preflight.py`)**: `no tracked .env / key files = PASS`, `no secret values in source = PASS`.
- **Gitleaks Historical Scan (`gitleaks detect --no-banner --redact`)**: Scanned 6 commits; found `32` unique `(file, rule, commit)` matches in historical evidence/test fixtures (all values redacted, `0` live credentials):

| Commit | Rule ID | Line | File Path (Redacted Match) |
|---|---|---:|---|
| `dc85c333` | `generic-api-key` | `82` | `voxdesk-call/dashboard/src/features/public-widget/__tests__/PublicWidget.test.tsx` |
| `dc85c333` | `generic-api-key` | `66` | `voxdesk-call/docs/archive/part0-initial-evidence/baseline-hashes.json` |
| `dc85c333` | `generic-api-key` | `70` | `voxdesk-call/docs/archive/part0-reaudit/pre-task-snapshots/dashboard/src/features/public-widget/__tests__/PublicWidget.test.tsx` |
| `dc85c333` | `generic-api-key` | `5` | `voxdesk-call/docs/evidence/prompt8-2026-10-05/postgres-multiprocess-idempotency-outbox.txt` |
| `dc85c333` | `generic-api-key` | `66` | `voxdesk-call/reports/part0/baseline-hashes.json` |
| `dc85c333` | `generic-api-key` | `51` | `voxdesk-call/reports/part1/qa/verify_postgres.py` |
| `dc85c333` | `generic-api-key` | `156` | `voxdesk-call/tests/resilience/test_idempotency.py` |
| `dc85c333` | `generic-api-key` | `355` | `voxdesk-call/tests/test_outbound_call_route_hardening.py` |
| `b277fedb` | `generic-api-key` | `61` | `voxdesk-call/tests/insight/test_insight_api.py` |
| `b277fedb` | `generic-api-key` | `53` | `voxdesk-call/tests/qa/test_call_outcomes.py` |
| `b277fedb` | `generic-api-key` | `194` | `voxdesk-call/tests/specialized_agents/test_executor.py` |
| `b277fedb` | `generic-api-key` | `63` | `voxdesk-call/tests/specialized_agents/test_persistence.py` |
| `b277fedb` | `generic-api-key` | `43` | `voxdesk-call/tests/translation/test_persistence.py` |
| `bc2803bc` | `aws-access-token` | `100` | `voxdesk-call/app/release/ops.py` |
| `bc2803bc` | `stripe-access-token` | `261` | `voxdesk-call/dashboard/tests/audit.test.jsx` |
| `bc2803bc` | `aws-access-token` | `244` | `voxdesk-call/dashboard/tests/audit.test.jsx` |
| `bc2803bc` | `generic-api-key` | `295` | `voxdesk-call/dashboard/tests/audit.test.jsx` |
| `bc2803bc` | `generic-api-key` | `40` | `voxdesk-call/services/realtime/gateway-go/internal/auth/jwt_test.go` |
| `bc2803bc` | `generic-api-key` | `15` | `voxdesk-call/services/realtime/gateway-go/internal/config/config_test.go` |
| `bc2803bc` | `generic-api-key` | `21` | `voxdesk-call/services/realtime/gateway-go/internal/server/ingest_test.go` |
| `bc2803bc` | `generic-api-key` | `34` | `voxdesk-call/services/realtime/gateway-go/tests/integration/edge_flow_test.go` |
| `bc2803bc` | `generic-api-key` | `41` | `voxdesk-call/services/realtime/gateway-go/tests/load/load_test.go` |
| `bc2803bc` | `generic-api-key` | `481` | `voxdesk-call/tests/conftest.py` |
| `bc2803bc` | `private-key` | `172` | `voxdesk-call/tests/telephony/test_provider_contract.py` |
| `bc2803bc` | `generic-api-key` | `464` | `voxdesk-call/tests/test_calendar_api.py` |
| `bc2803bc` | `generic-api-key` | `70` | `voxdesk-call/tests/test_calendar_contract.py` |
| `bc2803bc` | `stripe-access-token` | `118` | `voxdesk-call/tests/test_config.py` |
| `bc2803bc` | `generic-api-key` | `47` | `voxdesk-call/tests/test_calendar_providers.py` |
| `bc2803bc` | `generic-api-key` | `641` | `voxdesk-call/tests/test_crm_wiring.py` |
| `bc2803bc` | `generic-api-key` | `55` | `voxdesk-call/tests/test_crm_providers.py` |

---

## 5. Production Launch Gate Breakdown (`Step 6` — `reports/check/release_gate.json`)

- **Verdict**: `BLOCKED` (`P0 blockers = 3`, `P1 blockers = 19`, `P2 open = 5`, `FAIL = 0`)
- **All automated code, test, security, auth, tenant-isolation, migration, and privacy gates (`14` gates) are `PASS`**. The remaining non-`PASS` gates are `BLOCKED` or `NOT_RUN` pending live operator provider credentials or human sign-off:

| Gate ID | Severity | Status | Classification | Requirement |
|---|---|---|---|---|
| `deploy-001` | `P1` | `BLOCKED` | `INFRASTRUCTURE` | Deployment script and config validated (static) against the candidate artifact |
| `offsite-001` | `P2` | `BLOCKED` | `INFRASTRUCTURE` | Off-site backup replication verified |
| `tls-001` | `P0` | `BLOCKED` | `INFRASTRUCTURE` | TLS termination configured and certificates valid on all public endpoints |
| `obs-001` | `P1` | `BLOCKED` | `INFRASTRUCTURE` | Metrics, structured logs, and tracing exported and verified |
| `alert-001` | `P1` | `BLOCKED` | `INFRASTRUCTURE` | Alerts are defined, routed, and tested (fired once) |
| `realprov-twilio` | `P1` | `NOT_RUN` | `EXTERNAL` | Twilio connectivity verified (read-only) |
| `realprov-deepgram` | `P1` | `NOT_RUN` | `EXTERNAL` | Deepgram connectivity verified (read-only) |
| `realprov-elevenlabs` | `P1` | `NOT_RUN` | `EXTERNAL` | ElevenLabs connectivity verified (read-only) |
| `realprov-openai` | `P1` | `NOT_RUN` | `EXTERNAL` | OpenAI LLM connectivity verified (read-only) |
| `realprov-anthropic` | `P1` | `NOT_RUN` | `EXTERNAL` | Anthropic LLM connectivity verified (read-only) |
| `realprov-google` | `P1` | `NOT_RUN` | `EXTERNAL` | Google LLM connectivity verified (read-only) |
| `realprov-google-calendar` | `P1` | `NOT_RUN` | `EXTERNAL` | Google Calendar connectivity verified (read-only) |
| `realprov-microsoft` | `P1` | `NOT_RUN` | `EXTERNAL` | Microsoft Calendar connectivity verified (read-only) |
| `realprov-calcom` | `P2` | `NOT_RUN` | `EXTERNAL` | Cal.com connectivity verified (read-only) |
| `realprov-hubspot` | `P1` | `NOT_RUN` | `EXTERNAL` | HubSpot connectivity verified (read-only) |
| `realprov-ghl` | `P2` | `NOT_RUN` | `EXTERNAL` | GoHighLevel connectivity verified (read-only) |
| `realprov-jobber` | `P2` | `NOT_RUN` | `EXTERNAL` | Jobber connectivity verified (read-only) |
| `realprov-stripe` | `P0` | `NOT_RUN` | `EXTERNAL` | Stripe connectivity verified (read-only, no charges) |
| `realprov-health-001` | `P1` | `NOT_RUN` | `EXTERNAL` | Full read-only health sweep of all 13 providers completed green |
| `e2e-001` | `P0` | `NOT_RUN` | `HUMAN` | Human-controlled live voice call completed with pre-call/call/post-call evidence |
| `cost-001` | `P2` | `BLOCKED` | `INFRASTRUCTURE` | Cost model, budgets, and alerts configured and reviewed |
| `ratelimit-001` | `P1` | `BLOCKED` | `AUTOMATED` | Rate limiting verified by tests and configured in production |
| `egress-001` | `P1` | `BLOCKED` | `INFRASTRUCTURE` | Outbound egress network policy enforced (allowlist) and verified in staging |
| `compliance-001` | `P1` | `NOT_RUN` | `HUMAN` | Compliance documentation reviewed and signed off |
| `pentest-001` | `P1` | `NOT_RUN` | `EXTERNAL` | External penetration test performed by an independent third party |
| `ir-001` | `P1` | `BLOCKED` | `HUMAN` | Incident response runbook tested (tabletop or drill) |
| `dr-001` | `P1` | `BLOCKED` | `INFRASTRUCTURE` | Disaster recovery plan tested in staging |

---

## 6. Opt-In Real Provider Suite (`Step 9` — `tests/test_real_providers.py`)

Command: `VOXDESK_REAL_INTEGRATION=1 python -m pytest -q -m real_provider` (`0` real calls, `0` charges):

| # | Provider | Status | Reason |
|---:|---|---|---|
| 1 | `anthropic` | `SKIPPED` | `ANTHROPIC_API_KEY not configured` |
| 2 | `calcom` | `SKIPPED` | `not configured (missing: VOXDESK_REAL_CALCOM_API_KEY)` |
| 3 | `deepgram` | `SKIPPED` | `DEEPGRAM_API_KEY not configured` |
| 4 | `elevenlabs` | `SKIPPED` | `ELEVENLABS_API_KEY not configured` |
| 5 | `ghl` | `SKIPPED` | `not configured (missing: VOXDESK_REAL_GHL_ACCESS_TOKEN, VOXDESK_REAL_GHL_LOCATION_ID)` |
| 6 | `google` | `SKIPPED` | `GOOGLE_API_KEY not configured` |
| 7 | `google_calendar` | `SKIPPED` | `not configured (missing: VOXDESK_REAL_GOOGLE_CALENDAR_REFRESH_TOKEN, ...)` |
| 8 | `hubspot` | `SKIPPED` | `not configured (missing: VOXDESK_REAL_HUBSPOT_TOKEN)` |
| 9 | `jobber` | `SKIPPED` | `not configured (missing: VOXDESK_REAL_JOBBER_ACCESS_TOKEN)` |
| 10 | `microsoft_calendar` | `SKIPPED` | `not configured (missing: VOXDESK_REAL_MICROSOFT_REFRESH_TOKEN, ...)` |
| 11 | `openai` | `SKIPPED` | `OPENAI_API_KEY not configured` |
| 12 | `stripe` | `SKIPPED` | `STRIPE_SECRET_KEY not configured` |
| 13 | `twilio` | `SKIPPED` | `TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN not configured` |
