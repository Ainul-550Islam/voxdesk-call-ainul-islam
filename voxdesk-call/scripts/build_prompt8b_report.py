"""Build the exact A-Q validation report and a lossless source/evidence archive."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E = ROOT / ".prompt8b"
REPORT = ROOT / "PROMPT8B_FINAL_REPORT.md"
ARCHIVE = ROOT / "PROMPT8B_FULL_CODE_AND_EVIDENCE.zip"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    baseline = json.loads((E / "baseline-hashes.json").read_text())
    previous = json.loads((ROOT / ".prompt8-validation-final/modified-files.json").read_text())
    changed = set(previous)
    roots = ["app", "tests", "scripts", "docs", "alembic", "dashboard/src", "services/control-plane/crates"]
    for directory in roots:
        for path in (ROOT / directory).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = str(path.relative_to(ROOT))
            if path.suffix in {".py", ".ts", ".tsx", ".jsx", ".js", ".rs", ".md", ".json", ".yml", ".toml"} and baseline.get(relative) != sha(path):
                changed.add(relative)
    changed.add("requirements-validation.txt")
    changed = sorted(p for p in changed if (ROOT / p).is_file())
    hashes = {p: sha(ROOT / p) for p in changed}
    (E / "published-file-hashes.json").write_text(json.dumps(hashes, indent=2))
    final = json.loads((ROOT / "FINAL_TEST_RESULTS.json").read_text())
    tests = final["tests"]
    counts = final["counts"]
    e2e = [r for r in tests if r["nodeid"].startswith("tests/e2e/")]
    blocked = [r for r in tests if r["status"] == "skipped"]
    resource = json.loads((E / "resource-summary.json").read_text())
    browser = json.loads((E / "browser-smoke.json").read_text())
    dash = json.loads((E / "dashboard-results.json").read_text())
    shadow = json.loads((E / "dashboard-next-results.json").read_text())
    gates = {
        "status": "PARTIAL", "backend": counts,
        "backend_population": final["population"],
        "dashboard": {"passed": dash["numPassedTests"], "failed": dash["numFailedTests"], "skipped": dash["numPendingTests"]},
        "dashboard_next": {"passed": shadow["numPassedTests"], "failed": shadow["numFailedTests"], "skipped": shadow["numPendingTests"]},
        "ruff": "PASS", "compileall": "PASS", "python_mypy": "FAIL: 403 errors in 125 files, 926 checked",
        "next_typescript": "PASS", "vite_build": "PASS", "next_build": "PASS",
        "postgres_migration": "PASS through 0049_runtime_schema_alignment", "chromium_smoke": browser["status"],
        "rust_1_90": "PASS fmt/check/test/clippy", "go_1_27": "PASS three service gates",
        "cpp": "PASS", "protobuf": "PASS", "pip_check": "PASS", "production_npm_audit": "PASS",
        "zero_skip_acceptance": False, "production_ready": False,
    }
    (E / "gates.json").write_text(json.dumps(gates, indent=2))
    with (E / "placeholder-register.csv").open() as stream:
        indicator_count = len(list(csv.DictReader(stream)))
    sections = []
    def add(title, text):
        sections.append(f"## {title}\n\n{text.strip()}\n")
    add("A. EXECUTIVE RESULT", f"""
PROMPT 8B — FINAL ZERO FAILURE VALIDATION

**STATUS: PARTIAL. Zero-skip / all-gates acceptance is NOT met.**

| Gate | Actual final result |
|---|---|
| Backend collection | {final['population']} unique IDs; stable final collection twice |
| Backend reported outcomes | {counts['passed']} passed; 0 failed; 0 errors; {counts['skipped']} skipped/external blockers; 0 xfailed; 0 xpassed; 0 not-run |
| Dashboard | {dash['numPassedTests']} passed / {len(dash['testResults'])} files; 0 failed/skipped/todo |
| Next dashboard | {shadow['numPassedTests']} passed / {len(shadow['testResults'])} files; 0 failed/skipped/todo |
| Implemented API E2E journeys | {len(e2e)} passed, plus separately classified telephony/security/integration files |
| Real Chromium smoke | PASS: real login, real persisted agent creation, 11 authenticated page visits, desktop/mobile screenshots |
| Builds / compile / configured lint | Vite, Next, Python compileall, global Ruff, Rust, Go, C++, protobuf passed |
| Native/runtime | C++ 131,558 checks; Rust 59 tests; three Go modules; five protobuf contract checks passed |
| Typecheck | **FAIL**: Python mypy retains 403 diagnostics in 125 files. Next TypeScript passed. |
| Database / migrations | PostgreSQL upgrade/downgrade/upgrade passed through 0049; 236 mapped tables have no missing mapped columns |

All {final['population']} IDs have recorded outcomes; only {counts['passed']} have passing test assertions. The 13 provider preflight skips are NOT successful live checks. The runner correctly exits nonzero for that acceptance failure. Python typecheck and incomplete implementation/placeholder review are additional internal gaps, not external credential excuses. No COMPLETE, 100% parity, or production-ready claim is made.
""")
    add("B. CURRENT BASELINE", """
Repository: `voxdesk-call-ainul-islam/voxdesk-call`; branch `main`; starting commit `573df58` (`feat: apply latest updates and fixes for voxdesk-call project`). The working tree was already extensively modified. Baseline hashes and git status were captured before this pass; inherited edits were not reset.

`PROMPT8_FINAL_REPORT.md` and `PROMPT_6_CONTINUATION_REPORT.md` were inspected as historical reports, not current validation proof. The immediately preceding repair pass accounted for 4,145 nodes, 4,132 passing and 13 credential skips. This pass adds 48 genuine regression cases: eight ownership/trigger cases, 23 external-success cases, six JSON-container cases, seven session-timezone cases and four canonical-origin cases. The final population is 4,193.

The environment snapshot did not retain the Python virtualenv, PostgreSQL binaries, Rust tools or frontend dependencies. They were restored. Validation ultimately used Python 3.12.15, matching the repository's Python 3.12 CI/Docker target; PostgreSQL 17.11; Rust 1.90.0; Go 1.27.0; pinned production Python requirements and npm lockfiles. Optional validation tools are declared in `requirements-validation.txt`.

Full baseline/current inventories, exact dependency versions and statuses are included in the evidence archive.
""")
    add("C. TEST COLLECTION", """
The initial population was collected twice without test filters. After adding the regressions and applying the migration fix, two more complete collections independently returned the same 4,193 ordered, unique node IDs. Both final collection commands exited 0.

The collection plugin records actual pytest item.nodeid values, avoiding parsing randomized console output. `tests_manifest.txt` contains one exact ID per line. No marker exclusion, deselection, ignored directory, skip addition or xfail addition was used to create the final population.

Evidence: `.prompt8b/final-collection-1.jsonl`, `.prompt8b/final-collection-2.jsonl`, their complete logs, and `tests_manifest.txt`.
""")
    add("D. TEST MANIFEST ACCOUNTING", f"""
`FINAL_TEST_RESULTS.json` contains all {final['population']} current IDs with status, duration, chunk, attempt history/count, phase outcomes, failure text, error class, RSS high-water mark, thread count and file-descriptor count. Setup/call/teardown are retained separately.

Accounting identity: **4,193 = 4,180 passed + 13 skipped + 0 failed + 0 errors + 0 xfailed + 0 xpassed + 0 not-run.** Reruns are not additional unique tests. The ledger merges a complete 325-chunk population run and 51 recorded regression chunks; 515 unique current nodes have newer regression outcomes. Earlier interrupted Python 3.13/fork experiments are preserved separately and are not substituted for current passes.

The ledger includes 376 completed chunk records with no nonzero child exit. The parent full-run acceptance exit was 1 because skips remain. Per-test durations are measured, not estimated. Two manually launched regression chunk aggregate durations are null because no independent chunk wall timer was recorded; their individual phase/test durations are present.
""")
    fixes = [
        ("B01", "62 global Ruff findings", "Preserved intentional exports, corrected lazy type-only SDK declarations and import placement", "Global Ruff PASS"),
        ("B02", "FastAPI symbol/type and optional JSON narrowing errors", "Separated model import names, explicit docs kwargs, typed identity-preserving JSON helpers, async-iterator protocol signatures", "mypy 804 initial → 403 remaining; not a green gate"),
        ("B03", "Workflow trigger accepted absent/foreign workflow IDs", "Authoritative UUID + tenant-owned Workflow lookup before insert", "Four new cases; original malformed/missing/foreign cases failed before fix"),
        ("B04", "Monitoring owner/admin check was an executable no-op", "Session owner or effective scoped OWNER/ADMIN required", "Four new cases; unauthorized manager case failed before fix"),
        ("B05", "Webhook marked delivered without HTTP", "Existing signed delivery adapter, DNS/static destination guard, observed HTTP result, redacted failure category", "Transport success/redirect/401/503, inactive/private-target and signature contracts"),
        ("B06", "Salesforce generated pseudo OAuth tokens, limits and health", "Unsupported remote operations fail closed; local connection metadata stays unverified", "15 combined Salesforce/CRM unsupported-operation cases; no manufactured completion rows"),
        ("B07", "CRM/workflow writeback claimed completed without dispatch; mappings weren't persisted", "Explicit 501 contracts; retained real local context reads", "No remote success fabricated; implementation gaps remain"),
        ("B08", "Shared client requested /api/api/agents", "Prefix-aware URL composition", "Eight added frontend path/login regressions; real browser GET /api/agents 200"),
        ("B09", "Login sent ignored next_path field", "Wire payload now uses backend LoginIn.next", "Real sanitized return to /app/overview"),
        ("B10", "PostgreSQL session auth raised aware/naive datetime TypeError", "UTC-normalized expiry/idle checks, unchanged strict expiry/revocation semantics", "Seven new cases + session/security neighboring suite: 49 passed"),
        ("B11", "Canonical same-origin deployment refresh rejected by CORS-only CSRF allowlist", "Also trust configured PUBLIC_BASE_URL; never trust request Host; cross-site still denied", "Four new canonical-origin/spoofing cases and final browser refresh 200"),
        ("B12", "Migrated PostgreSQL lacked six ORM columns and retained incompatible required legacy columns/status types", "Explicit additive 0049 revision, tenant/agent-exact version adoption, optional legacy defaults, MCP timestamp backfill/RLS restoration", "236-table column scan clean; full round trip; real API agent create 201"),
        ("B13", "Rust session formatting gate failed", "Applied rustfmt to the actual session source", "Rust 1.90 fmt/check/tests/clippy PASS"),
    ]
    table = "| ID | Root cause | Repair | Evidence |\n|---|---|---|---|\n" + "\n".join("| " + " | ".join(row) + " |" for row in fixes)
    add("E. BUG FIX REGISTER", table + "\n\nComplete modified sources are reproduced in P. Unsupported-operation guards are documented contract corrections, not implementations or artificial test greening. Existing assertions were not weakened. Four migration-head expectations were updated to the real newly implemented revision.")
    add("F. DEPENDENCY FIX REGISTER", """
| Issue | Action | Result |
|---|---|---|
| Missing snapshot dependencies | Restored exact requirements/lockfile dependencies | pip check PASS; production npm audit PASS |
| Python target mismatch | Replaced exploratory Python 3.13 execution with Python 3.12.15 | Final validation uses supported target |
| Chromium missing libnspr4 and system libraries | Installed Playwright Chromium and official browser system dependencies | Real Chromium launches and smoke passes |
| Missing polyglot tools | Installed PostgreSQL, protoc, Go 1.27, Rust 1.90 + rustfmt/clippy and C++ build tools | Actual configured language gates pass |
| Typechecker/browser tooling wasn't declared | Added separate optional requirements-validation.txt | Production dependency pins unchanged |

Pydantic 2.12.5 and FastAPI 0.135.2 were briefly measured as resource probes, did not materially solve the eager-import footprint, and were reverted to the original production pins before final runs. No downgraded security pin remains. Exact installed versions and complete installation logs are retained.
""")
    add("G. RESOURCE FIX REGISTER", f"""
The sandbox has approximately 1.98 GiB RAM. An isolated real application import measured 1,269,704 KiB peak RSS and about 50 seconds, before running a single test. PostgreSQL, SDK imports and separate validation processes can exhaust the remaining memory. A native stack sample also captured garbage collection during the initial slowdown. This is measured resource pressure, not an assertion failure.

Python 3.12 alone and dependency probes were insufficient. The final lane used sequential file-grouped workers (at most 50 nodes), one preloaded application before any DB connection/event loop/test fixture, copy-on-write fork isolation and gc.freeze. The machine was given 3 GiB swap. No test was mocked, skipped or removed by the runner. Completed evidence reports all setup/call/teardown phases.

Measured final test-process high-water maximum: **{resource['max_test_process_peak_rss_kib']:,} KiB**. Teardown observations ranged from {resource['min_teardown_threads']}–{resource['max_teardown_threads']} threads and {resource['min_teardown_open_fds']}–{resource['max_teardown_open_fds']} file descriptors. The final backend/regression logs contain no matches for the checked unclosed-client/transport, destroyed-task or ResourceWarning patterns. The deployed smoke server also completed lifespan shutdown.

This is not proof of leak-free production operation: no sustained carrier/audio load or long-running soak was performed. Process isolation contains test-global state; it does not excuse unknown production retention. The eager import footprint remains a performance concern even though all scheduled tests completed.
""")
    add("H. SKIP ELIMINATION REGISTER", "No new skips or xfails were added. Actual PostgreSQL was supplied rather than skipping its enum/recovery checks. Existing deterministic provider contracts were executed without relabeling them as live integration.\n\nThe remaining live checks were explicitly opted in with `VOXDESK_REAL_INTEGRATION=1`. They still reported the following missing-credential blockers:\n\n" + "\n".join(f"- `{r['nodeid']}` — {r['failure']}" for r in blocked) + "\n\nSupply approved credentials through a secret store and rerun these exact IDs. Do not paste secrets in chat. Converting these tests into local contract assertions would change their live-verification intent, so their skips remain visible and zero-skip acceptance remains false.")
    add("I. BACKEND TEST RESULTS", """
Final unique population: **4,193**. Passing: **4,180**. Failed/error/xfailed/xpassed/not-run: **0 each**. Live-provider credential skips: **13**.

The baseline full run was followed by changed-contract and neighboring-file regressions, including all `tests/e2e`, all `tests/integration`, all `tests/telephony`, agent/calendar/TTS/security files, new honesty regressions, session invalidation and migration compatibility. All final regression commands exited 0.

Python compileall and repository-wide `ruff check app scripts tests` passed. **`python -m mypy app` failed: 403 errors in 125 files (926 source files checked).** The complete diagnostic log is provided; no ignore-all setting, module exclusion, broad cast conversion or checker disabling was introduced.

Pytest success is evidence about assertions actually run, not proof that all 926 application files are complete or production-ready. Deprecation warnings remain visible in original logs.
""")
    add("J. DASHBOARD TEST RESULTS", f"""
| Workspace | Tests | Files | Failure/skip/todo | Build | Configured typecheck |
|---|---:|---:|---|---|---|
| dashboard (production Vite) | {dash['numPassedTests']} passed | {len(dash['testResults'])} | 0/0/0 | PASS | No canonical tsc script/config exists; Vite transpilation is not full typechecking |
| dashboard-next (shadow/roadmap) | {shadow['numPassedTests']} passed | {len(shadow['testResults'])} | 0/0/0 | PASS | `npx tsc --noEmit` PASS |

The complete production dashboard suite was rerun after the shared API path and login-wire fixes. Production dependency audit reported no vulnerability finding at the selected gate. Vite's large-bundle advisory remains; warning thresholds were not raised to hide it. Both Vitest JSON reports include exact assertions and durations.
""")
    add("K. E2E TEST RESULTS", "| Exact implemented journey | Result | Measured test seconds |\n|---|---|---:|\n" + "\n".join(f"| `{r['nodeid']}` | {r['status']} | {r['duration']:.6f} |" for r in e2e) + "\n\nThese 16 API journeys include the master persisted lifecycle and cross-module tenant boundaries. Telephony runtime/security and provider contracts are separately included in the full ledger. Deterministic simulation assertions do not establish carrier audio, live listen/whisper/takeover, settled billing or SMS delivery.\n\nReal Chromium additionally completed HTTP `/health`, `/docs`, `/openapi.json`, `/`, `/dashboard`, `/login`; actual password login and refresh; actual API draft-agent insertion; and 11 authenticated page visits including builder, testing, calls, analytics, phone numbers, settings and billing. Desktop and mobile screenshots are included. There were zero captured JavaScript exceptions and zero failed browser requests; the final server log has no HTTP 4xx/5xx responses. This is navigation/rendering smoke, not an exhaustive browser CRUD/accessibility/performance audit.")
    add("L. API / DATABASE / MIGRATION RESULTS", """
A real PostgreSQL instance was initialized and migrated, not emulated by SQLite. The complete baseline→head→base→head round trip passed on a separate disposable database, including revision `0049_runtime_schema_alignment`. The application database reports the same single head.

Crucially, successful migration execution initially hid a real ORM/schema mismatch. The first Chromium agent-create request failed because `agents.published_version_id` was absent. Revision 0049 fixes six missing mapped columns plus incompatible retained legacy defaults and status types. The post-fix scan covers **236 mapped tables and reports no missing mapped columns**. MCP row security is both enabled and forced after backfill. This scan does not certify every type/index/constraint or orphan invariant across the whole schema.

Startup ran in staging mode against the migrated database, so development create_all did not mask the drift. Fresh random identity secrets were used for the final smoke; no provider credentials were added. A real browser created its agent through the authenticated API and loaded the builder. Application startup and shutdown completed.

Migration downgrade refuses to silently relabel superseded snapshots. The successful empty-database round trip is not a claim of lossless rollback for every populated deployment. A reviewed staging-data backup/restore and downgrade plan remains necessary.
""")
    add("M. SECURITY RESULTS", f"""
Auth/RBAC, tenant boundaries, immutable version selection, webhook verification, SSRF, billing consent and integration contracts were present in the full executed population. New regressions prove trigger tenant ownership, monitoring session-owner/effective-admin enforcement, webhook transport-observed status/signature behavior, private-destination rejection, strict session expiration/revocation across timezone representations, and canonical-origin CSRF handling without trusting Host.

The final browser traverses actual JWT/session authentication, HTTP-only refresh-cookie handling and tenant-scoped APIs against PostgreSQL. No API auth dependency was mocked in that smoke. Synthetic success was removed from unsupported Salesforce/CRM/workflow operations; these now expose missing implementations instead of concealing them.

Remaining limitations include DNS rebinding between resolution and connect (requires network egress policy), no complete penetration test or live-provider security run, incomplete manual review of placeholder/exception-swallowing indicators, and substantial Python typecheck findings. The repository-wide placeholder register contains {indicator_count} indicators; conservative REVIEW_REQUIRED labels are unresolved work, not clearance.
""")
    add("N. RETELL FINAL PARITY", """
**PARTIAL; no percentage or 100% parity claim.** The existing evidence matrix/gap register remains a capability inventory, not proof that a route or UI screen implements vendor functionality. Historical execution counts and migration-head references in older audit documents are superseded by this report.

Official A/B documentation was rechecked on 2026-10-06: https://docs.retellai.com/deploy/ab-testing . Retell documents inbound/outbound call/chat traffic splitting and version-scoped analytics. VoxDesk's persisted weighted drafts are still not integrated live assignment, metrics, promotion or rollback. That gap is not closed by this test pass.

The prior official benchmark sources remain relevant: https://docs.retellai.com/test/test-overview , https://docs.retellai.com/features/live-monitoring , https://docs.retellai.com/features/analytics-dashboard , https://docs.retellai.com/integrations/crm-overview , https://docs.retellai.com/accounts/privacy-disable and https://www.retellai.com/changelog . Vendor capabilities are not implementation evidence.

Live carrier/STT/TTS/LLM operation, media monitoring/takeover, provider-backed settlement, SMS sending, complete Salesforce operations and live A/B routing remain unverified or unimplemented. The newly explicit 501 contracts improve honesty but do not constitute parity.
""")
    add("O. REMAINING GAPS", f"""
1. **External block:** 13 live-provider checks lack approved credentials. Raw pytest outcome remains skipped; zero-skip acceptance fails.
2. **Internal failure:** 403 Python mypy diagnostics across 125 files remain. Typecheck acceptance fails independently of credentials.
3. **Implementation gaps:** Unsupported Salesforce/CRM/workflow writeback/mapping/backfill operations now return 501; custom webhook headers fail closed; no new autonomous retry worker, live A/B routing, SMS delivery or media bridge is claimed.
4. **Audit completeness:** {indicator_count} placeholder/no-op/exception indicators were inventoried and conservatively classified, but full manual production review is not complete. The scan exposed and led to concrete repairs; it is not a blanket certification.
5. **Production evidence:** No approved live carrier call, processor settlement, real customer account synchronization, production deployment test, sustained load/soak, full accessibility audit or lossless populated-data downgrade has been demonstrated.
6. **Quality/performance:** The eager application import is large; deprecation/bundle warnings and pre-existing working-tree whitespace findings remain. Configured global Ruff passed; `git diff --check` is separately retained with its nonzero findings and is not described as green.

This is a completed evidence delivery for a PARTIAL result, not a statement that the requested all-green objective has been achieved. Do not release on the basis of this report alone.
""")
    add("P. COMPLETE FILE CONTENTS", f"""
The following {len(changed)} complete source/config/test/script/document files include this pass's changes plus the 22 source/helper files carried forward from the preceding repair report. Each is reproduced from its current first through last line with SHA-256; no code is represented by a patch or an elision. Legitimate language syntax in existing interfaces is not an output placeholder.

`tests_manifest.txt`, `FINAL_TEST_RESULTS.json` and the machine gate register are also reproduced in full below. Complete raw logs, phase streams, inventories, intermediate failure evidence and screenshots are preserved losslessly in **PROMPT8B_FULL_CODE_AND_EVIDENCE.zip**, at their original relative paths. The archive contains this report and exact current copies of every reproduced source. The report itself is not recursively embedded inside itself. Large unpacked execution caches may be relocated after packaging to keep the persisted workspace within its size budget; the archive is the durable evidence source.
""")
    for relative in changed + ["tests_manifest.txt", "FINAL_TEST_RESULTS.json", ".prompt8b/gates.json"]:
        path = ROOT / relative
        text = path.read_text()
        fence = "`" * max(4, max((len(m.group()) + 1 for m in re.finditer(r"`+", text)), default=4))
        sections.append(f"### File: {relative}\n\nSHA-256: `{sha(path)}`\n\n{fence}\n{text}" + ("" if text.endswith("\n") else "\n") + f"{fence}\n")
    add("Q. FINAL ACCEPTANCE CHECKLIST", """
- [x] Complete resulting source contents supplied; no output shortening, elisions, or patch-only substitutes.
- [x] No test deleted, disabled, skipped by a new marker, xfailed, or assertion weakened.
- [x] No final filtering used to conceal a failed node; all 4,193 unique current IDs accounted for.
- [x] Backend assertion failures = 0; errors = 0; xfailed/xpassed = 0; not-run = 0.
- [ ] Backend skipped = 0 — actual count is 13, externally blocked.
- [x] All production dashboard tests executed: 567 passed, zero failed/skipped/unrun.
- [x] All shadow dashboard tests executed: 76 passed, zero failed/skipped/unrun.
- [x] Dependency consistency, Python compileall and global configured Ruff passed.
- [x] Vite/Next builds and Next TypeScript passed.
- [ ] All typecheck gates passed — Python mypy remains failed.
- [x] PostgreSQL migrations, new-head round trip and post-migration browser smoke passed.
- [x] New auth, RBAC, tenant, schema and false-success regressions repaired and rerun.
- [ ] Every API/database/auth/telephony/voice/billing/workflow/CRM/analytics problem is resolved — no such blanket claim is supported.
- [x] Resource pressure investigated; complete memory-budgeted execution verified; per-node telemetry retained.
- [x] Implemented golden/cross-tenant/telephony/billing/security API E2E and final regression lanes passed.
- [x] Rust 1.90, Go 1.27, C++ and protobuf gates passed.
- [ ] Complete live-provider / production / Retell parity acceptance achieved.
- [x] Report contains actual evidence and explicitly avoids a false green claim.
""")
    REPORT.write_text("# PROMPT 8B — Full validation and repair evidence\n\nDate: 2026-10-06 (Asia/Dhaka)\n\n" + "\n".join(sections))
    entries = set(changed + [REPORT.name, "FINAL_TEST_RESULTS.json", "tests_manifest.txt"])
    entries.update(str(p.relative_to(ROOT)) for p in E.rglob("*") if p.is_file())
    with zipfile.ZipFile(ARCHIVE, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for relative in sorted(entries):
            archive.write(ROOT / relative, relative)
    with zipfile.ZipFile(ARCHIVE) as archive:
        assert len(archive.namelist()) == len(set(archive.namelist()))
        for relative in changed:
            assert hashlib.sha256(archive.read(relative)).hexdigest() == hashes[relative]
    print(json.dumps({"report_bytes": REPORT.stat().st_size, "archive_bytes": ARCHIVE.stat().st_size,
                      "complete_sources": len(changed), "archive_entries": len(entries)}, indent=2))


if __name__ == "__main__":
    main()
