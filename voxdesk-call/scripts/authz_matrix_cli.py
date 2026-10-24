#!/usr/bin/env python3
"""Run the OpenAPI-driven authorization matrix and optionally gate on gaps.

    python3 scripts/authz_matrix_cli.py                  # print the matrix
    python3 scripts/authz_matrix_cli.py --fail-on-gap    # exit 1 on any gap
    python3 scripts/authz_matrix_cli.py --json out.json

A *gap* is any of:

* a public route that is not on the reviewed allowlist;
* a ``{tenant_id}`` route reachable without an authentication dependency;
* an authenticated mutation with no permission dependency (reported as a
  review item, which ``--fail-on-gap`` also fails on — a mutation guarded only by
  "somebody is logged in" is how a self-service bug becomes a cross-account one).

Exit codes: ``0`` clean, ``1`` gaps found under ``--fail-on-gap``, ``2`` the
application could not be imported.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: Probe statuses that count as a gap when the gate is on.
GAP_STATUSES = frozenset({"FAIL", "REVIEW", "ERROR"})


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Authorization matrix over the live route table")
    parser.add_argument("--fail-on-gap", action="store_true", help="exit 1 when a gap is found")
    parser.add_argument("--json", dest="json_path", help="write the matrix to this path")
    parser.add_argument(
        "--fail-on-review",
        action="store_true",
        help="also fail on REVIEW findings (implied by --fail-on-gap)",
    )
    return parser.parse_args(argv)


def build() -> dict[str, Any]:
    # Importing the application logs to stdout through structlog. The CLI's stdout
    # is its JSON document, so the import and the run are silenced here rather
    # than left to pollute a machine-readable result.
    with contextlib.redirect_stdout(io.StringIO()):
        from security_harness import authz_matrix

        from app.main import app

        report = authz_matrix.run(app)
        snapshot = authz_matrix.matrix(app)
    return {
        "status": report.status,
        "counts": report.by_status(),
        "summary": snapshot,
        "checks": [result.as_dict() for result in report.results],
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        body = build()
    except Exception as exc:  # noqa: BLE001 - the exit code is the answer
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}", "ran": False}))
        return 2

    gaps = [
        check
        for check in body["checks"]
        if check["status"] in GAP_STATUSES
        and (args.fail_on_gap or args.fail_on_review or check["status"] in {"FAIL", "ERROR"})
    ]
    body["gap_count"] = len(gaps)
    body["gate"] = bool(args.fail_on_gap or args.fail_on_review)

    text = json.dumps(body, indent=2, sort_keys=True, default=str)
    print(text)
    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")

    if gaps and body["gate"]:
        return 1
    if any(check["status"] in {"FAIL", "ERROR"} for check in body["checks"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
