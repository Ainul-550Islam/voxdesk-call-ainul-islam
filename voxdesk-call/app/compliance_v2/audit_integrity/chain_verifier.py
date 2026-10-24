"""Walk the tamper-evident chain and report every break it finds.

The chain is defined by ``app.governance``: :func:`app.governance.evidence.append_event`
writes every row with a monotone ``sequence`` inside a ``chain_scope``, links it
to its predecessor through ``previous_hash``, and stores ``event_hash`` — a
``chain_hash`` over the row's own immutable envelope. This verifier re-derives
that envelope field by field and walks the rows in sequence order.

Sharing no code with the writer beyond the hashing primitives is the point: the
verifier is an independent implementation of the same rule, so a bug in one
does not hide a break from the other.

Failure modes are reported separately because they mean different things:

``payload_hash_mismatch``
    the stored ``payload_hash`` is not the hash of the stored ``payload``: a
    payload was edited without touching the chain fields.
``hash_mismatch``
    ``event_hash`` is not what the row's own fields produce: any covered field
    was edited in place.
``broken_link``
    ``previous_hash`` does not name the preceding row: a row was removed, or the
    tail was re-pointed at a different history.
``sequence_gap`` / ``sequence_restart``
    sequences skip, or two rows claim the same sequence.
``out_of_order``
    ``occurred_at`` moves backwards against sequence: timestamps were rewritten.
``head_mismatch``
    the caller's expected head hash, count or the "empty chain" case.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any, AsyncIterator, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.governance.hashing import chain_hash, sha256_hex
from app.governance.models import EvidenceEvent

#: Issue kinds, stable strings (they appear in API responses, alerts and evidence).
PAYLOAD_HASH_MISMATCH = "payload_hash_mismatch"
HASH_MISMATCH = "hash_mismatch"
BROKEN_LINK = "broken_link"
SEQUENCE_GAP = "sequence_gap"
SEQUENCE_RESTART = "sequence_restart"
OUT_OF_ORDER = "out_of_order"
HEAD_MISMATCH = "head_mismatch"
EMPTY_CHAIN = "empty_chain"
ISSUE_LIMIT = "issue_limit"


@dataclasses.dataclass(frozen=True)
class ChainIssue:
    kind: str
    sequence: int
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "sequence": self.sequence, "detail": self.detail}


@dataclasses.dataclass
class ChainVerificationReport:
    tenant_id: str
    chain_scope: str
    checked: int = 0
    ok: bool = True
    first_broken_sequence: int | None = None
    head_hash: str = ""
    head_sequence: int | None = None
    issues: list[ChainIssue] = dataclasses.field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    def add(self, issue: ChainIssue) -> None:
        self.issues.append(issue)
        self.ok = False
        if self.first_broken_sequence is None and issue.sequence:
            self.first_broken_sequence = issue.sequence

    def as_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "chain_scope": self.chain_scope,
            "verified": self.ok,
            "checked": self.checked,
            "head_sequence": self.head_sequence,
            "head_hash": self.head_hash,
            "first_broken_sequence": self.first_broken_sequence,
            "issue_count": len(self.issues),
            "issues": [issue.as_dict() for issue in self.issues],
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


def _iso(moment: dt.datetime | None = None) -> str:
    value = moment or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def chain_scope_for(tenant_id: uuid.UUID | str) -> str:
    """The scope naming used by the writer for a tenant chain."""
    return f"tenant:{tenant_id}"


def event_envelope(row: EvidenceEvent) -> dict[str, Any]:
    """The hashed envelope, built from the row's own columns."""
    return {
        "actor_id": str(row.actor_id) if row.actor_id else None,
        "actor_type": row.actor_type,
        "correlation_id": row.correlation_id,
        "subject_type": row.subject_type,
        "subject_id": row.subject_id,
        "payload_hash": row.payload_hash,
        "payload": row.payload,
    }


def recompute_event_hash(row: EvidenceEvent) -> str:
    """The ``event_hash`` the stored row should carry."""
    return chain_hash(
        previous_hash=row.previous_hash,
        payload=event_envelope(row),
        event_type=row.event_type,
        sequence=int(row.sequence),
    )


def check_row(row: EvidenceEvent, *, previous_hash: str | None, previous_sequence: int | None,
              previous_recorded_at: dt.datetime | None) -> list[ChainIssue]:
    """All structural issues for one row, given its predecessor."""
    issues: list[ChainIssue] = []
    sequence = int(row.sequence)

    if previous_sequence is None:
        if sequence != 1:
            issues.append(
                ChainIssue(
                    SEQUENCE_GAP,
                    sequence,
                    f"chain starts at sequence {sequence} instead of 1",
                )
            )
    elif sequence == previous_sequence:
        issues.append(ChainIssue(SEQUENCE_RESTART, sequence, "duplicate sequence number"))
    elif sequence != previous_sequence + 1:
        issues.append(
            ChainIssue(
                SEQUENCE_GAP,
                sequence,
                f"expected sequence {previous_sequence + 1}, found {sequence}",
            )
        )

    if (row.payload_hash or "") != sha256_hex(row.payload):
        issues.append(
            ChainIssue(PAYLOAD_HASH_MISMATCH, sequence, "payload_hash does not hash the payload")
        )

    if (row.previous_hash or None) != (previous_hash or None):
        issues.append(
            ChainIssue(BROKEN_LINK, sequence, "previous_hash does not link to the preceding row")
        )

    if recompute_event_hash(row) != (row.event_hash or ""):
        issues.append(
            ChainIssue(HASH_MISMATCH, sequence, "event_hash does not match the row's own fields")
        )

    recorded_at = _aware(row.occurred_at)
    if previous_recorded_at is not None and recorded_at is not None:
        if recorded_at < previous_recorded_at:
            issues.append(
                ChainIssue(
                    OUT_OF_ORDER, sequence, "occurred_at moves backwards against sequence"
                )
            )
    return issues


async def iter_chain(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    chain_scope: str | None = None,
    batch_size: int = 500,
    start_sequence: int = 0,
) -> AsyncIterator[EvidenceEvent]:
    """Yield rows in sequence order, one keyset page at a time.

    Keyset pagination keeps a very long chain out of memory and keeps the walk
    stable while new rows are appended: a row added during the walk is simply
    not part of this report, and the next walk picks it up.
    """
    scope = chain_scope or chain_scope_for(tenant_id)
    cursor = int(start_sequence)
    while True:
        rows = (
            await session.execute(
                select(EvidenceEvent)
                .where(EvidenceEvent.chain_scope == scope, EvidenceEvent.sequence > cursor)
                .order_by(EvidenceEvent.sequence)
                .limit(max(1, batch_size))
            )
        ).scalars().all()
        if not rows:
            return
        for row in rows:
            yield row
        cursor = int(rows[-1].sequence)


async def verify_chain(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    chain_scope: str | None = None,
    batch_size: int = 500,
    max_issues: int = 100,
    expected_head_hash: str | None = None,
    expected_count: int | None = None,
    allow_empty: bool = False,
) -> ChainVerificationReport:
    """Verify one tenant's chain and return every issue found."""
    scope = chain_scope or chain_scope_for(tenant_id)
    report = ChainVerificationReport(
        tenant_id=str(tenant_id), chain_scope=scope, started_at=_iso()
    )
    previous_hash: str | None = None
    previous_sequence: int | None = None
    previous_recorded_at: dt.datetime | None = None
    hit_limit = False

    async for row in iter_chain(
        session, tenant_id=tenant_id, chain_scope=scope, batch_size=batch_size
    ):
        report.checked += 1
        for issue in check_row(
            row,
            previous_hash=previous_hash,
            previous_sequence=previous_sequence,
            previous_recorded_at=previous_recorded_at,
        ):
            report.add(issue)
        previous_hash = row.event_hash
        previous_sequence = int(row.sequence)
        previous_recorded_at = _aware(row.occurred_at)
        report.head_hash = row.event_hash or ""
        report.head_sequence = int(row.sequence)

        if len(report.issues) >= max_issues:
            report.add(
                ChainIssue(
                    ISSUE_LIMIT,
                    int(row.sequence),
                    f"verification stopped after {max_issues} issues",
                )
            )
            hit_limit = True
            break

    if report.checked == 0:
        if allow_empty:
            report.ok = True
        else:
            report.add(ChainIssue(EMPTY_CHAIN, 0, "chain is empty"))
    if not hit_limit:
        if expected_count is not None and report.checked != int(expected_count):
            report.add(
                ChainIssue(
                    HEAD_MISMATCH,
                    report.head_sequence or 0,
                    f"expected {expected_count} rows, verified {report.checked}",
                )
            )
        if expected_head_hash and report.head_hash != expected_head_hash:
            report.add(
                ChainIssue(
                    HEAD_MISMATCH,
                    report.head_sequence or 0,
                    "head hash does not match the expected value",
                )
            )

    report.finished_at = _iso()
    return report


def verify_rows(
    rows: Sequence[EvidenceEvent], *, expected_head_hash: str | None = None
) -> ChainVerificationReport:
    """Verify an in-memory sequence of rows (bundle re-checks, tests)."""
    report = ChainVerificationReport(
        tenant_id="",
        chain_scope=str(rows[0].chain_scope) if rows else "",
        started_at=_iso(),
    )
    previous_hash: str | None = None
    previous_sequence: int | None = None
    previous_recorded_at: dt.datetime | None = None
    for row in rows:
        report.checked += 1
        for issue in check_row(
            row,
            previous_hash=previous_hash,
            previous_sequence=previous_sequence,
            previous_recorded_at=previous_recorded_at,
        ):
            report.add(issue)
        previous_hash = row.event_hash
        previous_sequence = int(row.sequence)
        previous_recorded_at = _aware(row.occurred_at)
        report.head_hash = row.event_hash or ""
        report.head_sequence = int(row.sequence)
    if expected_head_hash and report.head_hash != expected_head_hash:
        report.add(ChainIssue(HEAD_MISMATCH, report.head_sequence or 0, "head hash mismatch"))
    report.finished_at = _iso()
    return report


def summarise(report: ChainVerificationReport) -> dict[str, Any]:
    """A compact form for collectors and the SOC 2 snapshot."""
    by_kind: dict[str, int] = {}
    for issue in report.issues:
        by_kind[issue.kind] = by_kind.get(issue.kind, 0) + 1
    return {
        "verified": report.ok,
        "checked": report.checked,
        "head_sequence": report.head_sequence,
        "issue_kinds": dict(sorted(by_kind.items())),
        "first_broken_sequence": report.first_broken_sequence,
    }


__all__ = [
    "BROKEN_LINK",
    "EMPTY_CHAIN",
    "HASH_MISMATCH",
    "HEAD_MISMATCH",
    "ISSUE_LIMIT",
    "OUT_OF_ORDER",
    "PAYLOAD_HASH_MISMATCH",
    "SEQUENCE_GAP",
    "SEQUENCE_RESTART",
    "ChainIssue",
    "ChainVerificationReport",
    "chain_scope_for",
    "check_row",
    "event_envelope",
    "iter_chain",
    "recompute_event_hash",
    "summarise",
    "verify_chain",
    "verify_rows",
]
