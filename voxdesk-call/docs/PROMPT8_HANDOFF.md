# Prompt 8 handoff — operational verification of Prompt 7 controls

**Prepared:** 2026-10-05 (Asia/Dhaka)  
**Detailed report:** [`PROMPT8_OPERATIONAL_VERIFICATION_REPORT.md`](PROMPT8_OPERATIONAL_VERIFICATION_REPORT.md)  
**Primary security report:** [`PROMPT7_SECURITY_HARDENING_REPORT.md`](PROMPT7_SECURITY_HARDENING_REPORT.md)  
**Scope boundary:** verify the controls already in the repository; do not rebuild identity, invent provider state, or imply production assurance without live evidence.

## Starting facts and identity to preserve

- Prompt 7 accounts for **30/30** mandatory backend/runtime targets; [`PROMPT7_BACKEND_REVIEW_INVENTORY.md`](PROMPT7_BACKEND_REVIEW_INVENTORY.md) describes the boundary. This does not mean every application module or deployment was fully audited.
- Dashboard tests: **529 passed** across 42 files; production build succeeded with a main-chunk size advisory.
- Backend runtime closure now passes **10/10** focused tests, including the clean-process health import regression. Focused test chunks passed; the full backend suite is **not** reported as green.
- Prompt 6's baseline is historical and must remain verbatim in interpretation: **4,048 collected**, about **401 passed** before memory pressure and intentional termination, exit **137** due resource termination, not an assertion failure.
- The verified Alembic head is **`0048_boolean_defaults`**. Twelve known overlength historical aliases (0017, 0018, 0019, 0020, 0024, 0031, 0032, 0034, 0035, 0036, 0038, and 0045; exact old/new pairs are in `app/db/migration_compatibility.py`) were exercised against matching PostgreSQL schemas. Ambiguous legacy+canonical state was refused without mutation. Local disposable PostgreSQL fresh-upgrade → base-downgrade → head-re-upgrade passed.
- Current collect-only result is **4,111 tests collected**. Collection is not execution. No current full backend suite or reconciled shard result is claimed. The earlier 4,108-test deterministic 16-shard attempt timed out at the 1,800-second harness limit; a supervised 257-item shard produced 51 individual passes, then stalled during tenant-hierarchy fixture setup at about 1.44 GB RSS and was stopped without a terminal summary. The formerly stalled hierarchy test subsequently passed alone (**1/1**) and the full `tests/tenancy` directory passed (**26/26**). This does not turn the incomplete earlier shard into a pass or complete the current suite.
- The worktree was already a dirty cumulative Prompt 1–7 tree before this continuation and remains uncommitted. Base Git revision: `573df5850648234f90ab8f9b6aa4062da30ac8c0`. The operational evidence describes that worktree plus the Prompt 8 changes, not a release artifact.
- **Local disposable infrastructure:** PostgreSQL 17.11 and Redis 8.0.2 were available. `voxdesk_prompt8`, `voxdesk_prompt8_aliases`, `voxdesk_prompt8_migrations`, and the test-only PostgreSQL role were removed after verification; Redis DBs 14/15 were empty at cleanup. Cleanup evidence: [`disposable-cleanup.txt`](evidence/prompt8-2026-10-05/disposable-cleanup.txt). Sanitized operational evidence remains in [`evidence/prompt8-2026-10-05/`](evidence/prompt8-2026-10-05/). No provider sandbox credentials, production service, external provider call, live deployment, independent audit, or certification was verified.
- `audit_logs` has application-level tenant/reference validation but no PostgreSQL RLS/FORCE RLS or append-only trigger. The successful RLS/append-only probe applies to `deployment_runtime_observations` only; do not generalize it to all tables or to audit logs.

## Prompt 8 acceptance-gate status

| Gate | Status | Result |
|---|---|---|
| 1 — PostgreSQL migration and audit invariants | **PASS locally, with a scoped limitation** | Fresh full upgrade/downgrade/re-upgrade and 12 alias fixtures passed on disposable PostgreSQL. `deployment_runtime_observations` forced RLS and append-only behavior passed. Application audit redaction/scope/rollback passed. `audit_logs` database-level isolation/immutability is absent, and no production database was touched. |
| 2 — Redis and multi-process behavior | **PASS locally, not production-certified** | Two independent processes shared the Redis rate limit; unreachable Redis failed closed and health marked it unavailable. PostgreSQL idempotency, outbox publication/dispatch concurrency, and DLQ replay after PostgreSQL restart passed. Provider HTTP/network side effects were not tested. |
| 3 — Resource-bounded full backend suite | **INCOMPLETE** | Current collection: 4,111. The previously stalled tenant-hierarchy test now passes alone, and `tests/tenancy` is 26/26; no current full-suite/shard aggregate exists. Prior resource/time termination remains classified separately and was not counted as a pass. |
| 4 — Provider/proxy/network smoke | **NOT RUN** | No provider sandbox credentials, live proxy origin, external provider request, network egress/redirect/DNS-rebinding test, or real telephony callback was used. Provider reachability/authentication remains `not_checked`. |
| 5 — Release/security review | **NOT RUN** | No dependency/SAST scan against an immutable release artifact and no independent penetration/compliance assessment were produced. |

## Local operational evidence

| Check | Exact outcome | Evidence |
|---|---|---|
| PostgreSQL migration cycle | `upgrade head`, `downgrade base`, `upgrade head` all exit 0; base has no version row or enum type; final `0048_boolean_defaults` | [`postgres-fresh-upgrade.log`](evidence/prompt8-2026-10-05/postgres-fresh-upgrade.log), [`postgres-full-downgrade.log`](evidence/prompt8-2026-10-05/postgres-full-downgrade.log), [`postgres-full-reupgrade.log`](evidence/prompt8-2026-10-05/postgres-full-reupgrade.log), [`postgres-base-after-downgrade.txt`](evidence/prompt8-2026-10-05/postgres-base-after-downgrade.txt) |
| Alias normalization | 12/12 matching PostgreSQL schema fixtures; ambiguous 0045 legacy+canonical state refused without mutation; full upgrade passed | [`postgres-alias-fixtures.log`](evidence/prompt8-2026-10-05/postgres-alias-fixtures.log) |
| RLS and append-only | Tenant reads isolated; cross-tenant insert rejected; UPDATE/DELETE rejected by trigger for `deployment_runtime_observations` | [`postgres-rls-append-only.txt`](evidence/prompt8-2026-10-05/postgres-rls-append-only.txt), [`postgres-schema-checks.txt`](evidence/prompt8-2026-10-05/postgres-schema-checks.txt) |
| Audit application behavior | Commit/redaction/reread, rollback coupling, and three invalid-scope cases passed; `audit_logs` DB RLS/trigger absence recorded | [`postgres-application-audit.txt`](evidence/prompt8-2026-10-05/postgres-application-audit.txt), [`postgres-schema-checks.txt`](evidence/prompt8-2026-10-05/postgres-schema-checks.txt) |
| Redis multi-process limit | Two processes, three allowed/one denied, shared counter `4`, TTL `60s`; unavailable Redis returned denied | [`redis-multiprocess-rate-limit.txt`](evidence/prompt8-2026-10-05/redis-multiprocess-rate-limit.txt) |
| Health probes | Live PostgreSQL/Redis probe passed; unreachable configured Redis changed cache/overall health to unavailable; providers stayed not checked | [`live-health-probe.txt`](evidence/prompt8-2026-10-05/live-health-probe.txt) |
| PostgreSQL idempotency/outbox concurrency | One idempotency claim, concurrent duplicate `in_progress`; same-key replay matched, changed body conflicted. Concurrent publication returned one event; dispatch scheduled one round/job. | [`postgres-multiprocess-idempotency-outbox.txt`](evidence/prompt8-2026-10-05/postgres-multiprocess-idempotency-outbox.txt) |
| Durable DLQ through service restart | Failed local secret validation before HTTP; event survived PostgreSQL restart, tenant lookup was non-enumerating, replay scheduled round 2 and re-dead-lettered | [`postgres-outbox-dlq-restart.txt`](evidence/prompt8-2026-10-05/postgres-outbox-dlq-restart.txt) |
| Schema catalog | Head `0048_boolean_defaults`, Alembic marker type `VARCHAR(32)`, seven Boolean defaults and precise RLS/audit-log state recorded | [`postgres-schema-checks.txt`](evidence/prompt8-2026-10-05/postgres-schema-checks.txt) |

## Post-fix automated checks

The latest focused combined test command was:

```text
python -m pytest -q tests/test_migration_compatibility.py tests/test_deployment.py tests/test_enterprise_persistence.py::TestSchema::test_the_revision_chains_off_the_previous_head tests/test_campaign_release_compatibility.py tests/test_stable_id_and_release_facts.py tests/outbox/test_delivery.py tests/outbox/test_isolation.py tests/resilience/test_idempotency.py tests/security/test_audit_redaction.py tests/test_rate_limit.py tests/test_health_readiness.py tests/jobs/test_retry.py
```

Result: **107 passed, 47 warnings**. A separate `python -m pytest -q tests/test_backend_runtime_closure.py` run yielded **10 passed, 6 warnings**. After the earlier shard stall, `/usr/bin/timeout -k 2s 240s python -m pytest -q tests/tenancy` yielded **26 passed, 271 warnings**; the six-file directory includes the specific formerly stalled hierarchy test. The isolated blocker passed **1/1 (14 warnings)**, and `test_tenant_hierarchy.py` passed **6/6 (67 warnings)**. The recorded warnings were deprecations; each cited test run exited successfully. Targeted Ruff checks on changed migrations, job/tenancy import-cycle changes and associated tests passed. These are focused selections, not a full suite. Logs: [`postfix-targeted-pytest.log`](evidence/prompt8-2026-10-05/postfix-targeted-pytest.log), [`postfix-runtime-closure-pytest.log`](evidence/prompt8-2026-10-05/postfix-runtime-closure-pytest.log), [`tenant-subsystem-pytest.log`](evidence/prompt8-2026-10-05/tenant-subsystem-pytest.log), [`tenant-hierarchy-file-pytest.log`](evidence/prompt8-2026-10-05/tenant-hierarchy-file-pytest.log), [`historical-shard-blocker-isolated-pytest.log`](evidence/prompt8-2026-10-05/historical-shard-blocker-isolated-pytest.log), and [`ruff-postfix-targeted.txt`](evidence/prompt8-2026-10-05/ruff-postfix-targeted.txt).

`python -m pytest --collect-only -q` exited 0 and collected **4,111 tests** in 63.75 seconds. It did not execute tests. The earlier attempt in this continuation exited during collection because the sandbox lacked pinned `signxml` and `pipecat` dependencies; those test dependencies were installed ephemerally, and the subsequent collector completed. See [`full-suite-collection-postfix.log`](evidence/prompt8-2026-10-05/full-suite-collection-postfix.log) and the initial [`full-suite-collection-missing-packages.log`](evidence/prompt8-2026-10-05/full-suite-collection-missing-packages.log).

## Corrections made during the operational gates

1. `0018_org_memberships_quotas.py` now declares the baseline PostgreSQL `userrole` enum with `create_type=False`, avoiding a duplicate `CREATE TYPE` during a real PostgreSQL upgrade.
2. `0038_agent_chat_conductor.py` now drops its three revision-owned PostgreSQL enum types after dependent tables are removed, allowing `downgrade base` followed by another full upgrade.
3. Boolean server defaults in the public-widget/telephony revisions now use portable Boolean expressions; additive `0048_boolean_defaults` repairs databases that already passed those revisions.
4. `app.jobs.types` and `app.tenancy.isolation` no longer import ORM models at runtime solely for type annotations. A clean-process `app.observability.health` import is now regression-tested and passes.
5. Tests asserting the current Alembic head now expect `0048_boolean_defaults`; the migration-release test parser handles annotated `down_revision`; provider capability tests distinguish a missing SDK (`capable=False`) from an installed but unchecked SDK (`capable=None`).

## Remaining actions and ownership

No owner is assigned in repository evidence; the organization must assign owners before these actions are scheduled.

1. **DB/platform owner:** decide whether `audit_logs` requires database-enforced tenant isolation/append-only behavior; if approved, design and test a migration before production rollout. Review actual production stamps and schemas in a disposable clone before applying any migration.
2. **CI/test owner:** plan a resource-bounded deterministic backend-suite shard execution and reconcile totals exactly to 4,111 collected. Distinguish assertions, skips, errors, timeouts and process/resource termination. Do not add artificial skips or convert a killed run to a pass. Preserve Prompt 6's 4,048/~401/exit-137 history as historical only.
3. **Platform/provider owner:** run approved provider sandbox and intended reverse-proxy/network tests; preserve configured/installed/capable/reachable/authenticated distinctions. Do not use production/customer credentials for this gate.
4. **Release/security owner:** scan an immutable release artifact with dependency audit/SAST and arrange independent assessment if required. No compliance or penetration-test claim follows from the local results here.

## Scope and safety constraints

- No live provider credentials, real calls, billing actions, production migrations, or destructive production changes were used.
- Do not replace authoritative database/Redis state with process-local state or report synthetic success.
- Do not claim the entire backend suite passed; collection and targeted tests are separate outcomes.
- Do not describe `audit_logs` as DB-enforced RLS or append-only, and do not generalize the `deployment_runtime_observations` RLS result to all tables.
- Do not claim production readiness, provider connectivity/authentication, SOC 2, HIPAA, PCI, ISO 27001, or other certification.
