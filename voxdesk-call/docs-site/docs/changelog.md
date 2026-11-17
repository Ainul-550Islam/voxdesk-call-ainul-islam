# Developer and API changelog

This changelog records changes relevant to VoxDesk integrators and operators.
The repository-root `CHANGELOG.md` remains the product-wide release history.

[Subscribe to the RSS feed](changelog.xml).

## [Unreleased] — 2026-11-08

### Added

- **Agent starter templates.** Added a built-in catalog of seven voice-agent
  templates, an API for browsing and instantiating them, and a dashboard
  gallery. Instantiation creates a draft; it does not publish or activate an
  agent.
- **Demo environment.** Added deterministic demo seeding and a gated
  request-a-demo-call flow. Consent, verification, rate limits, configured
  budget and anti-abuse checks run before a demo call can be placed.
- **Retell migration.** Added an importer for supported Retell agent exports,
  with explicit refusals for unsupported flow constructs rather than silently
  dropping behavior.
- **Versioned billing prices.** Added an immutable component price book,
  itemised usage cost attribution and provider/day cost summaries. Published
  price-book versions are retained for invoice reproducibility.
- **White-label rendering.** Connected the existing white-label theme engine
  to server-rendered page metadata, CSS variables, widget configuration and
  document metadata. Added a tenant branding editor and a surface coverage
  view; default VoxDesk names remain visible until the tenant fully replaces
  them.
- **Generated API reference.** Added the MkDocs developer site and reference
  generated from the application OpenAPI schema. It documents 93 buyer-facing
  operations across public, public-key, billing, white-label and agent-template
  APIs. The build and test guard fail if a published operation has no summary
  or description. Internal route families are counted but not advertised as a
  stable buyer contract.
- **Integration assets.** Added a Postman collection and an RSS changelog feed
  for the published API and developer documentation.

### Verification

- Published API documentation guard: 93 operations, no missing summaries or
  descriptions.
- MkDocs strict build: clean.
- Component verification and scope limitations are recorded in
  `reports/PART_18_REPORT.md`.
