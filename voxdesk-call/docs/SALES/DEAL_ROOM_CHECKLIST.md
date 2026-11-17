# Buyer due-diligence room checklist

Use this checklist to assemble a factual, version-specific data room. A checked
box means the artifact exists, is current for the candidate being reviewed, and
has been reviewed by the person named below. A document template or an automated
unit test is not evidence of a third-party certification or a live provider
integration.

## Deal-room cover sheet

- Candidate tag / commit: `________________________________`
- Package SHA-256: `________________________________________`
- Prepared by / date: `_____________________________________`
- Buyer contact / review window: `___________________________`
- Build and evidence freshness checked: `[ ]`

## Product and scope

- [ ] `README.md` — product overview and architecture.
- [ ] `docs/SALES/FEATURE_MATRIX_VERIFIED.md` — generated, fresh feature
  evidence; call out `API_ONLY`, `PENDING_CREDENTIALS`, `NOT_CONFIGURED`, and
  `PLANNED` rows instead of describing them as live.
- [ ] `docs/SALES/COMPETITIVE_COMPARISON.md` — dated, attributed comparison;
  re-check vendor claims and URLs before external circulation.
- [ ] `docs/SALES/LISTING_COPY.md` — claims match the verified feature matrix.
- [ ] `docs/CHANGELOG.md` and the generated developer API reference — changes
  and published contract for this candidate.
- [ ] `docs-site/docs/postman/voxdesk-published-api.postman_collection.json` —
  importable collection; environment tokens are blank placeholders.

## Deployment and operations

- [ ] `docs/BUYER_DEPLOY_GUIDE.md` — deployment topology and required
  configuration reviewed for the buyer's environment.
- [ ] `docs/DEPLOYMENT.md` — operator procedures and recovery steps.
- [ ] `docs/BCP.md` and the applicable backup/restore evidence — restore was
  exercised on the candidate environment, not inferred from a script existing.
- [ ] `docs/CAPACITY_MODEL.md` — stated limits and assumptions supplied with
  the estimate.
- [ ] Helm / Compose / infrastructure artifacts use the candidate's pinned
  chart and image digests; no unreviewed local overrides.

## Security, privacy and compliance

- [ ] `docs/CURRENT-SECURITY-STATUS.md`, root `SECURITY.md`, and relevant
  security test results reviewed.
- [ ] `docs/COMPLIANCE.md`, data-flow, retention and subprocessor documents
  reviewed with counsel/privacy staff.
- [ ] `docs/COMPLIANCE/SOC2_CONTROL_MATRIX.md`, `HIPAA_READINESS.md`, BAA and
  privacy templates are presented as preparation/template material unless a
  current independent attestation or signed agreement is attached.
- [ ] Threat model, incident-response contact, backup location and key-rotation
  ownership agreed with the buyer.
- [ ] Secret scan and dependency/SBOM review rerun; no `.env`, token, customer
  export, call recording or private key is included in the bundle.

## Commercial and legal

- [ ] `docs/SALES/PRICING_AND_TIERS.md` has current pricing and scope.
- [ ] `docs/SALES/LICENSE_TERMS_TEMPLATE.md` reviewed by qualified counsel;
  it is a template, not signed legal advice.
- [ ] Support, hosting, data residency, provider charges, service levels and
  acceptance criteria are written into the transaction documents.
- [ ] Third-party notices, licenses and generated SBOMs are included and
  reviewed for the chosen deployment model.
- [ ] The buyer has a named technical owner and a written handoff plan.

## Final package verification

- [ ] Run the required tests and truth gates on the candidate commit.
- [ ] Rebuild the API reference, Postman collection, RSS feed and static docs;
  strict MkDocs build succeeds.
- [ ] Build the release bundle from the same candidate tree.
- [ ] From the repository root, run `sha256sum -c dist/SHA256SUMS` and attach
  the successful output.
- [ ] Inspect the archive member list and open the packaged documents; verify
  that it contains no secrets or unrelated test/evidence dumps.
- [ ] Record any open P0/P1 gate or external dependency as a blocker. Do not
  label the candidate production-ready solely because the bundle built.

## Sign-off

| Role | Name | Decision | Date | Notes |
| --- | --- | --- | --- | --- |
| Product owner |  |  |  |  |
| Security / privacy reviewer |  |  |  |  |
| Operations owner |  |  |  |  |
| Commercial / legal reviewer |  |  |  |  |
