# Post-call analysis core — current runtime wiring

## Applied execution path

The code is applied in the main repository, not just supplied as a patch:

1. The existing terminal transition/provider/API producers publish `call_ended` through `app/webhooks/call_event_bridge.py`.
2. That bridge admits `JobType.POST_CALL` using the same session as the call change and outbox event. The durable key is `post-call:{call_id}:v1`. An admission failure propagates; rollback removes all three writes.
3. `app/jobs/registry.py::bootstrap` registers the post-call handler. The ordinary `JobWorker` used by `scripts/scheduler.py` already calls this bootstrap. No second queue, worker loop or service is introduced.
4. `PostCallPipeline` reads tenant, organization and environment from persisted job columns, locks the owned Call, and commits real per-step checkpoints.
5. Before inference, active environment-scoped custom definitions are pinned in an immutable analysis_plan.
6. Stored turns are finalized as immutable references plus a digest. Summary/outcome and sentiment run through `app/ai/post_call_llm.py` and the existing governed AI gateway.

Direct arbitrary writes to `Call.status` that bypass the existing event bridge are not a new event producer. Historical calls are processed only through explicit, bounded backfill admission; there is no automatic historical scan.

## What is stored

Migration **0051_post_call_pipeline**, following **0050_unify_webhooks**, adds `post_call_step_runs`. Each `(call_id, step, pipeline_version)` is unique. Rows carry tenant/environment scope, job reference, status, attempts, retry classification, safe error code, output, telemetry and timestamps.

Migration **0052_analysis_versions** extends the existing AnalysisSchema/AnalysisResult tables with scoped definitions, immutable revision history and explicit result provenance. Legacy results are preserved as unverified, with NULL versions.

Current executable steps:

| Step | Actual effect |
| --- | --- |
| analysis_plan | Pins up to 20 active schema definitions and versions before inference |
| transcript | Checks stored turns; saves turn IDs, count and digest; closes matching queued/processing TranscriptJob rows from existing text evidence |
| summary | Stores validated model summary and outcome; fills an empty Call.summary without overwriting an existing human/runtime summary |
| sentiment | Stores the model's validated sentiment independently of summary success |
| schema:{id}:v{version} | Validates custom output through the governed gateway and atomically persists AnalysisResult plus checkpoint |
| analyzed_webhook | Checks every pinned analysis dependency, then commits call_analyzed through the existing outbox with its final checkpoint |

A model outcome is a classification, not proof of a booking, transfer or human QA decision. The pipeline does not set `booked=True` or fabricate a human outcome event from model text.

Raw transcripts are not copied into job payloads, checkpoints or exception logs. Analysis output itself is ordinary application data, not a claim of complete PII/retention enforcement; the unified 1D privacy work remains pending.

## Failure, replay and concurrency

- Each step owns a transaction and locks its scoped Call. Completed checkpoints are skipped on retry and on duplicate pipeline invocation.
- A failed summary does not prevent independent sentiment processing. Provider errors are stored as safe categories, not provider bodies or transcripts. Database failures propagate rather than pretending later steps committed.
- Missing model credentials yield durable `not_configured` steps and a failed/dead-letter job, never an invented summary or successful analysis.
- Retryable failures use the existing durable job retry policy; admission allows four attempts. Nontransient steps are retried only when explicitly requested by an operator replay, using the existing durable replay count.
- Late transcript arrival can retry. Deleted or changed frozen evidence is not silently replaced, and erased text is not reconstructed from checkpoints.
- Cancellation intent is checked between steps. A running inference cannot be made externally atomic with a database transaction.
- Successful checkpoint serialization was tested with independent PostgreSQL sessions. Crash after provider response but before checkpoint commit can repeat inference; this is not exactly-once provider execution or perfect crash-time usage accounting.
- Finalized transcript references have a maximum of 1000 turns and rendered input of 6300 characters. Larger input is explicitly `unsupported`, not silently truncated. Governed long-call chunking remains pending.

## Historical custom analysis

`POST /api/analysis/backfill` admits a pinned custom schema for 1–200 distinct terminal calls in the resolved tenant/environment. Supply `Idempotency-Key` or the body key; conflicting keys fail. Use one call ID for a single-call run. Omitted IDs select by timezone-aware dates; oversized selections are rejected, not truncated. Same-key request changes return 409.

`analysis.backfill` is registered in the existing worker. The manifest stores schema/call/job references; each child uses the existing governed extractor and scoped step checkpoints. Redis is mandatory: 10 admission requests and 30 execution attempts per tenant/environment per 60-second fixed window, with initial two-second scheduling. Missing configuration fails closed. Six bounded attempts use the canonical retry/DLQ policy; limits do not guarantee eventual success under sustained overload.

Progress comes from canonical job acknowledgements. Legacy manifests without job evidence are `legacy_unverified`; missing ledger rows are `inconsistent`. Jobs can be cancelled/replayed through the existing authorized job controls. New requests for an already-completed schema version reuse its result; changing the schema version permits new extraction. This is not a force-refresh of the same version.

`POST /api/analysis/backfill/preview` performs a read-only heuristic estimate using existing routing/policy/pricing. Unknown price is null, not zero; retries and fallback are excluded and no quota is reserved. `GET /api/analysis/results/export` downloads one bounded JSON page with pagination metadata, not an unbounded database dump.

Backfill executes the chosen custom schema only and does not emit another once-per-call `call_analyzed` event or claim downstream QA/CRM/workflow completion. Full-analysis event publication remains with ordinary POST_CALL processing.

## Deployment and migration

Run `alembic upgrade head` before starting this code's workers. Use the existing scheduler (`python -m scripts.scheduler`) and deployment-specific provider credentials/budget policy. The code was tested in an isolated local PostgreSQL database; no user production database was migrated and no hosted deployment was performed.

Migration **0053_analysis_backfill** adds manifest scope, version snapshot, selection and child job IDs to the existing backfill table. Downgrading to 0052 loses those references but leaves child jobs and results; stop/drain workers and export/reconcile manifests before rollback. Reupgrade does not invent lost references.

Downgrade from 0052 to 0051 removes schema/result version metadata but preserves result bodies. Reupgrade cannot restore lost metadata and marks them legacy_unverified. Further downgrade to 0050 removes the checkpoint table, not existing Call.summary or billed UsageEvent rows. It cannot undo a model invocation. Stop/drain workers and export checkpoints before downgrade; losing checkpoints can permit repeated inference if new workers are resumed against surviving jobs. Schema roundtrip is not checkpoint data restoration.

## Acceptance evidence and remaining work

Current verification: **532 passed, 2 skipped** in selected regression, **41 passed** in the truth gate and **46 passed** in focused post-call/backfill tests. Counts overlap. Configured Ruff and contracts-check pass. Real PostgreSQL/Redis verifies concurrent admissions, audit rollback, two-worker claim-once, pinned results/progress and populated migration roundtrip. See `reports/part1/backfill/STATUS.md` and raw logs for commands and initial failure corrections.

There are **1159 routes**, including two added scoped preview/export endpoints. Exact runtime OpenAPI and route-count tests pass against the intentionally updated contracts.

This is not full 1C or PART 1. QA sampling/review admission, CRM and workflow stages and the remaining legacy extended analysis facades are still pending. Their facade allowlist entry remains. Unified PII/retention is still 1D work.

The two skipped live tests require external opt-in/credentials; neither is claimed LIVE. Source changes are applied locally, not deployed to production.
