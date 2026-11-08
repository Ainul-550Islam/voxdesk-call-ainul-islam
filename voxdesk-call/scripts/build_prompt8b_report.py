"""Build the A-Q validation report and evidence archive under gitignored artifacts/."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
E = ARTIFACTS / "prompt8b"
VALIDATION_DIR = ARTIFACTS / "prompt8-validation-final"
MANIFEST_PATH = ARTIFACTS / "tests_manifest.txt"
FINAL_RESULTS_PATH = ARTIFACTS / "FINAL_TEST_RESULTS.json"
REPORT = ARTIFACTS / "PROMPT8B_FINAL_REPORT.md"
ARCHIVE = ARTIFACTS / "PROMPT8B_FULL_CODE_AND_EVIDENCE.zip"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    E.mkdir(parents=True, exist_ok=True)
    baseline = json.loads((E / "baseline-hashes.json").read_text(encoding="utf-8"))
    previous = json.loads((VALIDATION_DIR / "modified-files.json").read_text(encoding="utf-8"))
    changed = set(previous)
    roots = ["app", "tests", "scripts", "docs", "alembic", "dashboard/src", "services/control-plane/crates"]
    for directory in roots:
        for path in (ROOT / directory).rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            relative = str(path.relative_to(ROOT))
            if (
                path.suffix in {".py", ".ts", ".tsx", ".jsx", ".js", ".rs", ".md", ".json", ".yml", ".toml"}
                and baseline.get(relative) != sha(path)
            ):
                changed.add(relative)
    changed.add("requirements-validation.txt")
    changed_list = sorted(p for p in changed if (ROOT / p).is_file())
    hashes = {p: sha(ROOT / p) for p in changed_list}
    (E / "published-file-hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")

    final = json.loads(FINAL_RESULTS_PATH.read_text(encoding="utf-8"))
    tests = final["tests"]
    counts = final["counts"]
    e2e = [r for r in tests if r["nodeid"].startswith("tests/e2e/")]
    blocked = [r for r in tests if r["status"] == "skipped"]
    resource = json.loads((E / "resource-summary.json").read_text(encoding="utf-8"))
    browser = json.loads((E / "browser-smoke.json").read_text(encoding="utf-8"))
    dash = json.loads((E / "dashboard-results.json").read_text(encoding="utf-8"))
    shadow = json.loads((E / "dashboard-next-results.json").read_text(encoding="utf-8"))
    gates = {
        "status": "PARTIAL",
        "backend": counts,
        "backend_population": final["population"],
        "dashboard": {
            "passed": dash["numPassedTests"],
            "failed": dash["numFailedTests"],
            "skipped": dash["numPendingTests"],
        },
        "dashboard_next": {
            "passed": shadow["numPassedTests"],
            "failed": shadow["numFailedTests"],
            "skipped": shadow["numPendingTests"],
        },
        "ruff": "PASS",
        "compileall": "PASS",
        "python_mypy": "FAIL: 403 errors in 125 files, 926 checked",
        "next_typescript": "PASS",
        "vite_build": "PASS",
        "next_build": "PASS",
        "postgres_migration": "PASS through 0049_runtime_schema_alignment",
        "chromium_smoke": browser["status"],
        "rust_1_90": "PASS fmt/check/test/clippy",
        "go_1_27": "PASS three service gates",
        "cpp": "PASS",
        "protobuf": "PASS",
        "pip_check": "PASS",
        "production_npm_audit": "PASS",
        "zero_skip_acceptance": False,
        "production_ready": False,
    }
    (E / "gates.json").write_text(json.dumps(gates, indent=2), encoding="utf-8")
    with (E / "placeholder-register.csv").open(encoding="utf-8") as stream:
        indicator_count = len(list(csv.DictReader(stream)))

    sections: list[str] = []

    def add(title: str, text: str) -> None:
        sections.append(f"## {title}\n\n{text.strip()}\n")

    add(
        "A. EXECUTIVE RESULT",
        f"""
PROMPT 8B — FINAL ZERO FAILURE VALIDATION

**STATUS: PARTIAL. Zero-skip / all-gates acceptance is NOT met.**

| Gate | Actual final result |
|---|---|
| Backend collection | {final['population']} unique IDs; stable final collection twice |
| Backend reported outcomes | {counts['passed']} passed; 0 failed; 0 errors; {counts['skipped']} skipped/external blockers; 0 xfailed; 0 xpassed; 0 not-run |
| Dashboard | {dash['numPassedTests']} passed / {len(dash['testResults'])} files; 0 failed/skipped/todo |
| Next dashboard | {shadow['numPassedTests']} passed / {len(shadow['testResults'])} files; 0 failed/skipped/todo |
| Implemented API E2E journeys | {len(e2e)} passed |
| Peak test RSS | {resource['max_test_process_peak_rss_kib']:,} KiB |
| Placeholder register indicators | {indicator_count} |
""",
    )
    add(
        "B. SKIPPED EXTERNAL PROVIDER CHECKS",
        "\n".join(f"- `{r['nodeid']}` — {r['failure']}" for r in blocked) or "None.",
    )

    for relative in changed_list:
        path = ROOT / relative
        text = path.read_text(encoding="utf-8")
        fence = "`" * max(4, max((len(m.group()) + 1 for m in re.finditer(r"`+", text)), default=4))
        sections.append(
            f"### File: {relative}\n\nSHA-256: `{sha(path)}`\n\n{fence}\n{text}"
            + ("" if text.endswith("\n") else "\n")
            + f"{fence}\n"
        )

    for extra_path in (MANIFEST_PATH, FINAL_RESULTS_PATH, E / "gates.json"):
        rel_label = str(extra_path.relative_to(ROOT))
        text = extra_path.read_text(encoding="utf-8")
        fence = "`" * max(4, max((len(m.group()) + 1 for m in re.finditer(r"`+", text)), default=4))
        sections.append(
            f"### Artifact: {rel_label}\n\nSHA-256: `{sha(extra_path)}`\n\n{fence}\n{text}"
            + ("" if text.endswith("\n") else "\n")
            + f"{fence}\n"
        )

    REPORT.write_text(
        "# PROMPT 8B — Full validation and repair evidence\n\nDate: 2026-10-06 (Asia/Dhaka)\n\n"
        + "\n".join(sections),
        encoding="utf-8",
    )

    archive_files: list[tuple[Path, str]] = [(ROOT / rel, rel) for rel in changed_list]
    archive_files.extend(
        [
            (REPORT, str(REPORT.relative_to(ROOT))),
            (FINAL_RESULTS_PATH, str(FINAL_RESULTS_PATH.relative_to(ROOT))),
            (MANIFEST_PATH, str(MANIFEST_PATH.relative_to(ROOT))),
        ]
    )
    for item in sorted(E.rglob("*")):
        if item.is_file():
            archive_files.append((item, str(item.relative_to(ROOT))))

    with zipfile.ZipFile(ARCHIVE, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        seen_names: set[str] = set()
        for disk_path, arc_name in archive_files:
            if arc_name in seen_names:
                continue
            seen_names.add(arc_name)
            archive.write(disk_path, arc_name)

    with zipfile.ZipFile(ARCHIVE) as archive:
        assert len(archive.namelist()) == len(set(archive.namelist()))
        for relative in changed_list:
            assert hashlib.sha256(archive.read(relative)).hexdigest() == hashes[relative]

    print(
        json.dumps(
            {
                "report_path": str(REPORT.relative_to(ROOT)),
                "archive_path": str(ARCHIVE.relative_to(ROOT)),
                "report_bytes": REPORT.stat().st_size,
                "archive_bytes": ARCHIVE.stat().st_size,
                "complete_sources": len(changed_list),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
