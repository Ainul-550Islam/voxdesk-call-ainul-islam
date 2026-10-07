# PART 1 / 1C — automatic QA wired into the main runtime

This is the CURRENT delivery record, superseding the historical backfill/analysis/core status sections below. The code is applied in the main working tree. It is not a production deployment or a full 1C/PART 1 completion. PART 2 remains deferred.

## Applied behavior

The canonical POST_CALL pipeline now executes a qa_sampling checkpoint after custom analysis. Existing app/qa/sampling.py admits real SampleSelection, QAReview, AutoReviewRun and canonical QA_AUTO_REVIEW job rows in a nested transaction. Completed checkpoints skip repeated admission. No parallel queue, worker loop, fake agent or invented human actor was introduced. scripts/scheduler.py uses the ordinary JobWorker whose registry now includes the QA handler.

- Opt-in rules bind an exact scorecard and environment. Only percentage rules are automatic; unbound legacy rules and other manual sampling kinds retain their semantics. The stable post-call:v1 hash window provides deterministic selection. Zero percent selects none, 100 percent selects all eligible future calls. Active bindings are capped at 20 per environment under an environment-row lock.
- Current-call selection lookup is bounded, not an ever-growing query returning the entire historical window. Multiple rules for the same call/scorecard share one review/run/job. A sampling admission failure rolls back its selections, review, audit and child job while previously committed analysis remains intact.
- qa_sampling completion certifies admission only. call_analyzed certifies built-in/custom analysis independently; it does not claim a completed AI or human review. Failed QA admission does not block publishing otherwise valid analysis.
- The worker validates persisted tenant/organization/environment, exact payload, review/run/job association, active scope, worker ownership, lease and cancellation. It executes through the existing governed StructuredExecutor with provider routing, budget, SSRF, timeout, fallback/circuit breaker and actual usage recording.
- A complete 1–30-item rubric snapshot is pinned at admission, with prompt version qa-auto-review-v2. Turn references/digest freeze at first execution. Full bounded evidence replaces the old silent 40-turn truncation. Transcript and complete-prompt limits both apply; oversize input fails instead of being silently shortened.
- The whole response validates before any score writes: exact item coverage, no duplicates/unknown fields, integer/range/N/A checks, and evidence IDs limited to the frozen call. Human fields, accepted source, overall score and review state are never finalized or replaced by inference.
- Missing credentials yield not_configured, late missing transcripts can retry, and modified/deleted frozen evidence blocks execution. Legacy missing snapshots and orphan jobs are not fabricated into valid runs. Three canonical automatic attempts remain bounded; authorized explicit job replay may retry nontransient failures while retaining the pinned inputs.
- Admission and validated suggestions emit transactional audit events. A provider response followed by a failed database/audit commit can be repeated on retry; this is not exactly-once provider execution or perfect crash-time billing.

## API and authorization

Four operations were added to the existing QA router, none removed:

1. POST /api/qa/sampling/automatic: QA_WRITE, exact environment access, explicit name/scorecard_id/integer percent, required durable Idempotency-Key. Changed same-key requests conflict. Config write and audit/receipt commit atomically.
2. GET /api/qa/sampling/automatic: QA_READ, environment-scoped bounded pagination; another tenant sees none of the first tenant's rules.
3. DELETE /api/qa/sampling/automatic/{rule_id}: QA_WRITE and environment scope; disables future admission. Foreign IDs return 404. Replaying a previous creation does not re-enable a disabled rule.
4. GET /api/qa/reviews/{review_id}/auto-review: QA_READ and exact environment access; returns stored suggestion/run separately from canonical job state. A result committed before acknowledgement is not reported as completed job execution. Missing/mismatched ledger evidence is inconsistent, absent legacy rubric metadata is legacy_unverified, and cancellation stays visible. Foreign review IDs return 404.

Redis-backed configuration writes allow 20 requests per tenant/environment per 60-second fixed window and fail closed. Existing manual POST auto-review keeps its request contract and now records the authenticated request audit atomically. No process-local authoritative idempotency/rate state was added.

Contract delta verification proves exactly four new operations, unchanged preexisting POST auto-review operation and unchanged preexisting schema definitions. Actual route count is 1163. Exact OpenAPI equality and route-count assertions remain intact.

## Migration and real PostgreSQL evidence

0054_qa_runtime follows 0053_analysis_backfill as the single head. It adds SamplingRule.scorecard_id and AutoReviewRun.rubric_snapshot/transcript_snapshot. No applied migration was edited.

An isolated PostgreSQL 17 database on 127.0.0.1:55432/post_call_test and Redis on 127.0.0.1:56379 were used; no production database or service was touched.

```sh
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic upgrade head
.venv/bin/python reports/part1/qa/verify_postgres.py
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic downgrade -1
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic upgrade head
.venv/bin/alembic heads
.venv/bin/python reports/part1/qa/verify_postgres.py
.venv/bin/python reports/part1/runtime/verify_post_call_postgres.py
```

PASS: empty upgrade; injected audit failure rolls back configuration and receipt; concurrent independent API sessions admit one rule; two concurrent pipeline sessions admit one selection/review/run/job; two canonical workers claim that job once; one measured governed suggestion persists while preserving a seeded human score and never finalizing the review. The existing custom-analysis PostgreSQL verifier also passes with seven actual checkpoints including QA admission.

Populated 0054→0053→0054 round-trip passes. SQL inspection shows the original run remains completed historically, with empty restored snapshots, and the original rule remains with NULL binding. Lost metadata is NOT reconstructed. Stop/drain workers and export/reconcile bindings and snapshots before downgrade; surviving jobs and external effects are not undone. Historical success is not new provenance certification.

## Measured commands and results

```sh
DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m pytest tests/ai tests/qa tests/webhooks tests/outbox tests/jobs tests/operations/test_notification_webhook_email.py tests/test_external_success_honesty.py tests/test_call_state.py tests/test_call_callbacks.py tests/test_transfer.py tests/telephony tests/campaign/test_concurrency.py tests/test_legacy_lead_routes.py tests/test_enterprise_batch01.py -q --junitxml=reports/part1/qa/regression-final.xml
```

580 passed, 3 skipped, 3587 warnings in 197.40s. Existing independent-process PostgreSQL recovery contracts are included. Deprecation warnings remain; they were not disabled.

```sh
.venv/bin/python -m pytest tests/qa tests/telephony -q
.venv/bin/python -m pytest tests/telephony/test_post_call_pipeline.py tests/ai/test_post_call_llm.py -q
PATH=$PWD/.venv/bin:$PATH OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 make verify-truth
PATH=$PWD/.venv/bin:$PATH make contracts-check
.venv/bin/ruff check app/qa/auto_review.py app/qa/models.py app/qa/sampling.py app/api/qa_routes.py app/telephony/post_call.py app/jobs/registry.py alembic/versions/0054_qa_runtime.py tests/qa/test_post_call_runtime.py tests/qa/test_auto_review.py tests/telephony/test_post_call_pipeline.py reports/part1/qa/verify_postgres.py reports/part1/runtime/verify_post_call_postgres.py
.venv/bin/python -m pytest tests/qa/test_post_call_runtime.py tests/ai/test_post_call_llm.py tests/webhooks/test_webhook_lifecycle_real.py -m live -q -rs
```

- QA/telephony: 143 passed, 1 skipped, 1137 warnings in 67.73s.
- Named 1C modules: 47 passed, 1 skipped, 247 warnings in 16.51s.
- Truth: 41 passed, 59 warnings in 15.60s.
- Contracts: five protobuf files compile, enum vocabulary and tenant-scope checks PASS.
- Selected Ruff: All checks passed.
- Explicit live command: 3 skipped, 63 deselected. Missing QA live opt-in/credential, existing post-call live credential, and signed echo receiver. These are NOT LIVE passes. The new QA test requires VOXDESK_LIVE_QA=1 and QA_LIVE_OPENAI_API_KEY and incurs a real model call.
- Counts overlap across commands. Recording-fake HTTP tests assert actual request method/host/auth/body and persist the resulting suggestion/usage; they do not certify live credentials.

Raw initial and final outputs are retained, including failures and corrections. No existing test was deleted, disabled, filtered away or marked skip/xfail to evade a failure. The new live skip is credential-gated as required by the task.

## Corrections discovered by verification

- Old scoring fixtures had no transcript despite expecting a model score. They now persist an actual stored Turn. The initial mistaken Speaker.AGENT fixture enum was corrected to the real Speaker.ASSISTANT. A dedicated missing-transcript test still requires failure without provider invocation.
- The existing pipeline checkpoint assertion was intentionally extended to require qa_sampling and its exact empty-selection output; it was not relaxed to ignore extra/missing steps.
- Forged-scope tests initially expected a handler category where the existing outer worker rejects tenant/environment mismatches first. Assertions now require those exact outer categories. The malformed bool version fixture explicitly marks JSON dirty because Python True compares equal to 1; without that, SQLAlchemy correctly emitted no update and the fixture never persisted malformed data.
- An intermediate focused run exposed SQLite numeric-affinity corruption for a UUID whose hex resembles scientific notation. QA explicit UUID columns now use SQLAlchemy's portable Uuid type: native UUID on PostgreSQL, character storage on SQLite. Two deterministic numeric/scientific-notation UUID round-trip tests prevent hiding this with a lucky random rerun. PostgreSQL runtime was reverified after the correction. This is scoped to QA's explicit UUID columns, not a claim that every legacy module's SQLite type declaration was audited.
- Sampling uniqueness handling now verifies an actual duplicate before treating IntegrityError as deduplication; unrelated integrity errors propagate.

## Source delivery and remaining scope

reports/PART_1_MAIN_CODE_APPLIED.zip and reports/PART_1_REPORT.md contain complete applied files, not shortened patches, plus raw verification outputs and SHA-256 manifest. Before-edit backups remain under reports/part1/qa/before. No new generated bulk source or null-byte source was introduced.

The existing staged AB3 patch is unchanged: SHA-256 d6381eee5322f484ca1ebfdb3a1ff42450ca7a29ba970f1b2eee98a650d65b10 before and after. No commit was made: full 1C is not closed, and its one task-ID completion commit must not sweep unrelated staged work into it.

Remaining 1C: post-call CRM write-back, workflow trigger execution and the legacy extended analysis facade closure. The CRM hook presently has a legacy swallowing wrapper and workflow trigger CRUD alone is not execution; neither was relabelled as complete. The corresponding facade allowlist remains. Next order remains 1C completion → 1D → 1E → 1B → 1F → 1G. No later part is claimed complete, no missing live credential is counted as success, and no production-ready claim is made.
