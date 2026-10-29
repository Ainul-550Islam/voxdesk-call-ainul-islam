#!/usr/bin/env python3
"""Turn the polyglot CI artifacts into one honest evidence summary.

The Go, Rust and C++ services live in their own workspaces and are built and
tested by their own toolchains (``.github/workflows/polyglot.yml``). This script
reads what those jobs uploaded and writes
``reports/POLYGLOT_EVIDENCE_SUMMARY.json``.

It parses, it does not invent:

* Go  -- ``go test -json`` streams (one JSON object per line) from the
  ``signal-go``, ``ops``, ``sip-gateway`` and ``realtime/gateway-go`` services;
* Rust -- ``cargo test`` stdout from ``control-plane`` and
  ``realtime/media-engine-rs``, matched on the ``test result:`` line;
* C++  -- ``make test`` stdout from ``media-plane`` (Catch2-style
  ``N checks, M failures`` lines, or GoogleTest ``[  PASSED  ] N tests``).

A service whose artifact is missing is reported as ``missing``, and the command
exits non-zero. A service whose artifact exists but records zero tests is
reported as ``no_tests`` and also fails: an empty test run is not evidence.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

#: Filename stem -> (display name, language). Matched as a substring of the
#: artifact's path, so a CI upload directory layout does not have to match
#: exactly. Anything unrecognised is still parsed by extension and reported
#: under its filename.
KNOWN_SERVICES: tuple[tuple[str, str, str], ...] = (
    ("signal-go", "services/signal-go", "go"),
    ("ops-go", "services/ops", "go"),
    ("sip-gateway", "services/sip-gateway", "go"),
    ("gateway-go", "services/realtime/gateway-go", "go"),
    ("control-plane", "services/control-plane", "rust"),
    ("media-engine-rs", "services/realtime/media-engine-rs", "rust"),
    ("media-plane", "services/media-plane", "cpp"),
)

# Rust: "test result: ok. 12 passed; 0 failed; 0 ignored; 0 measured; ...
_RUST_RESULT = re.compile(
    r"test result:\s*(?P<outcome>ok|FAILED)\.?\s+"
    r"(?P<passed>\d+)\s+passed;\s+"
    r"(?P<failed>\d+)\s+failed;\s+"
    r"(?P<ignored>\d+)\s+ignored"
)

# C++ Catch2: "42 assertions in 3 test cases" / "All tests passed (42 assertions)"
# and the older "N checks, M failures" form used by the media-plane suites.
_CPP_CHECKS = re.compile(r"(?P<checks>\d+)\s+checks,\s+(?P<failures>\d+)\s+failures")
_CPP_ASSERTIONS = re.compile(r"(?P<assertions>\d+)\s+assertions?", re.IGNORECASE)
_GTEST_PASSED = re.compile(r"\[\s*PASSED\s*\]\s+(?P<passed>\d+)\s+test")
_GTEST_FAILED = re.compile(r"\[\s*FAILED\s*\]\s+(?P<failed>\d+)\s+test")


def classify(path: Path) -> tuple[str, str, str]:
    """Return ``(service, workspace, language)`` for an artifact path."""
    text = path.as_posix().lower()
    for stem, workspace, language in KNOWN_SERVICES:
        if stem in text:
            return stem, workspace, language
    if path.suffix == ".jsonl":
        return path.stem, "", "go"
    return path.stem, "", "unknown"


def parse_go_jsonl(path: Path) -> dict[str, Any]:
    """Parse a ``go test -json`` stream into per-test outcomes."""
    passed: list[str] = []
    failed: list[str] = []
    skipped: list[str] = []
    packages: set[str] = set()
    build_errors: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        action = str(event.get("Action") or "")
        package = str(event.get("Package") or "")
        if package:
            packages.add(package)
        test_name = str(event.get("Test") or "")
        if action in {"pass", "fail", "skip"} and test_name:
            if action == "pass":
                passed.append(test_name)
            elif action == "fail":
                failed.append(test_name)
            else:
                skipped.append(test_name)
        # A package that does not compile reports Output lines and no Test.
        if action == "fail" and not test_name:
            output = str(event.get("Output") or "").strip()
            if output:
                build_errors.append(output[:200])
    return {
        "language": "go",
        "packages": sorted(packages),
        "package_count": len(packages),
        "passed": len(passed),
        "failed": len(failed),
        "skipped": len(skipped),
        "failed_tests": failed[:20],
        "build_errors": build_errors[:5],
    }


def parse_cargo_log(path: Path) -> dict[str, Any]:
    """Parse ``cargo test`` stdout: every ``test result:`` line is a target."""
    text = path.read_text(encoding="utf-8", errors="replace")
    passed = failed = ignored = 0
    for match in _RUST_RESULT.finditer(text):
        passed += int(match.group("passed"))
        failed += int(match.group("failed"))
        ignored += int(match.group("ignored"))
    return {
        "language": "rust",
        "targets": len(_RUST_RESULT.findall(text)),
        "passed": passed,
        "failed": failed,
        "skipped": ignored,
        "failed_tests": [],
        "build_errors": [] if _RUST_RESULT.search(text) else ["no 'test result:' line in output"],
    }


def parse_cpp_log(path: Path) -> dict[str, Any]:
    """Parse ``make test`` stdout for the media-plane suites."""
    text = path.read_text(encoding="utf-8", errors="replace")
    checks = sum(int(m.group("checks")) for m in _CPP_CHECKS.finditer(text))
    check_failures = sum(int(m.group("failures")) for m in _CPP_CHECKS.finditer(text))
    gtest_passed = sum(int(m.group("passed")) for m in _GTEST_PASSED.finditer(text))
    gtest_failed = sum(int(m.group("failed")) for m in _GTEST_FAILED.finditer(text))

    if _CPP_CHECKS.search(text):
        # Catch2 summarises assertions, not test cases; "N checks, M failures"
        # is the line the polyglot workflow asserts on today.
        return {
            "language": "cpp",
            "suites": len(_CPP_CHECKS.findall(text)),
            "checks": checks,
            "passed": checks - check_failures,
            "failed": check_failures,
            "skipped": 0,
            "failed_tests": [] if check_failures == 0 else [f"{check_failures} failing check(s)"],
            "build_errors": [],
        }
    return {
        "language": "cpp",
        "suites": len(_GTEST_PASSED.findall(text)),
        "checks": max(0, sum(int(m.group("assertions")) for m in _CPP_ASSERTIONS.finditer(text))),
        "passed": gtest_passed,
        "failed": gtest_failed,
        "skipped": 0,
        "failed_tests": [] if gtest_failed == 0 else [f"{gtest_failed} failing test(s)"],
        "build_errors": [] if (gtest_passed or gtest_failed) else ["no test result in output"],
    }


def parse_artifact(path: Path) -> dict[str, Any]:
    service, workspace, language = classify(path)
    if path.suffix == ".jsonl":
        parsed = parse_go_jsonl(path)
    elif language == "rust":
        parsed = parse_cargo_log(path)
    elif language == "cpp":
        parsed = parse_cpp_log(path)
    else:
        parsed = parse_cargo_log(path)
        if parsed.get("build_errors") and not parsed["passed"]:
            parsed = parse_cpp_log(path)
    return {
        "service": service,
        "workspace": workspace,
        "artifact": _relative(path),
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
        **parsed,
    }


def _relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_summary(artifacts_dir: Path) -> dict[str, Any]:
    """Parse every artifact under ``artifacts_dir`` into one summary document."""
    if not artifacts_dir.is_dir():
        return {
            "schema": "voxdesk.polyglot_evidence/v1",
            "generated_at": _now(),
            "artifacts_dir": _relative(artifacts_dir),
            "services": [
                {
                    "service": stem,
                    "workspace": workspace,
                    "language": language,
                    "status": "missing",
                    "reason": "no artifact found",
                }
                for stem, workspace, language in KNOWN_SERVICES
            ],
            "counts": {"passed": 0, "failed": 0, "skipped": 0},
            "overall": "BLOCKED",
            "problems": [f"artifacts directory not found: {artifacts_dir}"],
        }

    files = sorted(
        p for p in artifacts_dir.rglob("*")
        if p.is_file() and p.suffix in {".jsonl", ".log", ".txt", ".json"}
    )
    services: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for path in files:
        entry = parse_artifact(path)
        existing = services.get(entry["service"])
        if existing is None:
            services[entry["service"]] = entry
            continue
        # A service may upload several artifacts (unit + integration): merge.
        for key in ("passed", "failed", "skipped"):
            existing[key] = int(existing.get(key, 0)) + int(entry.get(key, 0))
        for key in ("failed_tests", "build_errors"):
            existing[key] = list(existing.get(key, [])) + list(entry.get(key, []))
        existing["artifact"] = f"{existing['artifact']} + {entry['artifact']}"

    rows: list[dict[str, Any]] = []
    for stem, workspace, language in KNOWN_SERVICES:
        entry = services.pop(stem, None)
        if entry is None:
            rows.append(
                {
                    "service": stem,
                    "workspace": workspace,
                    "language": language,
                    "status": "missing",
                    "reason": "no artifact found for this service",
                }
            )
            problems.append(f"{stem}: no artifact")
            continue
        entry["workspace"] = entry["workspace"] or workspace
        entry["language"] = entry.get("language") or language
        passed = int(entry.get("passed", 0))
        failed = int(entry.get("failed", 0))
        if entry.get("build_errors"):
            entry["status"] = "failed"
            problems.append(f"{stem}: build errors reported")
        elif failed:
            entry["status"] = "failed"
            problems.append(f"{stem}: {failed} failing test(s)")
        elif passed == 0:
            entry["status"] = "no_tests"
            problems.append(f"{stem}: artifact records zero passing tests")
        else:
            entry["status"] = "passed"
        rows.append(entry)

    # Anything not in the known list is reported too, so a newly added service
    # shows up here instead of vanishing.
    for entry in services.values():
        passed = int(entry.get("passed", 0))
        failed = int(entry.get("failed", 0))
        entry["status"] = "failed" if failed else ("passed" if passed else "no_tests")
        if entry["status"] != "passed":
            problems.append(f"{entry['service']}: {entry['status']}")
        rows.append(entry)

    counts = {
        "passed": sum(int(r.get("passed", 0)) for r in rows),
        "failed": sum(int(r.get("failed", 0)) for r in rows),
        "skipped": sum(int(r.get("skipped", 0)) for r in rows),
    }
    return {
        "schema": "voxdesk.polyglot_evidence/v1",
        "generated_at": _now(),
        "artifacts_dir": _relative(artifacts_dir),
        "artifact_count": len(files),
        "services": rows,
        "counts": counts,
        "overall": "PASS" if not problems else "FAIL",
        "problems": problems,
    }


def _now() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=ROOT / "artifacts" / "polyglot",
        help="directory holding the Go/Rust/C++ artifacts uploaded by CI",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports" / "POLYGLOT_EVIDENCE_SUMMARY.json",
    )
    parser.add_argument(
        "--allow-missing",
        action="store_true",
        help="write the summary even when a service has no artifact (status=missing)",
    )
    args = parser.parse_args(argv)

    summary = build_summary(args.artifacts_dir.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "overall": summary["overall"],
                "services": [
                    {"service": row["service"], "status": row["status"],
                     "passed": row.get("passed", 0), "failed": row.get("failed", 0)}
                    for row in summary["services"]
                ],
                "counts": summary["counts"],
                "output": str(args.output),
            },
            indent=2,
        )
    )
    if summary["overall"] == "PASS":
        return 0
    if args.allow_missing and not any(
        row["status"] in {"failed", "no_tests"} for row in summary["services"]
    ):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
