# PART 1 — 1C analysis compatibility facade applied to main code

Date: 2026-10-07. This delivery closes the nine extended analysis compatibility operations, not the whole of 1C or PART 1. **Workflow trigger execution remains pending.** No production deployment, live-provider success, or 1C completion commit is claimed. PART 2 has not started.

## Applied behavior

The main `app/api/post_call_analysis_routes.py` no longer contains generated facade helpers or fake extended responses. The nine existing paths now return actual scoped database observations, real definition validation/listing, metadata-only audit pagination, or canonical durable backfill admission. Health explicitly leaves provider connectivity unprobed. Cache deletion explicitly returns 501 UNSUPPORTED_CAPABILITY after authorization; it does not delete application evidence.

`app/services/post_call_analysis_service.py` now supplies scoped stored-object/provenance counts, separate durable-job/checkpoint aggregations and bounded analysis audit metadata. Legacy manifest progress is not used as execution evidence. All observations retain tenant/environment predicates. Read operations require QA_READ; bulk/cache require QA_WRITE; audit additionally requires AUDIT_READ and environment audit access. Revoked environment membership denies access.

The bulk alias accepts the canonical body including `idempotency_key`, and shares the canonical receipt/manifest/job, rate limiting, call eligibility, audit and rollback. It preserves HTTP 200 while canonical creation preserves HTTP 201. It does not accept an idempotency header. Validation is read-only and shares the actual create model/compiler; errors exclude raw structural input/context. Response payloads intentionally change from the old placeholders; operation descriptions were regenerated, not guards weakened.

The analysis route's exemption was removed from `scripts/fake_success_allowlist.txt` after verification. The eight unrelated exemptions were left intact. Routes remain 1163, and OpenAPI components/path inventory are unchanged. No migration was added; the single head remains 0054_qa_runtime.

## Verification — actual commands and outcomes

All commands ran in the main repository. Raw output is included in this folder and the consolidated report/archive. Counts overlap and must not be summed. These are selected regressions, not all 4456 collected pytest nodes.

| Command | Actual outcome / evidence |
| --- | --- |
| `.venv/bin/python -m pytest tests/telephony/test_analysis_extended.py tests/telephony/test_analysis_backfill.py tests/telephony/test_custom_analysis.py -q` | 44 passed, 506 warnings; focused-final.log. Includes 17 new compatibility cases, with final health/stats database-outage assertions. |
| `DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m pytest tests/ai tests/qa tests/test_crm*.py tests/webhooks tests/outbox tests/jobs tests/operations/test_notification_webhook_email.py tests/test_external_success_honesty.py tests/test_call_state.py tests/test_call_callbacks.py tests/test_transfer.py tests/telephony tests/campaign/test_concurrency.py tests/test_legacy_lead_routes.py tests/test_enterprise_batch01.py -q --junitxml=reports/part1/analysis-facade/regression.xml` | 948 passed, 4 skipped, 5185 warnings, 352.85 seconds; regression.log/xml. The final health assertion was subsequently added to an existing case and verified in the focused run above; production code did not change. |
| `PATH=$PWD/.venv/bin:$PATH OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 make verify-truth` | All static guards and 41 truth tests passed; truth.log. |
| `PATH=$PWD/.venv/bin:$PATH make contracts-check` | Five protobuf contracts compile; enum and tenant-scope checks pass; contracts.log. |
| `.venv/bin/ruff check app/api/post_call_analysis_routes.py app/services/post_call_analysis_service.py tests/telephony/test_analysis_extended.py reports/part1/analysis-facade/verify_postgres.py` | All checks passed; ruff-final.log. |
| `.venv/bin/python scripts/repo_stats.py` | 1163 registered routes; 4456 collected tests; collection does not assert passage; repo-stats.log. |
| `.venv/bin/alembic heads` | Exactly 0054_qa_runtime (head); heads.log. |
| `DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic upgrade head` | Fresh PostgreSQL migration passed; migration.log. |
| `DATABASE_URL=postgresql+asyncpg://user@127.0.0.1:55432/post_call_test .venv/bin/alembic downgrade -1` and the same URL with `alembic upgrade head` | 0054 → 0053 → 0054 passed; downgrade.log/reupgrade.log. Roundtrip is not restoration of discarded QA snapshot data. |
| `.venv/bin/python reports/part1/analysis-facade/verify_postgres.py` | Passed before and after roundtrip. Concurrent independent compatibility/canonical admissions share one durable receipt/manifest/job, actual worker acknowledgement/result/provenance and audit drive observations. PostgreSQL 17 and real Redis were used. Its model executor is an injected deterministic test fixture, not a live or recording HTTP provider; postgres-final.log. |
| `.venv/bin/python -m pytest tests/ai/test_post_call_llm.py tests/qa/test_post_call_runtime.py tests/webhooks/test_webhook_lifecycle_real.py tests/telephony/test_post_call_crm.py -m live -q -rs` | 4 skipped, 75 deselected. Missing explicit opt-ins/provider or receiver credentials; live.log lists each gate. NOT LIVE evidence. |
| `git diff --cached \| sha256sum` | d6381eee5322f484ca1ebfdb3a1ff42450ca7a29ba970f1b2eee98a650d65b10; index-sha.log. Preexisting staged AB3 is unchanged. |

`contracts-delta.log` records exact comparison against the saved pre-edit OpenAPI: only the nine extended operation descriptions changed, with identical components/path inventory and 1163 routes. The truth gate verifies the regenerated exact contract and route snapshot without changing their assertions.

The new tests cover owner/other-tenant reads, real nonempty data, isolated development-environment rows, revoked environment access, viewer denial, metadata-only audit fields, pagination limits, shared compiler validation without writes, durable alias/canonical replay, a real worker execution using an injected test executor, stale manifest counters, transactional audit failure rollback, missing Redis, unsupported cache without deletion, and structured database-outage errors instead of fake healthy/zero responses.

Two test-authoring errors were corrected without changing production behavior or suppressing tests: the existing SQLAlchemy exception mapper returns 503 rather than propagating to the HTTP client, and the HTTP fixture creates per-request sessions rather than reusing the standalone db fixture. Original failures remain in focused.log and health-test-fixture-failure.log; final assertions verify the real failure contract.

## Complete-file delivery and scope

The consolidated report contains every listed source file from first line to last, with SHA-256; the ZIP contains identical complete applied files plus logs/XML. The current delivery extends the previous 78-file manifest with this test module, status and PostgreSQL verifier (81 complete files total). No source-body excerpts substitute for full files. Historical evidence is labelled historical.

The current task edits the route, service, new tests/verifier, corresponding allowlist entry, OpenAPI descriptions, POST_CALL documentation and delivery files. It does not remove other routers/services, introduce a queue or migrate a production database. The preexisting Git index is preserved; the entire 1C task is not yet ready for its requested completion commit.

Remaining: workflow trigger admission/execution in 1C, then 1D → 1E → 1B → 1F → 1G. Unified PII/retention, enterprise-wide durable state, campaign engine wiring, generic Salesforce/CRM unification and runtime A/B publication are not certified by this slice. No external credential is invented and no missing live test is represented as passing.
