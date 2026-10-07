# PART 1 / 1C — post-call CRM admission and acknowledged note delivery

CURRENT delivery record. Previous QA/backfill/core records below are historical. Applied to the main working tree, not a production deployment. Full 1C and PART 1 remain unfinished; PART 2 remains deferred.

## Runtime applied

POST_CALL now runs crm_admission after the analyzed_webhook barrier. It pins the actual summary/outcome, sentiment and owned custom AnalysisResult values into the existing CrmEvent/CrmSync queue. The separate deterministic discriminator post-analysis:v1 prevents a pre-analysis call-completed event from suppressing the analyzed update. Duplicate pipeline execution does not duplicate admission. The checkpoint returns real event/sync IDs with delivery=admitted, never claims provider delivery. No configured target yields not_requested. Incomplete analysis blocks CRM payload publication; independent QA failure does not mislabel completed analysis.

The existing CRM scheduler performs delivery. No second queue, worker loop, credential store or connector model was added. Existing tenant-wide provider connections opt in with config.post_call_analysis_environment_id, through the real PUT /api/integrations/crm/{provider} API. The provider catalogue lists this supported field. The API validates UUID, tenant ownership, active environment, authenticated membership and environment role, including the old binding on updates. Foreign environments return 404. Bound configuration writes use Redis at 20 requests/tenant/60 seconds and now commit configuration and audit atomically, rather than committing the business action first.

Existing subscriptions still filter the underlying call.completed/call.missed event. Omit/remove the binding to stop future analyzed admission. Connections remain one per tenant/provider, not one per environment; generic CRM migration/Salesforce remains 1F.

Before dispatch, the worker verifies the surviving Call, tenant, active environment and unchanged environment binding. Analysis deliveries use strict Redis at 30 attempts/tenant/environment/provider/60 seconds, not the legacy process-local limiter. Contact identity hashes are separately salted by environment for this new path. Existing unbound legacy CRM behavior is preserved, not certified as globally fixed.

The provider must support CREATE_NOTE. A successful contact write alone is not analyzed delivery. The note body includes real analysis values and its title includes the call ID. Only an acknowledged note drives SYNCED and the stored external_id (the note acknowledgement); the contact link separately retains the contact ID. Missing provider configuration records not_configured, scope changes record scope_changed, and note failures retry/fail under the existing policy. A failed delivery audit cannot commit SYNCED. Admission audit, event and sync writes are atomic as well.

The CRM event emitter and contact-link uniqueness handling no longer roll back the caller's entire business transaction when a duplicate wins. Savepoints arbitrate duplicates, verify an actual existing record and rethrow unrelated integrity errors. SQLite native transaction handling prevents a released first savepoint from silently committing outside the caller's transaction.

## Measured verification

```sh
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m pytest tests/ai tests/qa tests/test_crm*.py tests/webhooks tests/outbox tests/jobs tests/operations/test_notification_webhook_email.py tests/test_external_success_honesty.py tests/test_call_state.py tests/test_call_callbacks.py tests/test_transfer.py tests/telephony tests/campaign/test_concurrency.py tests/test_legacy_lead_routes.py tests/test_enterprise_batch01.py -q --junitxml=reports/part1/crm-runtime/regression-final.xml
```

931 passed, 4 skipped, 4912 warnings in 307.61s. Warnings were not suppressed. No assertions were weakened or tests removed/disabled. The checkpoint assertion now requires the added CRM step and its exact no-target output. Initial and final logs remain available.

```sh
.venv/bin/python -m pytest tests/telephony/test_post_call_crm.py -q
PATH=$PWD/.venv/bin:$PATH OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 make verify-truth
PATH=$PWD/.venv/bin:$PATH make contracts-check
.venv/bin/ruff check app/api/integration_routes.py app/integrations/crm/events.py app/integrations/crm/service.py app/telephony/post_call.py tests/telephony/test_post_call_crm.py tests/telephony/test_post_call_pipeline.py reports/part1/crm-runtime/verify_postgres.py reports/part1/runtime/verify_post_call_postgres.py
```

New module: 12 passed, 1 skipped, 232 warnings in 15.70s. Truth: 41 passed, 59 warnings in 18.28s. Contracts: five protobuf files compile, enum and tenant-scope checks PASS. Selected Ruff: all checks passed. Counts overlap.

New contracts cover real signed HTTP request shape through the existing provider, note failure despite contact acknowledgement, duplicate admission, tenant/environment exclusion, scope recheck, Redis missing, actual custom values, emitter rollback, missing secret, admission/delivery/configuration audit failure, and real configuration API RBAC/ownership.

```sh
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic upgrade head
.venv/bin/python reports/part1/runtime/verify_post_call_postgres.py
.venv/bin/python reports/part1/crm-runtime/verify_postgres.py
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic downgrade -1
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic upgrade head
.venv/bin/python reports/part1/crm-runtime/verify_postgres.py
.venv/bin/alembic heads
```

Isolated PostgreSQL 17 and Redis only, on loopback ports 55432 and 56379. Empty upgrade and 0054→0053→0054 pass; the single head remains 0054_qa_runtime. No schema change or new migration was needed for this slice. Existing downgrade metadata-loss warnings still apply. The core PostgreSQL verifier proves eight checkpoints and retained custom results. The CRM verifier uses independent sessions for concurrent pipelines and concurrent workers: one event/sync, one claimed attempt, one recording-fake contact and note, persisted note acknowledgement. It is not a live provider test.

The explicit CRM live command was also run with -m live: one test skipped for missing explicit opt-in/HTTPS receiver credentials. Regression's four skips include earlier model/QA/webhook live tests. No LIVE or production-ready claim is made.

## Source and preserved work

Complete changed/new files are included in the main report and ZIP, with applied-source SHA-256 manifest. New/changed slice files: app/api/integration_routes.py, app/integrations/crm/events.py, app/integrations/crm/service.py, app/telephony/post_call.py, tests/telephony/test_post_call_crm.py, tests/telephony/test_post_call_pipeline.py, docs/POST_CALL.md, reports/part1/runtime/verify_post_call_postgres.py, reports/part1/runtime/build_delivery.py, this status file and reports/part1/crm-runtime/verify_postgres.py. Historical source delivery remains included.

Staged AB3 unchanged: d6381eee5322f484ca1ebfdb3a1ff42450ca7a29ba970f1b2eee98a650d65b10. No completion commit was made because 1C is still open; unrelated staged changes were not swept into a commit.

## Residual limits and next work

- Workflow trigger execution and legacy extended analysis facade closure remain 1C work. Their allowlist entry is retained.
- Provider response followed by a failed DB/audit commit can cause a repeated note after recovery. Exactly-once external effects across arbitrary CRMs are not claimed.
- This is opt-in analyzed delivery, not a rewrite/certification of legacy pre-analysis CRM enrichment, local rate limits, contact naming, webhook signatures, or every CRM management route. Their broader modernization belongs to 1E/1F.
- No automatic historical rescan. Explicit POST_CALL replay can execute a missing new checkpoint; custom backfill does not send CRM updates.
- Unified PII/retention/deletion of queued CRM payloads remains 1D work. The new opt-in sends analysis values, which may contain personal data; no complete privacy-enforcement claim is made.
- No new routes or OpenAPI schemas: route count 1163 and the exact contract equality test pass unchanged.
- Remaining sequence: finish 1C → 1D → 1E → 1B → 1F → 1G. PART 2 is not started.
