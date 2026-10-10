# VoxDesk SOC 2 Control Matrix (Control → Code → Test → Evidence Mapping)

> **Status Statement — SOC 2-Ready Controls (No Certification Claimed)**
> This matrix extends `docs/SOC2.md` by mapping AICPA Trust Services Criteria (Security, Availability, Processing Integrity, Confidentiality, Privacy) directly to source files, automated pytest suites, and evidence artifacts collected by `scripts/collect_compliance_evidence.py`. VoxDesk implements **SOC 2-ready controls** in code; an issued SOC 2 Type I or Type II report requires an independent CPA firm audit over an observation period.

---

## 1. Trust Services Criteria (TSC) Mapping

| TSC Reference | Control Objective | Code Implementation | Automated Test Suite | Evidence Artifact (`evidence/<date>/`) | Status |
|---|---|---|---|---|---|
| **CC6.1** Logical Access & Authentication | Authenticate human and machine principals with bcrypt, short-lived JWTs, MFA, OIDC/SAML SSO, and scoped API/SCIM keys | `app/auth/`, `app/auth/identity/{mfa,sessions,sso,scim}/` | `tests/auth/test_mfa_verification.py`, `tests/auth/test_mfa_login_e2e.py`, `tests/security/test_sso_oidc_keycloak.py`, `tests/security/test_sso_saml_keycloak.py`, `tests/security/test_scim_conformance.py` | `pytest-security-report.json` | Implemented & Tested |
| **CC6.2** Access Provisioning & Deprovisioning | Automated SCIM 2.0 user/group lifecycle, immediate session revocation on deactivation or credential rotation | `app/auth/identity/scim/service.py`, `app/auth/identity/sessions.py` | `tests/security/test_scim_conformance.py`, `tests/security/test_session_invalidation.py` | `pytest-security-report.json` | Implemented & Tested |
| **CC6.3** Role-Based Access & Tenant Isolation | Enforce RBAC permissions and strict tenant scoping (`ctx.tenant_id`) on every query; return 404 on cross-tenant access | `app/auth/dependencies.py`, `app/auth/permissions.py`, `app/tenancy/isolation.py` | `tests/security/test_tenant_isolation_matrix.py`, `tests/security/test_authorization_matrix.py` | `pytest-security-report.json` | Implemented & Tested |
| **CC6.6** Network & Boundary Protection (SSRF & Rate Limiting) | Block private/loopback/metadata outbound URLs (`follow_redirects=False`) and enforce distributed Redis rate limits across workers | `app/core/ssrf.py`, `app/core/rate_limit.py`, `app/core/security_headers.py` | `tests/security/test_ssrf_all_outbound.py`, `tests/security/test_rate_limit_matrix.py` | `pytest-security-report.json` | Implemented & Tested |
| **CC6.7** Encryption at Rest & Key Management | Seal all CRM, SSO, MFA, webhook, and tool secrets with AES-256-GCM + tenant AAD and optional KMS envelope wrapping; support key rotation | `app/integrations/crm/crypto.py`, `app/security/secret_store.py`, `app/security/kms/aws_kms.py`, `scripts/rotate_secrets.py` | `tests/security/test_mfa_secrets.py`, `tests/truth/test_no_default_secrets.py` | `secret-rotation-dry-run.json` | Implemented & Tested |
| **CC7.1 / CC7.2** Vulnerability Management & CI Security Scanning | Continuous SAST (Bandit), secret scanning (Gitleaks), dependency audits (`pip-audit`, `npm audit`, `cargo audit`, `govulncheck`), and Trivy container scans | `.github/workflows/security-scan.yml`, `scripts/audit_dependencies.sh` | `tests/truth/` | `dependency-audit.txt`, `sbom-python.json` | Implemented & Tested |
| **CC7.3** Security Event & Audit Logging | Durable, transactional `AuditLog` entries for auth, MFA, RBAC denials, DNC, live monitoring, and secret rotation; PII redacted in logs | `app/audit/service.py`, `app/audit/redaction.py`, `app/auth/identity/events.py` | `tests/security/test_audit_durability.py`, `tests/security/test_audit_redaction.py` | `pytest-security-report.json` | Implemented & Tested |
| **A1.1 / A1.2** Availability, Health Probes & Backup/Restore | Liveness/readiness probes, durable Postgres job/outbox queues, automated backup & verification scripts | `app/core/health.py`, `app/jobs/`, `app/outbox/`, `scripts/backup.sh`, `scripts/backup_verify.sh`, `scripts/restore.sh` | `tests/security/test_no_process_local_state.py` | `backup- drill-status.json` | Implemented & Tested |
| **PI1.1 / PI1.4** Processing Integrity & Webhook/Carrier Authenticity | Idempotent webhook/callback receipts, HMAC-SHA256 outbound signing, provider-sourced Number Trust (`NumberTrustProfile`) | `app/webhooks/delivery.py`, `app/resilience/idempotency.py`, `app/telephony/number_trust.py` | `tests/telephony/test_number_trust.py`, `tests/messaging/test_sms_flow.py` | `pytest-security-report.json` | Implemented & Tested |
| **C1.1 / C1.2** Confidentiality & Retention Disposal | Configurable `RetentionPolicy` and `RecordingPolicy` with automated purge and `legal_hold` enforcement | `app/core/retention.py`, `app/telephony/recording.py`, `app/api/retention_routes.py` | `tests/compliance/test_retention_enforcement.py` | `pytest-security-report.json` | Implemented & Tested |
| **P1.1 / P3.1 / P4.2** Privacy, Recording Consent & PII Redaction | Per-call `RecordingConsent` enforcement prior to recording; synchronous PII redaction in transcripts, LLM prompts, and webhooks | `app/telephony/consent.py`, `app/gdpr/redact.py`, `app/api/gdpr_routes.py` | `tests/compliance/test_recording_consent_flow.py`, `tests/compliance/test_pii_redaction_pipeline.py` | `pytest-security-report.json` | Implemented & Tested |

---

## 2. Residual Operational Gaps (Non-Code Evidence Required for External Audit)

An external SOC 2 auditor will require the following organizational artifacts in addition to the repository evidence bundle generated by `scripts/collect_compliance_evidence.py`:

1. **Observation Period Evidence (Type II)**: 3 to 12 months of continuous production change-management logs, access reviews, and incident-response tickets.
2. **Human Resources & Governance Controls (CC1 / CC2)**: Employee background checks, security awareness training completion records, and signed acceptable-use policies.
3. **Independent Third-Party Penetration Test**: Annual external penetration test report against a staging/production deployment (guided by `docs/PENTEST-CHECKLIST.md`).
4. **Vendor SOC 2 / Bridge Letters (CC9.2)**: Annual collection of SOC 2 Type II reports from subprocessors listed in `docs/COMPLIANCE/SUBPROCESSORS.md`.
