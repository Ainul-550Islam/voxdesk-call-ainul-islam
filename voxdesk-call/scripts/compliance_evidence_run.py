#!/usr/bin/env python3
"""Run the SOC 2 evidence collectors once and print a JSON summary.

    python3 scripts/compliance_evidence_run.py
    python3 scripts/compliance_evidence_run.py --tenant <uuid> --json evidence/run.json

Exit codes:

* ``0`` — every collected control is PASS or NOT_CONFIGURED;
* ``1`` — at least one control FAILed or ERRORed;
* ``2`` — the run itself could not be performed (bad arguments, database
  unreachable). Distinct from ``1`` so CI can tell "the controls are red" from
  "the tool did not run".

``NOT_CONFIGURED`` never counts as a failure here and never counts as a pass
anywhere: it means the evidence source is absent, and the summary says so.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the compliance evidence collectors once")
    parser.add_argument("--tenant", help="tenant uuid; omit to run the deployment-wide snapshot")
    parser.add_argument("--json", dest="json_path", help="also write the summary to this path")
    parser.add_argument(
        "--fail-on",
        default="FAIL,ERROR",
        help="statuses that make the command exit 1 (default: FAIL,ERROR)",
    )
    return parser.parse_args(argv)


async def _run(tenant: uuid.UUID | None) -> dict[str, Any]:
    from sqlalchemy import select

    from app.compliance_v2.soc2 import runner as evidence_runner
    from app.db.models import Tenant
    from app.db.session import get_sessionmaker

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        if tenant is not None:
            exists = await session.scalar(select(Tenant.id).where(Tenant.id == tenant))
            if exists is None:
                raise SystemExit(f"tenant {tenant} not found")
        report = await evidence_runner.run_evidence(session, tenant_id=tenant)
        await session.commit()
    return report.summary()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    tenant = uuid.UUID(args.tenant) if args.tenant else None
    fail_on = {part.strip().upper() for part in args.fail_on.split(",") if part.strip()}

    try:
        summary = asyncio.run(_run(tenant))
    except SystemExit as exc:
        print(json.dumps({"error": str(exc), "ran": False}))
        return 2
    except Exception as exc:  # noqa: BLE001 - the exit code is the report
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}", "ran": False}))
        return 2

    counts = dict(summary.get("counts") or {})
    failing = sorted(status for status in counts if status in fail_on and counts[status])
    body = {
        "ran": True,
        "tenant_id": str(tenant) if tenant else None,
        "run_id": summary.get("run_id"),
        "ok": not failing,
        "counts": counts,
        "failing_statuses": failing,
        "unmapped_controls": summary.get("unmapped_controls", []),
        "controls": summary.get("controls", {}),
        "matrix_present": summary.get("matrix_document_present"),
    }
    text = json.dumps(body, indent=2, sort_keys=True, default=str)
    print(text)
    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
    return 0 if not failing else 1


if __name__ == "__main__":
    raise SystemExit(main())
