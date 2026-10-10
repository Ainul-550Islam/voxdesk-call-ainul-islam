# PART 7 Execution Report — Enterprise, Compliance and Security (Gate G9 (part))

## 1. Mission & Scope Summary

Executed `SELL_PROMPT_08_PART7_Enterprise_Compliance_Security.md` (PART 7: Enterprise, Compliance and Security, Gate G9 (part)), closing Retell parity items `#20`, `#35`, and `#36` without claiming any formal SOC 2 / HIPAA certification ("SOC 2-ready controls" / "HIPAA-ready configuration" backed by reproducible technical evidence).

### Files Created / Modified / Verified (27 Scope Files + Supporting Registrations)

1. `.github/workflows/security-scan.yml` (`[MODIFY]`) — CI workflow executing Bandit SAST SARIF, `pip-audit`, `npm audit` (`dashboard` + `dashboard-next`), `cargo audit` (`media_bridge`), `govulncheck` (`loadtest/k6`), `gitleaks`, and Trivy filesystem + container image scan with SARIF upload and fail-on-HIGH/CRITICAL enforcement.
2. `alembic/versions/0061_number_trust_profile.py` (`[NEW]`) — Single-head Alembic revision (`down_revision = "0060_agent_turn_settings"`) creating `number_trust_profiles`.
3. `app/api/number_trust_routes.py` (`[NEW]`) — `GET /api/phone-numbers/{number_id}/trust-profile` and `POST /api/phone-numbers/{number_id}/trust-profile/refresh` (plus `/api/v1/telephony/phone-numbers/...` aliases), rejecting user-supplied attestation fields (`extra="forbid"`) and emitting an `AuditLog` in the same database transaction.
4. `app/security/kms/__init__.py` (`[NEW]`) — `KmsAdapter` protocol, `WrappedDataKey`, `UnconfiguredKmsAdapter` (fails closed with `NOT_CONFIGURED`), `LocalKeyRingKmsAdapter`, and `get_kms_adapter`.
5. `app/security/kms/aws_kms.py` (`[NEW]`) — `AwsKmsAdapter` (AWS KMS `GenerateDataKey` / `Decrypt` / `ReEncrypt` with SigV4 signing and SSRF validation) and `VaultTransitKmsAdapter` (HashiCorp Vault Transit `datakey/plaintext`, `decrypt`, `rewrap` with SSRF validation).
6. `app/security/secret_store.py` (`[MODIFY]`) — Envelope encryption (`put_with_kms`, `get_with_kms`, `extract_key_id`, `needs_rotation`, `rotate_reference`) using `KmsAdapter` and AES-256-GCM DEK wrapping.
7. `app/telephony/number_trust.py` (`[NEW]`) — `NumberTrustProfile` ORM model, `TwilioTrustHubClient`, `TelnyxTrustClient`, `get_number_trust_profile`, `refresh_number_trust_profile`, and `scheduled_refresh_number_trust_profiles` (never accepts attestation from user input; fails closed with HTTP 501 `NOT_CONFIGURED` when provider credentials are unconfigured).
8. `docs/COMPLIANCE/BAA_TEMPLATE.md` (`[NEW]`) — Business Associate Agreement template with explicit legal-counsel review banner, permitted uses, breach notification window, and termination / destruction clauses.
9. `docs/COMPLIANCE/DATA_FLOW.md` (`[NEW]`) — End-to-end data flow diagrams and trust boundaries across PSTN/WebRTC ingress, ASR/LLM/TTS, PostgreSQL, Redis, S3/MinIO, and webhooks.
10. `docs/COMPLIANCE/HIPAA_READINESS.md` (`[NEW]`) — HIPAA Security Rule §164.308 / §164.310 / §164.312 technical, physical, and administrative safeguard mapping with code + test references.
11. `docs/COMPLIANCE/SOC2_CONTROL_MATRIX.md` (`[NEW]`) — SOC 2 Trust Services Criteria (`CC6.1`–`CC9.2`, `A1.1`–`A1.3`, `C1.1`–`C1.2`, `PI1.1`) control matrix mapped to code, tests, and evidence artefacts.
12. `docs/COMPLIANCE/SUBPROCESSORS.md` (`[NEW]`) — Subprocessor inventory covering Twilio, Telnyx, Deepgram, OpenAI, Anthropic, ElevenLabs, AWS, and Stripe.
13. `docs/PENTEST-CHECKLIST.md` (`[MODIFY]`) — Updated OWASP API Security Top 10 (2023) & ASVS L2 checklist linked to automated regression tests.
14. `infra/idp-test/docker-compose.keycloak.yml` (`[NEW]`) — Ephemeral Keycloak 25.0 container configuration importing `realm-voxdesk.json`.
15. `infra/idp-test/realm-voxdesk.json` (`[NEW]`) — Preconfigured `voxdesk` Keycloak realm with OIDC client (`voxdesk-oidc`), SAML 2.0 client (`https://sp.voxdesk.local/saml/metadata`), groups, and test users.
16. `scripts/collect_compliance_evidence.py` (`[NEW]`) — Automated compliance evidence bundler generating `migration_heads.txt`, `routes_snapshot.json`, `openapi_sha256.txt`, `compliance_docs_manifest.json`, `security_pytest_output.txt`, and `SHA256SUMS`.
17. `scripts/rotate_secrets.py` (`[NEW]`) — Resumable, idempotent CLI re-encrypting `Connector`, `CrmIntegration`, `CalendarIntegration`, and `SsoConnection` secrets under the active key and emitting an `AuditLog` event.
18. `tests/auth/test_mfa_login_e2e.py` (`[NEW]`) — Full HTTP MFA login e2e (`require_mfa=True`, enrollment required, TOTP code verification, invalid code rejection + rate-limit, recovery code single-use consumption).
19. `tests/auth/test_mfa_verification.py` (`[KEEP]`) — Existing unit/HTTP TOTP verification suite (8 passed).
20. `tests/compliance/test_recording_consent_flow.py` (`[NEW]`) — Two-party & explicit consent gating, disclosure prepending, tenant isolation on signed URLs, and legal-hold purge protection.
21. `tests/security/test_rate_limit_matrix.py` (`[NEW]`) — All 8 rate-limit tiers (`auth`, `password_reset`, `admin`, `telephony`, `webhook`, `upload`, `execution`, `api`) returning HTTP 429 + `Retry-After`, plus fail-closed verification when Redis is unreachable.
22. `tests/security/test_scim_conformance.py` (`[NEW]`) — SCIM 2.0 RFC 7643 / 7644 conformance (`ServiceProviderConfig`, `Schemas`, `ResourceTypes`, `Users` CRUD + `active=false` session revocation, `Groups` PATCH role mapping, cross-tenant token rejection).
23. `tests/security/test_sso_oidc_keycloak.py` (`[NEW]`) — OIDC authorization-code + PKCE login, ID-token signature validation, JIT user provisioning, group-to-role mapping, and logout session revocation.
24. `tests/security/test_sso_saml_keycloak.py` (`[NEW]`) — SAML 2.0 SP-initiated AuthnRequest + ACS POST assertion verification, JIT user provisioning, ReplayAttack rejection, AudienceMismatch rejection, and SignatureInvalid rejection.
25. `tests/security/test_ssrf_all_outbound.py` (`[NEW]`) — Parametrized SSRF regression suite across all outbound HTTP call sites (`webhooks`, `http_tools`, `mcp`, `url_ingest`, `oauth`, `anomaly_notifier`).
26. `tests/security/test_tenant_isolation_matrix.py` (`[NEW]`) — Cross-tenant read/write/delete isolation matrix across all 13 core resources (`Agent`, `Call`, `Recording`, `PhoneNumber`, `KnowledgeBase`, `Campaign`, `Webhook`, `CustomDashboard`, `AuditLog`, `Ticket`, `Message`, `TestCase`, `AlertRule`).
27. `tests/telephony/test_number_trust.py` (`[NEW]`) — Provider-sourced `NumberTrustProfile` refresh, `NOT_CONFIGURED` fail-closed behaviour, rejection of user-supplied attestation fields, and cross-tenant isolation.

## 2. Verification Results

- `make verify-truth`: **61 passed** (`routes_snapshot.json` updated to 1187 routes; `contracts/openapi.json` synchronized)
- `alembic upgrade head` / `alembic heads` / `alembic downgrade -1 && alembic upgrade head`: **`0061_number_trust_profile (head)`** (single head verified)
- `python3 scripts/collect_compliance_evidence.py`: **0 exit code**, `SHA256SUMS` generated
- `pytest -q` across all 10 PART 7 suites: **62 passed**
  - `tests/security/test_sso_oidc_keycloak.py`: 2 passed
  - `tests/security/test_sso_saml_keycloak.py`: 2 passed
  - `tests/security/test_scim_conformance.py`: 2 passed
  - `tests/auth/test_mfa_verification.py`: 8 passed
  - `tests/auth/test_mfa_login_e2e.py`: 2 passed
  - `tests/telephony/test_number_trust.py`: 4 passed
  - `tests/compliance/test_recording_consent_flow.py`: 3 passed
  - `tests/security/test_ssrf_all_outbound.py`: 29 passed
  - `tests/security/test_tenant_isolation_matrix.py`: 1 passed (13 resources)
  - `tests/security/test_rate_limit_matrix.py`: 9 passed
