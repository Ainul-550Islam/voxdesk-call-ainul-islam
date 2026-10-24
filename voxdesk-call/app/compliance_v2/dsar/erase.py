"""Erasure and anonymisation of a data subject's records.

The registry decides which tables hold subject data and how each one is treated;
this module performs the erasure and then *proves* it.

Two properties matter more than speed here:

* **idempotence** — running an erasure twice must leave the second run with
  nothing to do and must not fail. A retried DSAR request is normal; a second
  erasure that raises is not.
* **a verification pass** — after the rows are touched, the same matchers run
  again and must find no rows still attributable to the subject. The verification
  is what turns "we called delete()" into "there is nothing left", and it is
  recorded in the request's ledger.

Legal hold and retention win over erasure: a hold blocks the whole request, and a
table inside its retention window is reported as ``retained`` rather than
silently skipped.
"""

from __future__ import annotations

import dataclasses
import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance_v2.dsar.export import (
    EXPORT_ROW_LIMIT,
    CollectedTable,
    DsarError,
    Subject,
    collect,
    digits_of,
)
from app.compliance_v2.dsar.registry import (
    STRATEGY_ANONYMIZE,
    STRATEGY_DELETE,
    STRATEGY_RETAIN,
    RegistryEntry,
)

#: Ledger action names. Stable strings: they appear in signed bundles and in the
#: operator's eyes after an incident, so they are never renamed casually.
ACTION_DELETE = "delete"
ACTION_ANONYMIZE = "anonymize"
ACTION_NO_ROWS = "no_rows"
ACTION_RETAINED = "retained"
ACTION_BLOCKED = "blocked_legal_hold"

_BOUNDARY = re.compile(r"[^0-9]")


@dataclasses.dataclass
class ErasureResult:
    """One table's outcome, written verbatim into the request ledger."""

    table: str
    action: str
    rows: int
    detail: dict[str, Any] = dataclasses.field(default_factory=dict)


def _replacement_map(entry: RegistryEntry) -> dict[str, Any]:
    return {
        field.column: field.replacement
        for field in entry.fields
        if field.replacement
    }


def _scrub_free_text(row: Any, entry: RegistryEntry) -> None:
    """Remove the subject's own identifiers from free-text columns.

    A call summary can quote the caller's number even though the column is not
    the phone column, so every non-identifying text column in the entry is
    scrubbed of the matched identifiers — and only of those; the rest of the
    operational record is left intact so the business keeps its history.
    """
    identifiers: list[str] = []
    for field in entry.fields:
        if not field.identifying or not field.replacement:
            continue
        value = getattr(row, field.replacement, "")
        if isinstance(value, str) and digits_of(value):
            identifiers.append(digits_of(value))
    if not identifiers:
        return
    identifying = {field.column for field in entry.fields if field.identifying}
    for field in entry.fields:
        if field.column in identifying:
            continue
        value = getattr(row, field.column, None)
        if not isinstance(value, str) or not value:
            continue
        scrubbed = value
        for identifier in identifiers:
            scrubbed = re.sub(re.escape(identifier), "[erased]", scrubbed)
        if scrubbed != value:
            setattr(row, field.column, scrubbed)


async def erase_subject(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    subject: Subject,
    legal_hold: bool,
    retained_tables: frozenset[str] = frozenset(),
    limit: int = EXPORT_ROW_LIMIT,
) -> list[ErasureResult]:
    """Anonymise or delete every row the registry attributes to the subject.

    ``legal_hold`` blocks the erasure outright — the caller lifts the hold first.
    Tables named in ``retained_tables`` are inside their retention window and are
    reported as retained rather than touched, so the requester's ledger explains
    exactly what remains and why.
    """
    if legal_hold:
        return [ErasureResult(table="*", action=ACTION_BLOCKED, rows=0)]

    collected = await collect(session, tenant_id=tenant_id, subject=subject, limit=limit)
    results: list[ErasureResult] = []

    # Turns first: deleting a call's turns before the call itself keeps the
    # foreign-key order valid without depending on database cascade behaviour.
    ordered = sorted(collected, key=lambda table: 0 if table.entry.table == "turns" else 1)
    for table in ordered:
        entry = table.entry
        if entry.strategy == STRATEGY_RETAIN:
            results.append(
                ErasureResult(
                    table=entry.table,
                    action=ACTION_RETAINED,
                    rows=0,
                    detail={"reason": entry.join_note or "retained under legal obligation"},
                )
            )
            continue
        if entry.table in retained_tables:
            results.append(
                ErasureResult(
                    table=entry.table,
                    action=ACTION_RETAINED,
                    rows=0,
                    detail={"reason": "inside retention window"},
                )
            )
            continue
        if not table.rows:
            results.append(
                ErasureResult(table=entry.table, action=ACTION_NO_ROWS, rows=0)
            )
            continue
        if entry.strategy == STRATEGY_DELETE:
            for row in table.rows:
                await session.delete(row)
            results.append(
                ErasureResult(table=entry.table, action=ACTION_DELETE, rows=len(table.rows))
            )
            continue
        if entry.strategy != STRATEGY_ANONYMIZE:
            raise DsarError(f"unknown erasure strategy {entry.strategy!r} for {entry.table}")
        replacements = _replacement_map(entry)
        for row in table.rows:
            for column, value in replacements.items():
                setattr(row, column, value)
            _scrub_free_text(row, entry)
        results.append(
            ErasureResult(
                table=entry.table,
                action=ACTION_ANONYMIZE,
                rows=len(table.rows),
                detail={"columns": sorted(replacements)},
            )
        )
    await session.flush()
    return results


async def verify_erasure(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    subject: Subject,
    retained_tables: frozenset[str] = frozenset(),
    limit: int = EXPORT_ROW_LIMIT,
) -> dict[str, Any]:
    """Re-run the matchers and report anything still attributable to the subject.

    A row in a ``delete`` table that still matches is a failure. A row in an
    ``anonymize`` table still matches only if a replacement value itself matched
    the subject key, which would mean the registry's replacement is wrong — also
    a failure. Retained tables are reported but do not fail the pass, because
    retention is a decision the requester was told about.
    """
    collected = await collect(session, tenant_id=tenant_id, subject=subject, limit=limit)
    remaining: dict[str, int] = {}
    retained: dict[str, int] = {}
    for table in collected:
        mismatched = _still_attributable(table, subject)
        if not mismatched:
            continue
        if (
            table.entry.table in retained_tables
            or table.entry.strategy == STRATEGY_RETAIN
        ):
            retained[table.entry.table] = mismatched
        else:
            remaining[table.entry.table] = mismatched
    return {
        "ok": not remaining,
        "remaining": remaining,
        "retained": retained,
        "checked_tables": [table.entry.table for table in collected],
    }


def _still_attributable(table: CollectedTable, subject: Subject) -> int:
    """Count rows in ``table`` that still carry the subject's key.

    ``collect`` already filtered by the matcher, so every returned row is a
    candidate; the extra check is that the surrogate the matcher matched on still
    equals the subject's — a row whose phone was replaced by ``[erased]`` no
    longer matches, but a row left untouched does.
    """
    if not table.rows:
        return 0
    subject_digits = digits_of(subject.key)
    count = 0
    for row in table.rows:
        if table.entry.strategy == STRATEGY_DELETE:
            count += 1
            continue
        if table.entry.strategy == STRATEGY_RETAIN:
            count += 1
            continue
        still_there = False
        for field in table.entry.fields:
            if not field.identifying:
                continue
            value = getattr(row, field.column, None)
            if not isinstance(value, str):
                continue
            if subject.subject_type == "contact":
                if digits_of(value) and digits_of(value) == subject_digits:
                    still_there = True
            elif value.strip().lower() == subject.key.strip().lower():
                still_there = True
        if still_there:
            count += 1
    return count


__all__ = [
    "ACTION_ANONYMIZE",
    "ACTION_BLOCKED",
    "ACTION_DELETE",
    "ACTION_NO_ROWS",
    "ACTION_RETAINED",
    "ErasureResult",
    "erase_subject",
    "verify_erasure",
]
