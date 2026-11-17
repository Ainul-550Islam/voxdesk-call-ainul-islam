# CAIQ-style security questionnaire, answered against this repository

Every answer below points at a file you can open or a test you can run. Answers
that would be a promise are marked **Not yet** — a questionnaire is only useful
if it can be wrong.

## A. Governance and policy

| # | Question | Answer |
|---|---|---|
| A.1 | Do you maintain a documented security program? | Partially. Code-level controls and their evidence are in `docs/COMPLIANCE/SOC2_CONTROL_MATRIX.md`. Written policies owned by a named officer: **Not yet**. |
| A.2 | Is there a named security owner? | **Not yet** (single-maintainer project at this stage). |
| A.3 | Do you perform background checks on staff? | **Not yet** — no employees yet; applies on first hire. |

## B. Access control

| # | Question | Answer |
|---|---|---|
| B.1 | Role-based access control? | Yes — five built-in roles (`app/auth/rbac.py`) plus tenant-defined roles (`app/auth/custom_roles.py`). Custom roles are **additive only**; escalation is refused server-side. |
| B.2 | MFA available and enforceable? | Yes — TOTP (`app/auth/identity/mfa/`), `tests/auth/test_mfa_login_e2e.py`. |
| B.3 | SSO (SAML/OIDC) and SCIM provisioning? | Yes — `app/auth/identity/sso/`, `app/auth/identity/scim/`; conformance tests in `tests/security/`. |
| B.4 | Are access reviews performed? | Tooling exists (`/api/auth/effective-permissions`, `/api/auth/custom-roles/{id}/assignments`); the periodic review is an operational process, **not yet scheduled**. |
| B.5 | Least privilege for machine credentials? | Yes — service accounts with explicit scopes; a credential's scopes can only narrow, never widen (`app/auth/dependencies.py`). |

## C. Data protection

| # | Question | Answer |
|---|---|---|
| C.1 | Encrypted in transit? | The app assumes TLS termination by the operator's proxy and validates it at startup (`app/deployment/validator.py`). Plain HTTP is refused in production config. |
| C.2 | Encrypted at rest? | Secrets: AES-256-GCM with tenant AAD, optional KMS envelope (`app/security/secret_store.py`). Recordings/DB volumes: the operator's storage layer — `docs/DUE_DILIGENCE/CMK_AND_KEYS.md`. |
| C.3 | Customer-managed keys? | Supported through the existing KMS adapters (AWS KMS, Vault Transit). Tested steps: `docs/DUE_DILIGENCE/CMK_AND_KEYS.md`. |
| C.4 | Data residency / region pinning? | Enforcement exists and **refuses** a write it cannot place (`app/tenancy/regions.py`, `tests/tenancy/test_region_pinning.py`). No region is configured in this deployment, so a pinned tenant is refused rather than served — that is the honest state. |
| C.5 | Retention and deletion? | `RetentionPolicy` + legal hold; `tests/compliance/test_retention_enforcement.py`. |
| C.6 | PII in logs? | No — audit writer and SIEM export redact by allowlist and by pattern (`app/audit/redaction.py`, `tests/audit/test_siem_export.py`). |

## D. Operations

| # | Question | Answer |
|---|---|---|
| D.1 | Vulnerability scanning? | Dependency + secret + container scans in `.github/workflows/security-scan.yml`; running-surface DAST in `.github/workflows/security-dast.yml` with reports in `reports/dast/`. |
| D.2 | Independent penetration test? | **Not yet** — scope and template ready in `docs/COMPLIANCE/PENTEST_SCOPE_AND_TEMPLATE.md`. |
| D.3 | Monitoring and alerting? | Threshold rules, dedupe/cooldown, delivery (`app/alerts/`, PART 15); public status page generator in `infra/status/`. |
| D.4 | Backup and restore tested? | Scripts exist and are gated by the residency check (`scripts/backup.sh`); a restore drill is operational evidence, **not yet performed on a schedule**. |
| D.5 | Incident response plan? | `docs/COMPLIANCE/INCIDENT_RESPONSE.md` (severities, roles, 72 h breach path). |
| D.6 | Change management? | Git history + migration discipline (`alembic`), path-contract test (`scripts/verify_docs_paths.py`), truth tests (`tests/truth/`). |
| D.7 | Subprocessors disclosed? | `docs/COMPLIANCE/SUBPROCESSORS.md`, kept in sync with shipped integrations. |

## E. Certifications

| Certification | Status |
|---|---|
| SOC 2 Type I / II | **PLANNED** — controls implemented and tested; no audit performed, no report issued. |
| HIPAA | **PLANNED** — technical safeguards implemented; no BAA signed, no assessment. |
| ISO/IEC 27001 | **PLANNED** — Annex A mapping in the control matrix; no certification body engaged. |
| PCI DSS | Not applicable to VoxDesk directly — card data never reaches our systems (Stripe-hosted). |
