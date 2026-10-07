# PART 1 — main-code post-call runtime applied

## Current state

The main working tree now executes a durable post-call analysis core. The previously separate structured LLM module is wired into the actual default job registry/worker and the terminal call-event transaction. This is a verified implementation slice, **not completion of the whole 1C task or PART 1**. PART 2 remains deferred per the user's choice. Necessary supporting-file changes were explicitly approved in the preceding turn.

Applied production files: app/jobs/types.py, app/jobs/idempotency.py, app/jobs/registry.py, app/db/telephony_models.py, app/webhooks/call_event_bridge.py, app/telephony/transcription.py, new app/telephony/post_call.py and new migration 0051_post_call_pipeline.py. Prior app/ai/post_call_llm.py is reused, not replaced. The scheduler already uses the canonical worker bootstrap; no new worker process architecture or HTTP route was added.

The applied path is: existing terminal transition/callback → call_ended bridge → transactional POST_CALL enqueue → ordinary JobWorker → stored transcript checkpoint → governed summary/outcome and sentiment → durable checkpoint and UsageEvent rows. Existing nonempty Call.summary is preserved. Model classification does not fabricate a confirmed booking or human QA action. Missing provider configuration is persisted as not_configured and the aggregate job does not succeed.

## Real verification

Commands ran in the application repository, with the pinned dependencies restored into Python 3.12.15. The disposable PostgreSQL 17 database was created at 127.0.0.1:55432/post_call_test. No production database was used.

```sh
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m pytest tests/ai tests/webhooks tests/outbox tests/jobs tests/operations/test_notification_webhook_email.py tests/test_external_success_honesty.py tests/test_call_state.py tests/test_call_callbacks.py tests/test_transfer.py tests/telephony tests/campaign/test_concurrency.py tests/test_legacy_lead_routes.py tests/test_enterprise_batch01.py -q --junitxml=reports/part1/runtime/final-regression.xml
```

**505 passed, 2 skipped, 2943 warnings in 220.71s.** The two skips are the explicitly opt-in external LLM and signed webhook echo tests. They are not failures hidden by new skips and are not claimed LIVE.

Other measured checks (overlapping, not additive):

- `pytest tests/telephony/test_post_call_pipeline.py -q`: **19 passed, 137 warnings in 19.05s**.
- `PATH=$PWD/.venv/bin:$PATH OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 make verify-truth`: **41 passed, 59 warnings in 19.46s**.
- `PATH=$PWD/.venv/bin:$PATH make contracts-check`: **PASS**, five proto files compile; vocabulary and tenant scoping agree.
- Configured Ruff over all runtime/core/LLM files, the migration, tests and PostgreSQL script: **All checks passed**.
- Actual route/OpenAPI inspection: **1157 routes**; runtime OpenAPI exactly equals contracts/openapi.json, so no artificial snapshot update was needed.
- `alembic upgrade head`: **PASS** from an empty isolated PostgreSQL database through 0051.
- `alembic downgrade -1` then `alembic upgrade head`: **PASS** after populating the new checkpoints. Downgrade removes checkpoint data by design; this is schema roundtrip, not restoration of deleted checkpoints.
- `alembic heads`: **0051_post_call_pipeline (head)**, exactly one head.
- `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python reports/part1/runtime/verify_post_call_postgres.py`: **PASS** against migrated PostgreSQL. Rolling back the terminal transaction removes the call change, outbox event and job. Two independently opened concurrent pipeline sessions execute each completed inference once, persisting three scoped checkpoints and the summary.
- Existing tests/jobs/test_recovery.py also passed with the disposable DATABASE_URL. It uses two spawned processes to verify SKIP LOCKED claim exclusion, committed lease survival after process exit, expiry/reclaim and duplicate prevention. This is not a full provider crash-after-response certification.

## Failures found and corrected, not concealed

The initial pipeline fixture accessed expired ORM attributes synchronously after rollback, raising MissingGreenlet. Capturing the call ID and scope before rollback corrected the fixture; no production behavior or assertion was weakened. Its original core-initial log/XML remain.

An initial broad run had 503 passes, 2 failures and 2 skips. One failure correctly required an explicit migrated disposable PostgreSQL DATABASE_URL; rerunning with that configuration exercised the real test rather than skipping it. The other exposed an objectively time-dependent campaign test: it asserted the actual clock could never fall within 01:00–02:00 in the tenant timezone. The production function already supports an explicit now parameter. The test now uses an explicit UTC tenant clock and verifies both outside-window refusal and inside-window acceptance. The original refusal assertion remains, with a new positive assertion. No production campaign logic was changed. Before-fix output is retained.

## Boundaries and remaining work

1. Current executable steps are transcript finalization, summary/outcome and sentiment. Custom schema/result versioning, analysis/backfill service/API, QA sampling/review, call_analyzed publication and CRM/workflow stages are still pending. The post-call facade allowlist entry remains. No 1C completion commit was made.
2. The call_analyzed webhook is deliberately not emitted before the full analysis contract is implemented. Call event producers that bypass the existing bridge and historical completed-call backfill are not automatically covered.
3. Maximum frozen transcript input is 1000 stored turns and 6300 rendered characters. Larger input is explicitly unsupported; long-call chunking is not implemented. No silent truncation.
4. No raw transcript is duplicated in jobs/checkpoints. Analysis output is not a claim of completed PII/retention enforcement; 1D remains pending. No fabricated actor/user/agent is created: worker AI context uses the actual persisted Tenant and environment and cannot authorize HTTP routes.
5. DB locks serialize successful checkpoints, but a crash between provider response and DB commit can repeat an external inference and lose uncommitted usage evidence. No exactly-once or perfect crash-time accounting claim.
6. Downgrade removes new checkpoint rows. Stop/drain workers and back up/export before production rollback. Existing jobs and external effects are not undone by schema downgrade.
7. Supporting changes are applied to the repository working tree, not pushed or deployed remotely. All remaining PART 1 tasks are still outstanding beyond the previously completed 1A and the implemented 1C core slice.

## Git and complete source delivery

The sandbox again restored source files/artifacts but reset Git HEAD to PART0 1d023b5. Before changing the 1A bridge, all 36 prior 1A source hashes were verified and its scoped commit was recreated as **63348f2**. Staged AB3 remained byte-for-byte unchanged. The new 1C work stays uncommitted until the entire 1C task is completed, preserving the one-task/one-completion-commit policy.

The complete current source report and ZIP are rebuilt by reports/part1/runtime/build_delivery.py. Every delivered source body is included from first to last, including changed existing files; SHA-256 verifies the ZIP against the working tree. Historical status files inside the source set are audit records: this current status supersedes their earlier not-wired statements. Older 1A/continuation artifacts remain as historical evidence.
