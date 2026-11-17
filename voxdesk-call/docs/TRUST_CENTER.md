# Trust Center (source for the public trust page)

This file is the *source* for a public trust page. `infra/status/generate_status.py`
renders the static pages; nothing here is hand-edited into HTML so the public page
cannot drift from the repository.

## Certifications — the honest status

| Certification | Status | What that means |
|---|---|---|
| SOC 2 Type I | **PLANNED** | Controls are implemented and tested (`docs/COMPLIANCE/SOC2_CONTROL_MATRIX.md`). No CPA firm has audited them. No report exists. |
| SOC 2 Type II | **PLANNED** | Requires a 3–12 month observation period after Type I. |
| HIPAA | **PLANNED** | Technical safeguards are implemented. **No BAA is signed and no PHI should be processed.** |
| ISO/IEC 27001:2022 | **PLANNED** | Annex A controls are mapped in the matrix; no certification body has been engaged. |
| GDPR | Technical measures implemented | DPA template in `docs/COMPLIANCE/BAA_TEMPLATE.md`; DPIA is per-customer. |
| PCI DSS | Not applicable | Card data never reaches VoxDesk; Stripe hosts checkout. |

If you need any of the above *today*, the honest answer is: not yet. Ask for the
evidence bundle instead — every control in the matrix links to a test you can run.

## Security practices you can verify in the repository

* **Multi-tenancy**: every query is tenant-scoped; cross-tenant access answers
  404, not 403 (`app/tenancy/isolation.py`, `tests/security/test_tenant_isolation_matrix.py`).
* **Least privilege**: five built-in roles plus tenant-defined roles that can only
  *add* permissions, with a server-side escalation guard
  (`app/auth/custom_roles.py`, `tests/auth/test_custom_roles.py`).
* **Machine identity**: scoped service accounts, rotation without overlap, and
  scopes that can only narrow (`tests/auth/test_service_accounts.py`).
* **Audit**: append-only ledger, PII-redacted, exportable to a SIEM as signed
  batches with a keyset cursor (`app/audit/siem_export.py`).
* **Residency**: writes that cannot be placed in the pinned region are refused,
  including backups (`app/tenancy/data_residency.py`, `scripts/backup_target_check.py`).
* **Scanning**: dependency/secret/container scans plus a running-surface DAST run
  (`.github/workflows/security-scan.yml`, `.github/workflows/security-dast.yml`,
  reports in `reports/dast/`).
* **Frontend**: strict CSP, `frame-ancestors 'none'`, HSTS, no third-party
  scripts (`dashboard-next/next.config.mjs`, `docs/SECURITY_FRONTEND_REVIEW.md`).

## Subprocessors

See `docs/COMPLIANCE/SUBPROCESSORS.md`. Summary: telephony carriers, STT/TTS/LLM
providers, Stripe, and the operator's own cloud. Only providers a tenant
configures receive that tenant's data.

## Data handling

* **What is stored**: call metadata, transcripts (PII-redactable), recordings if
  recording is enabled and consented, knowledge documents, and audit events.
* **Where**: the operator's cloud, in the region they configure — or nowhere at
  all if they have pinned a region this deployment cannot honour, in which case
  the write is refused.
* **How long**: `RetentionPolicy` per tenant, with `legal_hold` overriding purge.
* **Who sees it**: tenant members per their role; service accounts per their
  scopes. VoxDesk staff have no standing access.

## Reporting a vulnerability

Email the security contact with reproduction steps and the affected endpoint.
We acknowledge within 2 business days, triage within 5, and will credit you in
the advisory unless you prefer otherwise. Please do not test against production
data; a staging deployment with synthetic tenants is available on request.
