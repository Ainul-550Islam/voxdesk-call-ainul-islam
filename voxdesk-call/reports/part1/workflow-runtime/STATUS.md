# PART 1 / 1C — local runtime implementation completed

Date: 2026-10-07. This delivery finishes the remaining post-call workflow wiring in the main repository, preserving all previous 1C analysis/backfill/QA/CRM implementation. It is not a production deployment, LIVE certification, completion of all PART 1, or generic workflow feature-parity claim. PART 2 has not started.

## Delivered runtime

Terminal call events transactionally admit POST_CALL. The existing worker executes scoped transcript, built-in summary/outcome/sentiment, pinned custom schemas, sampled QA admission, analyzed outbox publication, CRM admission and now workflow admission. Each stage has durable scoped checkpoints and independent failure/replay semantics. Existing child workers execute QA, CRM delivery and workflow actions; parent admission is never reported as downstream completion. Missing provider credentials produce not_configured, not synthetic outputs.

The workflow adapter uses existing Workflow/WorkflowVersion/WorkflowExecution/ExecutionCheckpoint models, the existing graph interpreter and the registered canonical workflow.execution job type. No parallel executor, scheduler, process-local queue or new migration was introduced. Explicit post_call_environment_id configuration activates existing triggers. Published graph references/digests and analysis-input digests pin admitted work. Scope, admission identity, publication, evidence, lease and cancellation checks fail closed. PostgreSQL/Redis remain authoritative.

Supported graphs use trigger/condition/action/terminal/recorded-handoff nodes and real existing lead lifecycle actions or explicitly labelled persisted intents. Unsupported legacy notification/tag/resolution facades and timer/approval/retry/timeout nodes cannot be enabled as automatic post-call effects. Configuration returns 501 UNSUPPORTED_CAPABILITY; later incompatible publication fails the child job. Intent storage is not claimed to dial, notify or create an external task. Database graph effects, final checkpoint and audit are atomic; failed graphs roll back effects and expose effects_committed=false. Canonical queue retries/replay reuse completed results instead of repeating effects.

Trigger configuration now has explicit environment authorization, strict Redis limiting, durable creation receipts and transactional audits. Foreign resources return 404. Disabled bindings stay disabled on create replay. Static trigger routes precede the dynamic workflow ID route, fixing the previously shadowed GET trigger list. CORS accepts Idempotency-Key. Existing tenant-wide reusable workflow definitions are not silently assigned an environment; their automatic bindings are explicit.

The added GET /api/workflows/calls/{call_id}/executions is bounded, CALL_READ/environment-authorized and distinguishes committed workflow results from actual job acknowledgements. Environment-managed executions are excluded from legacy tenant-only manual inspection/history; cancellation/replay use the existing scoped job controls. The route inventory grows from 1163 to 1164 with no removed paths. OpenAPI and route snapshot changes are intentional; assertion logic and thresholds were not weakened.

## Acceptance matrix

| 1C requirement | Implementation / evidence |
| --- | --- |
| Exactly one terminal POST_CALL admission, rollback atomicity | Existing call event bridge, canonical post_call_key, PostgreSQL verifier and test_post_call_pipeline |
| Transcript finalization and governed summary/sentiment/outcome | Existing post_call_llm + pipeline; schema validation, bounded retry, provider budgets, timeout/fallback/circuit breaker and usage tests |
| Custom fields, versioned schemas/results, bounded backfill/preview/export | Existing analysis service/routes and canonical analysis.backfill worker; real compatibility endpoints retained |
| QA sampling and real QA_AUTO_REVIEW | Existing percentage bindings, frozen rubric/transcript inputs, canonical worker and suggestion-only persistence |
| call_analyzed delivery | Combined acceptance test receives signed bytes at a local HTTP receiver; existing webhook retry/DLQ/SSRF tests retained |
| CRM writeback | Existing opted-in CRM event/sync queue; recording-fake contact/note acknowledgements and prior PostgreSQL concurrency evidence retained |
| Workflow trigger effects | New main-runtime adapter; immutable admitted version, actual lead update, transactional result/audit, child failure isolation and scoped read API |
| Missing providers / unsupported capabilities | Explicit not_configured / unsupported failures; no fabricated successful model, cache, timer or external action |

## Real commands and outputs

Counts overlap. This is selected regression, not a claim that all 4481 collected nodes ran. Raw logs/XML are included below in the consolidated report and in the ZIP.

- Final regression command:
  `DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m pytest tests/ai tests/qa tests/test_crm*.py tests/webhooks tests/outbox tests/jobs tests/operations/test_notification_webhook_email.py tests/test_external_success_honesty.py tests/test_call_state.py tests/test_call_callbacks.py tests/test_transfer.py tests/telephony tests/campaign/test_concurrency.py tests/test_legacy_lead_routes.py tests/test_enterprise_batch01.py tests/test_workflow*.py tests/workflow -q --junitxml=reports/part1/workflow-runtime/regression-final.xml`
  **1022 passed, 4 skipped, 5900 warnings, 306.62s**; regression-final.log/xml.
- `.venv/bin/python -m pytest tests/telephony/test_post_call_workflow.py -q`: **25 passed**, 422 warnings; new-final.log.
- `PATH=$PWD/.venv/bin:$PATH make verify-truth`: static checks and **41 passed**, 59 warnings; truth-final.log.
- `PATH=$PWD/.venv/bin:$PATH make contracts-check`: five protobuf contracts compile; enum vocabulary and tenant scope pass; contracts.log.
- `.venv/bin/ruff check app/main.py app/services/post_call_workflow.py app/services/workflow_service.py app/api/workflow_event_routes.py app/telephony/post_call.py app/jobs/registry.py tests/telephony/test_post_call_workflow.py tests/telephony/test_post_call_pipeline.py reports/part1/workflow-runtime/verify_postgres.py`: All checks passed; ruff-final.log.
- `.venv/bin/python scripts/repo_stats.py`: **1164 routes; 4481 collected pytest nodes**, not a passage claim; repo-stats.log.
- `.venv/bin/alembic heads`: exactly **0054_qa_runtime**; heads.log.
- `DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic upgrade head`: fresh PostgreSQL upgrade passed; migration.log.
- Same URL with `alembic downgrade -1` and `alembic upgrade head`: **0054 → 0053 → 0054 passed**; downgrade.log/reupgrade.log. This does not reconstruct discarded QA snapshots.
- `.venv/bin/python reports/part1/workflow-runtime/verify_postgres.py`: passed before/after roundtrip; postgres-final.log and postgres-roundtrip.log. Two independent pipelines admit once, two workers claim once, an actual lead transition/execution/checkpoint/audit persists; custom analysis, QA review/run and analyzed event coexist for the same call. Uses an injected model test executor, not a live provider.
- `.venv/bin/python reports/part1/runtime/verify_post_call_postgres.py`: migrated ORM, terminal rollback, inference concurrency, **nine scoped core/custom checkpoints**, versioned result and one analyzed event pass; core-postgres.log. The expected checkpoint count was intentionally increased for the implemented workflow stage, not loosened.

Named 1A/1C acceptance commands were also run separately with `.venv/bin/python -m pytest -q`:

| Test module | Actual outcome |
| --- | --- |
| tests/webhooks/test_call_event_bridge.py | 45 passed |
| tests/webhooks/test_webhook_lifecycle_real.py | 15 passed, 1 skipped |
| tests/webhooks/test_webhook_isolation.py | 35 passed |
| tests/telephony/test_post_call_pipeline.py | 19 passed |
| tests/ai/test_post_call_llm.py | 28 passed, 1 skipped |

The explicit `-m live` command for model, QA, webhook and CRM modules produced **4 skipped, 75 deselected**; live.log identifies missing opt-ins/credentials. No skip/xfail was added, test deleted, or failure hidden. These skips are not LIVE success. Paid model credentials and hosted signed receivers remain operator-supplied.

Initial logs retain real failures: the new HTTP test exposed the old trigger-list route shadowing, which main registration now fixes; one new fixture incorrectly tried to mutate immutable Lead environment and was corrected to construct a separate foreign-environment lead; one broad command omitted its mandatory disposable DATABASE_URL and was rerun with real PostgreSQL. Final results above supersede those failed runs without suppressing their assertions.

## Delivery, commit and remaining scope

The report/ZIP contain **88 complete applied source/config/test/migration/documentation/script files**, each from first line to last with SHA-256 verification. Historical status sections remain explicitly historical. There are no placeholder source bodies. Generated bulk reports/archives/logs are deliverables, not staged as bulk code in the task commit.

The task-ID commit is recorded in commit.log. The preexisting staged AB3 patch is preserved byte-for-byte (SHA-256 d6381eee5322f484ca1ebfdb3a1ff42450ca7a29ba970f1b2eee98a650d65b10). Other supplied working-tree changes are not reset or claimed as this task's clean baseline; this is not a clean-checkout certification of uncommitted earlier foundation work.

Remaining PART 1 order: **1D → 1E → 1B → 1F → 1G**. Long-call chunking beyond the explicit transcript bound, arbitrary workflow actions/timers, unified privacy/retention, generic Salesforce integration and runtime A/B promotion are not silently claimed by this bounded post-call implementation. No production deployment or external LIVE verification was performed.
