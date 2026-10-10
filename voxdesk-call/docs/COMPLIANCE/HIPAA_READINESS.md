# VoxDesk HIPAA-Ready Configuration Guide

> **NO CERTIFICATION OR ATTESTATION CLAIMED**
> VoxDesk is **not** "HIPAA certified" (no such government certification exists), nor does this repository claim an issued third-party HIPAA or HITRUST attestation report. This document specifies the **HIPAA-ready configuration** controls implemented in the codebase and the operational steps an organization and its legal/compliance team must complete before processing Protected Health Information (ePHI).

---

## 1. Technical Safeguards Checklist (Code-Backed Controls)

Every item below maps to an enforced control in the VoxDesk codebase and an automated verification test:

| HIPAA Security Rule Reference | Control Requirement | VoxDesk Implementation | Verification Test / Script |
|---|---|---|---|
| **45 CFR § 164.312(a)(1)** Access Control | Unique user identification, role-based access, and strict tenant isolation | Every API/WS route resolves `TenantContext` from signed JWT / session / scoped credential; cross-tenant object IDs return `404` | `tests/security/test_tenant_isolation_matrix.py`, `tests/security/test_authorization_matrix.py` |
| **45 CFR § 164.312(d)** Person or Entity Authentication | Multi-Factor Authentication (TOTP + single-use recovery codes) and OIDC/SAML 2.0 Enterprise SSO | `app/auth/identity/mfa.py`, `app/auth/identity/sso/oidc.py`, `app/auth/identity/sso/saml.py` | `tests/auth/test_mfa_verification.py`, `tests/auth/test_mfa_login_e2e.py`, `tests/security/test_sso_oidc_keycloak.py`, `tests/security/test_sso_saml_keycloak.py` |
| **45 CFR § 164.312(a)(2)(iii)** Automatic Logoff | Session idle timeout and absolute session lifetime enforcement | `IdentityPolicy` (`session_idle_timeout_minutes`, `session_max_age_minutes`) enforced in `app/auth/identity/sessions.py` | `tests/auth/test_sessions.py`, `tests/security/test_session_invalidation.py` |
| **45 CFR § 164.312(a)(2)(iv)** Encryption and Decryption | AES-256-GCM encryption at rest for secrets/credentials with tenant-bound AAD and optional KMS key wrapping | `app/integrations/crm/crypto.py`, `app/security/secret_store.py`, `app/security/kms/aws_kms.py` | `tests/security/test_mfa_secrets.py`, `scripts/rotate_secrets.py` |
| **45 CFR § 164.312(b)** Audit Controls | Transactional audit logging of authentication, MFA, recording access, DNC, and policy changes | `app/db/models.py` (`AuditLog`), `app/audit/service.py`; audit failure aborts the mutating DB transaction | `tests/security/test_audit_durability.py`, `tests/security/test_audit_redaction.py` |
| **45 CFR § 164.312(c)(1)** Integrity & PII/PHI Scrubbing | Synchronous redaction of SSN, credit card, phone, email, DOB, and address before persistence and webhook egress | `app/gdpr/redact.py`, `app/ai/guardrails/pii.py`, `RecordingPolicy.redact_pii`; disabling requires `Permission.SECURITY_WRITE` and logs `AuditAction.PII_REDACTION_DISABLED` | `tests/compliance/test_pii_redaction_pipeline.py`, `tests/services/test_regression_from_calls.py` |
| **45 CFR § 164.312(e)(1)** Transmission Security | TLS 1.2+ with HSTS on ingress; SSRF guard requiring HTTPS on outbound webhooks, CRM, and HTTP tools | `app/core/security_headers.py`, `app/core/ssrf.py`, `scripts/verify_tls.py` | `tests/security/test_ssrf_all_outbound.py` |
| **45 CFR § 164.530(j)** Retention & Disposal | Automated tenant/agent retention policy enforcement and recording purge | `app/core/retention.py`, `app/telephony/recording.py::purge_for_calls`, `app/api/retention_routes.py` | `tests/compliance/test_retention_enforcement.py` |
| **State & Federal Call Recording Consent** | Recording blocked unless `RecordingPolicy.enabled=True` and `RecordingConsent` decision allows capture | `app/telephony/consent.py`, `app/telephony/recording.py::request_recording` | `tests/compliance/test_recording_consent_flow.py` |

---

## 2. Required Tenant & Deployment Configuration for Regulated Workloads

Before routing any healthcare or ePHI workload through a VoxDesk deployment, operators must verify all of the following settings:

1. **Enforce Multi-Factor Authentication or Enterprise SSO**:
   - Set `mfa_required = true` on the tenant's `IdentityPolicy` (`PATCH /api/identity/policy`) or require OIDC/SAML SSO with IdP-enforced MFA.
2. **Configure Recording & Redaction Policy (`RecordingPolicy`)**:
   - Enable synchronous PII redaction (`redact_pii = true`, the default).
   - Either disable call audio recording (`enabled = false`) for zero-audio-retention workflows or set `consent_mode = "all_party"` / `"one_party"` with explicit caller disclosure and a bounded `retention_days` window.
3. **Configure Data Retention Policy (`RetentionPolicy`)**:
   - Create an explicit tenant or per-agent retention policy (`POST /api/retention/policies`) and run the scheduled purge worker (`scripts/scheduler.py`).
4. **Configure Encryption Key Ring & KMS**:
   - Set `CRM_ENCRYPTION_KEYS` and `IDENTITY_ENCRYPTION_KEYS` (32-byte AES-256 keys) and optionally configure `AWS_KMS_KEY_ID` or `VAULT_TRANSIT_KEY` for envelope DEK wrapping (`app/security/kms/`).
5. **Execute Subprocessor BAAs**:
   - Verify signed Business Associate Agreements with the underlying cloud host, telephony carrier (e.g., Twilio Enterprise / Telnyx), STT provider (e.g., Deepgram BAA tier), TTS provider, and LLM vendor (e.g., OpenAI Zero-Data-Retention BAA / Azure OpenAI / AWS Bedrock BAA) per `docs/COMPLIANCE/SUBPROCESSORS.md`.
6. **Execute Customer BAA**:
   - Have legal counsel review and countersign the Business Associate Agreement (`docs/COMPLIANCE/BAA_TEMPLATE.md`).
