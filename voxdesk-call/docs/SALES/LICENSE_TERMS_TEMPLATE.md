# Commercial License & Asset Transfer Terms Template

> **LEGAL COUNSEL REVIEW TEMPLATE — NOT LEGAL ADVICE**
> This document is a commercial term-sheet template mirroring the root `LICENSE` file for review, customization, and execution by Seller's and Buyer's qualified legal counsel alongside a definitive Asset Purchase Agreement (APA) or Commercial Software License Order.

---

## Election of Transfer Structure (Schedule A)

- **[ ] Option A — Exclusive IP Assignment (Full Acquisition / APA Closing):**
  Upon receipt of full closing consideration under the executed Asset Purchase Agreement, Seller assigns all right, title, and interest in the first-party source code, database schemas, Alembic migrations, polyglot services (`app/`, `services/`, `dashboard/`, `dashboard-next/`, `sdk/`, `infra/`), test suites, and documentation of `voxdesk-call` to Buyer, subject solely to third-party open-source package licenses inventoried in `THIRD_PARTY_LICENSES.md` and `sbom/`.
- **[ ] Option B — Perpetual Commercial Source-Available License:**
  Upon payment in full of the license fee, Seller grants Buyer a perpetual, worldwide, non-exclusive, royalty-free license to modify, build, deploy, and operate the Software for internal use or hosted SaaS offerings for Buyer's end-customers, without source-code redistribution to third parties.

---

## Technical Scope & Verified Representations

1. **Measured Capabilities Only:** Functional representations are strictly bounded by the automated evidence in `docs/SALES/FEATURE_MATRIX_VERIFIED.md`, `docs/DUE_DILIGENCE/KNOWN_LIMITATIONS.md`, and the generated due-diligence archive produced by `python scripts/make_due_diligence_pack.py`.
2. **Third-Party Credentials & Accounts:** Carrier accounts (Twilio, Telnyx, Vonage, SIP trunks), AI provider accounts (OpenAI, Anthropic, Deepgram, ElevenLabs, Cartesia, Google), and cloud/KMS infrastructure are provisioned by Buyer per `docs/HANDOFF_CHECKLIST.md`.
3. **No Implied Regulatory Certification:** Technical controls (RBAC, SSO/SCIM, PII redaction, KMS envelope encryption, audit logs) do not constitute a third-party audited HIPAA BAA, SOC 2 Type II report, or ISO 27001 certificate; Buyer is responsible for organizational compliance assessment.
