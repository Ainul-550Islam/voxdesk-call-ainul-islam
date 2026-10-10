# Prompt 8 — Operational Verification Report

**Verification date:** 2026-10-05 (Asia/Dhaka)  
**Repository:** `voxdesk-call-ainul-islam/voxdesk-call`  
**Source base revision:** `573df5850648234f90ab8f9b6aa4062da30ac8c0`  
**Worktree identity:** dirty cumulative Prompt 1–8 worktree; no commit or immutable release artifact was produced. The migration head during verification was `0048_boolean_defaults`.  
**Environment:** local disposable PostgreSQL 17.11 and Redis 8.0.2 only. Database names used: `voxdesk_prompt8` and `voxdesk_prompt8_migrations`; the rate-limit and health probes used disposable Redis logical DBs 15 and 14. No production service, customer data, provider credential, or external provider call was used.

## Executive result

The local PostgreSQL and Redis operational gates were exercised and passed for the scopes listed below. PostgreSQL migrations completed a fresh upgrade → full downgrade to base → re-upgrade cycle; all twelve migration-alias fixtures passed; the tested `deployment_runtime_observations` table enforced tenant RLS and append-only behavior; application audit redaction/scope/rollback checks passed; and PostgreSQL idempotency/outbox behavior was verified across separate processes and a PostgreSQL service restart. Redis rate limiting was shared across two independent Python processes, failure was fail-closed, and the dependency health probe reported Redis reachable only after a successful ping. For Gate 3, the previously stalled tenant-hierarchy test now passes in isolation, and the complete six-file `tests/tenancy` directory passed **26/26**; this narrows one earlier blocker but does not complete the full backend suite.

This is **local disposable-environment evidence**, not production or provider certification. The `audit_logs` table has no PostgreSQL RLS/FORCE RLS or append-only trigger; its tested tenant/reference checks are application-level. Provider reachability/authentication, production deployment, reverse-proxy behavior, and independent assessment were not tested. The full backend test suite was **not run to completion**; only focused test groups and a current collect-only pass are reported.

## Command record

The following exact command lines were run from the repository working tree; PostgreSQL connection settings were supplied only through the ephemeral local environment and are not recorded here:

```text
alembic upgrade head
alembic downgrade base
alembic upgrade head
alembic current
sudo pg_ctlcluster 17 main restart
python -m alembic heads
python -m pytest -q tests/test_migration_compatibility.py tests/test_deployment.py tests/test_enterprise_persistence.py::TestSchema::test_the_revision_chains_off_the_previous_head tests/test_campaign_release_compatibility.py tests/test_stable_id_and_release_facts.py tests/outbox/test_delivery.py tests/outbox/test_isolation.py tests/resilience/test_idempotency.py tests/security/test_audit_redaction.py tests/test_rate_limit.py tests/test_health_readiness.py tests/jobs/test_retry.py
python -m pytest -q tests/test_backend_runtime_closure.py
/usr/bin/timeout -k 2s 90s python -m pytest -q tests/tenancy/test_tenant_hierarchy.py::test_hierarchy_is_server_derived
/usr/bin/timeout -k 2s 180s python -m pytest -q tests/tenancy/test_tenant_hierarchy.py
/usr/bin/timeout -k 2s 240s python -m pytest -q tests/tenancy
python -m pytest --collect-only -q
python -m ruff check alembic/versions/0018_org_memberships_quotas.py alembic/versions/0038_agent_chat_conductor.py alembic/versions/0042_audit_scope_and_redaction.py alembic/versions/0048_boolean_defaults.py app/jobs/types.py app/tenancy/isolation.py tests/test_migration_compatibility.py tests/test_campaign_release_compatibility.py tests/test_deployment.py tests/test_enterprise_persistence.py tests/test_stable_id_and_release_facts.py tests/test_backend_runtime_closure.py tests/outbox/test_delivery.py tests/outbox/test_isolation.py
```

The PostgreSQL alias fixture log also records its `alembic upgrade ...` and `alembic current` invocations. The live RLS/audit, Redis process, health, idempotency/outbox, and restart/replay probes were executed as one-off Python heredocs reading only the local `PROMPT8_ASYNC_URL`, `PROMPT8_SYNC_URL`, and/or `REDIS_URL` environment variables; those ad-hoc heredoc bodies were not preserved as standalone scripts. Their timestamped outputs, process IDs, outcomes, and environment boundaries are preserved in the evidence files below. No DSN or credential is included.

## Verified controls and saved evidence

| Gate / control | Result | Scope and evidence |
|---|---|---|
| PostgreSQL fresh migration cycle | **PASS** | Fresh `alembic upgrade head`, `alembic downgrade base`, and subsequent `alembic upgrade head` all exited 0 on disposable PostgreSQL. At base, `alembic_version` had no row and the enum catalog was empty. Final head: `0048_boolean_defaults`. Logs: [`postgres-fresh-upgrade.log`](evidence/prompt8-2026-10-05/postgres-fresh-upgrade.log), [`postgres-full-downgrade.log`](evidence/prompt8-2026-10-05/postgres-full-downgrade.log), [`postgres-full-reupgrade.log`](evidence/prompt8-2026-10-05/postgres-full-reupgrade.log), [`postgres-base-after-downgrade.txt`](evidence/prompt8-2026-10-05/postgres-base-after-downgrade.txt). |
| PostgreSQL legacy migration aliases | **PASS: 12/12** | Every known overlength alias was inserted only after migrating its matching physical schema to the canonical revision; actual `alembic/env.py` normalization was exercised. An ambiguous legacy+canonical 0045 marker was refused without rewriting either value; the final upgrade to head passed. Evidence: [`postgres-alias-fixtures.log`](evidence/prompt8-2026-10-05/postgres-alias-fixtures.log). |
| Database-enforced RLS and append-only | **PASS for `deployment_runtime_observations` only** | Forced tenant RLS restricted reads to the session tenant; a cross-tenant insert was rejected; UPDATE and DELETE were rejected by the append-only trigger. Evidence: [`postgres-rls-append-only.txt`](evidence/prompt8-2026-10-05/postgres-rls-append-only.txt). Catalog inspection confirms this is not a claim about every table. |
| `audit_logs` application behavior | **PASS at application layer; database controls absent** | On PostgreSQL, sensitive credential/PII values were scrubbed before commit; committed rows were reread; transaction rollback removed a flushed row; foreign-tenant environment, unowned actor, and environment-without-tenant references were rejected. Prior committed authorization-denial rows were still present after PostgreSQL restart. Evidence: [`postgres-application-audit.txt`](evidence/prompt8-2026-10-05/postgres-application-audit.txt). The catalog shows `audit_logs` has no RLS/FORCE RLS, policies, or noninternal append-only trigger. |
| PostgreSQL idempotency across processes | **PASS** | Two independent Python processes contended on one request key: one persisted the claim and the other saw `in_progress`. Completion/replay returned the same receipt; a changed canonical request with the same key raised conflict. Only a SHA-256 key digest was stored. Evidence: [`postgres-multiprocess-idempotency-outbox.txt`](evidence/prompt8-2026-10-05/postgres-multiprocess-idempotency-outbox.txt). |
| Outbox publication and dispatch concurrency | **PASS** | Two publisher processes returned one tenant-scoped event row; two dispatcher processes scheduled exactly one durable delivery round/job (the other observed it in flight). Same evidence file: [`postgres-multiprocess-idempotency-outbox.txt`](evidence/prompt8-2026-10-05/postgres-multiprocess-idempotency-outbox.txt). |
| Durable dead-letter replay through PostgreSQL restart | **PASS** | An intentionally invalid local webhook secret caused a permanent-failure/dead-letter state before any HTTP request. The event survived `pg_ctlcluster 17 main restart`, cross-tenant lookup returned the same non-enumerating 404 as a missing event, replay created round 2, and the post-restart worker persisted the second failed delivery and dead-letter state. Evidence: [`postgres-outbox-dlq-restart.txt`](evidence/prompt8-2026-10-05/postgres-outbox-dlq-restart.txt). The route's state-transition function was invoked directly with scoped context; its HTTP permission dependency was not exercised by this probe. |
| Redis shared rate limit and failure policy | **PASS** | Two independent Python processes shared one Redis counter: three requests allowed, the fourth denied, counter `4`, TTL `60` seconds. An unreachable Redis endpoint returned denied (`False`), not synthetic allow. Evidence: [`redis-multiprocess-rate-limit.txt`](evidence/prompt8-2026-10-05/redis-multiprocess-rate-limit.txt). |
| Runtime dependency health | **PASS for local DB/cache probes** | Through the standard application import, PostgreSQL and Redis returned successful live probes; provider reachability/authentication remained `not_checked`. Re-pointing configured Redis to an unreachable local port made cache and overall health `unavailable`. Evidence: [`live-health-probe.txt`](evidence/prompt8-2026-10-05/live-health-probe.txt). A separate clean-process import regression now also passes in the runtime-closure pytest run. |
| PostgreSQL schema/catalog | **PASS; explicitly scoped** | Catalog confirms head `0048_boolean_defaults`, version column `character varying(32)`, corrected Boolean defaults, enum inventory, `deployment_runtime_observations` RLS/policy/trigger, and absence of `audit_logs` RLS/trigger. Evidence: [`postgres-schema-checks.txt`](evidence/prompt8-2026-10-05/postgres-schema-checks.txt). |
| Focused post-fix pytest | **PASS: 107 passed, 47 warnings** | Cross-subsystem focused selection covering migration compatibility, deployment/release head assertions, outbox, idempotency, audit redaction, rate limits, health readiness, and job retry. Evidence: [`postfix-targeted-pytest.log`](evidence/prompt8-2026-10-05/postfix-targeted-pytest.log). Warnings were datetime/SQLAlchemy deprecations, not failures. |
| Tenant hierarchy blocker and subsystem | **PASS: 1/1 blocker test; 6/6 file; 26/26 `tests/tenancy`** | The specific test that stalled the earlier supervised shard passed alone (**1 passed, 14 warnings, 46.37 s**); `test_tenant_hierarchy.py` passed (**6 passed, 67 warnings, 46.65 s**); the entire six-file tenancy directory passed (**26 passed, 271 warnings, 55.88 s**). Evidence: [`historical-shard-blocker-isolated-pytest.log`](evidence/prompt8-2026-10-05/historical-shard-blocker-isolated-pytest.log), [`tenant-hierarchy-file-pytest.log`](evidence/prompt8-2026-10-05/tenant-hierarchy-file-pytest.log), [`tenant-subsystem-pytest.log`](evidence/prompt8-2026-10-05/tenant-subsystem-pytest.log). These focused passes do not complete a full-suite shard. |
| Runtime closure and clean health import | **PASS: 10 passed, 6 warnings** | `tests/test_backend_runtime_closure.py`, including a fresh subprocess that imports `app.observability.health` without first importing `app.main`. Evidence: [`postfix-runtime-closure-pytest.log`](evidence/prompt8-2026-10-05/postfix-runtime-closure-pytest.log). Warnings were Pydantic deprecations. |
| Targeted Ruff | **PASS** | Ruff checked the changed migrations, job/tenancy import-cycle fix, and associated focused tests. Evidence: [`ruff-postfix-targeted.txt`](evidence/prompt8-2026-10-05/ruff-postfix-targeted.txt). This is not repository-wide lint certification. |
| Current full-suite collection | **PASS: 4,111 collected; collection only** | `python -m pytest --collect-only -q` exited 0 in 63.75 seconds. No tests were executed by this command. Evidence: [`full-suite-collection-postfix.log`](evidence/prompt8-2026-10-05/full-suite-collection-postfix.log). |

## Fixes made from local operational evidence

1. **Revision 0018 enum handling:** PostgreSQL verification found `DuplicateObjectError` because `organization_memberships` revision 0018 tried to create the baseline `userrole` native enum again. `_role_type()` now returns PostgreSQL `ENUM(..., create_type=False)` and retains SQLite-compatible behavior. A focused regression test pins this behavior.
2. **Revision 0038 downgrade cleanup:** a full downgrade left three native enums introduced by revision 0038, causing the next upgrade to fail with `DuplicateObjectError`. Its downgrade now drops only those revision-owned types after dependent tables are removed. The clean full upgrade/downgrade/re-upgrade cycle passes.
3. **Boolean server defaults:** PostgreSQL rejects integer defaults on Boolean columns. Existing 0040/0041 declarations were corrected and additive revision `0048_boolean_defaults` normalizes the seven affected defaults for databases that already passed the earlier revisions. Rolling back only 0048 preserves valid Boolean defaults rather than restoring invalid integer literals.
4. **Clean-process health import:** the direct module import exposed a cycle through runtime imports of ORM types in `app.jobs.types` and `app.tenancy.isolation`. Those imports are now type-checking-only; the subprocess regression test passes without relying on `app.main` import order.
5. **Migration graph tests:** current-head assertions now name `0048_boolean_defaults`, and the release test's `down_revision` parser accepts both annotated and unannotated declarations. A stale provider-matrix test now distinguishes an SDK that is installed but unchecked (`capable=None`) from a missing SDK (`capable=False`); no status is promoted to connected or authenticated.

## Incomplete gates and remaining boundaries

### Gate 1 — PostgreSQL / audit

- The local migration cycle, all twelve PostgreSQL alias fixtures, and `deployment_runtime_observations` RLS/append-only behavior passed.
- The application audit writer passed redaction/scope/rollback probes, but **`audit_logs` has no DB RLS/FORCE RLS or append-only trigger** in the checked schema. Do not describe it as database-enforced tenant isolation or immutability. Adding such controls requires an explicit design/migration decision and production change process.
- No production or customer database was inspected or migrated. Before rollout, verify every real deployment's physical schema matches its Alembic marker using a disposable copy; do not normalize a marker solely because its text matches an alias.

### Gate 2 — Redis / multi-process

- Local Redis/PostgreSQL multi-process tests and restart/replay checks passed.
- No production Redis cluster, API fleet, external webhook endpoint, provider call, sustained load, or failover drill was exercised. The dead-letter test intentionally stopped before outbound HTTP because its local secret was invalid.

### Gate 3 — Resource-bounded full backend suite

- Current collect-only count is **4,111**, not a test pass count.
- **No current full backend suite or reconciled shard run is claimed.** The prior deterministic 16-shard attempt for the earlier 4,108-test code point timed out at the 1,800-second harness limit. A supervised 257-item shard showed 51 individual passes and was stopped without a terminal summary after it stalled at tenant-hierarchy fixture setup at about 1.44 GB RSS. That attempt remains classified as resource termination, not an assertion failure or a completed shard.
- In this continuation, the formerly stalled `test_hierarchy_is_server_derived` passed alone (**1 passed, 14 warnings**) and the complete `tests/tenancy` directory passed (**26 passed, 271 warnings**). This demonstrates a successful isolated/file-scoped rerun, not that the prior shard or current full suite completed.
- Owner: not assigned in repository evidence. A CI/operator owner must plan a resource-bounded shard run and reconcile pass/fail/skip/error/termination totals to the collector output.

### Gate 4 — Provider/proxy/network smoke

- **Not run.** No provider sandbox credentials, production secrets, external network reachability, reverse-proxy origin, real telephony callback, or authenticated provider request was tested. Provider reachability/authentication remains `not_checked`.
- Owner: not assigned in repository evidence; requires an organization-approved sandbox and proxy/network test environment.

### Gate 5 — Release/security review

- **Not run.** No dependency audit/SAST was performed against an immutable release artifact, and no independent penetration test or compliance assessment was obtained.
- Owner: not assigned in repository evidence; requires a release artifact and an organization-designated security/release owner.

## Test attempts superseded by later results

These outcomes are retained, not hidden, and are not current-pass claims:

- The first post-fix migration/release pytest attempt had **1 failed, 75 passed** because the test's source regex did not recognize the new migration's annotated `down_revision`; the parser was fixed and the later focused rerun passed. The initial and corrected logs are [`migration-release-pytest-initial-failure.log`](evidence/prompt8-2026-10-05/migration-release-pytest-initial-failure.log) and [`migration-release-pytest.log`](evidence/prompt8-2026-10-05/migration-release-pytest.log).
- A runtime-closure collection first lacked the pinned `websockets==13.1` dependency; after installing it ephemerally, one older assertion incorrectly expected missing Deepgram SDK to be “unknown” rather than “not capable.” The assertion was corrected to match the explicit install/capability distinction; the final file run is **10 passed**. Initial logs are preserved as `postfix-runtime-closure-pytest-missing-websockets.log` and `postfix-runtime-closure-pytest-stale-capability-assertion.log`.
- The first current-suite collect-only attempt found missing `signxml` and `pipecat` test imports and exited with collection errors; the exact pinned test dependencies were installed ephemerally, and the subsequent collector exited 0 with 4,111 collected. The failed attempt is [`full-suite-collection-missing-packages.log`](evidence/prompt8-2026-10-05/full-suite-collection-missing-packages.log).
- When isolating the earlier shard blocker in a later tool turn, several initial invocations exited before test collection because that turn's ephemeral environment lacked `pytest-asyncio`, SQLAlchemy, bcrypt, or `pydantic-settings`. The pinned test dependencies were installed ephemerally; after that, the previously stalled test passed alone and the full tenancy directory passed. These are environment setup failures, not test assertion results. Logs: [`historical-shard-blocker-isolated-pytest-missing-asyncio.log`](evidence/prompt8-2026-10-05/historical-shard-blocker-isolated-pytest-missing-asyncio.log), [`historical-shard-blocker-isolated-pytest-missing-sqlalchemy.log`](evidence/prompt8-2026-10-05/historical-shard-blocker-isolated-pytest-missing-sqlalchemy.log), [`historical-shard-blocker-isolated-pytest-missing-bcrypt.log`](evidence/prompt8-2026-10-05/historical-shard-blocker-isolated-pytest-missing-bcrypt.log), and [`historical-shard-blocker-isolated-pytest-missing-pydantic-settings.log`](evidence/prompt8-2026-10-05/historical-shard-blocker-isolated-pytest-missing-pydantic-settings.log).
- The isolated passes supersede only the earlier inability to verify the individual hierarchy test. They do **not** supersede the original supervised shard's terminal status: that earlier shard remains manually stopped/resource-terminated without a terminal summary, and no complete or reconciled full-suite result exists.
- The initial PostgreSQL full re-upgrade failed because revision 0038 retained native enum types; after its downgrade fix, the final cycle passed. The original failure remains in `postgres-full-reupgrade-initial-failure.log`.

## Cleanup and handling

The PostgreSQL databases `voxdesk_prompt8`, `voxdesk_prompt8_aliases`, and `voxdesk_prompt8_migrations`, and the test-only PostgreSQL role, were removed after checks. Redis logical DBs 14 and 15 were empty at cleanup. The ephemeral `/tmp/prompt8-db.env` file and temporary dead-letter fixture file were removed. Cleanup verification is recorded in [`disposable-cleanup.txt`](evidence/prompt8-2026-10-05/disposable-cleanup.txt); only sanitized text evidence under [`evidence/prompt8-2026-10-05/`](evidence/prompt8-2026-10-05/) is retained. No database credentials, API keys, provider credentials, or customer records are included.

## Certification boundary

This report records reproducible local verification against one sandbox worktree and local disposable services on 2026-10-05. It does not assert production migration safety, provider connectivity/authentication, a complete test-suite pass, production readiness, penetration-test completion, or SOC 2/HIPAA/PCI/ISO certification. See [`PROMPT7_SECURITY_HARDENING_REPORT.md`](PROMPT7_SECURITY_HARDENING_REPORT.md) for the broader security-control and residual-risk context.
