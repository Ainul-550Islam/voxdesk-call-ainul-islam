# Backend Prompt 7 — renewed target inventory and Lumay follow-up

## Scope and evidence

- Renewed mandatory targets: **30**; present before edits: **30/30**; re-read after edits: **30/30**; skipped targets: **0**.
- This inventory is the backend-only 30-target subreview; it does not account for the separate Prompt 7 dashboard integration. The overall Prompt 7 report records that UI work and its tests.
- The Lumay product page was reviewed on 2026-09-28 as vendor marketing only; it is not independent technical evidence, parity evidence, or a verification of live behavior.
- Source review does not certify production reachability, a deployed service, legal/regulatory compliance, or certification.
- The repository contains **702** Python modules under `app/`. The table below covers the renewed Prompt 7 integration/dependency/runtime scope; the remaining list is an inventory of paths not covered by this renewed target review, not a defect list.
- Current verification ran in Python **3.13.14** although the project tool target is Python 3.12; no Python 3.12-specific run is claimed.

## Mandatory target status

| # | Canonical target | Status |
|---:|---|---|
| 1 | `app/main.py` | Reviewed; health/dependency/deployment routes remain mounted and startup configuration validation is retained. |
| 2 | `app/agent/stt.py` | Reviewed; typed fail-fast provider configuration and pinned Deepgram/Pipecat surface are retained. |
| 3 | `app/agent/stt_stream.py` | Reviewed; provider-neutral streaming contract, cancellation and typed failures are retained. |
| 4 | `app/providers/compatibility.py` | Reviewed; the health inventory is import-free; installed/configured are separate from API-surface capability, reachability, and authentication. Unchecked capability is reported as unknown, not success. |
| 5 | `app/providers/errors.py` | Reviewed; typed compatibility and dependency errors are available. |
| 6 | `app/core/config_validation.py` | Reviewed; secret-safe runtime validation includes adapters, prices, and provider SDK capability checks. |
| 7 | `app/core/dependency_health.py` | Reviewed; health response reports probe/config state without claiming provider authentication or worker presence. |
| 8 | `app/observability/health.py` | Reviewed; readiness probes database/cache and provider configuration without external-provider API calls. |
| 9 | `app/observability/runtime.py` | Reviewed; bounded runtime metrics and structured safe correlation fields use existing metrics/logging. |
| 10 | `app/observability/cost.py` | Reviewed; normalized ROI costs require matching persisted measured-cost metadata; unavailable pricing stays unavailable. |
| 11 | `app/jobs/runtime.py` | Reviewed; persisted job scope, correlation, timeout, and lifecycle telemetry use the existing durable queue. |
| 12 | `app/jobs/registry.py` | Reviewed; canonical handler bootstrap reuses the existing type registry. |
| 13 | `app/jobs/ai_specialized_jobs.py` | Reviewed; four async specialized job types exist; absent privacy-approved durable input fails explicitly instead of fabricating success. |
| 14 | `app/deployment/runtime.py` | Reviewed; existing queue and governance approval/policy checks drive apply and re-observation. |
| 15 | `app/deployment/readiness.py` | Reviewed; unavailable/unknown controls remain not verified; readiness does not imply deployment. |
| 16 | `app/deployment/verification.py` | Reviewed; fresh, scoped, healthy artifact/revision observation is required for runtime verification. |
| 17 | `app/deployment/evidence.py` | Reviewed; allowlisted observation evidence is projected into existing audit/evidence ledgers. |
| 18 | `app/deployment/adapters/base.py` | Reviewed and hardened; exact full SHA-256 identity parsing helpers added for runtime image IDs and repo digests. |
| 19 | `app/deployment/adapters/kubernetes.py` | Reviewed and hardened; image digest suffix must exactly equal the approved SHA-256 digest. |
| 20 | `app/deployment/adapters/container.py` | Reviewed and hardened; repository and digest from RepoDigests must match exactly; generic apply remains explicitly unsupported without a deployment spec. |
| 21 | `app/deployment/adapters/airgap.py` | Reviewed; Ed25519 signature and referenced files are validated; package integrity is not installation/runtime proof. |
| 22 | `app/deployment/adapters/registry.py` | Reviewed; read-only OCI lookup is pinned to the configured HTTPS registry host and redirects are disabled. |
| 23 | `app/api/health_routes.py` | Reviewed; separate liveness, readiness, and dependency endpoints. |
| 24 | `app/api/deployment_runtime_routes.py` | Reviewed; tenant/environment scoped readiness, queue apply, status, and persisted-observation verification endpoints. |
| 25 | `requirements.txt` | Reviewed; 42 pinned distributions; clean install succeeded in current Python 3.13.14 sandbox. |
| 26 | `pyproject.toml` | Reviewed; Ruff and mypy tool configuration; pytest configuration canonically remains in pytest.ini. |
| 27 | `alembic/versions/0036_runtime_deployment_observability.py` | Reviewed; canonical target filename is present; revision ID is the 30-character `0036_runtime_deployment_observ` to fit Alembic's default version column; parent is `0036_durable_call_outcomes`. |
| 28 | `tests/deployment/test_runtime_adapters.py` | Reviewed and extended for malformed digest-suffix and wrong-repository rejection. |
| 29 | `tests/deployment/test_readiness_verification.py` | Reviewed; readiness and freshness/scope/state-transition checks retained. |
| 30 | `tests/test_backend_runtime_closure.py` | Reviewed; route, job-context, capability, secret-safety, and no-fake-verification checks retained. |

## Canonical-path and manifest reconciliation

- Dependency installation is canonical in `requirements.txt`; 42 pinned distributions were imported and version-matched by `scripts/verify_dependencies.py`. `pip check` reported no broken requirements.
- `pyproject.toml` is the tool manifest; `pytest.ini` remains the canonical asyncio/test discovery configuration and was not substituted for it.
- The canonical target file is `0036_runtime_deployment_observability.py`; its current `revision` is the shorter `0036_runtime_deployment_observ` because Alembic's default `version_num` is `VARCHAR(32)`. The parent is the distinct existing `0036_durable_call_outcomes` revision. A descriptive filename need not equal the revision ID. `alembic heads` reports one head; no duplicate migration was added.
- Offline SQL generation for the target edge `0036_durable_call_outcomes:head` succeeded and includes RLS/FORCE RLS and the append-only trigger. Whole-chain offline generation is blocked by legacy data-dependent migration `0017_organization_environment_foundation` (`bind.execute` returns `None` in offline mode); this does not establish a live migration result.
- `pytest.ini` is noted here as the canonical test configuration but is not one of the renewed 30 targets.

## Runtime/dependency verification boundary

- Full backend suite before the final exact-digest hardening: **3,889 passed, 46 skipped**. Post-hardening focused targets: **25 passed**; all deployment tests after hardening: **19 passed**.
- A second full-suite attempt after the final small adapter change timed out at approximately 71% and was terminated; it produced no final test summary. The previous full-suite result must not be read as a post-hardening full-suite run.
- Target Ruff: passed. Repository-wide Ruff: **502 findings** (mostly outside this target set); target-only lint remains clean.
- `DATABASE_URL` and `psql` are absent. No PostgreSQL migration application, live RLS/trigger behavior, or database-backed runtime verification is claimed.
- Live provider, Redis, S3, registry, Docker, Kubernetes, and production cluster availability/authentication were not established. Configured/installed/capable is not equivalent to reachable/authenticated.

## Remaining specialized-agent/runtime gaps (honestly bounded)

- The canonical durable worker registers deployment, voice-clone/outbox, and four asynchronous specialized job types (translation, insight, forecast, anomaly). The specialized job implementation intentionally fails when privacy-approved durable input references are unavailable; it does not synthesize a success.
- Legal and the OCG/QMS/healthcare/manufacturing/retail compliance flows exist via separate specialized-agent/compliance API/service paths but are not registered as durable job handlers in the inspected `app/jobs` registry. Do not claim all eight have end-to-end asynchronous execution.
- Review backlog gauge is defined but has no authoritative safe updater; a tenant-scoped request cannot truthfully set a global gauge under RLS.
- Cost projection helper requires matching immutable recorded-cost metadata. ROI calculations label caller-provided evidence references unverified when no authoritative source resolver exists; no ROI or savings claim is inferred.
- Human review remains workflow evidence, not legal/regulatory certification.

## Backend Python modules outside the renewed 30-target review

- Modules under `app/`: **702**. Paths explicitly covered by the renewed targets and prior inventory review tables: **36**; remaining paths listed below: **666**. This does not imply those paths are defective, and it is not a 100% product-parity audit.

### `app/`

- `app/__init__.py` — not reviewed in this renewed Prompt 7 pass
### `app/agent/`

- `app/agent/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/agent/errors.py` — not reviewed in this renewed Prompt 7 pass
- `app/agent/functions.py` — not reviewed in this renewed Prompt 7 pass
- `app/agent/humanize.py` — not reviewed in this renewed Prompt 7 pass
- `app/agent/llm_factory.py` — not reviewed in this renewed Prompt 7 pass
- `app/agent/pipeline.py` — not reviewed in this renewed Prompt 7 pass
- `app/agent/prompts.py` — not reviewed in this renewed Prompt 7 pass
- `app/agent/provider_observability.py` — not reviewed in this renewed Prompt 7 pass
- `app/agent/text_agent.py` — not reviewed in this renewed Prompt 7 pass
- `app/agent/tts.py` — not reviewed in this renewed Prompt 7 pass
- `app/agent/usage_tracker.py` — not reviewed in this renewed Prompt 7 pass
- `app/agent/voice_settings.py` — not reviewed in this renewed Prompt 7 pass
### `app/ai/`

- `app/ai/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/budget.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/circuit_breaker.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/context.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/costs.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/errors.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/fallback.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/gateway.py` — not reviewed in this renewed Prompt 7 pass
### `app/ai/guardrails/`

- `app/ai/guardrails/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/guardrails/input.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/guardrails/output.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/guardrails/pii.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/guardrails/safety.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/guardrails/tool_policy.py` — not reviewed in this renewed Prompt 7 pass
### `app/ai/`

- `app/ai/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/policy.py` — not reviewed in this renewed Prompt 7 pass
### `app/ai/prompts/`

- `app/ai/prompts/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/prompts/registry.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/prompts/rollout.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/prompts/versioning.py` — not reviewed in this renewed Prompt 7 pass
### `app/ai/`

- `app/ai/routing.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/runtime.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/telemetry.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/timeouts.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/trace.py` — not reviewed in this renewed Prompt 7 pass
- `app/ai/usage.py` — not reviewed in this renewed Prompt 7 pass
### `app/analytics/`

- `app/analytics/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/analytics/_stats.py` — not reviewed in this renewed Prompt 7 pass
- `app/analytics/conversation_metrics.py` — not reviewed in this renewed Prompt 7 pass
- `app/analytics/forecast.py` — not reviewed in this renewed Prompt 7 pass
- `app/analytics/test_conversation_metrics.py` — not reviewed in this renewed Prompt 7 pass
- `app/analytics/test_forecast.py` — not reviewed in this renewed Prompt 7 pass
- `app/analytics/test_stats.py` — not reviewed in this renewed Prompt 7 pass
### `app/anomaly/`

- `app/anomaly/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/anomaly/alerting.py` — not reviewed in this renewed Prompt 7 pass
- `app/anomaly/detectors.py` — not reviewed in this renewed Prompt 7 pass
- `app/anomaly/persistence.py` — not reviewed in this renewed Prompt 7 pass
- `app/anomaly/schemas.py` — not reviewed in this renewed Prompt 7 pass
- `app/anomaly/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/api/`

- `app/api/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/agent_management_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/agent_state_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/ai_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/analytics_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/anomaly_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/api_key_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/appointment_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/auth_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/automation_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/billing_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/calendar_webhook_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/call_outcome_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/campaign_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/compliance_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/connector_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/conversation_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/crm_webhook_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/deployment_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/domain_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/environment_access_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/environment_resource_export_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/environment_resource_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/environment_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/eval_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/evidence_admin_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/evidence_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/forecast_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/gdpr_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/governance_admin_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/governance_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/identity_errors.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/identity_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/inbox_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/industry_template_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/insight_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/integration_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/jobs_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/knowledge_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/lead_activity_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/lead_import_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/lead_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/lead_segment_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/legal_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/license_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/machine_guards.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/machine_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/mfa_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/model_registry_admin_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/model_registry_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/notification_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/organization_membership_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/organization_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/outbox_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/password_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/phone_numbers_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/prompt_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/public_webhook_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/qa_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/queue_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/risk_admin_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/risk_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/roi_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/routing_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/scim_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/security_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/security_session_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/service_account_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/session_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/skills_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/specialized_agent_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/sso_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/supervisor_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/team_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/tenant_admin_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/tenant_membership_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/tenant_security_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/tenant_usage_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/translation_routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api/workflow_routes.py` — not reviewed in this renewed Prompt 7 pass
### `app/api_tools/`

- `app/api_tools/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/api_tools/routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/api_tools/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/`

- `app/auth/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/api_keys.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/dependencies.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/domain/`

- `app/auth/domain/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/domain/policy.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/domain/service.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/domain/verification.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/email/`

- `app/auth/email/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/email/password_reset.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/email/verification.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/identity/`

- `app/auth/identity/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/api_keys.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/domains.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/email.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/events.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/exceptions.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/mfa.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/policies.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/principals.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/identity/scim/`

- `app/auth/identity/scim/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/scim/filter.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/scim/schemas.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/scim/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/identity/`

- `app/auth/identity/secrets.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/service.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/service_accounts.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/sessions.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/identity/sso/`

- `app/auth/identity/sso/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/sso/claims.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/sso/oidc.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/sso/saml.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/sso/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/identity/`

- `app/auth/identity/tokens.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/identity/totp.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/`

- `app/auth/jwt.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/mfa/`

- `app/auth/mfa/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/mfa/challenges.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/mfa/exceptions.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/mfa/policy.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/mfa/recovery_codes.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/mfa/totp.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/`

- `app/auth/password.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/permissions.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/rbac.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/scim/`

- `app/auth/scim/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/scim/auth.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/scim/exceptions.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/scim/groups.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/scim/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/scim/schemas.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/scim/service.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/scim/users.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/`

- `app/auth/service.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/service_accounts.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/sessions.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/sso/`

- `app/auth/sso/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/sso/base.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/sso/certificates.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/sso/claims.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/sso/discovery.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/sso/mapping.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/sso/metadata.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/sso/oidc.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/sso/saml.py` — not reviewed in this renewed Prompt 7 pass
- `app/auth/sso/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/auth/`

- `app/auth/tokens.py` — not reviewed in this renewed Prompt 7 pass
### `app/automation/`

- `app/automation/dlq.py` — not reviewed in this renewed Prompt 7 pass
- `app/automation/durable_repository.py` — not reviewed in this renewed Prompt 7 pass
- `app/automation/executor.py` — not reviewed in this renewed Prompt 7 pass
- `app/automation/idempotency.py` — not reviewed in this renewed Prompt 7 pass
- `app/automation/retry.py` — not reviewed in this renewed Prompt 7 pass
### `app/billing/`

- `app/billing/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/billing/base.py` — not reviewed in this renewed Prompt 7 pass
- `app/billing/cost.py` — not reviewed in this renewed Prompt 7 pass
- `app/billing/entitlements.py` — not reviewed in this renewed Prompt 7 pass
- `app/billing/errors.py` — not reviewed in this renewed Prompt 7 pass
- `app/billing/hooks.py` — not reviewed in this renewed Prompt 7 pass
- `app/billing/licensing.py` — not reviewed in this renewed Prompt 7 pass
- `app/billing/metering.py` — not reviewed in this renewed Prompt 7 pass
- `app/billing/periods.py` — not reviewed in this renewed Prompt 7 pass
- `app/billing/plans.py` — not reviewed in this renewed Prompt 7 pass
### `app/billing/providers/`

- `app/billing/providers/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/billing/providers/manual.py` — not reviewed in this renewed Prompt 7 pass
- `app/billing/providers/stripe.py` — not reviewed in this renewed Prompt 7 pass
### `app/billing/`

- `app/billing/reconciliation.py` — not reviewed in this renewed Prompt 7 pass
- `app/billing/registry.py` — not reviewed in this renewed Prompt 7 pass
- `app/billing/service.py` — not reviewed in this renewed Prompt 7 pass
- `app/billing/webhooks.py` — not reviewed in this renewed Prompt 7 pass
### `app/builder/`

- `app/builder/node.py` — not reviewed in this renewed Prompt 7 pass
- `app/builder/workflow_repository.py` — not reviewed in this renewed Prompt 7 pass
- `app/builder/workflow_schemas.py` — not reviewed in this renewed Prompt 7 pass
- `app/builder/workflow_state.py` — not reviewed in this renewed Prompt 7 pass
- `app/builder/workflow_store.py` — not reviewed in this renewed Prompt 7 pass
### `app/channels/`

- `app/channels/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/channels/messaging.py` — not reviewed in this renewed Prompt 7 pass
### `app/compliance/`

- `app/compliance/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/compliance/enums.py` — not reviewed in this renewed Prompt 7 pass
- `app/compliance/industry.py` — not reviewed in this renewed Prompt 7 pass
- `app/compliance/industry_templates.py` — not reviewed in this renewed Prompt 7 pass
- `app/compliance/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/compliance/ocg.py` — not reviewed in this renewed Prompt 7 pass
- `app/compliance/qms.py` — not reviewed in this renewed Prompt 7 pass
- `app/compliance/repository.py` — not reviewed in this renewed Prompt 7 pass
- `app/compliance/schemas.py` — not reviewed in this renewed Prompt 7 pass
- `app/compliance/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/contact_center/`

- `app/contact_center/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/contact_center/agent_state.py` — not reviewed in this renewed Prompt 7 pass
- `app/contact_center/callbacks.py` — not reviewed in this renewed Prompt 7 pass
- `app/contact_center/exceptions.py` — not reviewed in this renewed Prompt 7 pass
- `app/contact_center/metrics.py` — not reviewed in this renewed Prompt 7 pass
- `app/contact_center/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/contact_center/overflow.py` — not reviewed in this renewed Prompt 7 pass
- `app/contact_center/policies.py` — not reviewed in this renewed Prompt 7 pass
- `app/contact_center/presence.py` — not reviewed in this renewed Prompt 7 pass
- `app/contact_center/queues.py` — not reviewed in this renewed Prompt 7 pass
- `app/contact_center/repository.py` — not reviewed in this renewed Prompt 7 pass
- `app/contact_center/routing.py` — not reviewed in this renewed Prompt 7 pass
- `app/contact_center/service.py` — not reviewed in this renewed Prompt 7 pass
- `app/contact_center/skills.py` — not reviewed in this renewed Prompt 7 pass
### `app/core/`

- `app/core/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/cache.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/chaos.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/compliance.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/config.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/correlation.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/data_policy.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/errors.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/health.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/i18n.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/logging.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/metrics.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/observability.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/rate_limit.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/retention.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/security_headers.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/security_txt.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/ssrf.py` — not reviewed in this renewed Prompt 7 pass
- `app/core/tracing.py` — not reviewed in this renewed Prompt 7 pass
### `app/db/`

- `app/db/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/db/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/db/rls.py` — not reviewed in this renewed Prompt 7 pass
- `app/db/session.py` — not reviewed in this renewed Prompt 7 pass
### `app/deployment/`

- `app/deployment/__init__.py` — not reviewed in this renewed Prompt 7 pass
### `app/deployment/adapters/`

- `app/deployment/adapters/__init__.py` — not reviewed in this renewed Prompt 7 pass
### `app/deployment/`

- `app/deployment/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/deployment/schemas.py` — not reviewed in this renewed Prompt 7 pass
- `app/deployment/service.py` — not reviewed in this renewed Prompt 7 pass
- `app/deployment/validator.py` — not reviewed in this renewed Prompt 7 pass
### `app/domain/`

- `app/domain/agent_models.py` — not reviewed in this renewed Prompt 7 pass
- `app/domain/analytics_models.py` — not reviewed in this renewed Prompt 7 pass
- `app/domain/automation_models.py` — not reviewed in this renewed Prompt 7 pass
- `app/domain/campaign_models.py` — not reviewed in this renewed Prompt 7 pass
- `app/domain/conversation_models.py` — not reviewed in this renewed Prompt 7 pass
- `app/domain/inbox_models.py` — not reviewed in this renewed Prompt 7 pass
- `app/domain/notification_models.py` — not reviewed in this renewed Prompt 7 pass
- `app/domain/workflow_models.py` — not reviewed in this renewed Prompt 7 pass
### `app/email/`

- `app/email/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/email/delivery.py` — not reviewed in this renewed Prompt 7 pass
- `app/email/providers.py` — not reviewed in this renewed Prompt 7 pass
- `app/email/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/environments/`

- `app/environments/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/access.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/context_resolution.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/deployment.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/events.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/guard.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/membership.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/policies.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/policy_inheritance.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/quota.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/resource_binding.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/resource_context.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/resource_migration.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/resource_policy.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/resource_queries.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/resource_scope.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/resource_types.py` — not reviewed in this renewed Prompt 7 pass
- `app/environments/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/evaluation/`

- `app/evaluation/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/evaluation/evaluator.py` — not reviewed in this renewed Prompt 7 pass
- `app/evaluation/prompts.py` — not reviewed in this renewed Prompt 7 pass
- `app/evaluation/scoring.py` — not reviewed in this renewed Prompt 7 pass
- `app/evaluation/test_evaluator.py` — not reviewed in this renewed Prompt 7 pass
- `app/evaluation/test_prompts.py` — not reviewed in this renewed Prompt 7 pass
- `app/evaluation/test_scoring.py` — not reviewed in this renewed Prompt 7 pass
### `app/forecasting/`

- `app/forecasting/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/forecasting/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/gdpr/`

- `app/gdpr/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/gdpr/consent.py` — not reviewed in this renewed Prompt 7 pass
- `app/gdpr/redact.py` — not reviewed in this renewed Prompt 7 pass
- `app/gdpr/requests.py` — not reviewed in this renewed Prompt 7 pass
- `app/gdpr/schedule.py` — not reviewed in this renewed Prompt 7 pass
- `app/gdpr/test_consent.py` — not reviewed in this renewed Prompt 7 pass
- `app/gdpr/test_redact.py` — not reviewed in this renewed Prompt 7 pass
- `app/gdpr/test_requests.py` — not reviewed in this renewed Prompt 7 pass
- `app/gdpr/test_schedule.py` — not reviewed in this renewed Prompt 7 pass
### `app/governance/`

- `app/governance/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/access.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/attestation.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/audit.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/audit_bridge.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/context.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/dependencies.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/enums.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/events.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/evidence.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/exceptions.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/hashing.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/lineage.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/model_registry.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/packages.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/policy.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/repository.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/residency.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/retention.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/risk.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/schemas.py` — not reviewed in this renewed Prompt 7 pass
- `app/governance/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/inbox/`

- `app/inbox/assignment.py` — not reviewed in this renewed Prompt 7 pass
- `app/inbox/concurrency.py` — not reviewed in this renewed Prompt 7 pass
- `app/inbox/repository.py` — not reviewed in this renewed Prompt 7 pass
- `app/inbox/service.py` — not reviewed in this renewed Prompt 7 pass
- `app/inbox/sla.py` — not reviewed in this renewed Prompt 7 pass
### `app/insight/`

- `app/insight/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/integrations/`

- `app/integrations/__init__.py` — not reviewed in this renewed Prompt 7 pass
### `app/integrations/calendar/`

- `app/integrations/calendar/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/calendar/base.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/calendar/errors.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/calendar/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/calendar/nlp.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/calendar/policy.py` — not reviewed in this renewed Prompt 7 pass
### `app/integrations/calendar/providers/`

- `app/integrations/calendar/providers/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/calendar/providers/calcom.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/calendar/providers/google.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/calendar/providers/internal.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/calendar/providers/microsoft.py` — not reviewed in this renewed Prompt 7 pass
### `app/integrations/calendar/`

- `app/integrations/calendar/registry.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/calendar/service.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/calendar/timezones.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/calendar/tools.py` — not reviewed in this renewed Prompt 7 pass
### `app/integrations/`

- `app/integrations/connector.py` — not reviewed in this renewed Prompt 7 pass
### `app/integrations/crm/`

- `app/integrations/crm/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/crm/base.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/crm/crypto.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/crm/errors.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/crm/events.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/crm/hooks.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/crm/legacy.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/crm/mapping.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/crm/models.py` — not reviewed in this renewed Prompt 7 pass
### `app/integrations/crm/providers/`

- `app/integrations/crm/providers/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/crm/providers/ghl.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/crm/providers/hubspot.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/crm/providers/jobber.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/crm/providers/webhook.py` — not reviewed in this renewed Prompt 7 pass
### `app/integrations/crm/`

- `app/integrations/crm/registry.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/crm/retry.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/crm/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/integrations/`

- `app/integrations/google_calendar.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/notifications.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/oauth.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/reminders.py` — not reviewed in this renewed Prompt 7 pass
### `app/integrations/validation/`

- `app/integrations/validation/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/validation/business.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/validation/cli.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/validation/registry.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/validation/report.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/validation/status.py` — not reviewed in this renewed Prompt 7 pass
- `app/integrations/validation/voice.py` — not reviewed in this renewed Prompt 7 pass
### `app/jobs/`

- `app/jobs/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/jobs/concurrency.py` — not reviewed in this renewed Prompt 7 pass
- `app/jobs/dead_letter.py` — not reviewed in this renewed Prompt 7 pass
- `app/jobs/heartbeat.py` — not reviewed in this renewed Prompt 7 pass
- `app/jobs/idempotency.py` — not reviewed in this renewed Prompt 7 pass
- `app/jobs/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/jobs/queue.py` — not reviewed in this renewed Prompt 7 pass
- `app/jobs/replay.py` — not reviewed in this renewed Prompt 7 pass
- `app/jobs/repository.py` — not reviewed in this renewed Prompt 7 pass
- `app/jobs/retry.py` — not reviewed in this renewed Prompt 7 pass
- `app/jobs/scheduler.py` — not reviewed in this renewed Prompt 7 pass
- `app/jobs/types.py` — not reviewed in this renewed Prompt 7 pass
- `app/jobs/worker.py` — not reviewed in this renewed Prompt 7 pass
### `app/knowledge/`

- `app/knowledge/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/chunking.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/context.py` — not reviewed in this renewed Prompt 7 pass
### `app/knowledge/embeddings/`

- `app/knowledge/embeddings/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/embeddings/base.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/embeddings/hashing.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/embeddings/openai.py` — not reviewed in this renewed Prompt 7 pass
### `app/knowledge/extractors/`

- `app/knowledge/extractors/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/extractors/base.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/extractors/docx.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/extractors/pdf.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/extractors/tabular.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/extractors/text.py` — not reviewed in this renewed Prompt 7 pass
### `app/knowledge/`

- `app/knowledge/ingest.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/jobs.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/policy.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/rerank.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/retrieval.py` — not reviewed in this renewed Prompt 7 pass
### `app/knowledge/storage/`

- `app/knowledge/storage/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/storage/base.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/storage/local.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/storage/s3.py` — not reviewed in this renewed Prompt 7 pass
### `app/knowledge/`

- `app/knowledge/url_ingest.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge/vectorstore.py` — not reviewed in this renewed Prompt 7 pass
### `app/knowledge_ops/`

- `app/knowledge_ops/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge_ops/graph.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge_ops/test_graph.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge_ops/test_vectors.py` — not reviewed in this renewed Prompt 7 pass
- `app/knowledge_ops/vectors.py` — not reviewed in this renewed Prompt 7 pass
### `app/leads/`

- `app/leads/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/leads/activities.py` — not reviewed in this renewed Prompt 7 pass
- `app/leads/consent.py` — not reviewed in this renewed Prompt 7 pass
- `app/leads/dedup.py` — not reviewed in this renewed Prompt 7 pass
- `app/leads/enrichment.py` — not reviewed in this renewed Prompt 7 pass
- `app/leads/exceptions.py` — not reviewed in this renewed Prompt 7 pass
- `app/leads/exporters.py` — not reviewed in this renewed Prompt 7 pass
- `app/leads/importers.py` — not reviewed in this renewed Prompt 7 pass
- `app/leads/lifecycle.py` — not reviewed in this renewed Prompt 7 pass
- `app/leads/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/leads/repository.py` — not reviewed in this renewed Prompt 7 pass
- `app/leads/scoring.py` — not reviewed in this renewed Prompt 7 pass
- `app/leads/segmentation.py` — not reviewed in this renewed Prompt 7 pass
- `app/leads/service.py` — not reviewed in this renewed Prompt 7 pass
- `app/leads/tasks.py` — not reviewed in this renewed Prompt 7 pass
### `app/legal/`

- `app/legal/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/legal/citations.py` — not reviewed in this renewed Prompt 7 pass
- `app/legal/clause_engine.py` — not reviewed in this renewed Prompt 7 pass
- `app/legal/persistence.py` — not reviewed in this renewed Prompt 7 pass
- `app/legal/review_engine.py` — not reviewed in this renewed Prompt 7 pass
- `app/legal/review_service.py` — not reviewed in this renewed Prompt 7 pass
- `app/legal/schemas.py` — not reviewed in this renewed Prompt 7 pass
### `app/legal_agents/`

- `app/legal_agents/agent_registry.py` — not reviewed in this renewed Prompt 7 pass
- `app/legal_agents/billing_guard.py` — not reviewed in this renewed Prompt 7 pass
- `app/legal_agents/billing_ops.py` — not reviewed in this renewed Prompt 7 pass
- `app/legal_agents/intake_flow.py` — not reviewed in this renewed Prompt 7 pass
- `app/legal_agents/metrics_insights.py` — not reviewed in this renewed Prompt 7 pass
- `app/legal_agents/virtual_paralegal.py` — not reviewed in this renewed Prompt 7 pass
### `app/mcp/`

- `app/mcp/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/mcp/client.py` — not reviewed in this renewed Prompt 7 pass
- `app/mcp/routes.py` — not reviewed in this renewed Prompt 7 pass
### `app/notifications/`

- `app/notifications/dead_letter.py` — not reviewed in this renewed Prompt 7 pass
- `app/notifications/delivery.py` — not reviewed in this renewed Prompt 7 pass
- `app/notifications/preferences.py` — not reviewed in this renewed Prompt 7 pass
- `app/notifications/repository.py` — not reviewed in this renewed Prompt 7 pass
- `app/notifications/retry.py` — not reviewed in this renewed Prompt 7 pass
### `app/observability/`

- `app/observability/__init__.py` — not reviewed in this renewed Prompt 7 pass
### `app/orchestration/`

- `app/orchestration/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/orchestration/campaign.py` — not reviewed in this renewed Prompt 7 pass
- `app/orchestration/conditions.py` — not reviewed in this renewed Prompt 7 pass
- `app/orchestration/test_campaign.py` — not reviewed in this renewed Prompt 7 pass
- `app/orchestration/test_conditions.py` — not reviewed in this renewed Prompt 7 pass
- `app/orchestration/test_workflow.py` — not reviewed in this renewed Prompt 7 pass
- `app/orchestration/workflow.py` — not reviewed in this renewed Prompt 7 pass
### `app/organization/`

- `app/organization/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/organization/access.py` — not reviewed in this renewed Prompt 7 pass
- `app/organization/events.py` — not reviewed in this renewed Prompt 7 pass
- `app/organization/invitations.py` — not reviewed in this renewed Prompt 7 pass
- `app/organization/membership.py` — not reviewed in this renewed Prompt 7 pass
- `app/organization/membership_policies.py` — not reviewed in this renewed Prompt 7 pass
- `app/organization/membership_service.py` — not reviewed in this renewed Prompt 7 pass
- `app/organization/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/organization/permissions.py` — not reviewed in this renewed Prompt 7 pass
- `app/organization/policies.py` — not reviewed in this renewed Prompt 7 pass
- `app/organization/roles.py` — not reviewed in this renewed Prompt 7 pass
- `app/organization/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/outbox/`

- `app/outbox/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/outbox/dispatcher.py` — not reviewed in this renewed Prompt 7 pass
- `app/outbox/idempotency.py` — not reviewed in this renewed Prompt 7 pass
- `app/outbox/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/outbox/publisher.py` — not reviewed in this renewed Prompt 7 pass
- `app/outbox/retry.py` — not reviewed in this renewed Prompt 7 pass
### `app/providers/`

- `app/providers/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/providers/breaker.py` — not reviewed in this renewed Prompt 7 pass
- `app/providers/contracts.py` — not reviewed in this renewed Prompt 7 pass
- `app/providers/normalize.py` — not reviewed in this renewed Prompt 7 pass
- `app/providers/selection.py` — not reviewed in this renewed Prompt 7 pass
- `app/providers/test_breaker.py` — not reviewed in this renewed Prompt 7 pass
- `app/providers/test_contracts.py` — not reviewed in this renewed Prompt 7 pass
- `app/providers/test_normalize.py` — not reviewed in this renewed Prompt 7 pass
- `app/providers/test_selection.py` — not reviewed in this renewed Prompt 7 pass
### `app/qa/`

- `app/qa/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/auto_review.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/calibration.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/coaching.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/compliance.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/evidence.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/exceptions.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/exports.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/metrics.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/outcomes.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/repository.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/rubrics.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/sampling.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/scoring.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/sentiment.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/service.py` — not reviewed in this renewed Prompt 7 pass
- `app/qa/topics.py` — not reviewed in this renewed Prompt 7 pass
### `app/quotas/`

- `app/quotas/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/quotas/enforcement.py` — not reviewed in this renewed Prompt 7 pass
- `app/quotas/metrics.py` — not reviewed in this renewed Prompt 7 pass
- `app/quotas/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/quotas/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/realtime/`

- `app/realtime/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/realtime/events.py` — not reviewed in this renewed Prompt 7 pass
- `app/realtime/publisher.py` — not reviewed in this renewed Prompt 7 pass
### `app/release/`

- `app/release/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/release/checklist.py` — not reviewed in this renewed Prompt 7 pass
- `app/release/cli.py` — not reviewed in this renewed Prompt 7 pass
- `app/release/evidence.py` — not reviewed in this renewed Prompt 7 pass
- `app/release/facts.py` — not reviewed in this renewed Prompt 7 pass
- `app/release/gate.py` — not reviewed in this renewed Prompt 7 pass
- `app/release/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/release/ops.py` — not reviewed in this renewed Prompt 7 pass
### `app/resources/`

- `app/resources/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/resources/access.py` — not reviewed in this renewed Prompt 7 pass
- `app/resources/exceptions.py` — not reviewed in this renewed Prompt 7 pass
- `app/resources/lifecycle.py` — not reviewed in this renewed Prompt 7 pass
- `app/resources/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/resources/registry.py` — not reviewed in this renewed Prompt 7 pass
- `app/resources/repository.py` — not reviewed in this renewed Prompt 7 pass
- `app/resources/serialization.py` — not reviewed in this renewed Prompt 7 pass
- `app/resources/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/review/`

- `app/review/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/review/dependencies.py` — not reviewed in this renewed Prompt 7 pass
- `app/review/enums.py` — not reviewed in this renewed Prompt 7 pass
- `app/review/models.py` — not reviewed in this renewed Prompt 7 pass
- `app/review/repository.py` — not reviewed in this renewed Prompt 7 pass
- `app/review/routes.py` — not reviewed in this renewed Prompt 7 pass
- `app/review/schemas.py` — not reviewed in this renewed Prompt 7 pass
- `app/review/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/roi/`

- `app/roi/cost_engine.py` — not reviewed in this renewed Prompt 7 pass
- `app/roi/kpi_engine.py` — not reviewed in this renewed Prompt 7 pass
- `app/roi/schemas.py` — not reviewed in this renewed Prompt 7 pass
- `app/roi/service.py` — not reviewed in this renewed Prompt 7 pass
### `app/security/`

- `app/security/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/security/approvals.py` — not reviewed in this renewed Prompt 7 pass
- `app/security/privacy.py` — not reviewed in this renewed Prompt 7 pass
- `app/security/secret_store.py` — not reviewed in this renewed Prompt 7 pass
### `app/services/`

- `app/services/agent_service.py` — not reviewed in this renewed Prompt 7 pass
- `app/services/analytics_service.py` — not reviewed in this renewed Prompt 7 pass
- `app/services/automation_service.py` — not reviewed in this renewed Prompt 7 pass
- `app/services/campaign_service.py` — not reviewed in this renewed Prompt 7 pass
- `app/services/conversation_service.py` — not reviewed in this renewed Prompt 7 pass
- `app/services/enterprise_store.py` — not reviewed in this renewed Prompt 7 pass
- `app/services/inbox_service.py` — not reviewed in this renewed Prompt 7 pass
- `app/services/notification_service.py` — not reviewed in this renewed Prompt 7 pass
- `app/services/workflow_service.py` — not reviewed in this renewed Prompt 7 pass
### `app/specialized_agents/`

- `app/specialized_agents/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/specialized_agents/context.py` — not reviewed in this renewed Prompt 7 pass
- `app/specialized_agents/enums.py` — not reviewed in this renewed Prompt 7 pass
- `app/specialized_agents/evidence.py` — not reviewed in this renewed Prompt 7 pass
- `app/specialized_agents/exceptions.py` — not reviewed in this renewed Prompt 7 pass
- `app/specialized_agents/executor.py` — not reviewed in this renewed Prompt 7 pass
- `app/specialized_agents/persistence.py` — not reviewed in this renewed Prompt 7 pass
- `app/specialized_agents/registry.py` — not reviewed in this renewed Prompt 7 pass
- `app/specialized_agents/review_bridge.py` — not reviewed in this renewed Prompt 7 pass
- `app/specialized_agents/schemas.py` — not reviewed in this renewed Prompt 7 pass
- `app/specialized_agents/service.py` — not reviewed in this renewed Prompt 7 pass
- `app/specialized_agents/sources.py` — not reviewed in this renewed Prompt 7 pass
### `app/telephony/`

- `app/telephony/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/call_events.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/call_state.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/callback_dlq.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/callback_reconciliation.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/capabilities.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/consent.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/e2e_guard.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/ivr.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/media_storage.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/number_provisioning.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/outbound.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/phone.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/provider.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/provider_errors.py` — not reviewed in this renewed Prompt 7 pass
### `app/telephony/providers/`

- `app/telephony/providers/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/providers/base.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/providers/factory.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/providers/telnyx.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/providers/twilio.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/providers/vonage.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/providers/webhook_verifier.py` — not reviewed in this renewed Prompt 7 pass
### `app/telephony/`

- `app/telephony/qos.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/recording.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/recording_policy.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/replay.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/stream_auth.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/transcription.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/transfer.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/transfer_service.py` — not reviewed in this renewed Prompt 7 pass
- `app/telephony/twilio_handler.py` — not reviewed in this renewed Prompt 7 pass
### `app/tenancy/`

- `app/tenancy/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/access.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/context.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/data_residency.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/environment.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/exceptions.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/feature_flags.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/invitations.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/isolation.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/lifecycle.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/limits.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/membership.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/membership_service.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/permissions.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/policy.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/quota.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/regions.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/repository.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/roles.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/service.py` — not reviewed in this renewed Prompt 7 pass
- `app/tenancy/settings.py` — not reviewed in this renewed Prompt 7 pass
### `app/translation/`

- `app/translation/__init__.py` — not reviewed in this renewed Prompt 7 pass
- `app/translation/engine.py` — not reviewed in this renewed Prompt 7 pass
- `app/translation/glossary.py` — not reviewed in this renewed Prompt 7 pass
- `app/translation/job_service.py` — not reviewed in this renewed Prompt 7 pass
- `app/translation/persistence.py` — not reviewed in this renewed Prompt 7 pass
- `app/translation/quality.py` — not reviewed in this renewed Prompt 7 pass
- `app/translation/schemas.py` — not reviewed in this renewed Prompt 7 pass
### `app/tts/`

- `app/tts/__init__.py` — not reviewed in this renewed Prompt 7 pass
### `app/webhooks/`

- `app/webhooks/delivery.py` — not reviewed in this renewed Prompt 7 pass
- `app/webhooks/replay.py` — not reviewed in this renewed Prompt 7 pass
- `app/webhooks/repository.py` — not reviewed in this renewed Prompt 7 pass
- `app/webhooks/retry.py` — not reviewed in this renewed Prompt 7 pass
- `app/webhooks/signing.py` — not reviewed in this renewed Prompt 7 pass

## Non-target artifacts

- `/home/user/Prompt7_Backend_Files.zip` is regenerated from exactly the 30 mandatory target paths.
- The full backend-module inventory is this document; all files outside the required target set remain unreviewed in this renewal unless separately recorded in prior review notes.

## Continuation status as of 2026-10-05

- The exact target accounting above remains **30 present / 30 reread / 0 skipped**. It is a backend/runtime subreview, not a claim that the remaining 666 `app/` modules or all deployment environments were audited.
- The full `tests/test_backend_runtime_closure.py` file now passes **9/9** after removing provider SDK imports from the health/inventory `capability_matrix()`. Installed/configured state is reported separately; API capability is `null` when it has not been checked, and reachability/authentication remain `not_checked`. The earlier combined-file timeout is superseded by this later full-file run.
- The two newly identified 33-character Alembic IDs are now canonicalized to `0038_agent_chat_conductor` and `0045_request_idem_receipts` (both fit the default `VARCHAR(32)`). The compatibility helper also maps ten previously shortened overlength IDs (0017, 0018, 0019, 0020, 0024, 0031, 0032, 0034, 0035 and 0036) to their current canonical IDs. It fails closed on an ambiguous old+new state. All alias mappings have SQLite/source-graph tests; no live PostgreSQL migration has been applied.
- Migrations 0038–0040 now support offline SQL rendering by assuming the target schema is absent for `upgrade --sql` and present for `downgrade --sql`. Targeted offline SQL generation succeeded for 0038→0039, its reverse downgrade, 0044→head, and the current downstream edge `0036_durable_call_outcomes:head` (1,616 lines, including deployment-observation RLS and append-only SQL). These are generated SQL artifacts, not a database migration run; the full historical base-to-head SQL path remains blocked by data-dependent migration 0017.
- The broader Prompt 7 dashboard integration and its test/build evidence are recorded in `docs/PROMPT7_SECURITY_HARDENING_REPORT.md`; they are intentionally outside this backend-only 30-target inventory.
