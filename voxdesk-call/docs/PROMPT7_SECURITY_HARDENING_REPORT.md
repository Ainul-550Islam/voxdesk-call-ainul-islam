# Prompt 7 — Enterprise Security Hardening: Evidence, Gaps, and Handoff

**Status date:** 2026-10-05 (Asia/Dhaka)  
**Repository:** `voxdesk-call-ainul-islam/voxdesk-call`  
**Evidence class:** source review and sandbox tests unless explicitly called a live/operational check.  
**Certification boundary:** no external audit, penetration test, SOC 2/HIPAA/PCI/ISO certification, production deployment, or real provider authentication is claimed.

## 1. Executive status

Prompt 7's requested hardening and dashboard integration are implemented across the repository's existing architecture. Focused, chunked tests cover identity/RBAC, tenant and environment boundaries, sessions and API keys, audit redaction, SSRF, webhooks, jobs/idempotency, resilience, telephony/media, billing, deployment/readiness, and the dashboard security settings. Two additional defects discovered during continuation testing were fixed: provider capability inventory could import optional SDKs and stall a combined test; and a telephony recording denial could fail while trying to attach a foreign-tenant actor to the target tenant's audit record.

The important limits are equally clear:

- **The 30 mandatory backend/runtime targets are accounted for: 30/30 present before edits, 30/30 reread after edits, 0 skipped.** The full path-by-path inventory is [`PROMPT7_BACKEND_REVIEW_INVENTORY.md`](PROMPT7_BACKEND_REVIEW_INVENTORY.md). This is an exact target-set accounting, not a claim that all 702 Python modules or every production deployment were independently audited.
- **Dashboard:** latest recorded full suite is **42 files, 529 tests passed**; isolated audit coverage **41 passed** and the new security-settings integration tests **3 passed**. The post-test production build succeeds, with a known large-main-chunk advisory.
- **Backend runtime closure:** the entire file now passes **10/10**, including the clean-process health-module import regression; previous combined-file timeouts are superseded by this later run.
- **Local operational checks (2026-10-05):** disposable PostgreSQL 17.11 passed fresh `upgrade head → downgrade base → upgrade head`; all twelve real PostgreSQL legacy-alias fixtures and ambiguous-marker refusal passed; forced tenant RLS and append-only behavior passed for `deployment_runtime_observations`; application-level audit redaction, scope validation and rollback coupling passed. Redis 8.0.2 shared-rate limiting across two independent processes and fail-closed behavior passed. PostgreSQL idempotency/outbox concurrency, dependency health probes, and durable dead-letter replay after a PostgreSQL service restart passed. These are local disposable-environment results only. `audit_logs` itself has no PostgreSQL RLS/FORCE RLS or append-only trigger.
- **Production/provider:** no production database, production deployment, real provider authentication, external provider network call, proxy-origin deployment, independent audit, or certification was tested. Local evidence does not certify provider or production behavior.
- **Full backend suite:** no current full-suite green claim. Test work was executed in scoped chunks. The current 4,111-test suite has not completed; the formerly stalled hierarchy test passed alone and the complete `tests/tenancy` directory passed 26/26, but those isolated results do not complete or reconcile the earlier shard. The Prompt 6 historical baseline remains 4,048 collected; the monolithic run recorded about 401 passes before resource pressure and intentional termination with exit 137. Exit 137 was resource termination, not an assertion failure; it is not evidence that 4,048 tests passed.

## 2. Exact 30-target accounting

| Accounting item | Result |
|---|---:|
| Required backend/runtime targets | 30 |
| Present before review | 30/30 |
| Reread after edits | 30/30 |
| Skipped | 0 |
| Exact per-target status | [`PROMPT7_BACKEND_REVIEW_INVENTORY.md`](PROMPT7_BACKEND_REVIEW_INVENTORY.md), “Mandatory target status” |

The 30 canonical paths are:

1. `app/main.py`
2. `app/agent/stt.py`
3. `app/agent/stt_stream.py`
4. `app/providers/compatibility.py`
5. `app/providers/errors.py`
6. `app/core/config_validation.py`
7. `app/core/dependency_health.py`
8. `app/observability/health.py`
9. `app/observability/runtime.py`
10. `app/observability/cost.py`
11. `app/jobs/runtime.py`
12. `app/jobs/registry.py`
13. `app/jobs/ai_specialized_jobs.py`
14. `app/deployment/runtime.py`
15. `app/deployment/readiness.py`
16. `app/deployment/verification.py`
17. `app/deployment/evidence.py`
18. `app/deployment/adapters/base.py`
19. `app/deployment/adapters/kubernetes.py`
20. `app/deployment/adapters/container.py`
21. `app/deployment/adapters/airgap.py`
22. `app/deployment/adapters/registry.py`
23. `app/api/health_routes.py`
24. `app/api/deployment_runtime_routes.py`
25. `requirements.txt`
26. `pyproject.toml`
27. `alembic/versions/0036_runtime_deployment_observability.py`
28. `tests/deployment/test_runtime_adapters.py`
29. `tests/deployment/test_readiness_verification.py`
30. `tests/test_backend_runtime_closure.py`

The inventory records 702 Python modules under `app/`, of which 36 paths were covered across the referenced target/inventory tables and 666 were outside this renewed 30-target review. “Outside this review” is not a defect finding and does not mean those modules were tested here. The overall Prompt 7 also includes dashboard security-settings integration, documented and tested separately; the 30-target inventory is explicitly the backend/runtime subreview.

## 3. Before/after threat matrix

“Before” below means the starting code/test observations recorded during this work, not the result of a penetration test. No controlled exploit campaign was run. “After” means a code path and/or deterministic test exists; it does not mean production effectiveness has been established.

| Threat / asset | Starting observation | Prompt 7 state and evidence | Residual exposure / boundary |
|---|---|---|---|
| Authentication, privilege escalation, and machine-vs-human authority | Existing JWT, RBAC, MFA, SSO, SCIM, API-key and service-account code existed, but a broad route/policy and operator evidence pass was required. | Role/policy, fresh reauthentication, session revocation, key scopes, service-account limits, and tenant security settings are represented in the existing identity services and tested. `tests/security/test_authorization_matrix.py`: **115 passed**; identity/MFA/session chunks and SSO tests also passed. | Source/test evidence is not a review of every live route under every deployed configuration. No external identity provider was authenticated in production. Do not claim SSO/SCIM deployment or compliance from local tests. |
| Tenant/environment IDOR and audit attribution | A recording-read denial path could send an actor from tenant A to the tenant-B audit scope when the tenant argument was inconsistent. The audit writer correctly rejected that invalid scope, but the caller saw an audit-scope exception instead of a controlled authorization denial. | `app/telephony/recording.py` now checks the actor's tenant before querying the target recording, returns the non-enumerating denial, and records a cross-tenant attempt under the actor's own tenant with the attempted tenant ID. Regression coverage asserts both safe denial and correctly scoped audit. The final isolation chunk passed **11/11**. On disposable PostgreSQL, `deployment_runtime_observations` forced tenant RLS restricted reads, rejected a cross-tenant insert, and its append-only trigger rejected UPDATE/DELETE. | This live RLS/trigger evidence is specific to `deployment_runtime_observations`; it does not prove RLS across all tenant tables. `audit_logs` has no DB RLS/FORCE RLS or append-only trigger. Production RLS behavior is unverified. |
| API-key/session secret exposure | Identity and credential pathways need to keep raw secrets one-time and out of reads, logs, and audit events. | Existing digest-only key/session storage and one-time secret semantics remain in place; redaction is centralized in the audit pipeline. The security-settings page uses real identity APIs, requires fresh reauthentication for key creation, displays the issued secret once, and does not infer certification. | Existing secrets that may have been exposed before this change require operator rotation; code cannot retroactively revoke an unknown external copy. Browser integration against a deployed origin is not verified. |
| CSRF, CORS, host and response-header abuse | Browser/security policy must be enforced server-side and not inferred from dashboard presentation. | CSRF, explicit CORS configuration, trusted-host checks, cookie/security headers, security.txt, and startup validation are wired through the existing middleware/config surfaces. Security headers/rate-limit/regression/security.txt tests were recorded as a passing chunk. | Staging/prod origins, TLS termination, reverse-proxy behavior, and browser cookie flows were not tested against a live deployed stack. |
| SSRF, redirects, and provider credential forwarding | Outbound URL consumers and redirects can expose tenant credentials or reach private/metadata endpoints. | Static URL validation and redirect restrictions are applied at relevant outbound boundaries; the deployment registry adapter is pinned to its configured HTTPS host and does not follow redirects. SSRF/AI-tool/ops-toolkit chunk: **95 passed**. | Static parsing cannot establish DNS-rebinding safety or block network routes. Egress policy is still needed; live provider endpoints/credentials were not exercised. |
| Secret logging and audit integrity | A credential-shaped value entering logs or audit detail must not become a second secret store; audit must remain tenant-scoped and durable. | Redaction is recursive and secret-aware; audit writes ride the business transaction and scope-check actor/environment references. PostgreSQL application-level probes confirmed credential/PII scrubbing before commit, persisted-row reread, rollback coupling, and rejection of invalid tenant/environment/actor references. The `deployment_runtime_observations` append-only trigger was exercised against PostgreSQL. | `audit_logs` has no PostgreSQL RLS/FORCE RLS or noninternal append-only trigger; its tested tenant/reference enforcement is application-level. Retention, production backup/restore, and audit durability in a production environment remain unverified. |
| Distributed abuse, retries, concurrency, idempotency | Process-local throttles or in-memory idempotency are not authoritative across workers. | Existing Redis-backed rate-limit and persistent receipt/job/outbox paths are retained; idempotency and concurrency rely on database uniqueness/transactions rather than a process-local success cache. Two independent Python processes shared a Redis counter (three allowed, fourth denied); PostgreSQL idempotency claims, outbox publish and dispatch were exercised concurrently; a persisted dead-letter event survived PostgreSQL restart and replayed as a second durable attempt. Current focused pytest evidence includes **107 passed, 47 warnings** plus a separate runtime-closure run of **10 passed, 6 warnings**. | Local disposable Redis/PostgreSQL evidence is not a production multi-worker load test. Health probes did not test provider connectivity. The earlier jobs/outbox/idempotency chunk had **1 existing skip**; no skip was added to force a green result. |
| Webhooks, WebSockets, telephony, tools, files, billing | External callbacks, public widgets, media streams, tool calls, uploads, and billing affect data and paid actions. | Existing signature/origin/token boundaries, tenant/environment checks, replay/idempotency receipts, call/recording lifecycle rules, allowlisted tool dispatch, safe file handling, and server-resolved billing state have focused regression coverage. Telephony/provider security chunk: **31 passed**; billing files: **268 passed, 1 skipped**; public-key and media-isolation tests passed. | No real Twilio/Stripe/CRM call, external provider authentication, physical telephony call, or live widget origin was used. Provider availability/authentication remains `not_checked`. |
| Health, observability, deployment identity and provider status | “Configured”, “installed”, “API-capable”, “reachable”, and “authenticated” must not collapse into a false “connected” indicator. | `capability_matrix()` is now an import-free health inventory. It distinguishes configured/installed from API-surface capability: a present SDK reports `capable=null` until explicitly checked, while an absent SDK reports `false`; reachability/authentication remain `not_checked`. Full backend runtime-closure file: **10 passed**, including clean-process health import. Digest verification, safe readiness, and startup config checks remain wired to existing services. | Capability checks that actually import an SDK remain on the explicit operation/call-construction path. Health output does not prove a worker is running, an external credential works, or a deployment is healthy. |
| Migration history length and operational upgrade compatibility | Ten historical revision IDs had already been shortened; two additional IDs exceeded Alembic's default `VARCHAR(32)` version column during the recent review. | Current IDs are at most 32 characters, including `0038_agent_chat_conductor` and `0045_request_idem_receipts`; the compatibility helper maps twelve known overlength historical markers before Alembic reads the table and refuses ambiguous old+new state. On PostgreSQL 17.11, fresh upgrade, full downgrade to base, and re-upgrade to `0048_boolean_defaults` passed; all twelve matching-schema alias fixtures passed and an ambiguous 0045 pair was refused without mutation. PostgreSQL verification also exposed and fixed 0018's duplicate `userrole` enum creation and 0038's orphaned revision-owned enum types. | This is local disposable PostgreSQL evidence, not a production rollout. Inspect actual stamped DBs and verify physical schemas before production; offline SQL rendering remains supplementary, not a substitute for a production migration. |

## 4. Evidence-based scorecard

This is a control-evidence scorecard, **not a certification score or numerical security rating**.

| Domain | Evidence rating | Basis | Still not proven |
|---|---|---|---|
| 30 mandatory targets | Accounted and reread | 30/30 target accounting; path-level status and exclusions in the inventory | Full source audit of every application module |
| Authentication/RBAC/sessions/keys | Implemented + locally tested | Authorization matrix 115 passed; identity/MFA/session, OIDC/SSO and API-key/session chunks passed | Production IdP setup, deployed key policy, external audit |
| Tenant/environment isolation | Locally tested; partial DB-enforcement evidence | Isolation chunk 11 passed; subsequent complete `tests/tenancy` rerun passed 26/26, including the formerly stalled hierarchy test; recording actor scope regression fixed; PostgreSQL forced RLS/append-only behavior passed on `deployment_runtime_observations` | RLS coverage for other tables, `audit_logs` DB enforcement, and production tenant-boundary verification |
| Audit and redaction | Application-level controls locally tested; limited DB trigger evidence | PostgreSQL writer probe verified redaction, commit/reread, rollback, and invalid-scope rejection; append-only UPDATE/DELETE enforcement passed only for `deployment_runtime_observations` | `audit_logs` RLS/FORCE RLS and append-only trigger are absent; retention/backup/restore and production audit assurance remain unverified |
| CSRF/CORS/headers/SSRF | Implemented + locally tested | Security middleware and URL boundary tests; SSRF/AI-tool/ops-toolkit 95 passed; earlier headers/rate/security regression chunk passed | DNS rebinding, network egress policy, actual proxy/browser deployment |
| Rate limits / idempotency / jobs / retries | Locally tested across separate processes | Redis shared limit and fail-closed probe; PostgreSQL idempotency and outbox concurrency; DLQ state survived service restart and replayed | Production Redis/worker topology, sustained load, and real webhook/provider side effects |
| Telephony, tools, files, webhooks, billing | Implemented + locally tested | Telephony/provider 31 passed; billing 268 passed/1 skipped; webhook/public-key/media and tool/file chunks passed | Live provider authentication and paid-action end-to-end behavior |
| Health and provider reporting | Implemented + locally tested | Runtime closure 10/10 including clean-process import; live local dependency probes and unavailable-Redis behavior also passed; provider matrix reports unverified state honestly | Provider reachability/authentication, worker presence, production metrics/alerts |
| Dashboard integration | Implemented + locally tested | Security-settings tests 3 passed; full dashboard suite 529/529; build succeeded | Deployed browser/API integration and live user acceptance |
| Operational assurance | **Partially verified locally** | Disposable PostgreSQL/Redis migration, RLS/append-only, multi-process, health and restart/replay probes; evidence dated 2026-10-05 | Production migration/deployment, backup/restore, alerting, providers, and production readiness |
| External assessment/certification | **None** | No independent evidence supplied or generated | Penetration test, SOC 2/HIPAA/PCI/ISO assessment or certification |

### Prompt 6 systems regression assessment

This is a scoped regression assessment of the existing Prompt 6/cumulative systems covered by recorded tests; it is not a claim that every Prompt 6 feature or the full backend suite was rerun.

| System | Current regression evidence | Assessment and boundary |
|---|---|---|
| Dashboard and security-settings integration | Dashboard suite: **42 files / 529 passed**; build succeeded. | Local UI/API contract and build regressions passed. No deployed-origin browser test or production API check. |
| Telephony, voice, provider and recording | Telephony/provider/transfer chunk: **31 passed**; final four-file isolation chunk: **11 passed**; recording tenant-attribution regression fixed. | Focused regressions passed; real provider authentication, calls, media transport, and PostgreSQL RLS remain unverified. Chunk overlap is not additive. |
| Billing, metering, subscriptions and voice billing | Four separate billing files: **268 passed, 1 skipped**. | Local regression passed with one existing skip; no live Stripe/provider or paid-action test. |
| Jobs, workflows, inbox, outbox and durable idempotency | Prompt 8 focused rerun: **107 passed, 47 warnings**; separate runtime-closure run: **10 passed, 6 warnings**. Local PostgreSQL idempotency/outbox concurrency and durable DLQ replay after service restart also passed. | Local disposable-service results; no production worker topology, provider network side effect, or sustained load test. Earlier chunk counts overlap and are not added here. |
| Runtime, provider capability and speech failure behavior | Complete backend runtime-closure file: **10 passed**, including truthful provider inventory, clean-process health import, and typed missing-STT-credential behavior. | Local backend closure passed; provider reachability/authentication was not checked. |
| Alembic chain / deployment schema | One current head (`0048_boolean_defaults`); latest focused migration/release rerun **107 passed**; PostgreSQL fresh upgrade → base downgrade → head re-upgrade passed, as did all twelve alias fixtures. | Local disposable PostgreSQL only. No production DB was migrated; prior Prompt 6 historical suite result remains unchanged. |
| Full backend monolith | Prompt 6 history: **4,048 collected, about 401 passed, exit 137** after memory pressure and intentional termination. Current code collected **4,111**; the formerly stalled hierarchy test passes alone and `tests/tenancy` passes **26/26**, but no current full backend run completed. | **INCOMPLETE**; historical termination was resource-related, not a test assertion failure or a 4,048-pass result. Isolated tenancy success does not close the full-suite gate. |

## 5. Corrections made while closing the test gaps

1. **Provider capability matrix stall:** the earlier matrix called `capabilities_for()` for every provider, importing SDKs during a status inventory. In the combined route/runtime test this could hang in an optional SDK import after `app.main` was loaded. `capability_matrix()` now uses the import-free distribution inventory; `capable=None` means an installed SDK's API surface was not checked, while a missing SDK is truthfully `capable=False`; reachability/authentication remain `not_checked`. The previously timing-out combined file completed **9 passed, 6 deprecation warnings** before the later clean-process regression was added; the latest runtime-closure file is **10 passed**. The warnings are not failures.
2. **Cross-tenant audit scope in recording denial:** the isolation chunk exposed the actor/scope mismatch described above. The implementation now refuses before target-row lookup and logs under the actor's verified tenant. After correction, the entire four-file isolation chunk passed **11/11**. The first run with the bug had **1 failed**; it is superseded by the passing rerun and is retained here rather than hidden.
3. **Alembic version column:** both overlength IDs were shortened and their immediate dependants/tests were updated. A narrow, fail-closed alias normalizer preserves databases that were stamped with the known old values. All twelve known aliases and ambiguous-state refusal have since passed through the actual migration environment against disposable PostgreSQL schemas.
4. **Offline migrations:** migrations 0038–0040 used schema inspection incompatible with Alembic's offline `MockConnection`; the helpers now render upgrade assuming objects absent and downgrade assuming objects present. This is offline SQL generation, not proof of a database upgrade.
5. **PostgreSQL enum lifecycle:** live disposable-PostgreSQL verification found that revision 0018 attempted to create the already-existing native `userrole` enum and revision 0038 left its three revision-owned enums behind on downgrade. Revision 0018 now uses PostgreSQL `ENUM(create_type=False)` for the baseline type, and revision 0038 drops its own enum types after dependent tables. Fresh full upgrade, downgrade-to-base, and re-upgrade now pass.
6. **Portable boolean defaults:** PostgreSQL rejected integer-valued server defaults on Boolean columns from the public-widget/telephony migrations. Existing declarations were corrected to true/false expressions and additive revision `0048_boolean_defaults` repairs already-upgraded schemas; its downgrade preserves the valid Boolean semantics rather than restoring invalid integer defaults.
7. **Clean-process health import:** a direct fresh-process import of `app.observability.health` exposed a circular dependency through runtime-only ORM imports in job contracts and tenancy annotations. Those model references are now type-checking-only; a subprocess regression test verifies the health module imports without first loading `app.main`.

## 6. Chunked test execution matrix

Commands below were run from the repository root unless `dashboard/` is noted. Results are kept as chunks because the large backend monolith has a known resource-termination history. Counts from separate chunks are not summed into a claimed full-suite total.

### Current continuation runs

| Scope / command | Result | Notes |
|---|---:|---|
| `python -m pytest --collect-only -q` | **4,111 collected** | Latest post-fix collection exited 0 in 63.75 s under Python 3.13.14; collection only, no tests executed. Evidence: [`full-suite-collection-postfix.log`](evidence/prompt8-2026-10-05/full-suite-collection-postfix.log). |
| Deterministic file-based 16-shard full-suite attempt (earlier 4,108-test code point) | **Incomplete; harness timed out at 1,800 s** | No aggregate result. Its only saved shard log contains progress dots and no pytest terminal summary; do not count these as a shard pass or infer assertion failures. The incomplete attempt is a resource/time-limit outcome, not a green suite; the current 4,111-test suite was collected but not executed. |
| Supervised verbose retry of the first 257-item shard (earlier 4,108-test code point) | **51 individual `PASSED` results observed; shard manually stopped without a terminal summary** | It stalled at `tests/tenancy/test_tenant_hierarchy.py::test_hierarchy_is_server_derived`; process RSS reached about **1.44 GB** (about 70.6% of the sandbox memory reported by `ps`) and entered uninterruptible I/O wait. Stopped to protect the workspace; no assertion failure was observed before stopping. This is not a completed 51-test chunk or an aggregate. |
| `cd dashboard && npm test -- --reporter=dot` | **42 files, 529 passed** | Dashboard suite after security-settings tests were added; existing React `act(...)`/key warnings were observed. |
| `cd dashboard && npm test -- tests/audit.test.jsx --reporter=dot` | **41 passed** | Paginated/server-filtered audit API plus legacy array compatibility. |
| `cd dashboard && npm test -- tests/security-settings.test.jsx --reporter=dot` | **3 passed** | Live posture/policy/session/key data, fresh reauth, one-time secret, current-session revocation, no inferred certification. |
| `cd dashboard && npm run build` | **Succeeded** | Vite main JS chunk 1,346.71 kB (261.08 kB gzip); advisory recommends code splitting. |
| `python -m pytest -q tests/test_backend_runtime_closure.py` | **10 passed, 6 warnings** | Latest complete-file run includes a clean-process import regression for `app.observability.health`; the warnings are Pydantic deprecations. Earlier combined timeout and 9-test run are superseded for current status. Evidence: [`postfix-runtime-closure-pytest.log`](evidence/prompt8-2026-10-05/postfix-runtime-closure-pytest.log). |
| `/usr/bin/timeout -k 2s 240s python -m pytest -q tests/tenancy` | **26 passed, 271 warnings** | The previously stalled `test_hierarchy_is_server_derived` also passed as a single test (1/1) and within the full six-file tenancy directory; the earlier shard remains incomplete/resource-terminated. Evidence: [`tenant-subsystem-pytest.log`](evidence/prompt8-2026-10-05/tenant-subsystem-pytest.log), [`historical-shard-blocker-isolated-pytest.log`](evidence/prompt8-2026-10-05/historical-shard-blocker-isolated-pytest.log). |
| `python -m pytest -q tests/test_migration_compatibility.py tests/test_deployment.py tests/test_enterprise_persistence.py::TestSchema::test_the_revision_chains_off_the_previous_head tests/test_stable_id_and_release_facts.py tests/test_campaign_release_compatibility.py` | **75 passed** | Earlier focused run checked target revision graph and release assertions; subsequently rerun in the 107-pass Prompt 8 focused set after adding the PostgreSQL enum regression. Live PostgreSQL evidence is documented separately. |
| `python -m pytest -q tests/test_enterprise_persistence.py tests/test_campaign_release_compatibility.py tests/test_migration_compatibility.py` | **48 passed** | Full persistence file plus release and all alias-mapping tests after the compatibility review. |
| `python -m pytest -q tests/security/test_authorization_matrix.py` | **115 passed** | Pytest marker, Pydantic and datetime deprecation warnings; no assertion failures. |
| `python -m pytest -q tests/security/test_contact_center_isolation.py tests/security/test_identity_tenant_isolation.py tests/security/test_telephony_media_isolation.py tests/security/test_tenant_isolation_enterprise.py` | **11 passed** | Final rerun after recording-audit scope fix; first pre-fix run had 1 failure. |
| `python -m pytest -q tests/security/test_identity_enterprise.py tests/security/test_mfa_secrets.py tests/security/test_session_invalidation.py` | **44 passed** | Existing identity/session/MFA controls; deprecation warnings. |
| `python -m pytest -q tests/security/test_sso.py` | **51 passed** | Local IdP fixtures only; no external IdP. Pytest marker and short test-key warnings. |
| `python -m pytest -q tests/security/test_audit_redaction.py tests/security/test_conductor_security.py tests/security/test_public_key_permissions.py tests/security/test_telephony_media_isolation.py` | **7 passed** | Audit, conductor, public key and media scope. This overlaps the isolation rerun; do not add counts. |
| `python -m pytest -q tests/auth/scim/test_scim_protocol.py` | **15 passed** | Protocol/config behavior; Pydantic/datetime warnings. |
| `python -m pytest -q tests/auth/scim/test_scim_users.py` | **22 passed** | SCIM user operations; Pydantic/datetime warnings. |
| `python -m pytest -q tests/test_billing_api.py` | **50 passed** | API billing chunk. |
| `python -m pytest -q tests/test_billing_metering.py` | **56 passed** | Metering chunk. |
| `python -m pytest -q tests/test_billing_subscriptions.py` | **116 passed, 1 skipped** | One pre-existing skip; not introduced to obtain green. |
| `python -m pytest -q tests/test_billing_voice.py` | **46 passed** | Voice billing/callback/idempotency chunk. |
| Targeted `ruff check` on provider matrix, recording, migration helpers, affected Alembic revisions, runtime-closure and associated tests | **Passed** | Target-only lint; no claim of repository-wide lint cleanliness. |
| `alembic upgrade 0036_durable_call_outcomes:head --sql` | **Generated 1,616 lines** | Current downstream migration chain rendered, including RLS/append-only SQL; no connection/migration applied. |
| `alembic upgrade 0038_retell_parity_foundation:0039_conductor_control_plane --sql` | **Generated 482 lines** | Offline SQL only; no connection/migration applied. |
| `alembic downgrade 0039_conductor_control_plane:0038_retell_parity_foundation --sql` | **Generated 128 lines** | Offline SQL only. |
| `alembic upgrade 0044_conductor_webhook_receipts:head --sql` | **Generated 46 lines** | Offline SQL only; confirms canonical 0045 short ID in generated chain. |
| `alembic heads` + revision-length scan | **One head: `0048_boolean_defaults`; 0 IDs >32 characters** | Source graph validation; local disposable PostgreSQL upgrade/downgrade evidence is recorded separately below. |

### Prompt 8 local operational verification (2026-10-05)

These are live checks against explicitly disposable local services, not production/provider certification:

- PostgreSQL 17.11 fresh migration upgrade → full downgrade to base → re-upgrade to `0048_boolean_defaults`: all steps exited 0. The base-state catalog had no Alembic row or enum types. See [`postgres-fresh-upgrade.log`](evidence/prompt8-2026-10-05/postgres-fresh-upgrade.log), [`postgres-full-downgrade.log`](evidence/prompt8-2026-10-05/postgres-full-downgrade.log), [`postgres-full-reupgrade.log`](evidence/prompt8-2026-10-05/postgres-full-reupgrade.log), and [`postgres-base-after-downgrade.txt`](evidence/prompt8-2026-10-05/postgres-base-after-downgrade.txt).
- PostgreSQL alias fixtures: **12/12 passed** through the actual environment normalization path; ambiguous old+canonical 0045 markers were refused without mutation. See [`postgres-alias-fixtures.log`](evidence/prompt8-2026-10-05/postgres-alias-fixtures.log).
- Forced tenant RLS, cross-tenant `WITH CHECK`, and append-only UPDATE/DELETE rejection passed for `deployment_runtime_observations`. `audit_logs` has neither RLS/FORCE RLS nor an append-only trigger; see [`postgres-rls-append-only.txt`](evidence/prompt8-2026-10-05/postgres-rls-append-only.txt) and [`postgres-schema-checks.txt`](evidence/prompt8-2026-10-05/postgres-schema-checks.txt).
- PostgreSQL application-audit probes passed redaction, commit/reread, rollback, and tenant/environment/actor-scope rejection; see [`postgres-application-audit.txt`](evidence/prompt8-2026-10-05/postgres-application-audit.txt).
- Two-process Redis rate limiting shared the same counter (3 allowed, 1 denied); unreachable Redis failed closed. Live PostgreSQL/Redis probes passed, then an unreachable Redis probe made health unavailable while providers stayed `not_checked`. See [`redis-multiprocess-rate-limit.txt`](evidence/prompt8-2026-10-05/redis-multiprocess-rate-limit.txt) and [`live-health-probe.txt`](evidence/prompt8-2026-10-05/live-health-probe.txt).
- PostgreSQL request idempotency and outbox publication/dispatch were exercised across separate processes. A durable dead-letter event survived a PostgreSQL service restart, replayed as round 2, and was again rejected before outbound HTTP because the test secret was deliberately invalid. See [`postgres-multiprocess-idempotency-outbox.txt`](evidence/prompt8-2026-10-05/postgres-multiprocess-idempotency-outbox.txt) and [`postgres-outbox-dlq-restart.txt`](evidence/prompt8-2026-10-05/postgres-outbox-dlq-restart.txt). The direct route-state probe did not exercise the HTTP permission dependency.
- The latest focused post-fix pytest set passed **107 tests with 47 warnings**; runtime closure passed separately **10 tests with 6 warnings**; targeted Ruff checks passed. This is not a full-suite result. See [`postfix-targeted-pytest.log`](evidence/prompt8-2026-10-05/postfix-targeted-pytest.log), [`postfix-runtime-closure-pytest.log`](evidence/prompt8-2026-10-05/postfix-runtime-closure-pytest.log), and [`ruff-postfix-targeted.txt`](evidence/prompt8-2026-10-05/ruff-postfix-targeted.txt).

### Earlier Prompt 7 chunk results retained from the same work log

These passing chunks predate the continuation corrections above. They are evidence for their listed scopes only; they are not a full backend-suite result.

| Scope | Recorded result |
|---|---:|
| Deployment adapter/readiness focused chunk | **19 passed** |
| Deployment test chunk | **32 passed** |
| Configuration/readiness/runtime-cost chunk | **26 passed** |
| Observability configuration | **8 passed** |
| Security headers + rate limits + security regression + security.txt + audit redaction | **28 passed** |
| OIDC | **53 passed** |
| SCIM filter/schema | **23 passed** |
| SCIM credentials/groups | **29 passed** |
| SSRF + AI-tool + ops-toolkit | **95 passed** |
| Identity-policy + audit redaction | **17 passed** |
| API keys/sessions/revocation | **34 passed** |
| MFA | **80 passed** |
| Tenant isolation | **10 passed** |
| Environment/resource isolation | **59 passed** |
| Jobs/outbox/idempotency | **37 passed, 1 skipped** |
| Workflow/durable persistence/notification/CRM idempotency | **83 passed** |
| Telephony/provider and transfer security group | **31 passed** |

The six SCIM modules were exercised across the earlier and current chunks: filter/schema **23**, credentials/groups **29**, protocol **15**, users **22**. This yields **89 passing tests across those chunks**, not a claim that they ran in one process. Billing's four files were each run separately: **268 passed, 1 skipped** total across those chunks.

### Full-suite status and historical baseline

- **Current full backend suite:** not completed; therefore no current full-suite pass count is claimed. The latest post-fix collector found **4,111 tests** in collect-only mode; no tests ran in that command. The deterministic 16-shard harness and supervised first-shard attempt were run against the earlier 4,108-test code point: the harness timed out at 1,800 s without a reconciled result, and the supervised shard showed 51 individual passes before stalling during the tenant-hierarchy fixture at about 1.44 GB RSS and being manually stopped without a terminal summary. Subsequent isolated reruns passed the formerly stalled test (1/1) and the complete `tests/tenancy` directory (26/26), but do not retroactively complete or reconcile that shard, and the current 4,111-test suite has not run to completion. Those partial/isolated observations are not a current full-suite pass; no assertion failure was observed before the earlier manual stop.
- **Prompt 6 historical baseline, retained without alteration:** collection was **4,048 tests**; the monolithic run recorded about **401 passes** before memory pressure and intentional termination. Exit **137** was resource termination, not an assertion failure. It is not a 4,048-pass result.
- The target inventory's earlier 3,889-pass/46-skip result is a historical result for a different code point and must not be read as the current Prompt 7 full-suite result.
- Tests ran under Python **3.13.14** in this workspace; the project tool target is Python 3.12. No Python 3.12 test run is claimed.

## 7. Migration compatibility and deployment caveats

- Canonical revision IDs: `0038_agent_chat_conductor` and `0045_request_idem_receipts`, both no longer than 32 characters.
- `app/db/migration_compatibility.py` recognizes twelve known overlength historical IDs: 0017, 0018, 0019, 0020, 0024, 0031, 0032, 0034, 0035, 0036, 0038, and 0045. It updates only the `alembic_version.version_num` marker before Alembic configures its context; it does not alter business tables or application rows. If both a legacy and canonical marker are present, it raises and requires an operator to inspect the schema.
- SQLite compatibility tests still validate alias mappings and ambiguous-state refusal, but SQLite does not enforce PostgreSQL's `VARCHAR(32)` length limit. Separate PostgreSQL fixtures exercised every alias against its matching physical schema through the actual Alembic environment path; an ambiguous legacy+canonical marker was refused without mutation.
- `alembic heads` is a single head, `0048_boolean_defaults`. Offline `alembic upgrade 0036_durable_call_outcomes:head --sql` rendered 1,616 lines, and offline focused ranges were exercised. Separately, local disposable PostgreSQL 17.11 completed fresh `upgrade head`, `downgrade base`, and re-upgrade to `head`; the base had no version row or remaining enum types. The refreshed catalog shows `alembic_version.version_num` as `character varying(32)` and records Boolean defaults, RLS/policy/trigger state, and the explicit `audit_logs` boundary.
- These checks used disposable local databases, not production. Before production rollout, inspect actual stamps and schema in a disposable copy of each deployment. Do not manually change revision rows without confirming their physical schema state.

## 8. Gaps and prioritized follow-up

### P0 — must be verified before production assurance

1. **PostgreSQL migration and audit invariants:** the local disposable PostgreSQL 17.11 upgrade/downgrade/re-upgrade, all twelve alias fixtures, `deployment_runtime_observations` forced RLS/append-only checks, and application-level audit redaction/scope/rollback probes passed. Before production, validate actual stamped schemas and migration rollout. Separately, `audit_logs` currently lacks PostgreSQL RLS/FORCE RLS and an append-only trigger; its tested tenant/reference guarantees are application-level only.
2. **Redis/distributed controls:** local Redis 8.0.2 and PostgreSQL tests passed across independent processes, including shared throttling, fail-closed Redis behavior, concurrent idempotency/outbox operations, and durable DLQ replay after PostgreSQL restart. Still verify the deployed Redis/worker topology, actual production failure policy, and sustained load; local evidence is not a production multi-worker test.
3. **Production config and provider auth:** stage the production settings with real secret-management inputs and sandbox provider credentials; verify health semantics without logging credentials. `configured`/`installed`/`capable` must remain distinct from `reachable`/`authenticated`; provider reachability and authentication remain `not_checked`.

### P1 — network and release evidence

4. **SSRF network boundary:** deploy egress restrictions for private, loopback, link-local and metadata ranges; test redirect and DNS-rebinding behavior at the network layer. String validation alone cannot guarantee this.
5. **Full backend suite:** run resource-bounded CI shards and publish exact collection/pass/fail/skip/error/termination totals. Preserve the Prompt 6 historical exit-137 record; do not turn resource termination into a pass.
6. **Deployment/adapters:** validate the actual reverse proxy, CORS/trusted-host origins, headers, signed webhooks, WebSockets, telephony callbacks, backup/restore, and deployment observations in an isolated staging stack.
7. **Dependency/security scanning:** rerun pinned dependency audit and SAST against the exact release artifact; review explicit accepted risks in the historical Step 9/10 document before making a current supply-chain claim.

### P2 — independent assurance

8. Arrange an independent scoped penetration test and a separately evidenced compliance assessment if required. Passing these tests is not a substitute for either. No SSO/SCIM, legal compliance, or certification claim should be inferred from code presence or local tests.

## 9. Prompt 8 handoff

Prompt 8 is scoped as **operational verification of Prompt 7**, not a second authentication architecture rewrite. Local disposable PostgreSQL 17.11 and Redis 8.0.2 gates have now been exercised; their evidence and exact limits are recorded in [`PROMPT8_OPERATIONAL_VERIFICATION_REPORT.md`](PROMPT8_OPERATIONAL_VERIFICATION_REPORT.md) and the updated [`PROMPT8_HANDOFF.md`](PROMPT8_HANDOFF.md). The resource-bounded full suite, production/provider/proxy checks, dependency/SAST scan of a release artifact, and independent assessment remain incomplete. No production rollout or compliance claim follows from these local results.

## 10. Complete resulting file contents

The earlier **`/home/user/PROMPT_7_FULL_SOURCE_CONTENTS.zip`** and **`/home/user/Prompt7_Backend_Files.zip`** were generated before this Prompt 8 operational continuation and are not the current source snapshot. The refreshed complete worktree bundle is **`/home/user/PROMPT8_OPERATIONAL_SOURCE_CONTENTS.zip`**. Its manifest records **1,401 project paths: 1,391 complete modified/created file bodies and 10 deletions**, including the ignored Prompt 7 and Prompt 8 report deliverables and all 33 evidence files under `docs/evidence/prompt8-2026-10-05/`. `MANIFEST.tsv` lists Git status, path, byte size, SHA-256, and archive member; deleted paths are listed without a file body. The bundle includes current full contents of modified and created source, tests, documentation, and saved evidence. No file body is replaced with an ellipsis, “rest unchanged,” or an omission placeholder. The archive is a snapshot of the current dirty worktree, not a committed release artifact.
