# SELL CHECK 1 of 4 — Backend Check & Test Report (`BACKEND_CHECK.md`)

Generated at: `2026-10-09T09:59:27.515005+00:00`

---

## 1. Dependency & Static Audit Table

| Check / Script | Command | Exit Code | Finding Count | Verdict / Summary |
|---|---|---:|---:|---|
| `pip check` | `python -m pip check` | `0` | `0` | `No broken requirements found.` |
| `verify_dependencies.py` | `python scripts/verify_dependencies.py` | `0` | `0` errors (`0` warnings) | `44` pinned distributions verified; `OK: all declared packages installed, importable and version-matched; no missing source imports.` |
| `compileall` | `python -m compileall -q app scripts tests` | `0` | `0` | `0` `SyntaxError` across `app/`, `scripts/`, `tests/` |
| `ruff check` | `ruff check app scripts tests loadtest` | `0` | `0` | `All checks passed!` (0 lint findings) |
| `verify_no_null_bytes.py` | `python scripts/verify_no_null_bytes.py` | `0` | `0` | `[]` (0 files with NUL bytes) |
| `verify_no_filler.py` | `python scripts/verify_no_filler.py` | `0` | `0` | `[]` (0 filler/padding markers) |
| `verify_no_fake_success.py` | `python scripts/verify_no_fake_success.py` | `0` | `0` | `0` fake-success findings; `scripts/fake_success_allowlist.txt` is `0` bytes (`test ! -s` exit `0`) |
| `verify_contracts.py` | `python scripts/verify_contracts.py` | `0` | `0` | `contracts: 5 .proto files` — `OK: contracts compile; enum vocabulary matches app.db.models; every operational message is tenant-scoped.` |
| `audit_except_handlers.py` | `python scripts/audit_except_handlers.py app` | `0` | `0` silent (`254` logged/handled) | `broad `except Exception` handlers: 254 / silent (no observable effect):  0 / handled (logged / re-raised / recorded): 254` |
| `audit_missing_files.py` | `python scripts/audit_missing_files.py` | `0` | `0` | `reference-driven checks: 0 finding(s)` |

---

## 2. Boot & Route Summary

- **FastAPI Uvicorn Subprocess Boot Probe (`scripts/boot_probe.py --timeout 30`)**:
  - Status: **`ok`**
  - Uvicorn child RSS: **`663.54 MB`** (`695771136` bytes)
  - Boot-to-ready time: **`16.049 s`** (total probe duration: **`20660.03 ms`**)
  - `GET /health`: HTTP **`200`** (`{"status": "ok"}`)
  - `GET /health/ready`: HTTP **`200`**
  - `GET /health/dependencies`: HTTP **`503`**
  - `GET /openapi.json`: HTTP **`200`** (`1274905` bytes in `4.301s`, OpenAPI `3.1.0`, **`969`** paths, **`1166`** operations)
- **Route Inventory (`scripts/route_inventory.py` -> `reports/check/routes.json` & `reports/check/routes.csv`)**:
  - Total routes on `app.main:app`: **`1182`** (`1173` `APIRoute`, `5` `WebSocketRoute`, `4` built-in OpenAPI/docs routes)
  - HTTP / WebSocket method breakdown:
    - `GET`: **`502`**
    - `POST`: **`548`**
    - `PATCH`: **`55`**
    - `DELETE`: **`54`**
    - `PUT`: **`14`**
    - `WEBSOCKET`: **`5`**
  - Duplicate `(method, path)` pairs: **`0`** (must be `0`)
  - `/endpoint-N` routes: **`0`** (must be `0`)
  - Legacy filler banner routes (`NO SKIP FULL CODE`, `1050+ lines`): **`0`** (must be `0`)
  - `APIRoute` missing explicit `response_model`: **`432`**
  - `APIRoute` missing explicit `status_code`: **`970`**
  - Authentication coverage across all `1173` `APIRoute` endpoints:
    - Dependency-authenticated (`Depends(current_user)` / `current_context` / `require_role` / `scim_auth` / etc.): **`1081`**
    - Allowlisted public endpoints (`/health*`, `/metrics`, `/auth/*`, `/api/v1/public/*`, `/widget/embed.js`, etc.): **`55`**
    - Allowlisted handler-verified / provider-signature-verified endpoints (Twilio/Telnyx/Stripe/SAML/OAuth/SCIM webhooks & internal token-checked handlers): **`37`**
    - **Unauthenticated routes outside allowlist**: **`0`**

---

## 3. Alembic Migration Verdict

| Migration Gate | Command | Result | Details |
|---|---|---|---|
| Single Head Check | `alembic heads` | **PASS** (`1` head) | `0062_drop_pcap_artifacts (head)` across `62` linear migration files (`0001_initial` -> `0062_drop_pcap_artifacts`). *(Note: prompt text mentioned `0045_request_idempotency_receipts`, which was superseded by migrations `0046`–`0062` in Parts 0–8.)* |
| Full Postgres Round-Trip | `alembic upgrade head && alembic downgrade base && alembic upgrade head` | **PASS** | Clean `0 -> 62 -> 0 -> 62` round-trip on PostgreSQL 17 (`postgresql+asyncpg://voxdesk:voxdesk-ci@localhost:5432/voxdesk_ci`) |
| Production Advisory-Lock Runner | `python scripts/migrate.py` | **PASS** (exit `0`) | Acquired `pg_try_advisory_lock(72720011)`, executed `alembic upgrade head`, and cleanly released advisory lock |
| Schema Drift Check | `alembic check` | **PASS** (exit `0`) | `No new upgrade operations detected.` (`0` missing tables, `0` missing columns across all 28 SQLAlchemy model modules) |

---

## 4. Pytest Execution Table

### 4.1 Overall Summary

| Metric | Count |
|---|---:|
| Total Collected Tests (`pytest --collect-only -q`) | **4,715** (across **381** test files) |
| Non-Live Executed (`-m "not real_provider and not live"`) | **4692** |
| **Passed** | **4692** |
| **Failed** | **0** |
| **Errors** | **0** |
| **Skipped** (in non-live run) | **0** |
| **XFailed / XPassed** | **0 / 0** |
| **Deselected** (`real_provider` / `live`) | **23** |
| **Timed-out** (`--timeout=30`) | **0** |
| **Crashed** (worker non-zero exit without JUnit XML) | **0** |
| **Live / Real-Provider Gate** (`pytest -m "real_provider or live" -rs`) | **23 skipped, 0 failed, 0 errors** (1.69s) |
| Total Chunked Wall-Clock Duration | **767.05s** |

### 4.2 Per-Chunk Breakdown (`16` Chunks of `25` Files)

| Chunk | Files | Passed | Failed | Errors | Skipped | Deselected | Duration | Exit Code |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `01` | 25 | 133 | 0 | 0 | 0 | 2 | 32.07s | `0` |
| `02` | 25 | 332 | 0 | 0 | 0 | 0 | 97.40s | `0` |
| `03` | 25 | 191 | 0 | 0 | 0 | 0 | 47.35s | `0` |
| `04` | 25 | 66 | 0 | 0 | 0 | 0 | 32.95s | `0` |
| `05` | 25 | 129 | 0 | 0 | 0 | 0 | 34.04s | `0` |
| `06` | 25 | 156 | 0 | 0 | 0 | 4 | 45.64s | `0` |
| `07` | 25 | 124 | 0 | 0 | 0 | 1 | 34.61s | `0` |
| `08` | 25 | 299 | 0 | 0 | 0 | 0 | 51.15s | `0` |
| `09` | 25 | 183 | 0 | 0 | 0 | 1 | 63.84s | `0` |
| `10` | 25 | 424 | 0 | 0 | 0 | 1 | 52.58s | `0` |
| `11` | 25 | 1068 | 0 | 0 | 0 | 0 | 49.13s | `0` |
| `12` | 25 | 489 | 0 | 0 | 0 | 0 | 60.26s | `0` |
| `13` | 25 | 400 | 0 | 0 | 0 | 0 | 48.75s | `0` |
| `14` | 25 | 337 | 0 | 0 | 0 | 13 | 34.23s | `0` |
| `15` | 25 | 272 | 0 | 0 | 0 | 0 | 53.02s | `0` |
| `16` | 6 | 89 | 0 | 0 | 0 | 1 | 30.03s | `0` |

### 4.3 20 Slowest Tests

| Rank | Test Node ID | Status | Duration |
|---:|---|---|---:|
| 1 | `tests.test_ops_toolkit::test_egress_without_allowlist_is_blocked` | `passed` | 12.023s |
| 2 | `tests.auth.test_mfa_enrollment::test_enrollment_requires_an_authenticated_session` | `passed` | 10.925s |
| 3 | `tests.auth.scim.test_scim_credentials::test_a_credential_is_issued_once_and_stored_hashed` | `passed` | 9.973s |
| 4 | `tests.test_e2e_guard::test_disarmed_voice_answers_normal_traffic_and_creates_a_call` | `passed` | 9.938s |
| 5 | `tests.webhooks.test_webhook_isolation::test_http_crud_idempotency_rotation_and_audit` | `passed` | 9.782s |
| 6 | `tests.test_calendar_api.TestAppointmentApi::test_availability_endpoint` | `passed` | 9.596s |
| 7 | `tests.test_knowledge_tenant_isolation::test_a_cannot_list_bs_documents` | `passed` | 9.545s |
| 8 | `tests.messaging.test_sms_flow::test_inbound_sms_routes_to_chat_agent_and_returns_twiml_reply` | `passed` | 9.320s |
| 9 | `tests.test_api_contract::test_no_route_returns_500_when_probed` | `passed` | 6.759s |
| 10 | `tests.smoke.test_app_boot::test_openapi_schema_generates_cleanly_with_unique_operation_ids` | `passed` | 5.111s |
| 11 | `tests.jobs.test_recovery::test_postgres_worker_crash_lease_reclaim_and_duplicate_prevention` | `passed` | 4.672s |
| 12 | `tests.telephony.test_analysis_extended::test_bulk_is_atomic_and_redis_fail_closed` | `passed` | 4.306s |
| 13 | `tests.truth.test_no_fake_success_markers::test_production_fake_success_guard` | `passed` | 4.235s |
| 14 | `tests.webhooks.test_webhook_isolation::test_audit_failure_rolls_back_api_creation` | `passed` | 3.966s |
| 15 | `tests.truth.test_no_template_routes::test_openapi_matches_the_reviewed_current_contract` | `passed` | 3.891s |
| 16 | `tests.telephony.test_post_call_workflow::test_trigger_permissions_isolation_and_atomic_audit` | `passed` | 3.295s |
| 17 | `tests.webhooks.test_call_event_bridge::test_producer_publication_failure_is_not_acknowledged[/telephony/ivr]` | `passed` | 3.292s |
| 18 | `tests.test_imports::test_pipecat_service_import_paths_used_by_the_code` | `passed` | 3.093s |
| 19 | `tests.telephony.test_analysis_backfill::test_audit_failure_rolls_back_manifest_jobs_and_receipt` | `passed` | 3.033s |
| 20 | `tests.agent.test_provider_registry::test_configured_providers_instantiate_and_format_boosted_keywords` | `passed` | 2.954s |

---

## 5. Failure / Hang / Skip Triage & Dialect-Gap Analysis

### 5.1 Failures, Errors, Timeouts, and Crashes
- **Failures**: `0`
- **Errors**: `0`
- **Timeouts (`--timeout=30`)**: `0`
- **Worker Crashes**: `0`

### 5.2 Live-Gated Tests (`real_provider` / `live` Markers)
When executed without external provider credentials (`OPENAI_API_KEY`, `DEEPGRAM_API_KEY`, `ELEVENLABS_API_KEY`, `TWILIO_ACCOUNT_SID`, `TELNYX_API_KEY`, `SALESFORCE_CLIENT_ID`, etc.):
- **`23` tests selected, `23` cleanly skipped (`0` failed, `0` errors)** with explicit skip reasons:
  - `tests/agent/test_s2s_smoke.py`: `2` skipped (`OPENAI_API_KEY not set — skipping live OpenAI Realtime smoke test`, `GOOGLE_API_KEY / GEMINI_API_KEY not set — skipping live Gemini Live smoke test`)
  - `tests/ai/test_post_call_llm.py`: `1` skipped (`OPENAI_API_KEY not set`)
  - `tests/integrations/test_crm_live_matrix.py`: `5` skipped (`HUBSPOT_LIVE_TOKEN not set`, `PIPEDRIVE_LIVE_TOKEN not set`, `ZOHO_LIVE_TOKEN not set`, `SALESFORCE_LIVE_INSTANCE_URL and SALESFORCE_LIVE_TOKEN required`, `WEBHOOK_LIVE_URL not set`)
  - `tests/integrations/test_salesforce_live.py`: `1` skipped (`Live Salesforce credentials not configured`)
  - `tests/qa/test_post_call_runtime.py`: `1` skipped (`OPENAI_API_KEY not set for live QA evaluation`)
  - `tests/telephony/test_post_call_crm.py`: `1` skipped (`No live CRM provider env vars set`)
  - `tests/telephony/test_telnyx_media_path.py`: `1` skipped (`TELNYX_API_KEY not configured for live Telnyx call control test`)
  - `tests/test_real_providers.py`: `10` skipped (`DEEPGRAM_API_KEY not set`, `OPENAI_API_KEY not set`, `ANTHROPIC_API_KEY not set`, `GOOGLE_API_KEY not set`, `ELEVENLABS_API_KEY not set`, `TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN not set`, `STRIPE_SECRET_KEY not set`, `REDIS_URL not set`, `LIVEKIT_URL/LIVEKIT_API_KEY/LIVEKIT_API_SECRET not set`)
  - `tests/webhooks/test_webhook_lifecycle_real.py`: `1` skipped (`SVIX_AUTH_TOKEN not configured`)

### 5.3 SQLite vs. PostgreSQL Dialect-Gap Analysis
| Test / Module | PostgreSQL-Specific Feature | Verification Status in This Check |
|---|---|---|
| `tests/jobs/test_recovery.py::test_postgres_worker_crash_lease_reclaim_and_duplicate_prevention` | `SELECT ... FOR UPDATE SKIP LOCKED` cross-process lease recovery on `durable_jobs` | **VERIFIED ON LIVE POSTGRES 17** (`DATABASE_URL=postgresql+asyncpg://voxdesk:voxdesk-ci@localhost:5432/voxdesk_ci`) |
| `tests/test_identity_pg_enum.py` (`2` tests) | Native PostgreSQL `ENUM` (`auditaction`) DDL & value insertion | **VERIFIED ON LIVE POSTGRES 17** (`DATABASE_URL=postgresql+asyncpg://voxdesk:voxdesk-ci@localhost:5432/voxdesk_ci`) |
| `alembic/versions/0001_initial.py` .. `0062_drop_pcap_artifacts.py` + `scripts/migrate.py` | Full `upgrade head -> downgrade base -> upgrade head`, `pg_try_advisory_lock(72720011)`, `JSONB`, `UUID`, `RLS` policies, and `alembic check` | **VERIFIED ON LIVE POSTGRES 17** (`0 -> 62 -> 0 -> 62`, `0` drift) |
| `tests/operations/test_durable_jobs.py::test_single_winner_on_concurrent_claim` | `FOR UPDATE SKIP LOCKED` on `concurrent_sessionmaker` (uses file-backed SQLite fixture by default) | Covered by `test_recovery.py` against real Postgres; file-backed SQLite serializes via database lock |
| `tests/campaign/test_concurrency.py` & `tests/telephony/test_outbound_environment_scope.py` | Concurrent lead/recipient claiming (`FOR UPDATE SKIP LOCKED`) | Uses `concurrent_sessionmaker` SQLite fixture; row-level `SKIP LOCKED` semantics require PostgreSQL |
| `app/db/rls.py` & `tests/security/test_tenant_isolation_matrix.py` | PostgreSQL Row-Level Security (`CREATE POLICY`, `SET LOCAL app.current_tenant_id`) | Application-layer tenant scoping verified in SQLite; database-enforced RLS policies verified via Alembic migration `0029_prompt3_surfaces` on Postgres |
