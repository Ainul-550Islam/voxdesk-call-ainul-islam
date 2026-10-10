# Security Policy

This document describes how to report security vulnerabilities in the VoxDesk
Voice AI Platform (`voxdesk-call`) and summarizes the supported release line and
enforced security controls. Detailed architectural threat models, cryptographic
specifications, and SAST/CVE audit procedures live in [`docs/SECURITY.md`](docs/SECURITY.md).

## Supported Versions

| Version Line | Status | Security Updates | Notes |
|---|---|---|---|
| `v1.0.x` (`main` at `0062_drop_pcap_artifacts`) | Supported | Active | Truth-verified release line (Gates G0–G9) |
| `< v1.0.0` (pre-truth-gate commits) | Unsupported | None | Historical development snapshots; upgrade to `v1.0.0+` |

## Reporting a Vulnerability

Do **not** open a public GitHub issue for suspected security vulnerabilities.

1. **Email**: Report vulnerabilities privately to `security@voxdesk.example.com`
   (or the security contact configured in `SECURITY_CONTACT_EMAIL` and exposed
   at `GET /.well-known/security.txt` per RFC 9116).
2. **Required Details**:
   - Affected component (`app/`, `dashboard/`, `dashboard-next/`, `services/realtime/gateway-go/`, `services/realtime/media-engine-rs/`, `services/control-plane/`, or `sdk/`)
   - Reproduction steps, proof-of-concept request/payload, and tenant/environment boundary impact
   - Commit SHA or release tag tested
3. **Response Targets**:
   - Initial acknowledgment: within **24 hours**
   - Triage & severity classification (CVSS v3.1): within **72 hours**
   - Remediation patch for Critical/High findings: within **7 calendar days**

## Enforced Security Invariants (Verified in CI)

Every push, pull request, and `make verify-sale` run enforces the following
automated security gates:

- **Zero Hardcoded or Fallback Secrets**: Verified by `tests/truth/test_no_default_secrets.py`
  and `tests/test_deployment.py`. Production startup (`Settings.validate_security()`)
  fails closed if `SECRET_KEY`, `JWT_SECRET`, or `CRM_ENCRYPTION_KEYS` are missing
  or placeholder values.
- **Envelope Encryption (AES-256-GCM)**: External OAuth tokens, webhook signing
  keys, custom HTTP tool headers, and IdP credentials are encrypted at rest via
  `app/security/secret_store.py` and `app/integrations/crm/crypto.py` with key
  rotation support in `scripts/rotate_secrets.py`.
- **Outbound SSRF Protection**: Every outbound webhook, custom HTTP tool, OIDC
  discovery fetch, SAML metadata fetch, and CRM API call validates targets and
  resolved DNS records against RFC 1918, loopback, link-local (`169.254.169.254`),
  and CGNAT ranges via `app/core/ssrf.py` (`tests/security/test_ssrf_all_outbound.py`).
- **Tenant & Environment Isolation**: Every database query is scoped by
  `tenant_id` (and `environment_id` where modeled); cross-tenant resource access
  returns `HTTP 404` (`tests/security/test_tenant_isolation_matrix.py`).
- **PII Redaction & Retention Enforcement**: Transcripts, LLM inputs, webhook
  payloads, and audio buffers redact credit cards (Luhn), SSNs, phone numbers,
  and emails (`app/compliance/pii_redaction.py`, `app/core/retention.py`).
- **Automated Supply-Chain & SAST Scanning**: `.github/workflows/security-scan.yml`
  runs Bandit SAST, `pip-audit`, `npm audit --omit=dev`, `cargo audit`,
  `govulncheck`, `gitleaks`, and Trivy container image scanning.
