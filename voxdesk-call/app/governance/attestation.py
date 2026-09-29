"""Evidence-backed, reproducible attestations without compliance claims."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .context import GovernanceScope
from .evidence import append_event, verify_chain
from .exceptions import GovernanceNotFound, GovernanceValidation, EvidenceIntegrityError
from .hashing import merkle_root
from .models import Attestation, EvidenceEvent

_FORBIDDEN_CLAIM_WORDS = frozenset(
    {"compliant", "certified", "certification", "soc2", "hipaa", "iso27001", "pci"}
)


def _claims_are_factual(claims: dict[str, Any]) -> None:
    lowered = {str(key).lower() for key in claims}
    if lowered & _FORBIDDEN_CLAIM_WORDS:
        raise GovernanceValidation(
            "Compliance claims require an external attestation and are not generated here"
        )


async def current_evidence(
    session: AsyncSession, scope: GovernanceScope
) -> list[EvidenceEvent]:
    rows = list(
        (
            await session.execute(
                select(EvidenceEvent)
                .where(
                    EvidenceEvent.tenant_id == scope.tenant_id,
                    EvidenceEvent.organization_id == scope.organization_id,
                )
                .order_by(EvidenceEvent.sequence.asc())
            )
        ).scalars()
    )
    if not rows:
        raise GovernanceValidation("No evidence events are available for verification")
    verify_chain(rows)
    return rows


def _range(rows: list[EvidenceEvent], start: int | None, end: int | None) -> list[EvidenceEvent]:
    chosen = [
        row for row in rows
        if (start is None or row.sequence >= start)
        and (end is None or row.sequence <= end)
    ]
    if not chosen:
        raise GovernanceValidation("At least one evidence event is required")
    return chosen


async def issue_attestation(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    subject_type: str,
    subject_id: str,
    issuer: str,
    claims: dict[str, Any],
    expires_at: dt.datetime | None,
    actor_user_id: uuid.UUID,
    attestation_type: str = "evidence_attestation",
    period_start: dt.datetime | None = None,
    period_end: dt.datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> Attestation:
    _claims_are_factual(claims)
    events = await current_evidence(session, scope)
    chosen = _range(events, None, None)
    root = merkle_root([row.event_hash for row in chosen])
    row = Attestation(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        environment_id=scope.environment_id,
        attestation_type=attestation_type.strip(),
        subject_type=subject_type.strip(),
        subject_id=subject_id.strip(),
        period_start=period_start,
        period_end=period_end,
        status="issued",
        issuer=issuer.strip(),
        issued_by=actor_user_id,
        claims={**claims, "evidence_event_count": len(events), "evidence_root": root},
        metadata_json=metadata or {},
        evidence_root=root,
        from_sequence=chosen[0].sequence,
        to_sequence=chosen[-1].sequence,
        issued_at=dt.datetime.now(dt.timezone.utc),
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    await append_event(
        session,
        scope,
        event_type="attestation_issued",
        payload={
            "attestation_id": str(row.id),
            "subject_type": row.subject_type,
            "subject_id": row.subject_id,
            "evidence_root": row.evidence_root,
        },
        actor_user_id=actor_user_id,
    )
    return row


async def generate_evidence_package(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    from_sequence: int | None = None,
    to_sequence: int | None = None,
    actor_user_id: uuid.UUID,
):
    """Build a reproducible evidence package through the package service."""
    from .packages import create_package

    return await create_package(
        session,
        scope,
        from_sequence=from_sequence,
        to_sequence=to_sequence,
        actor_user_id=actor_user_id,
    )


async def verify_attestation(
    session: AsyncSession, scope: GovernanceScope, attestation_id: uuid.UUID
) -> Attestation:
    row = await session.scalar(
        select(Attestation).where(
            Attestation.id == attestation_id,
            Attestation.tenant_id == scope.tenant_id,
            Attestation.organization_id == scope.organization_id,
        )
    )
    if row is None:
        raise GovernanceNotFound()
    events = await current_evidence(session, scope)
    chosen = _range(events, row.from_sequence, row.to_sequence)
    root = merkle_root([event.event_hash for event in chosen])
    if root != row.evidence_root:
        raise EvidenceIntegrityError("Attestation evidence root no longer verifies")
    if row.expires_at and row.expires_at <= dt.datetime.now(dt.timezone.utc):
        row.status = "expired"
    return row
