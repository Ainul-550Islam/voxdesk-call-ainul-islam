# VoxDesk — Technical Due-Diligence Evidence Index

This directory (`docs/DUE_DILIGENCE/`), `evidence/pack/SHA256SUMS`, and the archive
built on demand by `scripts/make_due_diligence_pack.py` (due-diligence-2026-11-23.zip) provide a self-contained, cryptographically checksummed
evidence package for technical due-diligence reviewers.
Verify current inputs without an archive: `sha256sum -c evidence/pack/SHA256SUMS`. Only a real pack build produces the archive digest in `dist/SHA256SUMS`.
For the cleanup, current gate and historical Batch 07 evidence distinction, see `reports/PART_10_REPORT.md`.

---

## 1. Repository & Runtime Snapshot (`scripts/repo_stats.py`)

- **Source Files (`app/`, `services/`, `dashboard-next/`, `sdk/`)**: `1969`
- **Physical Source Lines**: `459,613`
- **Registered FastAPI Routes (`app.main:app`)**: `1838` (`1536` OpenAPI paths / `1811` HTTP operations)
- **Collected Pytest Nodes**: `8,504` (collection count, not an execution count)
- **Alembic Migration Head**: `0095_calls_number_width` (`360` registered tables)
- **Fake-Success Allowlist (`scripts/fake_success_allowlist.txt`)**: `0` bytes (`0` entries)

---

## 2. Index of Due-Diligence Documents & Evidence Artifacts

| Category | Document / Artifact Path | Description |
|---|---|---|
| **Architecture** | [`docs/DUE_DILIGENCE/ARCHITECTURE.md`](ARCHITECTURE.md) | 5 Mermaid diagrams: Live PSTN call path, Control Plane, Durable Outbox/Jobs, Go/Rust/C++ Realtime SFU, and Browser Web-Call path. |
| **Test Report** | [`docs/DUE_DILIGENCE/TEST_REPORT.md`](TEST_REPORT.md) | Batch 07 platform tests: `744` passed; truth guards: `96` passed; `8,504` pytest nodes collected (collection, not execution). Historical UI/polyglot reports and exact toolchain availability are identified explicitly. |
| **Git History** | [`docs/DUE_DILIGENCE/GIT_HISTORY.md`](GIT_HISTORY.md) | Historical log of the earlier release, including the Oct 3, 2026 corruption account and the CI guard (`scripts/verify_no_null_bytes.py`). |
| **Known Limitations** | [`docs/DUE_DILIGENCE/KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) | Auto-generated disclosure of the 3 non-`LIVE` feature rows (`#3` `API_ONLY`, `#19` `NOT_CONFIGURED`, `#35` `PLANNED`) and single-worker concurrency limits. |
| **Verified Feature Matrix** | [`docs/SALES/FEATURE_MATRIX_VERIFIED.md`](../SALES/FEATURE_MATRIX_VERIFIED.md) | 41-row capability matrix (`38 LIVE`, `1 API_ONLY`, `1 NOT_CONFIGURED`, `1 PLANNED`) generated from `tests/truth/feature_manifest.yaml` and `evidence/junit/feature_evidence.xml`. |
| **Competitive Comparison** | [`docs/SALES/COMPETITIVE_COMPARISON.md`](../SALES/COMPETITIVE_COMPARISON.md) | Sourced 41-row comparison vs. Retell AI with vendor claims explicitly labelled. |
| **Pricing & Sell Gates** | [`docs/SALES/PRICING_AND_TIERS.md`](../SALES/PRICING_AND_TIERS.md) | Band A / B / C commercial tiers mapped to Gates `G0`–`G9`, handoff terms, and explicit exclusions. |
| **Marketplace Listing Copy** | [`docs/SALES/LISTING_COPY.md`](../SALES/LISTING_COPY.md) | Auto-generated Fiverr/Upwork/Acquire copy built strictly from `LIVE` rows. |
| **Static OpenAPI Reference** | [`docs/api/index.html`](../api/index.html), [`docs/api/openapi.json`](../api/openapi.json) | Redoc HTML + OpenAPI 3.1.0 specification built by `scripts/build_api_docs.sh`. |
| **Quickstart & Operations** | [`docs/QUICKSTART.md`](../QUICKSTART.md), [`docs/OPS.md`](../OPS.md) | 5-command clean-VM install guide, provider key checklist, backups, secret rotation, and Kubernetes HPA runbook. |
| **Provider Evidence Status** | [`docs/EVIDENCE_STATUS.md`](../EVIDENCE_STATUS.md) | Auto-generated summary of the latest real external provider integration run (`<= 7` day freshness gate, `provider_fakes: false`). |
| **Latency Benchmark (Real)** | [`docs/LATENCY_BENCHMARK.md`](../LATENCY_BENCHMARK.md), [`docs/LATENCY_BENCHMARK_MOCK.md`](../LATENCY_BENCHMARK_MOCK.md) | Dual-channel caller-leg PSTN latency methodology & `evidence/latency/<date>/real_study.json` schema; mock in-process harness numbers isolated in `docs/LATENCY_BENCHMARK_MOCK.md`. |
| **Capacity Model & Load Test** | [`docs/CAPACITY_MODEL.md`](../CAPACITY_MODEL.md), `evidence/capacity/` | Simulated single-worker cost ramp; not measured PSTN concurrency or a production capacity guarantee. |
| **Disaster Recovery Drill** | [`docs/DR_RUNBOOK.md`](../DR_RUNBOOK.md), `evidence/dr/dr_drill_report.json` | Isolated, destructive PostgreSQL fixture drill. Historical seeded result: `RPO = 0.35s`, `RTO = 2.23s`; not a production recovery guarantee. |
| **Demo Script & Recordings** | [`docs/DEMO/SCRIPT.md`](../DEMO/SCRIPT.md), `evidence/demo/` | 10-minute walkthrough script, 8 synthetic traces and transcripts indexed by checksum in `evidence/demo/manifest.json`. No verified release download or real carrier recording is claimed. |
| **CycloneDX SBOMs** | `sbom/*.cdx.json` | CycloneDX 1.5 SBOMs for Python (`53`), Node (`18`), Go (`35`), and Rust (`159`). |
| **Third-Party Licenses** | [`THIRD_PARTY_LICENSES.md`](../../THIRD_PARTY_LICENSES.md) | Dependency license table + CC0 synthesized ambient WAV provenance. |
| **Governance & Security** | [`LICENSE`](../../LICENSE), [`NOTICE`](../../NOTICE), [`SECURITY.md`](../../SECURITY.md), [`CHANGELOG.md`](../../CHANGELOG.md) | Commercial license templates, attribution, vulnerability policy, and release history. |

---

## 3. Generated evidence in this pack

| Evidence | Status | Artifact | Generated (UTC) |
|---|---|---|---|
| `test` | **PRESENT** | `reports/TEST_EVIDENCE_SUMMARY.json` | 2026-11-23T06:26:51Z |
| `polyglot` | **PRESENT** | `reports/POLYGLOT_EVIDENCE_SUMMARY.json` | 2026-11-21T05:41:33Z |
| `security` | **PRESENT** | `reports/SECURITY_EVIDENCE_SUMMARY.json` | 2026-11-18T11:51:52Z |
| `deploy-assets` | **PRESENT** | `reports/deploy_asset_validation.json` | 2026-11-18T10:11:37Z |
| `staging` | **PRESENT** | `evidence/staging/2026-11-23/staging_proof.json` | 2026-11-23T06:35:20Z |
| `live` | **STALE** | `evidence/live/twilio-twilio-control-plane-20261101T142529Z.json` | 2026-11-01T14:25:29Z |

A status other than `PRESENT` is stated, not hidden: see
[`KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) for the same table with
regeneration commands.

---

## 4. One-Command Verification (`make verify-sale`)

```bash
make verify-sale
ls -lh dist/due-diligence-*.zip dist/SHA256SUMS
```
