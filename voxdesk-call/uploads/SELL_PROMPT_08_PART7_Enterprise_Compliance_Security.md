# SELL PROMPT 8 of 10 — PART 7: Enterprise, Compliance and Security

Copy the whole block below with one click and paste it into the coding agent. It contains everything for this part: mission, rules, the file tree with a `# comment` beside every file, and the acceptance commands.

```text
# ======================================================================================================================
# SELL PROMPT 8 of 10 — PART 7: ENTERPRISE, COMPLIANCE AND SECURITY   (Gate G9 (part))
# Series: VoxDesk x Retell sell-ready gap closure (target sale band $30K-$60K) | repo root: voxdesk-call/ | master plan:
#   VOXDESK_SELL_READY_GAP_CLOSURE_30K_60K.md
# PREREQUISITE: SELL PROMPT 1 (PART 0) accepted. SELL PROMPT 2 sub-part 1E is recommended first.
# MISSION: Verify enterprise identity against a real IdP, add security regression suites (SSRF, tenant isolation, rate
#   limits), number-trust profiles from provider data, and compliance documentation + evidence collection - without
#   claiming any certification.
# SCOPE GUARD: change only what appears in the tree below. Other prompts own the rest. Do not start the next prompt in
#   this session.
# RULE: document controls and produce evidence; never claim a certification (HIPAA/SOC 2/ISO) that has not been issued.
#   Say "SOC 2-ready controls" or "HIPAA-ready configuration" only when the matching evidence exists.
# CLOSES Retell parity rows: #20 Branded caller ID / verified numbers | #35 HIPAA / SOC 2 Type II / GDPR / ISO 27001 |
#   #36 SSO / SCIM / RBAC / PII redaction
# RULES (apply to every file below)
#   R1  Read every file fully before editing. Output the COMPLETE final content of every created/modified file.
#       Never write "...", "rest unchanged", "omitted for brevity".
#   R2  Extend REAL assets (tag [KEEP]); never build a parallel copy of something that already works.
#   R3  NO FAKE SUCCESS: do the real external effect or return NOT_CONFIGURED / UNSUPPORTED_CAPABILITY /
#       PENDING_PROVIDER (HTTP 501/409). Never record delivered/connected/success without proof.
#   R4  NO filler, padding, clones or line-count targets: no StructN, *_function_N, /endpoint-N, "Padding ... line
#       N" or "1050+ lines" banners.
#   R5  Durable multi-worker state only (Postgres / Redis / app/jobs / app/outbox). No process-local dict/list for
#       idempotency, rate limits, OAuth state, sessions, queues.
#   R6  Every outbound URL goes through app/core/ssrf.py. Secrets via app/security/secret_store.py or
#       app/integrations/crm/crypto.py (AES-GCM). No base64/reverse "encryption", no fallback secrets.
#   R7  Every query tenant-scoped (and environment-scoped where modelled). Cross-tenant = 404. Each new route ships
#       a two-tenant isolation test.
#   R8  Audit/outbox writes happen in the same transaction as the action. No `except Exception: pass` around
#       audit/outbox/webhook code.
#   R9  Every task ships unit + contract tests. External calls get a recording-fake test (asserts the real request)
#       AND an opt-in @pytest.mark.real_provider test (existing marker; runs only with VOXDESK_REAL_INTEGRATION=1). No
#       passing test = not LIVE.
#   R9b New tests/<dir>/ gets __init__.py; reuse the existing real_provider marker (never add a second `live`
#       marker); register only slow/docker in pytest.ini.
#   R10 Alembic: exactly ONE head (as shipped: 0045_request_idempotency_receipts). Use the next free revision, never
#       edit an applied migration, run `alembic heads` before and after.
#   R11 Docs contain only measured facts (scripts/repo_stats.py). Anything without an end-to-end/contract test is
#       documented as API_ONLY or PLANNED.
#   R12 One commit per task ID: "<TASK-ID>: <what>". No generated bulk. No null-byte files.
#   R13 After this part write reports/PART_<n>_REPORT.md: files changed (complete content), real command output,
#       test counts pass/fail/skip, residual gaps. Never claim "production ready".
#   R14 pipecat-ai is pinned at 0.0.94: inspect the installed package and use only classes that exist there.
#   R15 Register every new router/WebSocket in app/main.py, add an RBAC permission + isolation test, regenerate
#       contracts (make contracts-check). Never reuse an existing file name (ls app/api first).
# TAGS: [NEW] create | [MODIFY] read fully, change only what is described, keep other behaviour | [DELETE] remove +
#   every import/registration/compose/CI reference | [KEEP] REAL asset, extend only as stated | [VERIFY] inspect first,
#   record the decision in the report
# COMMENT FORMAT: # [TAG][sub-part] kind — what the file contains (classes / functions / behaviour / tests). Several
#   changes to one file are merged on one line as "sub-part: change || sub-part: change".
# ======================================================================================================================

voxdesk-call/
├── .github/
│   └── workflows/
│       └── security-scan.yml                # [MODIFY] ci — add gitleaks, pip-audit, npm audit, cargo audit, govulncheck,
│                                            #   trivy image scan; upload SARIF; fail on high/critical
├── alembic/
│   └── versions/
│       └── <next>_number_trust_profile.py   # [NEW] migration — number_trust_profiles table
├── app/
│   ├── api/
│   │   └── number_trust_routes.py           # [NEW] route — GET trust profile per number, POST refresh; never writes
│   │                                        #   attestation values from user input
│   ├── security/
│   │   ├── kms/
│   │   │   ├── __init__.py                  # [NEW] package — KmsAdapter protocol (wrap/unwrap data keys); default adapter
│   │   │   │                                #   returns NOT_CONFIGURED
│   │   │   └── aws_kms.py                   # [NEW] module — at least ONE real adapter (AWS KMS or Vault Transit) with an
│   │   │                                    #   integration test behind the `real_provider` marker
│   │   └── secret_store.py                  # [MODIFY] module — every provider/CRM/webhook/tool secret sealed; envelope
│   │                                        #   encryption with a KmsAdapter interface; key-rotation support
│   └── telephony/
│       └── number_trust.py                  # [NEW] module — NumberTrustProfile(shaken_attestation,
│                                            #   branded_caller_name/CNAM, spam_status, a2p_registration, last_checked)
│                                            #   populated ONLY from provider API responses (Twilio Trust Hub / Voice
│                                            #   Integrity, Telnyx equivalents); NOT_CONFIGURED otherwise; scheduled refresh
│                                            #   job
├── docs/
│   ├── COMPLIANCE/
│   │   ├── BAA_TEMPLATE.md                  # [NEW] doc — template for counsel review (not legal advice)
│   │   ├── DATA_FLOW.md                     # [NEW] doc — diagram + table of where audio, transcripts, PII and secrets live
│   │   │                                    #   and for how long (matches the retention implementation)
│   │   ├── HIPAA_READINESS.md               # [NEW] doc — configuration checklist (zero-retention, PII redaction,
│   │   │                                    #   encryption, audit, BAA process); states clearly that no attestation is
│   │   │                                    #   claimed
│   │   ├── SOC2_CONTROL_MATRIX.md           # [NEW] doc — control → code/test/evidence mapping (extends docs/SOC2.md); gaps
│   │   │                                    #   listed
│   │   └── SUBPROCESSORS.md                 # [NEW] doc — Twilio, Deepgram, ElevenLabs, LLM vendors, Stripe, hosting; data
│   │                                        #   categories sent to each
│   └── PENTEST-CHECKLIST.md                 # [MODIFY] doc — each item gets a test id or evidence link; unchecked items
│                                            #   stay visible
├── infra/
│   └── idp-test/
│       ├── docker-compose.keycloak.yml      # [NEW] config — Keycloak container (compose profile `idp-test`) for real
│       │                                    #   OIDC/SAML integration tests
│       └── realm-voxdesk.json               # [NEW] config — realm export: test users, groups, clients, SAML client, role
│                                            #   mappers
├── scripts/
│   ├── collect_compliance_evidence.py       # [NEW] script — gathers CI artifacts (test reports, SBOM, audit outputs,
│   │                                        #   backup-restore drill logs) into evidence/<date>/ with SHA256SUMS
│   └── rotate_secrets.py                    # [NEW] script — re-seal all stored secrets with a new key, resumable, dry-run
│                                            #   mode, audit entries
└── tests/
    ├── auth/
    │   ├── test_mfa_login_e2e.py            # [NEW] test — full login-with-MFA path through the real auth routes (enroll,
    │   │                                    #   challenge, recovery code, lockout); add only if not already covered
    │   └── test_mfa_verification.py         # [KEEP] test — MFA tests already exist in
    │                                        #   tests/auth/test_mfa_{enrollment,verification,logging}.py (docs/MFA.md); keep
    │                                        #   green
    ├── compliance/
    │   └── test_recording_consent_flow.py   # [NEW] test — live call flow plays the required disclosure per state/category
    │                                        #   from app/telephony/consent.py before recording starts; no recording without a
    │                                        #   decision row
    ├── security/
    │   ├── test_rate_limit_matrix.py        # [NEW] test — sensitive routes (login, web-calls, webhooks test-send,
    │   │                                    #   monitoring) enforce Redis rate limits across two workers
    │   ├── test_scim_conformance.py         # [NEW] test — SCIM 2.0: create/patch/deactivate users, groups, filters,
    │   │                                    #   pagination, error format; tenant isolation
    │   ├── test_sso_oidc_keycloak.py        # [NEW] test — real OIDC authorization-code flow: login, JIT provisioning, role
    │   │                                    #   mapping, logout, token expiry (marker `integration`)
    │   ├── test_sso_saml_keycloak.py        # [NEW] test — real SAML login against Keycloak if SAML is implemented
    │   │                                    #   (docs/SSO-SAML.md); otherwise mark the docs `PLANNED`
    │   ├── test_ssrf_all_outbound.py        # [NEW] test — static scan of httpx/aiohttp/requests usage reachable from
    │   │                                    #   webhooks, HTTP tools, CRM, URL ingest, calendar, widget: each must pass
    │   │                                    #   through app/core/ssrf.py; dynamic cases (private IP, metadata IP, DNS
    │   │                                    #   rebinding, redirects)
    │   └── test_tenant_isolation_matrix.py  # [NEW] test — generated from the OpenAPI schema: every route with an id
    │                                        #   path-param is called by tenant B for tenant A's object and must return 404
    │                                        #   (allowlist for admin routes)
    └── telephony/
        └── test_number_trust.py             # [NEW] test — provider-fake responses mapped correctly; user input cannot set
                                             #   attestation; NOT_CONFIGURED path

# ======================================================================================================================
# ACCEPTANCE — run every command; paste the REAL output into reports/PART_7_REPORT.md
# ======================================================================================================================
make verify-truth                                   # truth guards must stay green
alembic upgrade head
alembic heads                                       # exactly ONE head
alembic downgrade -1 && alembic upgrade head           # migration is reversible
pytest -q tests/security/test_sso_oidc_keycloak.py
pytest -q tests/security/test_sso_saml_keycloak.py
pytest -q tests/security/test_scim_conformance.py
pytest -q tests/auth/test_mfa_verification.py
pytest -q tests/auth/test_mfa_login_e2e.py
pytest -q tests/security/test_ssrf_all_outbound.py
pytest -q tests/security/test_tenant_isolation_matrix.py
pytest -q tests/security/test_rate_limit_matrix.py
pytest -q tests/telephony/test_number_trust.py
pytest -q tests/compliance/test_recording_consent_flow.py

# ACCEPTANCE: Keycloak OIDC test green; SCIM conformance green; tenant-isolation matrix green; SSRF scan green;
#   compliance docs contain no certification claims.

# NEXT: SELL PROMPT 9 of 10 — PART 8: RELIABILITY AND SCALE PROOF. Start it only after every acceptance command above
#   passes and reports/PART_7_REPORT.md exists.
```
