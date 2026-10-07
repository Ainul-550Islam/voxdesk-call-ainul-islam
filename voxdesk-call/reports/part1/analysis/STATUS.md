# PART 1 / 1C — versioned custom analysis applied to main runtime

## Current applied scope (supersedes earlier runtime/STATUS.md)

This is a tested continuation of 1C, not a claim that all of 1C or PART 1 is complete. PART 2 remains deferred. The main default POST_CALL handler now runs:

terminal call transaction → existing durable job → immutable analysis_plan → frozen transcript → governed summary/outcome and sentiment → independently checkpointed custom schemas → transactional call_analyzed outbox fact.

Custom schema CRUD now delegates validation/versioning to app/services/post_call_analysis_service.py. Text, boolean, number, enum, object/custom and bounded primitive lists are supported, with typed examples. Updates preserve prior definition snapshots. Deletes deactivate instead of deleting version history. Schema CRUD and result reads resolve server-selected or credential-bound environments and verify membership; mutations audit in the business transaction. Existing router paths and permissions remain.

Each call pins up to 20 active definitions on its first analysis attempt, not at call start. Retries use those exact definitions despite later edits/deactivation. Provider-validated results use the existing AnalysisResult table with environment, schema version, full definition snapshot and provider provenance. Unique (schema_id, call_id, schema_version) prevents duplicate versioned results. Invalid or unconfigured inference creates no successful AnalysisResult. Completed checkpoints are not reinvoked.

call_analyzed is a reference-only, once-per-call version-one fact published only after every pinned analysis dependency completes. Its outbox write and final checkpoint commit atomically. It certifies analysis, NOT QA review, CRM writeback or workflow completion. An empty schema plan still permits built-in analysis and an empty custom result ID list. Later schema edits do not automatically reanalyze old calls or send a second version-one fact.

## Migration

New head: 0052_analysis_versions after 0051. Existing schemas map to the tenant's default environment; migration fails rather than guessing if no default exists. Legacy result rows, including duplicates/orphans, are preserved. Their version stays NULL and provenance is legacy_unverified; matching tenant-owned calls determine their environment. No legacy provider evidence is invented.

Actual isolated PostgreSQL 17 validation on 127.0.0.1:55432/post_call_test:
- Empty database upgrade through 0052: PASS.
- Independent concurrent sessions: PASS; each of three model invocations executes once after successful commits, six checkpoints persist, a version-one custom result persists, and one call_analyzed fact refers to it.
- Populated downgrade 0052→0051 and reupgrade: PASS.
- One Alembic head: 0052_analysis_versions.

Downgrade loses schema revision/provenance metadata, NOT results themselves. Reupgrade labels retained results unverified. Stop/drain workers, export version metadata and checkpoints, and reconcile surviving jobs/events before any rollback. Schema roundtrip is not restoration of lost metadata. No production database or remote deployment was touched.

## Current measured checks

All commands ran in the repository with restored pinned Python 3.12.15 dependencies. Counts overlap.

```sh
.venv/bin/python -m pytest tests/telephony/test_custom_analysis.py tests/telephony/test_post_call_pipeline.py -q
```

33 passed, 182 warnings in 22.27s.

```sh
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m pytest tests/ai tests/webhooks tests/outbox tests/jobs tests/operations/test_notification_webhook_email.py tests/test_external_success_honesty.py tests/test_call_state.py tests/test_call_callbacks.py tests/test_transfer.py tests/telephony tests/campaign/test_concurrency.py tests/test_legacy_lead_routes.py tests/test_enterprise_batch01.py -q --junitxml=reports/part1/analysis/regression.xml
```

519 passed, 2 skipped, 2988 warnings in 238.54s. The two existing skips are opt-in live provider/echo tests, not LIVE evidence.

```sh
PATH=$PWD/.venv/bin:$PATH OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 make verify-truth
PATH=$PWD/.venv/bin:$PATH make contracts-check
.venv/bin/ruff check app/services/post_call_analysis_service.py app/api/post_call_analysis_routes.py app/db/enterprise_models.py app/telephony/post_call.py alembic/versions/0052_analysis_versions.py tests/telephony/test_custom_analysis.py tests/telephony/test_post_call_pipeline.py reports/part1/runtime/verify_post_call_postgres.py
```

Truth: 41 passed, 59 warnings in 20.70s. Contracts: all five protobuf files compile, vocabulary and tenant scope PASS. Selected Ruff: All checks passed. Runtime route count remains 1157. OpenAPI paths remain identical; only CustomFieldDef and AnalysisSchemaOut components intentionally gain list/examples/version/environment fields. The exact OpenAPI comparison test remains unchanged and passes.

Migration commands and concurrent verifier:

```sh
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic upgrade head
.venv/bin/python reports/part1/runtime/verify_post_call_postgres.py
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic downgrade -1
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic upgrade head
.venv/bin/alembic heads
```

## Initial failures retained

initial-tests.log: existing inference telemetry assertion incorrectly included the newly added non-provider analysis_plan. It now explicitly checks summary/sentiment token counts and asserts empty plan/event telemetry. The former no-call_analyzed assertion was intentionally replaced by exactly-one correctly shaped event assertion for the implemented contract. No assertion was dropped to hide a failure.

core.log: initial permission helper import used the wrong module; corrected to app.auth.rbac. OpenAPI precheck initially found a removed create-route docstring; restored it, then verified all path definitions were unchanged. No test filters, skips or xfails were added.

## Remaining work and limits

- Full 1C still requires real run/backfill/rate/cost-preview/export service/API, QA sampling/review admission, CRM and workflow stages. Legacy backfill and extended analysis endpoints remain unwired facades; their old pending/health responses are NOT accepted as runtime evidence. The analysis facade allowlist entry is intentionally unchanged. This delivery does not certify those endpoints.
- Per-schema field failures are independent; DB failures propagate. Missing providers never produce fabricated output. Provider-response/DB-commit crashes can repeat external inference; no exactly-once guarantee.
- Frozen transcripts remain limited to 1000 turns/6300 characters; schema/gateway prompt budgets can reject oversized requests, not silently truncate them. Unified PII/retention is still 1D work.
- Remaining PART 1 order is 1C completion → 1D → 1E → 1B → 1F → 1G.
- Git HEAD was restored by the sandbox to 1d023b5 again. No claim is made that historical 63348f2 still exists. Prior source is preserved; staged AB3 patch is byte-identical to the saved baseline. No broad staging/reset or unrelated edits were performed, and no premature 1C completion commit was made.

## Full source delivery

reports/PART_1_MAIN_CODE_APPLIED.zip and reports/PART_1_REPORT.md contain complete first-to-last source files, not patches or omitted bodies. reports/part1/runtime/build_delivery.py regenerates the bundle and verifies every source SHA-256 against the applied working tree. Historical logs/status are labeled historical; this file is the current scope/evidence record.
