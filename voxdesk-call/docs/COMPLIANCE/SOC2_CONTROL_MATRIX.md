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

---

## 3. Evidence columns added in PART 16 (P16-03)

The tables above answer "is there a control". A buyer's questionnaire asks a
different question — *where do I look?* — so every row below names three things:
the **code path**, the **test that proves it**, and the **file that is the
evidence**. A row with an empty test column is a promise, and it says so.

| Control | Framework refs | Code path | Test (id) | Evidence file | Status |
|---|---|---|---|---|---|
| Tenant-defined roles can never escalate | CC6.3, ISO A.9.2.3, HIPAA §164.312(a)(1) | `app/auth/custom_roles.py` (`assert_grantable`), `app/api/role_routes.py` | `tests/auth/test_custom_roles.py::test_an_actor_cannot_bundle_a_permission_it_does_not_hold`, `::test_platform_permissions_can_never_be_bundled`, `::test_an_admin_cannot_write_a_role_above_itself` | `reports/PART_16_REPORT.md` §P16-01 | Implemented & Tested |
| Custom roles are additive; denial stays in one place | CC6.3 | `app/auth/rbac.py` (`effective_permissions`), `app/auth/dependencies.py` (`TenantContext.can`) | `tests/auth/test_custom_roles.py::test_the_union_is_additive_and_deny_by_default`, `::test_a_machine_scope_still_narrows_a_custom_role` | `reports/PART_16_REPORT.md` | Implemented & Tested |
| Cross-tenant role or grant is a 404, never a 403 | CC6.3, ISO A.9.4.1 | `app/api/role_routes.py` (`_load_role`, `_check_subject_exists`) | `tests/auth/test_custom_roles.py::test_another_tenants_role_is_a_404_not_a_403`, `::test_an_assignment_cannot_name_a_subject_from_another_tenant` | `reports/PART_16_REPORT.md` | Implemented & Tested |
| Agency portfolios do not imply access to client data | CC6.1, CC6.2 | `app/organization/agency_service.py`, `app/db/agency_models.py` | `tests/organization/test_agency_flows.py::test_switching_into_a_client_outside_the_portfolio_is_a_404`, `::test_another_agencys_client_never_appears_in_the_rollup` | `reports/PART_16_REPORT.md` §P16-02 | Implemented & Tested |
| Cross-account agent copy strips secrets | CC6.7, C1.1 | `app/organization/agency_service.py` (`copy_agent`), `app/services/agent_portability_service.py` | `tests/organization/test_agency_flows.py::test_an_agent_copy_between_clients_strips_the_secret`, `::test_a_real_copy_without_the_secret_is_refused` | `reports/PART_16_REPORT.md` | Implemented & Tested |
| Audit export is signed, bounded and leak-free | CC7.1, CC7.3, A1.2 | `app/audit/siem_export.py` | `tests/audit/test_siem_export.py` (18 tests; signature, keyset cursor, redaction, tenant isolation) | `reports/dast/LAST_RUN.txt`, this file §3 | Implemented & Tested |
| Region pinning refuses a foreign write for recordings, transcripts and backups | CC6.6, C1.1, GDPR Art. 44–46, HIPAA §164.312(e)(1) | `app/tenancy/regions.py` (`require_write_region`), `app/tenancy/data_residency.py` (`enforce_artifact_write`, `enforce_tenant_write`), `app/telephony/media_storage.py`, `app/telephony/transcription.py`, `scripts/backup_target_check.py`, `scripts/backup.sh` | `tests/tenancy/test_region_pinning.py` (16 tests) | `docs/COMPLIANCE/DATA_FLOW.md`, `reports/PART_16_REPORT.md` §P16-07 | Implemented & Tested (no region configured — every pin is refused, honestly) |
| Running-surface vulnerability scanning | CC7.1, ISO A.12.6.1 | `scripts/run_zap_baseline.sh`, `.github/workflows/security-dast.yml` | `reports/dast/zap-baseline-*.json` from a real run | `reports/dast/LAST_RUN.txt` | Implemented; findings ticketed (see `reports/dast/README.md`) |
| Frontend hardening: CSP, frame-ancestors, referrer, HSTS | CC6.6, ISO A.13.1.1 | `dashboard-next/next.config.mjs` | `docs/SECURITY_FRONTEND_REVIEW.md` (manual review + rationale) | `docs/SECURITY_FRONTEND_REVIEW.md` | Implemented; nonce-based `script-src` is PART 17A |

---

## 4. HIPAA readiness mapping (technical safeguards only)

**Certification status: PLANNED. No BAA exists between VoxDesk and any covered
entity, and no accredited assessment has been performed.** What the code does is
listed; what it does not do is stated just as plainly.

| HIPAA reference | Requirement | Where the code addresses it | Test | Gap |
|---|---|---|---|---|
| §164.312(a)(1) | Access control | RBAC + custom roles + tenant scoping (`app/auth/`) | `tests/security/test_authorization_matrix.py`, `tests/auth/test_custom_roles.py` | — |
| §164.312(a)(2)(i) | Unique user identification | `users.email` unique per tenant; machine principals are `service_accounts` with their own credentials | `tests/auth/test_service_accounts.py` | — |
| §164.312(a)(2)(iii) | Automatic logoff | Short-lived JWT + refresh cookie rotation (`app/auth/identity/sessions.py`) | `tests/security/test_session_invalidation.py` | — |
| §164.312(a)(2)(iv) | Encryption and decryption | AES-256-GCM secret store, optional KMS envelope (`app/security/secret_store.py`) | `tests/security/test_mfa_secrets.py` | Recording-at-rest encryption is the operator's storage layer; see `docs/DUE_DILIGENCE/CMK_AND_KEYS.md` |
| §164.312(b) | Audit controls | `app/audit/service.py`, immutable `EvidenceEvent`, SIEM export | `tests/security/test_audit_durability.py`, `tests/audit/test_siem_export.py` | — |
| §164.312(c)(1) | Integrity | Digest-pinned evidence, HMAC-signed exports | `tests/audit/test_siem_export.py::test_a_tampered_body_fails_and_a_truncated_body_fails` | — |
| §164.312(e)(1) | Transmission security | TLS terminated by the operator's proxy; signed webhooks; SSRF guard | `tests/security/test_ssrf_all_outbound.py` | Operator must configure TLS — asserted in `app/deployment/validator.py` |
| §164.316(b)(2) | 6-year retention of documentation | RetentionPolicy + legal hold | `tests/compliance/test_retention_enforcement.py` | Organizational policy document is out of scope of this repository |
| §164.308(a)(7) | Contingency plan | `scripts/backup.sh`, `scripts/backup_verify.sh`, `scripts/restore.sh`, region gate | `tests/tenancy/test_region_pinning.py::test_the_backup_gate_refuses_a_pinned_tenant` | Restore drills are operational evidence, not code |

## 5. GDPR mapping (data-subject rights)

| Article | Requirement | Where | Test |
|---|---|---|---|
| Art. 15 / 20 | Access and portability | `app/api/gdpr_routes.py` (export), `app/audit/siem_export.py` (audit portability) | `tests/compliance/test_pii_redaction_pipeline.py`, `tests/audit/test_siem_export.py` |
| Art. 17 | Erasure | Tenant/user deletion paths; recordings purge under `RetentionPolicy`; `legal_hold` blocks purge deliberately | `tests/compliance/test_retention_enforcement.py` |
| Art. 30 | Records of processing | `docs/COMPLIANCE/DATA_FLOW.md`, subprocessor matrix | Documentation, not code |
| Art. 32 | Security of processing | RBAC, encryption, audit, DAST | `tests/security/`, `reports/dast/` |
| Art. 44–46 | Transfers outside the EEA | Region pinning + refusal of unproven placement | `tests/tenancy/test_region_pinning.py` — and note: **no region is configured, so a pinned tenant is refused rather than served** |
| Art. 33 | Breach notification (72 h) | `docs/COMPLIANCE/INCIDENT_RESPONSE.md` | Documentation |

## 6. ISO/IEC 27001:2022 Annex A mapping (selected)

| Control | Where | Test / evidence |
|---|---|---|
| A.5.15 Access control | `app/auth/`, `app/organization/` | `tests/security/test_authorization_matrix.py` |
| A.5.17 Authentication information | `app/security/secret_store.py`, `scripts/rotate_secrets.py` | `tests/truth/test_no_default_secrets.py` |
| A.8.2 Privileged access rights | `app/auth/rbac.py` (owner/admin/manager/agent/viewer), platform permissions separated | `tests/auth/test_custom_roles.py::test_platform_permissions_can_never_be_bundled` |
| A.8.9 Configuration management | `app/core/config.py`, `app/deployment/validator.py` (fails closed on weak config) | `tests/truth/test_no_default_secrets.py` |
| A.8.12 Data leakage prevention | Audit redaction + SIEM export allowlist + transcript PII redaction | `tests/audit/test_siem_export.py::test_a_secret_in_detail_never_leaves` |
| A.8.15 Logging | `app/audit/service.py`, evidence chain | `tests/security/test_audit_durability.py` |
| A.8.16 Monitoring | Alerts (PART 15) + `/health` probes + status page | `tests/alerts/test_alert_rules.py` |
| A.8.25 Secure development | `tests/truth/`, `scripts/verify_docs_paths.py`, DAST workflow | `.github/workflows/security-scan.yml`, `.github/workflows/security-dast.yml` |
| A.8.28 Secure coding | Bandit + `ruff` gates; no `eval`, no shell string interpolation of user input | `tests/truth/test_no_dangerous_patterns.py` (see also `security-scan.yml`) |

---

## 7. Honest status summary

| Claim | State |
|---|---|
| SOC 2 Type I / Type II | **PLANNED** — controls implemented and tested; no audit, no report |
| HIPAA | **PLANNED** — technical safeguards implemented; no BAA, no assessment |
| ISO/IEC 27001 | **PLANNED** — Annex A mappings above; no certification body engagement |
| GDPR | Implemented technical measures; DPA template in `docs/COMPLIANCE/BAA_TEMPLATE.md`, DPIA is per-customer |
| Penetration test | **NOT PERFORMED** by a third party; scope and template in `docs/COMPLIANCE/PENTEST_SCOPE_AND_TEMPLATE.md`; automated baseline in `reports/dast/` |
| Subprocessors | Code-level list in `docs/COMPLIANCE/SUBPROCESSORS.md`; vendor SOC 2 letter collection is operational |
