"""Build a complete, hash-verified PART 0 re-audit source and evidence delivery."""
from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "reports/PART_0_REPORT.md"
ZIP_PATH = ROOT / "reports/PART_0_MAIN_CODE_APPLIED.zip"
MANIFEST = ROOT / "reports/part0-reaudit/source-manifest.json"

FIXED_SOURCE_PATHS = {
    "Makefile",
    "README.md",
    "dashboard/package.json",
    "dashboard/package-lock.json",
    "dashboard/src/__tests__/no-generated-tails.test.ts",
    "dashboard/src/__tests__/fixtures/route-registry.json",
    "dashboard/src/app/router.tsx",
    "dashboard/src/features/public-widget/__tests__/PublicWidget.test.tsx",
    "dashboard/src/tests/use-case-detail.test.tsx",
    "dashboard/src/tests/use-cases.test.tsx",
    "dashboard/tests/knowledge.test.jsx",
    "docs/archive/dashboard-static-mockups/README.md",
    "scripts/generate_feature_matrix.py",
    "scripts/repo_stats.py",
    "scripts/strip_generated_tails.py",
    "scripts/strip_generated_tails.mjs",
    "scripts/strip_padding_markers.py",
    "scripts/verify_no_filler.py",
    "scripts/verify_retired_references.py",
    "tests/truth/routes_snapshot.json",
    "tests/truth/test_feature_evidence_dates.py",
    "tests/truth/test_generated_tails.py",
    "tests/truth/test_no_filler.py",
    "tests/truth/test_retired_references.py",
}
EVIDENCE_PATHS = {
    "reports/part0-reaudit/tail-removal-audit.json",
    "reports/part0-reaudit/unreferenced-html-audit.json",
    "reports/part0-reaudit/repo-stats-final-acceptance.json",
    "reports/part0-reaudit/truth-final-acceptance.log",
    "reports/part0-reaudit/contracts-final-acceptance.log",
    "reports/part0-reaudit/ruff-final-acceptance.log",
    "reports/part0-reaudit/route-openapi-final.log",
    "reports/part0-reaudit/compose-final-acceptance.log",
    "reports/part0-reaudit/npm-ci-final.log",
    "reports/part0-reaudit/build-acceptance-final.log",
    "reports/part0-reaudit/vitest-acceptance-final.log",
    "reports/part0-reaudit/knowledge-test-fix.log",
    "reports/part0-reaudit/tail-guard-final.log",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect() -> tuple[list[str], list[str]]:
    tail_audit = json.loads((ROOT / "reports/part0-reaudit/tail-removal-audit.json").read_text())
    source_paths = FIXED_SOURCE_PATHS | {row["path"] for row in tail_audit}
    source_paths |= {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "docs/archive/dashboard-static-mockups").rglob("*.html")
    }
    evidence_paths = EVIDENCE_PATHS
    missing = sorted(path for path in source_paths | evidence_paths if not (ROOT / path).is_file())
    if missing:
        raise FileNotFoundError("Delivery paths missing: " + ", ".join(missing))
    return sorted(source_paths), sorted(evidence_paths)


def file_record(relative: str) -> dict:
    path = ROOT / relative
    data = path.read_bytes()
    return {
        "path": relative,
        "sha256": sha256(data),
        "bytes": len(data),
        "physical_lines": len(data.decode("utf-8").splitlines()),
    }


def source_section(relative: str) -> str:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    longest = max((len(run) for run in __import__("re").findall(r"`+", text)), default=2)
    fence = "`" * max(3, longest + 1)
    language = path.suffix.lstrip(".") or "text"
    return f"\n### `{relative}`\n\n{fence}{language}\n{text.rstrip()}\n{fence}\n"


def write_report(source_paths: list[str], evidence_paths: list[str], records: list[dict], zip_hash: str) -> None:
    tail = json.loads((ROOT / "reports/part0-reaudit/tail-removal-audit.json").read_text())
    html = json.loads((ROOT / "reports/part0-reaudit/unreferenced-html-audit.json").read_text())
    stats = json.loads((ROOT / "reports/part0-reaudit/repo-stats-final-acceptance.json").read_text())
    candidate_count = sum(row["candidate_declarations_before"] for row in tail)
    pair_count = sum(row["unsupported_verification_pairs_before"] for row in tail)
    line_count = sum(row["before_lines"] - row["after_lines"] for row in tail)
    byte_count = sum(row["before_bytes"] - row["after_bytes"] for row in tail)
    lines = [
        "# PART 0 — Complete specification re-audit and generated-UI cleanup",
        "",
        f"Date: {date.today().isoformat()} (Asia/Dhaka).",
        "",
        "## Result and scope",
        "",
        "The supplied PART 0 specification was rechecked against the actual working tree. The previous report is preserved as historical material at `docs/archive/PART_0_REPORT_pre-reaudit.md`; its acceptance claim did not cover the later-discovered incomplete P0-08 generated-tail guard. This report records the new check and its real outputs. No production-readiness, provider-live, or clean-checkout claim is made.",
        "",
        "P0-08 was incomplete: the TypeScript/JavaScript tail stripper and its Vite guard test were absent; active UI source contained numbered generated exports, unsupported verification pairs, repeated placeholder comments, and unreferenced static mockups. A TypeScript-aware parser was added. It plans all edits before writing, refuses referenced or mixed declarations, refuses ambiguous namespace re-exports, supports idempotent check/apply modes, and strips only the targeted unsupported `verified`/`real` properties from object literals. A direct Babel parser dependency is locked in the dashboard package. A pre-task SHA-256 snapshot and exact before/after audit are retained under `docs/archive/part0-reaudit/pre-task-snapshots/` and `reports/part0-reaudit/tail-removal-audit.json`.",
        "",
        f"Across {len(tail)} fully snapshotted TypeScript/JavaScript files, the before/after audit reports {candidate_count:,} removable generated declarations, {pair_count:,} unsupported verification-property pairs, {line_count:,} physical lines and {byte_count:,} source bytes removed. The final scan reports zero numbered declarations and zero paired hard-coded verification claims. This audit derives counts by reading and hashing each complete file; no hand-entered estimates are used.",
        "",
        f"Fifteen HTML mockups were separately checked for exact filename references in the dashboard tree and for corresponding built output; none had references or appeared in the shipped build. They are archived intact under `docs/archive/dashboard-static-mockups/`, not deleted. The live React/TypeScript login, signup and routed pages remain. The audit records each pre-move SHA-256 at `reports/part0-reaudit/unreferenced-html-audit.json`.",
        "",
        "The final UI suite exposed an objectively ambiguous knowledge-statistics selector: the text `Ready` could resolve to the status-filter option instead of the statistics label. The test now selects `.stat__label`; it still asserts the returned value and no invented values. No assertion was deleted or weakened. No routed page, real router, truth threshold, or external-provider test was skipped or xfailed.",
        "",
        "The analyzer also found a no-op 900-iteration helper loop and comments in two tests. Only that dead generated tail was removed; all real tests remain. Identical route aliases were factored without changing order or metadata; a complete JSON route snapshot contract verifies equality. A public-widget fixture was deduplicated so the unchanged 0.35 filler threshold correctly distinguishes maintainable fixtures from repeated generated blocks.",
        "",
        "## PART 0 acceptance matrix",
        "",
        "| Requirement | Current evidence | Outcome |",
        "|---|---|---|",
        "| P0-01: retired filler-engine references | Exact repository grep excluding `docs/archive` returns no matching active file; runtime truth scans pass. | PASS in the current working tree |",
        "| P0-02: template-clone API routes/modules | `/endpoint-0` module count is zero; retired clone banners in `app/api` count is zero; live parity and Conductor routers remain mounted. | PASS in the current working tree |",
        "| P0-03: padding/banner cleanup | `strip_padding_markers.py --check` returns `{}`; legacy padding grep under `app`/`services` returns zero. | PASS |",
        "| P0-04: permanent CI truth gates | CI already contains the required `truth-guards` job; `make verify-truth` now also runs the AST-aware UI-tail check, filler, facade-history, retired-reference, null-byte and padding checks plus truth tests. | PASS |",
        "| P0-05: fallback secret | Existing truth test for production secret refusal passes; no fallback secret was added by this slice. | PASS |",
        "| P0-06: evidence-backed sales material | README numbers are regenerated from `repo_stats.py`; the only current sales table is the manifest/JUnit-generated feature matrix. Missing evidence stays API_ONLY/PLANNED; a newly generated matrix cannot refresh stale JUnit timestamps. | PASS; no LIVE claim follows |",
        "| P0-07: repository hygiene | `.gitattributes` is present; tracked/new text scan finds no null bytes; README documents scoped task commits. | PASS |",
        "| P0-08: dashboard generated tails | Parser guard, zero-marker scan, full Vite suite, route equality snapshot and production build all pass. Fifteen unused HTML artifacts were preserved outside active sources. | PASS in the current working tree |",
        "",
        "## Actual acceptance outputs",
        "",
        f"`repo_stats.py --write-readme` measured: {stats['source_files']:,} source files, {stats['source_physical_lines']:,} physical source lines, {stats['registered_routes']:,} mounted routes, and {stats['collected_pytest_nodes']:,} collected pytest nodes. Collection count is not a pass count.",
        "",
        "`make verify-truth`: 61 passed, 0 failed, 59 warnings. Warnings are Pydantic/SQLAlchemy datetime deprecations in existing code. All static gates printed an empty findings list; padding check printed `{}`.",
        "",
        "Dashboard: `npm ci` installed 153 packages; audit reported 0 vulnerabilities. `npm run build` passed (Vite noted a >500 kB chunk advisory). Full `npx vitest run`: 50 files, 575 passed, 0 failed. The added parser/route-preservation test module contributes 8 passing cases. The repaired knowledge statistic test also passed separately.",
        "",
        "`make contracts-check`: five protobuf files compile; enum vocabulary and tenant-scope checks pass. `ruff check` on all changed Python scripts/tests: All checks passed. `alembic heads`: one head, `0054_qa_runtime`.",
        "",
        "Route/OpenAPI assertion: 1,166 mounted routes; 959 OpenAPI paths; live OpenAPI exactly matches `contracts/openapi.json`; no path was added or removed by the UI cleanup. The snapshot was updated to the actually measured working-tree count. The original `reports/STEP0_BASELINE.md` explicitly says that the initial route count could not be measured because FastAPI was missing. Therefore this report does **not** claim a before/after route-count delta.",
        "",
        "Both production and full Docker Compose configurations parsed successfully with local non-secret validation placeholders; no container was started. The exact retired-reference file grep excluding `docs/archive` returned zero files. The legacy padding grep returned `0`.",
        "",
        "## Boundaries and workspace state",
        "",
        "This is measured local-workspace acceptance, not hosted deployment or provider LIVE verification. No external provider was invoked. Existing staged AB3 and unrelated working-tree changes were not reset. The route-count/OpenAPI observation includes the wider current application source present in this workspace; it is not certification that every unrelated uncommitted change is reproducible from a clean checkout. The archival pre-task source copies and prior report remain available for audit. `PART 1` acceptance beyond the already completed 1C is not certified by this re-audit.",
        "",
        f"Full-source ZIP: `reports/PART_0_MAIN_CODE_APPLIED.zip` (SHA-256 `{zip_hash}`). It contains complete source files and final evidence logs. The SHA-256 manifest is `reports/part0-reaudit/source-manifest.json`. Every source file body is reproduced below, from its first to last line, without omissions.",
        "",
        "## Complete final source files",
        "",
    ]
    lines.extend(source_section(path) for path in source_paths)
    lines.extend(["", "## Complete raw acceptance logs", ""])
    for path in evidence_paths:
        text = (ROOT / path).read_text(encoding="utf-8")
        longest = max((len(run) for run in __import__("re").findall(r"`+", text)), default=2)
        fence = "`" * max(3, longest + 1)
        lines.extend([f"### `{path}`", "", f"{fence}text", text.rstrip(), fence, ""])
    REPORT.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    source_paths, evidence_paths = collect()
    records = [file_record(path) for path in source_paths]
    evidence_records = [file_record(path) for path in evidence_paths]
    manifest = {
        "task": "PART 0 specification re-audit / P0-08 generated UI cleanup",
        "source_files": records,
        "evidence_files": evidence_records,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        archive.write(MANIFEST, MANIFEST.relative_to(ROOT).as_posix())
        for relative in source_paths + evidence_paths:
            archive.write(ROOT / relative, relative)
    with zipfile.ZipFile(ZIP_PATH) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP integrity check failed")
        for record in records + evidence_records:
            if sha256(archive.read(record["path"])) != record["sha256"]:
                raise RuntimeError("ZIP content hash mismatch: " + record["path"])
    zip_hash = sha256(ZIP_PATH.read_bytes())
    write_report(source_paths, evidence_paths, records, zip_hash)
    print(f"{len(source_paths)} complete source/archive files; {len(evidence_paths)} evidence files; ZIP SHA-256 {zip_hash}")
    print(REPORT)
    print(ZIP_PATH)


if __name__ == "__main__":
    main()
