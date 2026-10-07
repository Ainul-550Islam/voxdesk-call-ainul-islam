# 1A — canonical webhooks and runtime call events

Date: 2026-10-07. Local 1A implementation and acceptance completed; **PART 1 as a whole is not complete**. No PART 2 work. No hosted LIVE or production-ready claim.

## Implemented

- Canonical WebhookSubscription/WebhookDelivery storage, migration 0050 from actual head 0049; removed duplicate enterprise classes/tables. Seeded legacy data is encrypted and preserved, while historical unverified claimed successes are quarantined rather than recertified.
- Existing lifecycle router now uses real repository, signed HTTP delivery, durable outbox/jobs, actual diagnostics, retry/replay/DLQ, secret rotation, tenant/environment scope, RBAC and transactional audit. No independent replacement queue or dispatcher. New writes require AES encryption; strict Redis write limits fail closed.
- Versioned reference-only call-event contracts, unique transactional publication, enabled subscription matching, replay-safe centralized status/Twilio/transfer/recording/reconciliation/outbound producers. A provider accepting transfer is not human pickup. Callback duplicate receipts cannot leak another tenant's call.
- New rotation route registered by the existing router. Only the webhook lifecycle entry was removed from the fake-success allowlist. Other subpart exceptions remain.
- Current OpenAPI regenerated: one added rotation path, no removed paths, changed operations only under /api/webhooks. Runtime route count 1157.

## Actual commands and results

All commands ran from the application repository with Python 3.12. These separate runs overlap; counts are not additive.

| Command | Actual result |
| --- | --- |
| `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m pytest tests/webhooks tests/outbox tests/operations/test_notification_webhook_email.py tests/test_external_success_honesty.py tests/test_call_state.py tests/test_call_callbacks.py tests/test_transfer.py tests/telephony tests/campaign/test_concurrency.py tests/test_legacy_lead_routes.py tests/test_enterprise_batch01.py -q --junitxml=reports/part1/final-1a/regression.xml` | 375 passed, 1 skipped, 2302 warnings, 160.77s |
| `.venv/bin/python -m pytest -q tests/webhooks/test_call_event_bridge.py` (thread variables as above) | 45 passed, 208 warnings, 24.92s |
| `.venv/bin/python -m pytest -q tests/webhooks/test_webhook_lifecycle_real.py` (thread variables as above) | 15 passed, 1 skipped, 59 warnings, 10.26s |
| `.venv/bin/python -m pytest -q tests/webhooks/test_webhook_isolation.py` (thread variables as above) | 35 passed, 413 warnings, 28.91s |
| `PATH=$PWD/.venv/bin:$PATH OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 make verify-truth` | 41 passed, 59 warnings, 19.97s |
| `PATH=$PWD/.venv/bin:$PATH make contracts-check` | PASS: 5 proto files compile, vocabulary and tenant scoping match |
| `.venv/bin/ruff check app tests/webhooks tests/operations/test_notification_webhook_email.py tests/test_external_success_honesty.py` | All checks passed |
| `.venv/bin/python reports/part1/final-1a/verify_migration.py` | PASS seeded migration, missing-key atomic rollback, sealed preservation, quarantine, repeated head, downgrade/re-upgrade, one head |
| `.venv/bin/python reports/part1/final-1a/verify_concurrency.py` | PASS two independent PostgreSQL sessions serialize one HTTP request/ledger, concurrent secret rotations reach version 3, rollback leaves no event |
| `alembic upgrade head`, `alembic heads`, `alembic downgrade -1`, `alembic upgrade head` | PASS on isolated PostgreSQL via the migration script; one head 0050_unify_webhooks |
| `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/repo_stats.py` | 1592 source files, 340045 physical source lines, 1157 routes, 4330 collected pytest nodes; collection is not passing |

Raw outputs and XML are in reports/part1/final-1a and the delivery ZIP. Prior unsuccessful fixture/import/migration runs are retained and named separately; they are not the final result. The final regression includes the fix for missing outbox.updated_at during migration and removal of the new-write local signing-secret fallback.

## Evidence boundaries and residual limitations

1. Actual local TCP receiver test: validated test Twilio HMAC → callback → transactional outbox → dispatch_due → real JobWorker → signed HTTP bytes received. The test transport relays to loopback after URL validation. This is neither hosted Twilio nor public HTTPS/TLS evidence.
2. Recording-fake HTTP contract: actual request/body/signature inspected; observed 503 persisted with sealed diagnostics, repeat idempotency key avoids another send, rotate and queue redrive, worker observes 204 using new secret. No status fabricated as receipt.
3. The one skipped test is the explicitly opt-in signed HTTPS echo receiver, absent credentials/configuration. No live receiver, CRM or Salesforce certification is claimed. Remaining PART 1 acceptance modules and live CRM/Salesforce commands are NOT RUN in this 1A-only scope, not reported as passing or credential skips.
4. Independent PostgreSQL sessions are not two OS worker processes. Crash-after-ack behavior remains at-least-once. A crash after test intent commits can leave its API receipt processing/409 while the outbox event survives. No exactly-once external-effect promise.
5. DNS-private-answer rejection is tested. DNS rebinding and TLS/address pinning are not certified. The response snippet is bounded but the HTTP response is fully buffered before truncation.
6. Migration downgrade deliberately recreates EMPTY legacy tables and drops new options/archive fields, as specified by the task. Back up/export before downgrade; passing schema roundtrip does not establish legacy data restoration. Historical quarantine cannot be replayed even after downgrade/re-upgrade.
7. Version one emits at most one event type per call, not each DTMF digit, recording or transfer attempt. An early unmatched voicemail SID is not queued by that route. Provider configuration for initiated/ringing transfer callbacks is not certified. call_analyzed, batch events and agent_published are schemas only pending their owning subparts.
8. Scope exception: supporting edits include canonical app/db/models.py, app/outbox/dispatcher.py and existing lifecycle producers, beyond the prompt's narrowly enumerated 1A files. Earlier permission questions were skipped, NOT approved. These edits were made to avoid a parallel implementation; this report does not retroactively claim authorization or perfect scope compliance.
9. Tests and source were already in a broadly dirty working tree. The 1A commit is restricted to an explicit file list; unrelated changes and staged AB3 additions are preserved. PART0 commit 1d023b5 is retained. Validation measures the working tree, not a newly cloned clean checkout of the commit.

## Other PART 1 tasks

1C, 1D, 1E, 1B, 1F and 1G remain unimplemented/uncertified by this delivery, in that order. Narrow webhook request durability is not closure of enterprise-wide 1E. No PART 2 was started.

## Complete code delivery

Run `.venv/bin/python reports/part1/build_report.py` to rebuild:

- reports/PART_1_REPORT.md: this acceptance summary, complete source bodies and actual output, without shortened file contents.
- reports/PART_1A_FULL_CODE.zip: complete changed/new files plus evidence.
- reports/part1/source-manifest.json: byte lengths and SHA-256 for the delivered source.

The generated full-source report/ZIP are delivery artifacts, not duplicate source bulk in the commit. The report does not recursively embed itself. Historical PART_1_WORK_IN_PROGRESS.zip is superseded, not current acceptance evidence.
