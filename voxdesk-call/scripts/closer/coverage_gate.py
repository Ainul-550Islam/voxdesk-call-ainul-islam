#!/usr/bin/env python3
"""Coverage gate for ``app/``: a total floor plus a ratchet on untested modules.

1. Total branch coverage must be at least ``[tool.coverage.report] fail_under``
   in pyproject.toml. A missing value is a failure, not a default.
2. Every module with zero covered lines must appear in
   ``evidence/closer/zero_coverage_baseline.json``. The baseline records why each
   module is untested. A module that is not in it fails the gate, so new untested
   code cannot land silently. A baseline entry that now has coverage is reported
   as stale so the baseline can be shrunk.

Each baseline entry has a ``classification``:

* ``orphan``                - no code in app/, tests/, scripts/ or alembic/ refers to it;
* ``referenced_unexecuted`` - referenced, but no test executes it;
* ``misnamed_test``         - a ``test_*.py`` file under app/, which pytest never collects
                              because pytest.ini sets ``testpaths = tests``.

Usage:
    python scripts/closer/coverage_gate.py evidence/closer/coverage.json            # check
    python scripts/closer/coverage_gate.py evidence/closer/coverage.json --write-baseline
"""

from __future__ import annotations

import ast
import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BASELINE = REPO_ROOT / "evidence" / "closer" / "zero_coverage_baseline.json"
REFERENCE_ROOTS = ("app", "tests", "scripts", "alembic", "security_harness")


def threshold() -> float:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    value = data.get("tool", {}).get("coverage", {}).get("report", {}).get("fail_under")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SystemExit("FAIL  [tool.coverage.report] fail_under is missing or not a number")
    return float(value)


def _dotted(rel: str) -> str:
    parts = list(Path(rel).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _reference_map() -> dict[str, set[str]]:
    """Module name -> files that import it (absolute, relative and string references)."""
    files: dict[str, Path] = {}
    for root in REFERENCE_ROOTS:
        for path in (REPO_ROOT / root).rglob("*.py"):
            if "__pycache__" not in path.parts:
                files[path.relative_to(REPO_ROOT).as_posix()] = path
    refs: dict[str, set[str]] = {}
    for rel, path in files.items():
        text = path.read_text(encoding="utf-8", errors="ignore")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        package = _dotted(rel).split(".")
        if Path(rel).name != "__init__.py":
            package = package[:-1]
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    base = package[: len(package) - (node.level - 1)] if node.level > 1 else package
                    module = ".".join(base + ([node.module] if node.module else []))
                else:
                    module = node.module or ""
                names.append(module)
                names += [f"{module}.{alias.name}" for alias in node.names]
            elif isinstance(node, ast.Import):
                names += [alias.name for alias in node.names]
            for name in names:
                refs.setdefault(name, set()).add(rel)
        for match in re.findall(r"[\"'](app(?:\.\w+)+)[\"']", text):
            refs.setdefault(match, set()).add(rel)
    return refs


def zero_covered(report: dict) -> list[str]:
    out = []
    for name, body in report["files"].items():
        if not name.startswith("app/"):
            continue
        summary = body["summary"]
        if summary["num_statements"] > 0 and summary["covered_lines"] == 0:
            out.append(name)
    return sorted(out)


def classify(paths: list[str]) -> dict[str, dict[str, str]]:
    refs = _reference_map()
    result: dict[str, dict[str, str]] = {}
    for rel in paths:
        if Path(rel).name.startswith("test_"):
            result[rel] = {
                "classification": "misnamed_test",
                "reason": "test file under app/; pytest.ini testpaths = tests never collects it",
            }
            continue
        dotted = _dotted(rel)
        users = sorted(
            f
            for f in refs.get(dotted, set()) | refs.get(dotted + ".__init__", set())
            if not _dotted(f).startswith(dotted)
        )
        if users:
            result[rel] = {
                "classification": "referenced_unexecuted",
                "reason": f"referenced by {users[0]}; no test executes it",
            }
        else:
            result[rel] = {
                "classification": "orphan",
                "reason": "no code in app/, tests/, scripts/ or alembic/ refers to it",
            }
    return result


def check(report_path: Path, minimum: float) -> tuple[bool, list[str]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    percent = float(report["totals"]["percent_covered"])
    lines = [f"total branch coverage {percent:.2f}% (minimum {minimum:.2f}%)"]
    ok = percent >= minimum
    current = set(zero_covered(report))
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["modules"]
    new = sorted(current - set(baseline))
    stale = sorted(set(baseline) - current)
    if new:
        ok = False
        lines.append(
            f"FAIL  {len(new)} untested app module(s) not in the baseline: {', '.join(new[:10])}"
        )
    if stale:
        lines.append(
            f"NOTE  {len(stale)} baseline module(s) now have coverage; shrink the baseline: {', '.join(stale[:5])}"
        )
    lines.append(
        f"      zero-covered modules: {len(current)} (all recorded in the baseline: {not new})"
    )
    return ok, lines


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "report", nargs="?", default=str(REPO_ROOT / "evidence" / "closer" / "coverage.json")
    )
    parser.add_argument("--write-baseline", action="store_true")
    args = parser.parse_args(argv)
    report_path = Path(args.report)
    if args.write_baseline:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        modules = classify(zero_covered(report))
        BASELINE.write_text(
            json.dumps({"source": report_path.name, "modules": modules}, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        print(f"wrote {BASELINE.relative_to(REPO_ROOT)} with {len(modules)} module(s)")
        return 0
    ok, lines = check(report_path, threshold())
    for line in lines:
        print(line)
    print("OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
