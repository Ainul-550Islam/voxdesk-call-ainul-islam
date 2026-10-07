"""Durable, tenant-scoped human call outcomes and aggregate reporting."""
from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import dataclass

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.auth.service import record_audit
from app.db.models import AuditAction, Base, Call, CallStatus, TransferState
from app.governance.context import GovernanceScope
from app.governance.evidence import append_event
from app.governance.hashing import sha256_hex
from app.qa.evidence import add_evidence
from app.tenancy.isolation import BoundaryDenied, Conflict, LifecycleDenied, NotFound


OUTCOME_REASONS: dict[str, frozenset[str]] = {
    "resolved": frozenset({"agent_completed_task", "human_completed_task"}),
    "unresolved": frozenset({"unable_to_resolve", "caller_disconnected", "provider_failure"}),
    "follow_up_required": frozenset({"follow_up_needed", "external_dependency"}),
    "handed_off": frozenset({"transfer_connected"}),
}
TERMINAL_CALL_STATUSES = frozenset({
    CallStatus.COMPLETED,
    CallStatus.TRANSFERRED,
    CallStatus.FAILED,
    CallStatus.NO_ANSWER,
})


def validate_outcome(outcome: str, reason_code: str) -> None:
    permitted = OUTCOME_REASONS.get(outcome)
    if permitted is None or reason_code not in permitted:
        raise LifecycleDenied("outcome and reason code are not a supported pair")


class CallOutcomeEvent(Base):
    """Versioned outcome event. Highest event_version per call is current state."""

    __tablename__ = "call_outcome_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_call_outcome_idempotency"),
        UniqueConstraint("tenant_id", "environment_id", "call_id", "event_version", name="uq_call_outcome_call_version"),
        CheckConstraint("event_version >= 1", name="ck_call_outcome_event_version"),
        CheckConstraint(
            "outcome IN ('resolved','unresolved','follow_up_required','handed_off')",
            name="ck_call_outcome_value",
        ),
        CheckConstraint(
            "reason_code IN ('agent_completed_task','human_completed_task','unable_to_resolve',"
            "'caller_disconnected','provider_failure','follow_up_needed','external_dependency',"
            "'transfer_connected')",
            name="ck_call_outcome_reason",
        ),
        Index("ix_call_outcome_call_created", "tenant_id", "environment_id", "call_id", "event_version"),
        Index("ix_call_outcome_scope_created", "tenant_id", "organization_id", "environment_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False)
    environment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("environments.id", ondelete="RESTRICT"), nullable=False)
    call_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("calls.id", ondelete="CASCADE"), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    event_version: Mapped[int] = mapped_column(Integer, nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(40), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: dt.datetime.now(dt.timezone.utc),
    )

    def as_dict(self) -> dict:
        return {
            "id": str(self.id),
            "call_id": str(self.call_id),
            "event_version": self.event_version,
            "outcome": self.outcome,
            "reason_code": self.reason_code,
            "recorded_by": str(self.actor_id),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass(frozen=True)
class OutcomeWrite:
    event: CallOutcomeEvent
    duplicate: bool


async def record_call_outcome(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    call_id: uuid.UUID,
    actor_id: uuid.UUID,
    outcome: str,
    reason_code: str,
    idempotency_key: str,
) -> OutcomeWrite:
    """Record a human classification through QA evidence, audit, and governance evidence."""
    if scope.environment_id is None:
        raise BoundaryDenied()
    validate_outcome(outcome, reason_code)
    fingerprint = sha256_hex({
        "call_id": str(call_id),
        "outcome": outcome,
        "reason_code": reason_code,
    })
    existing = await session.scalar(
        select(CallOutcomeEvent).where(
            CallOutcomeEvent.tenant_id == scope.tenant_id,
            CallOutcomeEvent.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.payload_fingerprint != fingerprint or existing.environment_id != scope.environment_id:
            raise Conflict("Idempotency key was already used for a different call outcome")
        return OutcomeWrite(existing, True)

    call = await session.scalar(
        select(Call).where(
            Call.id == call_id,
            Call.tenant_id == scope.tenant_id,
            Call.environment_id == scope.environment_id,
        ).with_for_update()
    )
    if call is None:
        raise NotFound("call not found")
    if call.status not in TERMINAL_CALL_STATUSES:
        raise LifecycleDenied("call outcome can only be recorded after the call reaches a terminal state")
    if outcome == "handed_off" and not (
        call.status is CallStatus.TRANSFERRED or call.transfer_state is TransferState.CONNECTED
    ):
        raise LifecycleDenied("handed_off requires a persisted connected transfer")
    latest_version = await session.scalar(
        select(func.max(CallOutcomeEvent.event_version)).where(
            CallOutcomeEvent.tenant_id == scope.tenant_id,
            CallOutcomeEvent.organization_id == scope.organization_id,
            CallOutcomeEvent.environment_id == scope.environment_id,
            CallOutcomeEvent.call_id == call.id,
        )
    )
    next_version = int(latest_version or 0) + 1

    row = CallOutcomeEvent(
        tenant_id=scope.tenant_id,
        organization_id=scope.organization_id,
        environment_id=scope.environment_id,
        call_id=call.id,
        actor_id=actor_id,
        event_version=next_version,
        outcome=outcome,
        reason_code=reason_code,
        idempotency_key=idempotency_key,
        payload_fingerprint=fingerprint,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:
        existing = await session.scalar(
            select(CallOutcomeEvent).where(
                CallOutcomeEvent.tenant_id == scope.tenant_id,
                CallOutcomeEvent.idempotency_key == idempotency_key,
            )
        )
        if existing is None or existing.payload_fingerprint != fingerprint or existing.environment_id != scope.environment_id:
            raise Conflict("Idempotency key was already used for a different call outcome") from None
        return OutcomeWrite(existing, True)

    await add_evidence(
        session,
        tenant_id=scope.tenant_id,
        call_id=call.id,
        evidence_type="note",
        target_kind="call_outcome",
        target_id=row.id,
    )
    await record_audit(
        session,
        action=AuditAction.RESOURCE_BOUND,
        tenant_id=scope.tenant_id,
        actor_user_id=actor_id,
        detail={
            "operation": "call_outcome.recorded",
            "call_id": str(call.id),
            "outcome_event_id": str(row.id),
            "event_version": row.event_version,
            "outcome": outcome,
            "reason_code": reason_code,
        },
        commit=False,
    )
    await append_event(
        session,
        scope,
        event_type="call_outcome_recorded",
        payload={
            "call_id": str(call.id),
            "outcome_event_id": str(row.id),
            "event_version": row.event_version,
            "outcome": outcome,
            "reason_code": reason_code,
            "payload_fingerprint": fingerprint,
        },
        actor_user_id=actor_id,
        subject_type="call",
        subject_id=str(call.id),
    )
    return OutcomeWrite(row, False)


def _outcome_window(start: dt.datetime, end: dt.datetime) -> None:
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("start and end must include a timezone")
    if start >= end:
        raise ValueError("start must be before end")
    if (end - start).days > 400:
        raise ValueError("outcome report range must be at most 400 days")


async def latest_call_outcomes(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    call_id: uuid.UUID,
    limit: int = 20,
) -> list[CallOutcomeEvent]:
    if scope.environment_id is None:
        raise BoundaryDenied()
    rows = await session.scalars(
        select(CallOutcomeEvent).where(
            CallOutcomeEvent.tenant_id == scope.tenant_id,
            CallOutcomeEvent.organization_id == scope.organization_id,
            CallOutcomeEvent.environment_id == scope.environment_id,
            CallOutcomeEvent.call_id == call_id,
        ).order_by(CallOutcomeEvent.event_version.desc()).limit(max(1, min(limit, 100)))
    )
    return list(rows.all())


async def outcome_metrics(
    session: AsyncSession,
    scope: GovernanceScope,
    *,
    start: dt.datetime,
    end: dt.datetime,
) -> dict:
    _outcome_window(start, end)
    if scope.environment_id is None:
        raise BoundaryDenied()
    ranked = (
        select(
            CallOutcomeEvent.outcome.label("outcome"),
            func.row_number().over(
                partition_by=CallOutcomeEvent.call_id,
                order_by=CallOutcomeEvent.event_version.desc(),
            ).label("row_number"),
        )
        .where(
            CallOutcomeEvent.tenant_id == scope.tenant_id,
            CallOutcomeEvent.organization_id == scope.organization_id,
            CallOutcomeEvent.environment_id == scope.environment_id,
            CallOutcomeEvent.created_at >= start,
            CallOutcomeEvent.created_at < end,
        )
        .subquery()
    )
    rows = await session.execute(
        select(ranked.c.outcome, func.count()).where(ranked.c.row_number == 1).group_by(ranked.c.outcome)
    )
    counts = {str(outcome): int(count) for outcome, count in rows.all()}
    resolved = counts.get("resolved", 0)
    unresolved = counts.get("unresolved", 0)
    classified = sum(counts.values())
    resolution_denominator = resolved + unresolved
    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "classified_calls": classified,
        "resolved": resolved,
        "unresolved": unresolved,
        "follow_up_required": counts.get("follow_up_required", 0),
        "handed_off": counts.get("handed_off", 0),
        "resolution_rate": round(resolved / resolution_denominator * 100, 1) if resolution_denominator else 0.0,
        "handoff_rate": round(counts.get("handed_off", 0) / classified * 100, 1) if classified else 0.0,
        "follow_up_rate": round(counts.get("follow_up_required", 0) / classified * 100, 1) if classified else 0.0,
        "resolution_rate_denominator": "resolved + unresolved; follow-up and handoff are excluded",
        "handoff_rate_denominator": "all latest classified calls in the period",
        "data_basis": "latest human-recorded outcome per call within the selected period",
    }
