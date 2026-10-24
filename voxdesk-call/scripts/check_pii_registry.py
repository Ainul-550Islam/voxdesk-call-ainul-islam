#!/usr/bin/env python3
"""Fail when a column that holds personal data is missing from the DSAR registry.

Two independent checks, because they catch different mistakes:

1. :func:`app.compliance_v2.dsar.registry.check_registry` validates the registry
   itself — every registered table and column must exist in ``Base.metadata``,
   every PHI-tagged column must be registered, and every table on the required
   list must have an entry.
2. This script's own inventory scan looks at the *metadata*: any column whose
   name is a recognised personal-data column, in a tenant-scoped table, must be
   covered by a registry entry (as an identifying field or as a listed column) or
   appear in :data:`REVIEWED_EXCLUSIONS` with a stated reason.

The scan is deliberately independent of the registry's own list; a registry that
checks itself proves only that it is self-consistent.

    python3 scripts/check_pii_registry.py
    python3 scripts/check_pii_registry.py --json      # machine-readable output

Exit codes: ``0`` clean, ``1`` coverage gaps, ``2`` the check could not run.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: Column names that carry personal data in this schema. Deliberately narrow:
#: a strong identifier or a direct name, not anything that merely looks similar
#: (``email_sent_at`` is not personal data, ``email`` is).
PII_COLUMNS: frozenset[str] = frozenset(
    {
        "address",
        "caller_number",
        "contact_name",
        "customer_number",
        "customer_phone",
        "date_of_birth",
        "email",
        "first_name",
        "from_number",
        "full_name",
        "last_name",
        "phone",
        "phone_number",
        "ssn",
        "to_number",
    }
)

#: (table, column) -> why it is not in the DSAR registry.
#:
#: An exclusion is a decision someone made and must justify. Two justifications
#: are acceptable here: the column is not personal data at all (an operator's
#: configured business number, a tenant-level support address), or the value is a
#: secret rather than subject data. Anything else belongs in the registry.
REVIEWED_EXCLUSIONS: dict[tuple[str, str], str] = {}

#: Columns that look like personal data but are a secret or an operator
#: setting, keyed by ``table.column``. Empty today: every personal-data column in
#: the metadata is covered by the registry, which is the state this check exists
#: to keep.

def _iter_metadata_columns() -> list[tuple[str, str]]:
    """Every (table, column) in the live metadata that looks like personal data."""
    import app.db.models  # noqa: F401  (registers the model set)
    from app.db.models import Base

    hits: list[tuple[str, str]] = []
    for table in Base.metadata.tables.values():
        if "tenant_id" not in table.columns:
            # A table without a tenant column is not reachable through a
            # tenant-scoped subject request; it is still reported by the
            # registry check when it is on the required list.
            continue
        for column in table.columns:
            if column.name in PII_COLUMNS:
                hits.append((table.name, column.name))
    return sorted(hits)


def _coverage_findings() -> list[str]:
    from app.compliance_v2.dsar.registry import REGISTRY_BY_TABLE

    findings: list[str] = []
    for table, column in _iter_metadata_columns():
        if (table, column) in REVIEWED_EXCLUSIONS:
            continue
        entry = REGISTRY_BY_TABLE.get(table)
        if entry is None:
            findings.append(
                f"{table}.{column} looks like personal data but {table!r} is not in the "
                "DSAR registry"
            )
            continue
        covered = column in entry.columns or any(
            field.column == column for field in entry.fields
        )
        if not covered:
            findings.append(
                f"{table}.{column} looks like personal data but is not a registered field "
                f"of registry entry {entry.name!r}"
            )
    return findings


def _stale_exclusions() -> list[str]:
    """Exclusions that no longer match reality are worse than none at all."""
    live = set(_iter_metadata_columns())
    return [
        f"exclusion {table}.{column} no longer exists in the metadata"
        for table, column in sorted(REVIEWED_EXCLUSIONS)
        if (table, column) not in live
    ]


def run() -> dict[str, Any]:
    from app.db.models import Base

    from app.compliance_v2.dsar.registry import REGISTRY, check_registry

    registry_problems = check_registry(Base.metadata)
    coverage = _coverage_findings()
    stale = _stale_exclusions()
    return {
        "ok": not (registry_problems or coverage or stale),
        "registered_tables": sorted(entry.table for entry in REGISTRY),
        "registry_problems": registry_problems,
        "coverage_gaps": coverage,
        "stale_exclusions": stale,
        "scanned_columns": len(_iter_metadata_columns()),
        "exclusions": [
            {"table": table, "column": column, "reason": reason}
            for (table, column), reason in sorted(REVIEWED_EXCLUSIONS.items())
        ],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the DSAR/PII registry against the models")
    parser.add_argument("--json", dest="json_path", help="also write the report to this path")
    args = parser.parse_args(argv)

    try:
        body = run()
    except Exception as exc:  # noqa: BLE001 - the exit code is the answer
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}", "ran": False}))
        return 2

    text = json.dumps(body, indent=2, sort_keys=True, default=str)
    print(text)
    if args.json_path:
        target = Path(args.json_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text + "\n", encoding="utf-8")
    return 0 if body["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
