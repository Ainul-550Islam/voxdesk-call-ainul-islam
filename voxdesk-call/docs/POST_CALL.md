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
| qa_sampling | Evaluates explicitly bound percentage rules and atomically admits real selections, reviews and QA_AUTO_REVIEW jobs; completion means admission, not scoring |
| analyzed_webhook | Checks every pinned analysis dependency, then commits call_analyzed through the existing outbox with its final checkpoint |
| crm_admission | Admits opted-in scoped analysis to the existing CrmEvent/CrmSync queue; delivery acknowledgement is tracked separately |
| workflow_admission | Pins explicitly bound triggers to published workflow versions and analysis digests; admits canonical workflow.execution jobs, not fictional completed actions |

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

## Automatic QA sampling and suggestion

Migration **0054_qa_runtime** adds an explicit SamplingRule.scorecard_id binding and immutable rubric/transcript-reference snapshots on AutoReviewRun. The ordinary POST_CALL pipeline runs qa_sampling after custom analysis. Only enabled, explicitly bound percentage rules in the exact call environment are automatic. Legacy unbound rules and count/min-per-agent/manual modes retain their manual semantics. No arbitrary/latest scorecard is selected. At most 20 automatic rules are active per environment; 0 percent selects none and 100 percent selects all eligible future calls.

Configure through `POST /api/qa/sampling/automatic` with a durable `Idempotency-Key` header and a body containing name, scorecard_id and integer percent. This requires QA_WRITE and environment access. `GET /api/qa/sampling/automatic` lists scoped rules with bounded pagination; `DELETE /api/qa/sampling/automatic/{rule_id}` disables future admission. Redis-backed configuration writes are limited to 20 attempts per tenant/environment per 60-second fixed window and fail closed. Configuration and audit/receipt writes share one transaction. Same-key changes conflict; replaying a disabled rule's creation does not re-enable it.

The existing deterministic bucket uses the stable post-call:v1 window. Current-call lookup is bounded rather than returning every historical selection in that window. Duplicate rules targeting one scorecard share the same call/scorecard review and durable job. Admission errors roll back all children of that checkpoint. The separately committed call_analyzed fact certifies built-in/custom analysis, not QA scoring, and is not blocked by a QA admission failure.

The existing worker registry now executes QA_AUTO_REVIEW through the governed StructuredExecutor: actual stored tenant/organization/environment scope, current lease and cancellation checks, provider budgets, SSRF, timeout/fallback/circuit-breaker policy, and measured usage accounting. The rubric (1–30 items) is pinned at admission; prompt version is qa-auto-review-v2. Transcript IDs and digest freeze at first execution. Full bounded evidence is used without a silent 40-turn truncation. Both transcript limits and the gateway's complete-prompt limit apply; oversized prompts fail rather than silently dropping evidence.

Every model response must cover each pinned item exactly once with valid integer/range/N/A semantics and owned frozen evidence IDs. The entire response validates before any score is written. Only suggestion columns change; human scores, accepted source, final score and review state remain unchanged. Missing credentials become not_configured, missing transcript can retry, and changed/deleted frozen evidence blocks execution. Orphan preexisting jobs and unsupported legacy prompt/snapshot metadata are not guessed into valid runs. Canonical jobs allow three automatic attempts; explicit authorized replay can retry a nontransient failure without changing pinned inputs.

`GET /api/qa/reviews/{review_id}/auto-review` requires QA_READ and exact environment access. It returns the stored run separately from the canonical job acknowledgement. A provider result committed before acknowledgement is not presented as a completed job. Missing/mismatched job evidence is inconsistent, missing legacy rubric metadata is legacy_unverified, and cancellations stay visible. Existing job controls own cancellation/replay; no second scheduler or queue was introduced.

Admission and successful suggestion emit transactional audit events. A provider response followed by a database/audit failure can still be repeated on retry: this is not exactly-once external execution or perfect crash-time billing.

## Post-call CRM delivery

The existing `PUT /api/integrations/crm/{provider}` now accepts `config.post_call_analysis_environment_id`. Supply the exact environment UUID alongside that provider's normal configuration. The caller must have INTEGRATION_WRITE and access to both old/new bound environments. Foreign environments return 404. Removing the binding stops future analysis admission. The connection remains tenant-wide and supports one analysis environment per provider; this does not introduce a second connector model or multi-environment credential store.

Configuration writes involving an analysis binding use strict Redis throttling (20 requests per tenant per 60-second fixed window) and commit their audit atomically. Existing credential encryption and URL validation remain. Legacy unbound connections do not automatically receive the new analysis event. Normal subscribed-event filters still apply.

After the completed analysis barrier, crm_admission captures summary/outcome, sentiment and actual custom result values into a separate `post-analysis:v1` CRM event. It does not re-use an earlier call-ended payload that might lack analysis. Raw transcript text/references are not added. The stage records event/sync IDs and **admitted**, not delivered. With no opted-in connection it records **not_requested**. The existing scheduler CRM loop sends these rows; no parallel queue or worker is added.

Dispatch rechecks tenant, active environment, surviving Call and the connection's environment binding. Analysis deliveries use Redis (30 attempts per tenant/environment/provider per 60-second fixed window), not the legacy local token bucket. New analysis contact fingerprints include environment scope. Missing configuration fails, and changed scope prevents delivery.

A contact write alone is insufficient. The selected provider must support CREATE_NOTE, and the analysis note must return an acknowledgement before the sync becomes SYNCED. Note errors use the existing bounded retry policy; note-less providers return unsupported. The persisted external_id for this event is the **note** acknowledgement, while the contact link retains the contact ID. Delivery audit and SYNCED state commit together. The note title includes the call ID, avoiding the legacy webhook's same-title key collision between different calls.

Event and contact-link uniqueness races use savepoints rather than rolling back a caller's entire business transaction. A failure after the provider accepts a note but before the acknowledgement transaction commits remains an at-least-once delivery window; exactly-once notes across arbitrary vendors are not claimed. Rebinding credentials, deletion/retention policy and the broader generic CRM/Salesforce unification remain 1F/1D work. The legacy non-analysis CRM path retains its existing enrichment/rate behavior and is not recertified by this addition.

Existing successfully acknowledged POST_CALL jobs are not automatically rescanned by deployment. Explicit authorized replay can execute a newly added missing admission checkpoint. Backfill remains custom-analysis-only and does not send CRM updates.

## Extended analysis compatibility endpoints

The nine `/api/analysis/extended/*` paths remain, but the generated facade helpers, invented totals and fake healthy/processed/cache-cleared responses have been removed. These response bodies intentionally differ from the old placeholders:

- `GET health` performs an actual scoped schema-count query. `database=reachable` means that query succeeded; `provider_connectivity=not_probed` is not a model-provider health claim. Database failures use the application's structured 503 handler.
- `GET stats` returns stored schema/result/backfill-manifest counts, active schema count and stored provenance groups. A manifest is not proof of execution; legacy results are not promoted to verified output.
- `GET metrics` groups actual POST_CALL/ANALYSIS_BACKFILL job states separately from post-call checkpoint states. It never derives completion from legacy BackfillJob counters. These observational aggregates are not a single cross-query, repeatable-read accounting snapshot.
- `GET config` exposes the real create-definition JSON schema and implemented backfill limits, without secrets or invented feature settings.
- `POST validate` runs the same Pydantic definition contract and field compiler as create. It is read-only. Structurally invalid definitions return `valid=false`; raw input/context are excluded from structural error details.
- `GET audit` returns only bounded, ordered `analysis.*` audit metadata in the exact tenant/environment, not event bodies, actor email/IP or all tenant audits. It requires AUDIT_READ plus QA_READ, and environment audit access.
- `GET list` delegates the real schema listing with the existing bounded pagination.
- `POST bulk` accepts the canonical backfill body, including **body `idempotency_key`**. It delegates the same scoped admission, Redis limits, durable receipt, jobs and atomic audit transaction. The compatibility path keeps HTTP 200; the canonical creation path keeps HTTP 201. Both return stored admission/progress evidence, not a processed-items success assertion. The alias does not accept an idempotency header; use the canonical endpoint if that is required.
- `DELETE cache` requires QA_WRITE/environment write access and returns **501 UNSUPPORTED_CAPABILITY**. No cache exists to clear, and persisted evidence is not deleted under that label.

Read/validate compatibility operations require QA_READ; bulk/cache require QA_WRITE. The audit path has the additional permissions above. Explicit environment revocation denies access even when the tenant role is owner. The analysis route file's fake-success allowlist exemption was removed only after real API, isolation, admission/worker, rollback and PostgreSQL verification. Other registered facades remain unchanged.

## Post-call workflow execution

Publish a workflow through the existing workflow service, then use `POST /api/workflows/triggers` with `Idempotency-Key` and:

```json
{
  "workflow_id": "<persisted workflow UUID>",
  "event_type": "after_call",
  "config": {"post_call_environment_id": "<selected environment UUID>"},
  "is_enabled": true
}
```

The binding is explicit: legacy unbound triggers do not begin executing after deployment. Workflows remain the existing tenant-wide reusable definitions, while each automatic trigger and execution is authorized/bound to an exact call environment. Configuration requires TENANT_UPDATE at tenant and environment levels. Static trigger routes are registered before the dynamic workflow-ID route, fixing the previous GET-list shadowing. Browser CORS permits Idempotency-Key.

Supported post-call events are after_call, on_completion (completed call), on_failure (failed/no_answer/cancelled call), and on_booking (actual persisted booked=True, never a model prediction). They run after the successful analysis barrier, not before-call or during transfer. At most 20 bound triggers per environment are retained, including disabled ones. Configuration uses a strict Redis limit of 20 per 60 seconds; execution uses 30 per tenant/environment per 60 seconds. Missing Redis fails closed. Creation uses the existing durable receipt; a changed same-key body conflicts and replay does not re-enable a disabled trigger. Mutations and audits share a transaction.

Admission pins the existing published WorkflowVersion ID, a graph digest and an analysis-input digest in the existing job ledger. The job payload contains references/digests, not transcripts or model text. Re-publication does not select the latest graph for an already admitted job. Changed/deleted evidence, disabled/rebound triggers, inactive scope or mismatched versions block execution. Every job must have the canonical call/trigger idempotency identity and a committed admission checkpoint; arbitrary queue creation cannot bypass admission.

The registered workflow.execution handler uses the existing workflow interpreter/repository/tables, not the legacy process-local WorkflowExecutor fallback. It locks the scoped Call and job, checks the current lease/cancellation, and executes a bounded graph inside one database transaction. Supported nodes are trigger, condition, action, terminal and recorded handoff intent. Supported actions are update_lead_status, apply_dnc (the existing lead lifecycle), record_escalation_intent and create_followup_intent. Lead actions can only target this call's tenant/environment-owned lead. A graph-supplied lead override is rejected. Conditions receive summary, sentiment, outcome, booked, call_status and custom results keyed by schema UUID.

Timers/delays, retry/timeout nodes, approval suspension, notification delivery, conversation tagging and resolution facades are **not** represented as implemented post-call effects. Binding those graphs returns 501 UNSUPPORTED_CAPABILITY; a subsequently published unsupported graph fails its child job. Handoff/follow-up/escalation intents are stored workflow evidence, not actual calls or delivered notifications. This bounded capability contract closes post-call trigger wiring, not generic workflow feature parity.

For supported graphs, database actions, execution checkpoint and completion audit commit together. A failed graph rolls back its actions and records a failed execution with effects_committed=false; step entries describe attempts, not committed effects. Audit/database failure cannot leave successful effects without execution evidence. Queue retry after a committed result reuses that result. Explicit authorized replay can retry a failed attempt without changing its pinned inputs. Cancellation is checked before execution and cannot undo already committed actions. No external exactly-once claim is made.

Use `GET /api/workflows/calls/{call_id}/executions?limit=50&offset=0` with CALL_READ and environment access. It returns actual workflow execution evidence separately from job acknowledgement/error state. Foreign calls return 404. Environment-managed executions are excluded from the legacy tenant-only manual execution inspection/history; canonical job controls own their cancellation/replay. No process-local queue, new scheduler or migration is introduced.

Completed historical POST_CALL jobs are not automatically rescanned. Explicit operator replay can run the new missing admission stage; normal retries skip completed stages. Custom-schema backfill does not trigger workflows.

## Deployment and migration

Run `alembic upgrade head` before starting this code's workers. Use the existing scheduler (`python -m scripts.scheduler`) and deployment-specific provider credentials/budget policy. The code was tested in an isolated local PostgreSQL database; no user production database was migrated and no hosted deployment was performed.

Migration **0053_analysis_backfill** adds manifest scope, version snapshot, selection and child job IDs to the existing backfill table. Downgrading to 0052 loses those references but leaves child jobs and results; stop/drain workers and export/reconcile manifests before rollback. Reupgrade does not invent lost references.

Downgrade from 0054 to 0053 discards automatic rule bindings and frozen-input metadata, but retains rules, runs, suggestions and jobs. Stop/drain workers and export/reconcile bindings/snapshots before rollback. Reupgrade leaves rules unbound and snapshots empty; it does not reconstruct them or certify legacy scoring.

Downgrade from 0052 to 0051 removes schema/result version metadata but preserves result bodies. Reupgrade cannot restore lost metadata and marks them legacy_unverified. Further downgrade to 0050 removes the checkpoint table, not existing Call.summary or billed UsageEvent rows. It cannot undo a model invocation. Stop/drain workers and export checkpoints before downgrade; losing checkpoints can permit repeated inference if new workers are resumed against surviving jobs. Schema roundtrip is not checkpoint data restoration.

## Acceptance evidence and remaining work

**1C's bounded local implementation is complete:** terminal admission, transcript finalization, governed built-in/custom analysis, durable backfill, sampled QA, analyzed webhook, CRM admission/delivery and post-call workflow admission/execution are wired to the main runtime. Unsupported capabilities and absent providers remain explicit failures, not fabricated results.

Final selected regression: **1022 passed, 4 skipped**. New workflow module: **25 passed**. Truth: **41 passed**. Counts overlap. Ruff and protobuf contracts pass. A combined test verifies stored built-in/custom analysis, QA review/run, a real lead transition and receipt of signed call_analyzed bytes by a local HTTP server. The model is an injected fixture and the webhook uses a test-only public-URL-to-loopback relay; this does not certify hosted TLS or live providers. Separate existing recording-HTTP contracts cover model/QA/CRM requests.

PostgreSQL verifies two independent pipeline sessions and workers, once-only persisted workflow effects, audit/checkpoint atomicity and nine core/custom checkpoints. Fresh migration and 0054 → 0053 → 0054 roundtrip pass. The sole head is 0054_qa_runtime. There are **1164 routes**, with one new scoped execution-read endpoint and no removed paths. Reviewed exact OpenAPI and route guards pass. Evidence: `reports/part1/workflow-runtime/STATUS.md` and raw logs.

Remaining PART 1 order: **1D → 1E → 1B → 1F → 1G**. Unified PII/retention, enterprise-wide durable state, batch dialing, generic Salesforce/CRM unification and runtime A/B publication are not certified here. PART 2 has not started. Staged AB3 and unrelated working-tree changes are preserved; the task commit is recorded in workflow-runtime/commit.log. This is local implementation acceptance, not a production deployment or clean-checkout certification of other uncommitted foundation work.

Four external live tests are skipped in the selected regression. None is claimed LIVE. The new CRM live test requires VOXDESK_LIVE_CRM_ANALYSIS=1, CRM_ANALYSIS_WEBHOOK_URL and CRM_ANALYSIS_WEBHOOK_SECRET for a real signed HTTPS receiver. It uses fixture analysis to test real CRM delivery, not a live model. Recording-fake contracts validate request behavior, not live credentials. Source changes are local, not a production deployment.
