#!/usr/bin/env python3
"""Refuse a backup whose destination contradicts the tenant's region pin.

PART 16 / P16-07. ``scripts/backup.sh`` dumps the whole database — recordings
metadata, transcripts, everything — so the dump is the artifact where a residency
mistake costs the most. This check runs *before* ``pg_dump``:

    python3 scripts/backup_target_check.py --out-dir ./backups

Exit codes, which the shell script uses:

    0  allowed — either nothing is pinned (nothing is claimed), or the pin
       matches a configured region
    3  refused — a pin exists and this deployment cannot honour it
    4  the check itself could not run (no database, bad arguments). Fail closed:
       a backup of a residency-pinned tenant is not taken on a guess.

``--region`` states where the dump will actually be written (``BACKUP_REGION`` in
the environment). It defaults to unset on purpose: "we did not say" is a refusal
for a pinned tenant, not an assumption.

This deployment's region catalogue is empty (``app.tenancy.regions`` says so), so
for a pinned tenant the honest answer today is 3. When a region is configured the
same code path returns 0 without changes — the check is the mechanism, not a
fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

EXIT_REFUSED = 3
EXIT_UNAVAILABLE = 4


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default="./backups", help="where the dump will be written")
    parser.add_argument(
        "--region",
        default=os.environ.get("BACKUP_REGION", ""),
        help="region the dump will be written to (default: BACKUP_REGION, else unset)",
    )
    parser.add_argument(
        "--tenant",
        default=os.environ.get("BACKUP_TENANT_ID", ""),
        help="tenant to check the pin for (default: BACKUP_TENANT_ID, else 'any pinned tenant')",
    )
    return parser.parse_args(argv)


async def _pins(tenant: str) -> list[tuple[str, str]]:
    """(tenant_id, pinned_region) for every tenant with a live pin."""
    from sqlalchemy import select

    from app.db.session import get_sessionmaker
    from app.governance.models import ResidencyIntent

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        query = select(ResidencyIntent).where(
            ResidencyIntent.status.in_(("requested", "verified"))
        )
        if tenant:
            query = query.where(ResidencyIntent.tenant_id == uuid.UUID(tenant))
        rows = (await session.scalars(query)).all()
    return [(str(row.tenant_id), row.requested_region) for row in rows]


def _read_pins(tenant: str) -> list[tuple[str, str]]:
    """Synchronous wrapper, so ``main`` is testable without an event loop.

    A test can replace this one name; nothing else in the check touches the DB.
    """
    return asyncio.run(_pins(tenant))


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    target = (args.region or "").strip() or None

    try:
        pins = _read_pins(args.tenant)
    except Exception as exc:  # noqa: BLE001 - the message is the useful part
        print(f"residency check unavailable: {exc}", file=sys.stderr)
        return EXIT_UNAVAILABLE

    from app.tenancy import data_residency, regions

    if not pins:
        print(
            "residency: no tenant has a region pin; the dump is written to "
            f"{args.out_dir} and no physical residency is claimed",
        )
        return 0

    refused: list[str] = []
    for tenant_id, pinned in pins:
        verdict = data_residency.check_artifact_write(
            artifact="backup", pinned=pinned, target=target
        )
        line = (
            f"residency: tenant {tenant_id} pinned {pinned}; "
            f"target {target or '(unset)'}; {verdict.reason}"
        )
        if verdict.allowed:
            print(line)
            continue
        refused.append(line)
        print(line, file=sys.stderr)

    if refused:
        print(
            "Backup refused. Configure the region catalogue "
            "(app.compliance_v2.residency.regions) and write the dump there, "
            "or move the tenant off its pin deliberately.",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    # A matched pin still is not proof of physical placement.
    print(f"residency: catalogue placement is {regions.PLACEMENT}; physical proof not claimed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
