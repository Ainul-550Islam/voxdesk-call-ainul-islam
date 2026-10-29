#!/usr/bin/env python3
"""Run the Python backend gate and record every result under evidence/closer/.

Each gate is a real subprocess. A gate passes only when its exit code is 0 and,
where it matters, its output says what was expected (one alembic head). Full output
goes to evidence/closer/logs/<gate>.log and the summary to evidence/closer/python_gate.json.

``make verify-truth`` is not part of this runner: it rewrites tracked docs and
must be run on its own and its diff reviewed.

Usage:
    DATABASE_URL=postgresql+asyncpg://USER:PASS@HOST:5432/DB python scripts/closer/python_gate.py [--only NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = REPO_ROOT / "evidence" / "closer"
PY = sys.executable

GATES: list[tuple[str, list[str], bool]] = [
    ("import_app_main", [PY, "-c", "import app.main"], False),
    ("verify_wiring", [PY, "scripts/verify_wiring.py"], False),
    ("verify_no_dead_routers", [PY, "scripts/verify_no_dead_routers.py"], False),
    ("verify_dependencies", [PY, "scripts/verify_dependencies.py"], False),
    ("verify_contracts", [PY, "scripts/verify_contracts.py"], False),
    ("alembic_heads", [PY, "-m", "alembic", "heads"], False),
    ("alembic_upgrade_head", [PY, "-m", "alembic", "upgrade", "head"], True),
    ("autogenerate_empty", [PY, "scripts/closer/autogenerate_empty_check.py"], True),
    ("enum_members", [PY, "scripts/closer/enum_members_check.py"], False),
    (
        "ruff",
        ["ruff", "check", "app", "tests", "scripts", "security_harness", "--select", "E4,E7,E9,F"],
        False,
    ),
    (
        "coverage_gate",
        [PY, "scripts/closer/coverage_gate.py", "evidence/closer/coverage.json"],
        False,
    ),
    (
        "closer_tests",
        [
            PY,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/closer",
            "tests/telephony/test_callback_retry_job.py",
        ],
        False,
    ),
]


def _single_head(output: str) -> bool:
    heads = [line for line in output.splitlines() if "(head)" in line]
    return len(heads) == 1


def run_gate(name: str, argv: list[str], needs_db: bool) -> dict:
    env = dict(os.environ)
    if needs_db and not env.get("DATABASE_URL"):
        return {
            "gate": name,
            "status": "fail",
            "exit_code": None,
            "detail": "DATABASE_URL is not set",
        }
    started = time.monotonic()
    proc = subprocess.run(argv, cwd=REPO_ROOT, env=env, capture_output=True, text=True)
    elapsed = round(time.monotonic() - started, 1)
    output = proc.stdout + proc.stderr
    logs = EVIDENCE / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{name}.log").write_text(output, encoding="utf-8")
    ok = proc.returncode == 0
    detail = ""
    if name == "alembic_heads":
        ok = ok and _single_head(output)
        detail = "exactly one head" if ok else "expected exactly one '(head)' line"
    return {
        "gate": name,
        "status": "pass" if ok else "fail",
        "exit_code": proc.returncode,
        "seconds": elapsed,
        "detail": detail,
        "log": f"evidence/closer/logs/{name}.log",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args(argv)
    selected = [g for g in GATES if args.only is None or g[0] in args.only]
    results = [run_gate(*gate) for gate in selected]
    summary = {
        "scope": "python-backend",
        "git_head": subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
        ).stdout.strip(),
        "gates": results,
        "all_passed": all(r["status"] == "pass" for r in results),
    }
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / "python_gate.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    for r in results:
        print(
            f"{r['status'].upper():4}  {r['gate']}  exit={r.get('exit_code')}  {r.get('detail', '')}"
        )
    return 0 if summary["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
