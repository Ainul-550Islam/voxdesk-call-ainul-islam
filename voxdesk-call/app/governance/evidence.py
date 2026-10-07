"""Append-only, hash-chained governance evidence."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.identity.events import scrub

from .audit import record_governance_audit
from .exceptions import EvidenceIntegrityError
from .hashing import chain_hash, sha256_hex
from .models import EvidenceEvent


async def append_event(
    session: AsyncSession,
    scope,
    *,
    event_type: str,
    payload: dict[str, Any],
    actor_user_id: uuid.UUID | None,
    actor_type: str = "user",
    correlation_id: str | None = None,
    subject_type: str | None = None,
    subject_id: str | None = None,
    occurred_at: dt.datetime | None = None,
    commit: bool = False,
) -> EvidenceEvent:
    """Append one event without ever updating a historical event.

    The sequence and previous hash are read from the chain tail. The database
    uniqueness constraints make an unsafe concurrent append fail rather than
    silently fork or overwrite history; callers may retry the complete
    transaction when their database reports that conflict.
    """
    chain_scope = f"tenant:{scope.tenant_id}"
    tail = await session.scalar(
        select(EvidenceEvent)
        .where(EvidenceEvent.chain_scope == chain_scope)
        .order_by(EvidenceEvent.sequence.desc())
        .limit(1)
        .with_for_update()
    )
    sequence = (tail.sequence + 1) if tail is not None else 1
    previous_hash = tail.event_hash if tail is not None else None
    payload = scrub(payload)
    payload_hash = sha256_hex(payload)
    envelope = {
        "actor_id": str(actor_user_id) if actor_user_id else None,
        "actor_type": actor_type,
        "correlation_id": correlation_id,
        "subject_type": subject_type,
        "subject_id": subject_id,
        "payload_hash": payload_hash,
        "payload": payload,
    }
    event_hash = chain_hash(
        previous_hash=previous_hash,
        payload=envelope,
        event_type=event_type,
        sequence=sequence,
    )
    row = EvidenceEvent(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        environment_id=scope.environment_id,
        chain_scope=chain_scope,
        sequence=sequence,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_user_id,
        subject_type=subject_type,
        subject_id=subject_id,
        correlation_id=correlation_id,
        payload=payload,
        payload_hash=payload_hash,
        previous_hash=previous_hash,
        event_hash=event_hash,
        occurred_at=occurred_at or dt.datetime.now(dt.timezone.utc),
    )
    session.add(row)
    # Materialize the evidence ID before returning it to callers that link the
    # event to their domain record in the same transaction.
    await session.flush()
    await record_governance_audit(
        session,
        scope,
        event=event_type,
        actor_user_id=actor_user_id,
        detail={"evidence_sequence": sequence, "event_hash": event_hash},
        commit=False,
    )
    if commit:
        await session.commit()
        await session.refresh(row)
    return row


def _hash_for_event(row: EvidenceEvent) -> str:
    return chain_hash(
        previous_hash=row.previous_hash,
        payload={
            "actor_id": str(row.actor_id) if row.actor_id else None,
            "actor_type": row.actor_type,
            "correlation_id": row.correlation_id,
            "subject_type": row.subject_type,
            "subject_id": row.subject_id,
            "payload_hash": row.payload_hash,
            "payload": row.payload,
        },
        event_type=row.event_type,
        sequence=row.sequence,
    )


def verify_event_integrity(row: EvidenceEvent) -> bool:
    if row.payload_hash != sha256_hex(row.payload):
        raise EvidenceIntegrityError("Evidence payload hash does not verify")
    if row.event_hash != _hash_for_event(row):
        raise EvidenceIntegrityError("Evidence chain hash does not verify")
    return True


def verify_chain(
    rows: list[EvidenceEvent],
    *,
    expected_last_hash: str | None = None,
    expected_count: int | None = None,
) -> bool:
    """Verify ordering, links, and hashes for an already tenant-scoped list."""
    if not rows:
        raise EvidenceIntegrityError("Evidence chain is empty")
    previous: str | None = None
    expected_sequence = 1
    for row in rows:
        if row.sequence != expected_sequence:
            raise EvidenceIntegrityError("Evidence sequence is not contiguous")
        if row.previous_hash != previous:
            raise EvidenceIntegrityError("Evidence previous hash does not link")
        verify_event_integrity(row)
        previous = row.event_hash
        expected_sequence += 1
    if expected_count is not None and len(rows) != expected_count:
        raise EvidenceIntegrityError("Evidence event count does not verify")
    if expected_last_hash is not None and previous != expected_last_hash:
        raise EvidenceIntegrityError("Evidence terminal hash does not verify")
    return True


async def append_evidence(*args, **kwargs) -> EvidenceEvent:
    """Public descriptive alias for :func:`append_event`."""
    return await append_event(*args, **kwargs)


def verify_evidence_chain(
    rows: list[EvidenceEvent],
    *,
    expected_last_hash: str | None = None,
    expected_count: int | None = None,
) -> bool:
    return verify_chain(
        rows, expected_last_hash=expected_last_hash, expected_count=expected_count
    )
