# VoxDesk — Technical Due-Diligence Evidence Index

This directory (`docs/DUE_DILIGENCE/`) and the generated archive
`dist/due-diligence-2026-10-10.zip` provide a self-contained, cryptographically checksummed
evidence package for technical due-diligence reviewers.

---

## 1. Repository & Runtime Snapshot (`scripts/repo_stats.py`)

- **Source Files (`app/`, `services/`, `dashboard/`, `dashboard-next/`, `sdk/`, `tests/`, `scripts/`, `alembic/`)**: `1679`
- **Physical Source Lines**: `322,507`
- **Registered FastAPI Routes (`app.main:app`)**: `1182` (`969` OpenAPI paths / `1,166` HTTP operations)
- **Collected Pytest Nodes**: `4,719`
- **Alembic Migration Head**: `0062_drop_pcap_artifacts` (`239` tables)
- **Fake-Success Allowlist (`scripts/fake_success_allowlist.txt`)**: `0` bytes (`0` entries)

---

## 2. Index of Due-Diligence Documents & Evidence Artifacts

| Category | Document / Artifact Path | Description |
|---|---|---|
| **Architecture** | [`docs/DUE_DILIGENCE/ARCHITECTURE.md`](ARCHITECTURE.md) | 5 Mermaid diagrams: Live PSTN call path, Control Plane, Durable Outbox/Jobs, Go/Rust/C++ Realtime SFU, and Browser Web-Call path. |
| **Test Report** | [`docs/DUE_DILIGENCE/TEST_REPORT.md`](TEST_REPORT.md) | Verified counts across Pytest (`4,707` collected), Vitest (`660` passed), Rust Cargo (`118` passed), C++ CTest (`23` passed), and Go (`10` packages passed). |
| **Git History** | [`docs/DUE_DILIGENCE/GIT_HISTORY.md`](GIT_HISTORY.md) | Factual log of all 6 commits in `.git`, including the Oct 3, 2026 NUL-byte corruption incident and its permanent CI guard (`scripts/verify_no_null_bytes.py`). |
| **Known Limitations** | [`docs/DUE_DILIGENCE/KNOWN_LIMITATIONS.md`](KNOWN_LIMITATIONS.md) | Auto-generated disclosure of the 3 non-`LIVE` feature rows (`#3` `API_ONLY`, `#19` `NOT_CONFIGURED`, `#35` `PLANNED`) and single-worker concurrency limits. |
| **Verified Feature Matrix** | [`docs/SALES/FEATURE_MATRIX_VERIFIED.md`](../SALES/FEATURE_MATRIX_VERIFIED.md) | 41-row capability matrix (`38 LIVE`, `1 API_ONLY`, `1 NOT_CONFIGURED`, `1 PLANNED`) generated from `tests/truth/feature_manifest.yaml` and `evidence/junit/feature_evidence.xml`. |
| **Competitive Comparison** | [`docs/SALES/COMPETITIVE_COMPARISON.md`](../SALES/COMPETITIVE_COMPARISON.md) | Sourced 41-row comparison vs. Retell AI with vendor claims explicitly labelled. |
| **Pricing & Sell Gates** | [`docs/SALES/PRICING_AND_TIERS.md`](../SALES/PRICING_AND_TIERS.md) | Band A / B / C commercial tiers mapped to Gates `G0`–`G9`, handoff terms, and explicit exclusions. |
| **Marketplace Listing Copy** | [`docs/SALES/LISTING_COPY.md`](../SALES/LISTING_COPY.md) | Auto-generated Fiverr/Upwork/Acquire copy built strictly from `LIVE` rows. |
| **Static OpenAPI Reference** | [`docs/api/index.html`](../api/index.html), [`docs/api/openapi.json`](../api/openapi.json) | Redoc HTML + OpenAPI 3.1.0 specification built by `scripts/build_api_docs.sh`. |
| **Quickstart & Operations** | [`docs/QUICKSTART.md`](../QUICKSTART.md), [`docs/OPS.md`](../OPS.md) | 5-command clean-VM install guide, provider key checklist, backups, secret rotation, and Kubernetes HPA runbook. |
| **Latency Benchmark** | [`docs/LATENCY_BENCHMARK.md`](../LATENCY_BENCHMARK.md) | Per-stage STT, LLM TTFT, TTS TTFB, and E2E p50/p95/p99 latency breakdown (`e2e_p50 = 285.3 ms`, `e2e_p95 = 318.7 ms` in-process). |
| **Capacity Model & Load Test** | [`docs/CAPACITY_MODEL.md`](../CAPACITY_MODEL.md), `evidence/loadtest/` | Single-worker concurrency ramp (`10 -> 60` calls), CPU/RSS/FD telemetry, and cost-per-minute model. |
| **Disaster Recovery Drill** | [`docs/DR_RUNBOOK.md`](../DR_RUNBOOK.md), `evidence/dr/dr_drill_report.json` | Automated PostgreSQL dump/drop/restore/audit-chain verification (`RPO = 0.35s`, `RTO = 2.23s`). |
| **Demo Script & Recordings** | [`docs/DEMO/SCRIPT.md`](../DEMO/SCRIPT.md), `evidence/demo/` | 10-minute walkthrough script and 8 captured execution traces + WAV audio artifacts (`manifest.json`). |
| **CycloneDX SBOMs** | `sbom/*.cdx.json` | CycloneDX 1.5 SBOMs for Python (`44`), Node (`24`), Go (`24`), and Rust (`159`). |
| **Third-Party Licenses** | [`THIRD_PARTY_LICENSES.md`](../../THIRD_PARTY_LICENSES.md) | Dependency license table + CC0 synthesized ambient WAV provenance. |
| **Governance & Security** | [`LICENSE`](../../LICENSE), [`NOTICE`](../../NOTICE), [`SECURITY.md`](../../SECURITY.md), [`CHANGELOG.md`](../../CHANGELOG.md) | Commercial license templates, attribution, vulnerability policy, and release history. |

---

## 3. One-Command Verification (`make verify-sale`)

```bash
make verify-sale
ls -lh dist/due-diligence-*.zip dist/SHA256SUMS
```
