# PART 1 / 1C — durable backfill, preview and export applied

This is the CURRENT delivery record and supersedes the historical analysis/core status files included below. Main working-tree code is applied, not deployed remotely. Full 1C and PART 1 are still unfinished; PART 2 remains deferred.

## Actual runtime path

POST /api/analysis/backfill → scoped schema lock → strict Redis admission limit → existing durable idempotency receipt → pinned BackfillJob manifest and canonical jobs in one transaction with an audit event → existing JobWorker/registry → analysis.backfill handler → scoped call lock and current claim validation → strict Redis execution limit → frozen transcript references → existing governed extraction → AnalysisResult and PostCallStepRun commit → ordinary guarded worker acknowledgement.

No parallel queue or worker loop was introduced. The BackfillJob is a selection/version manifest over the existing jobs table. Its historical counter columns are not treated as execution evidence: API progress is calculated from scoped canonical job rows. Deleted or mismatched job references produce inconsistent, not completed. Cancellation is reported separately; all-cancelled batches report cancelled. Old backfill records without actual job references are legacy_unverified; original historical counters remain stored but are not certified as processed work.

- Selection: 1–200 distinct terminal calls in the authenticated tenant/environment. Explicit IDs are all-or-nothing, including date filters. Omitted IDs use a bounded date selection; >200 is rejected instead of silently truncated. Dates require timezone offsets and are normalized to UTC before querying.
- Version: the entire current schema definition is pinned at admission. Later edits/deactivation do not rewrite admitted work. Existing results for the exact pinned version can be reused; a new schema version yields a separate result.
- Idempotency: existing transactional receipt uniqueness arbitrates concurrent replicas. Same key/different request returns 409. Header-only or body keys work; conflicting header/body keys are rejected. New manifest keys are hashed; raw keys are not returned in status responses.
- Admission rate: 10 requests per tenant/environment per 60-second Redis window. Execution rate: 30 extraction attempts per tenant/environment per 60-second Redis window. Initial children are scheduled two seconds apart. Redis owns counters/TTL, never process-local state. Missing Redis returns NOT_CONFIGURED on admission; outages/limits fail closed. Fixed-window limits are not smooth per-second throughput guarantees.
- Execution: up to six canonical job attempts with existing retry/backoff/DLQ/replay policy. Rate denial and late transcript availability can retry. Provider/configuration/governance failures remain honest failures. No fabricated output, human actor, booking or QA outcome.
- Progress: canonical acknowledgement drives succeeded counts, not merely provider output or a handler return. A committed result before failed acknowledgement is recoverable without reinvoking a completed checkpoint. Provider-response/DB-commit crashes remain at-least-once external effects, not exactly-once inference.
- Backfill executes one requested custom schema only. It does not certify full built-in analysis, emit another version-one call_analyzed fact, or replay downstream QA/CRM/workflows. The ordinary POST_CALL path still owns full-analysis event publication.

## API additions

POST /api/analysis/backfill/preview is read-only: bounded selection plus a heuristic token/cost estimate from existing tenant routing, governance policy and billing.cost prices. Formula: approximate characters/4 plus 2048 output tokens per selected call. Estimates exclude retries/fallback, do not reserve budget, do not certify transcript eligibility and are not tenant charges. Unknown price remains null/known=false, never fabricated zero.

GET /api/analysis/results/export downloads one scoped JSON page, with explicit limit/offset/total and Cache-Control: no-store. It is not an unbounded full-database export. Foreign schema/call filters return 404. Both new routes require QA_READ plus resolved environment access. Backfill writes continue to require QA_WRITE. A single-call run uses the same real backfill endpoint with one call ID.

Actual route count: 1159 (two additions, none removed). Existing path changes are limited to the backfill description and its reviewed input/output schema components. The exact OpenAPI comparison and route-count tests remain intact.

## Migration and real PostgreSQL verification

New single head: 0053_analysis_backfill, after 0052_analysis_versions. It adds environment scope, pinned definition, call selection and canonical job ID references to the existing backfill_jobs table. Legacy rows get scope only through a same-tenant schema; no historical jobs or successful executions are invented.

A disposable PostgreSQL 17 database at 127.0.0.1:55432/post_call_test and Redis at 127.0.0.1:56379 were used. No production services or databases were touched.

```sh
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic upgrade head
.venv/bin/python reports/part1/backfill/verify_postgres.py
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic downgrade -1
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic upgrade head
.venv/bin/alembic heads
.venv/bin/python reports/part1/backfill/verify_postgres.py
```

PASS: empty upgrade through 0053; audit failure rolls back manifest/jobs/receipt; independent concurrent API admissions create one batch and one job; two canonical workers claim that job once; the pinned schema survives an edit; one actual stored result drives completed progress. Populated downgrade/reupgrade passes. SQL inspection proves backfill rows and results remain, lost manifests are NOT reconstructed/fabricated, and existing result provenance is preserved. The verifier passes again after reupgrade.

Downgrade discards backfill manifests/references but does not cancel surviving jobs or undo provider effects. Stop/drain workers and export manifests/checkpoints first; do not resume surviving jobs after rollback without explicit reconciliation. Reupgrade is not restoration of deleted metadata. No production-ready or LIVE claim is made.

## Measured acceptance commands

```sh
.venv/bin/python -m pytest tests/telephony/test_analysis_backfill.py tests/telephony/test_custom_analysis.py tests/telephony/test_post_call_pipeline.py -q
```

46 passed, 372 warnings in 30.97s. This includes 13 new backfill tests: recorded provider HTTP request/response, pinned version, idempotent admission, ownership, nonterminal refusal, mismatch conflicts, default registered worker failure without credentials, atomic audit rollback, Redis fail-closed and actual exhausted Redis budget, legacy honesty, preview/export scope, bounded selection, cancellation/missing ledgers, RBAC and UTC-offset selection.

```sh
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m pytest tests/ai tests/webhooks tests/outbox tests/jobs tests/operations/test_notification_webhook_email.py tests/test_external_success_honesty.py tests/test_call_state.py tests/test_call_callbacks.py tests/test_transfer.py tests/telephony tests/campaign/test_concurrency.py tests/test_legacy_lead_routes.py tests/test_enterprise_batch01.py -q --junitxml=reports/part1/backfill/regression-final.xml
```

532 passed, 2 skipped, 3178 warnings in 234.35s. Existing two-process PostgreSQL recovery contracts are included. An earlier intermediate regression was 531 passed/2 skipped before the additional offset/export test; both outputs are retained, not conflated.

```sh
PATH=$PWD/.venv/bin:$PATH OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 make verify-truth
PATH=$PWD/.venv/bin:$PATH make contracts-check
.venv/bin/ruff check app/services/post_call_analysis_service.py app/api/post_call_analysis_routes.py app/jobs/types.py app/jobs/registry.py app/db/enterprise_models.py alembic/versions/0053_analysis_backfill.py app/resilience/idempotency.py tests/telephony/test_analysis_backfill.py reports/part1/backfill/verify_postgres.py
.venv/bin/python -m pytest tests/ai/test_post_call_llm.py tests/webhooks/test_webhook_lifecycle_real.py -m live -q -rs
```

Truth: 41 passed, 59 warnings in 19.74s. Contracts: five protobuf files compile; enum and tenant-scope checks PASS. Selected Ruff: All checks passed. Explicit live command: 2 skipped, 43 deselected; live opt-in/provider credential and signed echo receiver are not configured. These are not LIVE successes. Counts across commands overlap.

## Bugs found and corrected without weakening tests

Initial focused tests: 5 passed, 2 failed. SQLite legacy transaction mode released the first SAVEPOINT outside a real outer transaction, committing an idempotency receipt despite later rollback. This both broke audit atomicity and poisoned a subsequent rejected call-selection request with a conflicting receipt. app/resilience/idempotency.py now explicitly begins SQLite's native transaction when needed before opening its receipt savepoint. PostgreSQL behavior is unchanged. The original rollback and 404 assertions remain and pass; independent PostgreSQL audit rollback is separately verified.

Initial Ruff correctly rejected misplaced imports and unused fixture imports; those were corrected. Initial logs are retained. No tests were disabled, removed, weakened or marked skip/xfail. Contract snapshots changed only for actual API additions and intentional backfill schema changes.

## Remaining PART 1 scope

Full 1C still requires QA sampling/QA_AUTO_REVIEW admission, CRM writeback and workflow stages, plus replacement of the remaining legacy extended analysis facades. Those old extended health/bulk/metrics responses are NOT accepted as runtime evidence. The analysis facade allowlist entry remains until verified closure. Retention/PII 1D, enterprise control-plane cleanup 1E, campaign-backed batch 1B, real Salesforce 1F and runtime A/B 1G remain in the requested order. PART 2 is deferred.

No completion commit for unfinished 1C was made. Existing staged AB3 is byte-identical to the turn-start patch. No blanket stage/reset or unrelated source edits occurred. Full first-to-last applied files, current evidence, manifests and historical evidence are in PART_1_MAIN_CODE_APPLIED.zip and PART_1_REPORT.md; source SHA-256 is verified against the working tree.
