# PART 10 REPORT — Repository Hygiene, Guard Hardening & Doc-Code Truth Reconciliation (Gate G0 / G9)

Date: `2026-11-07` (`Asia/Dhaka`).

## 1. Root Cause Analysis

1. **Tracked Build, Archive & Evidence Bloat (`~60 MiB` excess)**:
   - Historical audit/packaging runs committed `17` `*.zip` bundles across `reports/` and `docs/archive/`, a compiled static C++ library (`services/media-plane/libvoxdesk_media.a`), multi-MB inline source dumps (`reports/PART_0_REPORT.md` at `2.91 MB`, `reports/PART_8_REPORT.md` at `300.8 KB`, `FINAL_TEST_RESULTS.json` at `8.94 MB`), duplicate OpenAPI snapshots under `reports/part0*` and `reports/part1/`, static HTML mockup copies (`docs/archive/dashboard-static-mockups/`), internal prompt specs (`uploads/`), and a generated local env file (`scripts/live_local.env`).
2. **Dead Router & Retired Model Drift**:
   - `app/api/pcap_routes.py` remained on disk as an allowlisted unmounted router after `alembic/versions/0062_drop_pcap_artifacts.py` dropped the `pcap_artifacts` table, while `app/core/retention.py` and `app/api/retention_routes.py` still carried residual `pcap` / `purged_pcaps` references.
   - *(Note: the 94 NUL-corrupted template clones, `advanced_analytics_routes.py`, `realtime-engine/`, `voxdesk-concurrency-engine/`, `voxdesk-native-audio-engine/`, and `dashboard/src/pages/**/*.html` were already purged in earlier repo-hygiene commits and verified absent here.)*
3. **Truth-Guard Fragility on `.git`-less Exports & Corrupted Inputs**:
   - `scripts/verify_no_null_bytes.py` relied on `git ls-files` without an `os.walk` fallback for `.git`-less buyer archive exports, and `scripts/verify_no_fake_success.py` / `scripts/strip_padding_markers.py` raised unhandled Python tracebacks on NUL-corrupted or syntactically invalid `.py` files instead of reporting structured findings with exit code `1`.
4. **Documentation Path & Single-Source Metric Drift**:
   - Buyer-facing Markdown files (`README.md`, `docs/SALES/*.md`, `docs/DUE_DILIGENCE/*.md`) lacked a single machine-readable metric source (`docs/STATS.json`) and contained stale module paths (e.g. `KNOWN_LIMITATIONS.md` and missing `docs/DR_RUNBOOK.md`) with no automated path-existence gate.

---

## 2. Summary of Completed Scope

1. **Repository Artifact, Archive & Dead-Module Deletion (`[DELETE]` / `[MODIFY]`)**:
   - Deleted dead router `app/api/pcap_routes.py` and removed retired `pcap` / `purged_pcaps` references from `app/api/retention_routes.py`, `app/core/retention.py`, `scripts/verify_no_dead_routers.py`, and regenerated `contracts/openapi.json` + `docs/api/openapi.json` (`app/api/outbound_call_routes.py` verified present and left untouched for PART 17B).
   - Deleted `services/media-plane/libvoxdesk_media.a`, all `17` committed `*.zip` archives, `PROMPT2_REPORT.md` (+ archived copy), `CSS_HTML_TS_API_CONNECTION_REPORT.md`, `DOCKER_BUILD_SIMULATION.md` (+ archived copy), `docs/archive/SALES_PACKAGE_20K_60K_GAP_CLOSURE.md`, `FINAL_TEST_RESULTS.json`, `tests_manifest.txt`, `FILE_LIST.txt` (+ archived copy), `scripts/live_local.env`, `uploads/`, `audit/retell_*.md`, `reports/part0*`, `reports/part1/`, and `docs/archive/{part0-reaudit,part0-initial-evidence,PART_0_REPORT_pre-reaudit.md,dashboard-static-mockups}`.
   - Compacted `reports/PART_0_REPORT.md` (`2.91 MB` -> `5.5 KB`) and `reports/PART_8_REPORT.md` (`300.8 KB` -> `4.6 KB`), well within the `<= 100 KB` `R13` cap.
2. **Guard Hardening, Repo-Size Budget & `artifacts/` Relocation (`[MODIFY]` / `[NEW]`)**:
   - Hardened `scripts/verify_no_null_bytes.py`, `scripts/verify_no_fake_success.py`, and `scripts/strip_padding_markers.py` with `.git`-less `os.walk` traversal, `--root`, and structured JSON corruption reporting (`exit 1`, zero tracebacks).
   - Added `scripts/verify_docs_paths.py` and `scripts/check_repo_size.py` and wired both into `Makefile` (`verify-truth`).
   - Relocated root test/report outputs into gitignored `artifacts/` across `scripts/validate_full_population.py`, `scripts/reconcile_validation_results.py`, `scripts/build_prompt8b_report.py`, and `scripts/validate_final_regressions.py`, and hardened `.gitignore` (`.env.check`, `.env.*`, `!*.example`, `artifacts/`, `*.a`).
3. **Single-Source Metrics (`docs/STATS.json`), Widget Label & Buyer Docs (`[NEW]` / `[MODIFY]`)**:
   - Extended `scripts/repo_stats.py` to generate `docs/STATS.json` (`voxdesk.repo_stats/v1`), update `README.md`, and sync gate statuses into `docs/SALES/PRICING_AND_TIERS.md` without hard-coded `GREEN` badges.
   - Updated `widget/embed.js` header/default title to `"Voice Widget"`, created `docs/DR_RUNBOOK.md`, and synchronized `scripts/make_due_diligence_pack.py`, `docs/DUE_DILIGENCE/{KNOWN_LIMITATIONS,README,TEST_REPORT}.md`, and `docs/SALES/{COMPETITIVE_COMPARISON,PRICING_AND_TIERS,LISTING_COPY}.md`.
   - Added permanent truth tests: `tests/truth/test_guards_report_corruption.py`, `tests/truth/test_docs_paths_exist.py`, `tests/truth/test_repo_size_budget.py`, and `tests/truth/test_stats_single_source.py`.

---

## 3. Guard Outputs (Tails) & Fresh-Export Proof

```text
$ make verify-truth
frontend/backend paths verified: 626 calls across 635 files match 1264 live routes
python3 scripts/strip_generated_tails.py --check dashboard/src dashboard-next -> []
python3 scripts/verify_no_filler.py -> []
python3 scripts/verify_no_fake_success.py -> []
python3 scripts/verify_retired_references.py -> []
python3 scripts/verify_no_null_bytes.py -> []
python3 scripts/strip_padding_markers.py --check -> {}
python3 scripts/verify_docs_paths.py -> []
python3 scripts/check_repo_size.py -> []   # --summary: ok=true, file_count=3584, tree_mib=34.68 (<= 40.0 MiB target)
python3 -m pytest tests/truth -q -> 167 passed

$ python -m compileall -q app services scripts tests alembic
(exit 0)

$ ruff check app tests scripts
All checks passed!

$ alembic heads
0069_voice_demo_sessions (head)

$ python3 scripts/verify_no_tracked_artifacts.py
PASS  no generated artifacts tracked (3719 files checked)

$ python3 scripts/verify_no_dead_routers.py
route modules: allowlisted=3, helper=8, mounted=157
no dead route modules and no filler markers

$ python3 scripts/verify_docs_match_code.py --skip-tests
documented numbers and references match the code

# Fresh .git-less export proof (/tmp/voxdesk-fresh-export without .git):
$ du -sh /tmp/voxdesk-fresh-export
45M /tmp/voxdesk-fresh-export   # 34.68 MiB logical bytes (<= 40.0 MiB target, <= 60.0 MiB ceiling)
$ python3 scripts/verify_no_null_bytes.py --root /tmp/voxdesk-fresh-export -> [] (exit 0)
$ python3 scripts/verify_no_fake_success.py --root /tmp/voxdesk-fresh-export -> [] (exit 0)
$ python3 scripts/strip_padding_markers.py --check --root /tmp/voxdesk-fresh-export -> {} (exit 0)
$ python3 scripts/verify_docs_paths.py --root /tmp/voxdesk-fresh-export -> [] (exit 0)
$ python3 scripts/check_repo_size.py --root /tmp/voxdesk-fresh-export -> [] (exit 0)
```

---

## 4. Residual Gaps (Explicitly Scoped to Later Parts)

- **Real SIP/RTP PCAP Capture & Retention**: `app/api/pcap_routes.py` and dead `pcap` purge branches were removed in PART 10; real packet-capture diagnostics return in **PART 13**.
- **Embeddable Widget Chat Surface**: `widget/embed.js` displays `"Voice Widget"` in PART 10; browser chat tab wiring ships in **PART 14**.
- **Legacy `outbound_call_routes.py` Handlers**: Verified untouched in PART 10 per specification; legacy `*_not_implemented` handlers are retired in **PART 17B**.
- **Disclosed Non-`LIVE` Matrix Rows (`docs/DUE_DILIGENCE/KNOWN_LIMITATIONS.md`)**: `#3` Direct Speech-to-Speech (`API_ONLY`), `#19` Non-Twilio Multi-Carrier Live Media (`NOT_CONFIGURED`), and `#35` External CPA/BAA Compliance Attestation (`PLANNED`).
