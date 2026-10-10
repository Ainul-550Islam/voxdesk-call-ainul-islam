# Business Associate Agreement (BAA) — Template for Legal Counsel Review

> **IMPORTANT DISCLAIMER — NOT LEGAL ADVICE & NO CERTIFICATION CLAIMED**
> This document is a draft contractual template prepared for review and adaptation by qualified legal counsel. It does **not** constitute legal advice, nor does its presence in the repository indicate that VoxDesk holds an issued HIPAA, HITRUST, SOC 2, or ISO 27001 certification. A Business Associate Agreement is effective only when countersigned by authorized representatives of both the Covered Entity (or upstream Business Associate) and the VoxDesk operator after verifying the tenant's **HIPAA-ready configuration** (see `docs/COMPLIANCE/HIPAA_READINESS.md`) and executed upstream subprocessor BAAs (see `docs/COMPLIANCE/SUBPROCESSORS.md`).

---

## 1. Parties and Effective Date

This Business Associate Agreement ("**Agreement**" or "**BAA**") is entered into as of `[EFFECTIVE_DATE]` ("**Effective Date**") by and between:

- **Covered Entity / Upstream Business Associate**: `[CUSTOMER_LEGAL_NAME]`, having its principal place of business at `[CUSTOMER_ADDRESS]` ("**Covered Entity**"), and
- **Business Associate**: `[OPERATOR_LEGAL_NAME]`, operating the VoxDesk voice and messaging platform at `[OPERATOR_ADDRESS]` ("**Business Associate**").

This Agreement supplements the Master Services Agreement or Terms of Service ("**Underlying Agreement**") between the Parties.

---

## 2. Definitions

Capitalized terms used but not defined in this Agreement have the meanings assigned by the Health Insurance Portability and Accountability Act of 1996, the HITECH Act, and their implementing regulations at 45 CFR Parts 160 and 164 (collectively, the "**HIPAA Rules**"), including **Protected Health Information** ("**PHI**") and **Electronic Protected Health Information** ("**ePHI**").

---

## 3. Permitted Uses and Disclosures of PHI

3.1 **Performance of Services.** Business Associate may create, receive, maintain, or transmit ePHI solely as necessary to provide the voice, messaging, transcription, and workflow services configured by Covered Entity under the Underlying Agreement, or as Required by Law.

3.2 **Minimum Necessary.** Business Associate shall request, use, and disclose only the minimum amount of PHI necessary to accomplish the intended purpose of the use, disclosure, or request, consistent with 45 CFR § 164.502(b).

3.3 **No AI Model Training on PHI.** Business Associate shall not use Covered Entity's ePHI (including call audio, transcripts, or extracted fields) to train, fine-tune, or improve foundation models, and shall configure upstream AI/speech subprocessors in zero-data-retention mode where covered by an executed upstream BAA.

---

## 4. Safeguards and Technical Controls (HIPAA Security Rule — 45 CFR Part 164 Subpart C)

Business Associate shall implement administrative, physical, and technical safeguards that reasonably and appropriately protect the confidentiality, integrity, and availability of ePHI, including the following technical controls enforced in the VoxDesk platform:

1. **Access Control (45 CFR § 164.312(a)(1))**:
   - Tenant-scoped authorization on every API and WebSocket route (`app/auth/dependencies.py`, verified by `tests/security/test_tenant_isolation_matrix.py`).
   - Role-Based Access Control (`app/auth/permissions.py`) and enforced Multi-Factor Authentication (`app/auth/identity/mfa.py`) or enterprise OIDC/SAML SSO (`app/auth/identity/sso/`).
2. **Audit Controls (45 CFR § 164.312(b))**:
   - Transactional, tamper-evident audit logging (`AuditLog` in `app/db/models.py` and `app/audit/service.py`) recording access to recordings, transcripts, exports, and security policy changes.
3. **Integrity and Encryption at Rest (45 CFR § 164.312(a)(2)(iv), § 164.312(c)(1))**:
   - AES-256-GCM authenticated encryption (`app/integrations/crm/crypto.py`, `app/security/secret_store.py`) with tenant-bound Additional Authenticated Data (AAD) and optional external KMS key wrapping (`app/security/kms/`).
4. **Transmission Security (45 CFR § 164.312(e)(1))**:
   - TLS 1.2+ with HSTS on all HTTP/WebSocket ingress and SSRF-validated HTTPS on all outbound webhook, CRM, and tool requests (`app/core/ssrf.py`).
5. **PII/PHI Redaction and Retention Enforcement**:
   - Configurable synchronous transcript/webhook PII scrubbing (`app/gdpr/redact.py`) and automated retention purge (`app/core/retention.py`, `RecordingPolicy.retention_days`).

---

## 5. Subcontractors and Subprocessors (45 CFR § 164.308(b)(1))

Business Associate shall ensure that any subcontractor or subprocessor (including telephony carriers, STT/TTS providers, and LLM providers listed in `docs/COMPLIANCE/SUBPROCESSORS.md`) that creates, receives, maintains, or transmits ePHI on behalf of Business Associate enters into a written Business Associate Agreement imposing restrictions and conditions no less stringent than those in this Agreement prior to routing Covered Entity's traffic through that subprocessor.

---

## 6. Reporting of Security Incidents and Breaches (45 CFR § 164.410)

6.1 **Notification.** Business Associate shall notify Covered Entity without unreasonable delay, and in no event later than **`[BREACH_NOTIFICATION_WINDOW_HOURS, e.g., 48 or 72]` hours** after discovering a Breach of Unsecured PHI or any Security Incident that compromises ePHI.

6.2 **Notice Contents.** Notice shall include, to the extent known: (a) the identification of each individual whose Unsecured PHI has been or is reasonably believed to have been accessed, acquired, used, or disclosed; (b) a description of what happened, including dates; (c) the types of PHI involved (`call_audio`, `transcript`, `phone_number`, `extracted_fields`); and (d) the mitigation and corrective steps taken (`docs/INCIDENT-RESPONSE.md`).

---

## 7. Individual Rights (45 CFR §§ 164.524, 164.526, 164.528)

Business Associate shall assist Covered Entity in fulfilling individual requests for access, export, amendment, or accounting of disclosures via the tenant-scoped data-subject endpoints (`app/api/gdpr_routes.py` and `app/api/call_search_export_routes.py`) within `[DAYS, e.g., 10]` business days of Covered Entity's request.

---

## 8. Term, Termination, and Return or Destruction of PHI

8.1 **Term.** This Agreement remains in effect until all ePHI held by Business Associate is returned or destroyed in accordance with Section 8.2.

8.2 **Return or Destruction.** Upon termination of the Underlying Agreement, Business Associate shall purge all ePHI (including `CallRecording`, `Turn`, `Call`, and post-call analysis rows) using the platform's cryptographic erasure and retention purge routines (`app/core/retention.py` and `app/telephony/recording.py::purge_for_calls`), except where retention is Required by Law or subject to an active `legal_hold`.

---

## 9. Shared Responsibility Obligations of Covered Entity

Covered Entity acknowledges and agrees that HIPAA readiness requires Covered Entity to:
1. Enable `redact_pii = True` and set an appropriate `retention_days` window (or disable call recording) in `RecordingPolicy`.
2. Require MFA (`mfa_required = True`) or enterprise SSO for all tenant users.
3. Configure only subprocessors and models covered by an active BAA for healthcare workflows.
4. Provide any legally required caller consent and privacy disclosures at the start of inbound or outbound calls (`app/telephony/consent.py`).

---

## Signatures

| Covered Entity: `[CUSTOMER_LEGAL_NAME]` | Business Associate: `[OPERATOR_LEGAL_NAME]` |
|---|---|
| Signature: ___________________________ | Signature: ___________________________ |
| Name: `[NAME]` | Name: `[NAME]` |
| Title: `[TITLE]` | Title: `[TITLE]` |
| Date: `[DATE]` | Date: `[DATE]` |
