"""
app/telephony/call_session.py
Explicit CallSession state machine, persistence helpers, transcript turn logging,
and deterministic terminal usage finalization.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.telephony_models import TelephonyCallSession
from app.services.telephony_usage import TelephonyUsageService
from app.telephony.enums import (
    TERMINAL_CALL_STATES,
    InternalTelephonyEventType,
    MediaSessionState,
    TelephonyCallState,
)
from app.telephony.exceptions import (
    CallSessionNotFoundError,
    CallStateTransitionError,
)
from app.telephony.schemas import CallSessionListResponse, CallSessionResponse

ALLOWED_CALL_STATE_TRANSITIONS: dict[TelephonyCallState, frozenset[TelephonyCallState]] = {
    TelephonyCallState.CREATED: frozenset(
        {
            TelephonyCallState.DIALING,
            TelephonyCallState.RINGING,
            TelephonyCallState.ANSWERED,
            TelephonyCallState.IN_PROGRESS,
            TelephonyCallState.ENDING,
            TelephonyCallState.COMPLETED,
            TelephonyCallState.FAILED,
            TelephonyCallState.CANCELLED,
            TelephonyCallState.BUSY,
            TelephonyCallState.NO_ANSWER,
            TelephonyCallState.VOICEMAIL,
        }
    ),
    TelephonyCallState.DIALING: frozenset(
        {
            TelephonyCallState.RINGING,
            TelephonyCallState.ANSWERED,
            TelephonyCallState.IN_PROGRESS,
            TelephonyCallState.ENDING,
            TelephonyCallState.COMPLETED,
            TelephonyCallState.FAILED,
            TelephonyCallState.CANCELLED,
            TelephonyCallState.BUSY,
            TelephonyCallState.NO_ANSWER,
            TelephonyCallState.VOICEMAIL,
        }
    ),
    TelephonyCallState.RINGING: frozenset(
        {
            TelephonyCallState.ANSWERED,
            TelephonyCallState.IN_PROGRESS,
            TelephonyCallState.ENDING,
            TelephonyCallState.COMPLETED,
            TelephonyCallState.FAILED,
            TelephonyCallState.CANCELLED,
            TelephonyCallState.BUSY,
            TelephonyCallState.NO_ANSWER,
            TelephonyCallState.VOICEMAIL,
        }
    ),
    TelephonyCallState.ANSWERED: frozenset(
        {
            TelephonyCallState.IN_PROGRESS,
            TelephonyCallState.TRANSFERRING,
            TelephonyCallState.TRANSFERRED,
            TelephonyCallState.ENDING,
            TelephonyCallState.COMPLETED,
            TelephonyCallState.FAILED,
            TelephonyCallState.CANCELLED,
            TelephonyCallState.VOICEMAIL,
        }
    ),
    TelephonyCallState.IN_PROGRESS: frozenset(
        {
            TelephonyCallState.TRANSFERRING,
            TelephonyCallState.TRANSFERRED,
            TelephonyCallState.ENDING,
            TelephonyCallState.COMPLETED,
            TelephonyCallState.FAILED,
            TelephonyCallState.CANCELLED,
        }
    ),
    TelephonyCallState.TRANSFERRING: frozenset(
        {
            TelephonyCallState.TRANSFERRED,
            TelephonyCallState.IN_PROGRESS,
            TelephonyCallState.ENDING,
            TelephonyCallState.COMPLETED,
            TelephonyCallState.FAILED,
            TelephonyCallState.CANCELLED,
        }
    ),
    TelephonyCallState.TRANSFERRED: frozenset(
        {
            TelephonyCallState.IN_PROGRESS,
            TelephonyCallState.TRANSFERRING,
            TelephonyCallState.ENDING,
            TelephonyCallState.COMPLETED,
            TelephonyCallState.FAILED,
            TelephonyCallState.CANCELLED,
        }
    ),
    TelephonyCallState.ENDING: frozenset(
        {
            TelephonyCallState.COMPLETED,
            TelephonyCallState.FAILED,
            TelephonyCallState.CANCELLED,
        }
    ),
    # Terminal states allow no outgoing transitions to non-identical states
    TelephonyCallState.COMPLETED: frozenset(),
    TelephonyCallState.FAILED: frozenset(),
    TelephonyCallState.CANCELLED: frozenset(),
    TelephonyCallState.BUSY: frozenset(),
    TelephonyCallState.NO_ANSWER: frozenset(),
    TelephonyCallState.VOICEMAIL: frozenset(),
}


def validate_call_state_transition(
    current: TelephonyCallState | str,
    target: TelephonyCallState | str,
) -> tuple[TelephonyCallState, TelephonyCallState, bool]:
    """Validate transition `(current -> target)`. Returns `(current_enum, target_enum, is_noop)`."""
    curr_enum = TelephonyCallState(current)
    targ_enum = TelephonyCallState(target)
    if curr_enum == targ_enum:
        return curr_enum, targ_enum, True
    allowed = ALLOWED_CALL_STATE_TRANSITIONS.get(curr_enum, frozenset())
    if targ_enum not in allowed:
        raise CallStateTransitionError(
            f"Illegal call state transition: {curr_enum.value} -> {targ_enum.value}",
            detail={
                "current_state": curr_enum.value,
                "target_state": targ_enum.value,
                "allowed_targets": sorted(s.value for s in allowed),
            },
        )
    return curr_enum, targ_enum, False


def serialize_call_session(row: TelephonyCallSession) -> CallSessionResponse:
    return CallSessionResponse(
        id=row.id,
        organization_id=row.tenant_id,
        environment_id=row.environment_id,
        legacy_call_id=row.legacy_call_id,
        phone_number_id=row.phone_number_id,
        agent_id=row.agent_id,
        agent_version_number=row.agent_version_number,
        provider=row.provider,
        provider_call_id=row.provider_call_id,
        direction=row.direction,
        from_number=row.from_number,
        to_number=row.to_number,
        status=row.status,
        media_state=row.media_state,
        transfer_state=row.transfer_state,
        transfer_mode=row.transfer_mode,
        transfer_target=row.transfer_target,
        transferred_to_agent_id=row.transferred_to_agent_id,
        parent_call_id=row.parent_call_id,
        is_simulation=bool(row.is_simulation),
        execution_kind=row.execution_kind,
        started_at=row.started_at,
        answered_at=row.answered_at,
        ended_at=row.ended_at,
        duration_ms=int(row.duration_ms or 0),
        billable_seconds=int(row.billable_seconds or 0),
        usage_finalized=bool(row.usage_finalized),
        hangup_reason=row.hangup_reason,
        recording_reference=row.recording_reference,
        transcript_reference=row.transcript_reference,
        transcript_turns=list(row.transcript_turns or []),
        dtmf_buffer=row.dtmf_buffer or "",
        dtmf_events=list(row.dtmf_events or []),
        runtime_events=list(row.runtime_events or []),
        metadata=dict(row.metadata_json or {}),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def append_runtime_event(
    row: TelephonyCallSession,
    *,
    event_type: InternalTelephonyEventType | str,
    detail: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    ts = (occurred_at or datetime.utcnow()).isoformat()
    evt_str = (
        event_type.value
        if isinstance(event_type, InternalTelephonyEventType)
        else str(event_type)
    )
    entry = {
        "event_id": f"rt_{uuid.uuid4().hex[:12]}",
        "event_type": evt_str,
        "state": row.status,
        "timestamp": ts,
        "detail": dict(detail or {}),
    }
    events = list(row.runtime_events or [])
    events.append(entry)
    row.runtime_events = events
    return entry


def append_transcript_turn(
    row: TelephonyCallSession,
    *,
    role: str,
    content: str,
    agent_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> dict[str, Any]:
    turns = list(row.transcript_turns or [])
    entry = {
        "turn_index": len(turns),
        "role": role,
        "content": content,
        "timestamp": (occurred_at or datetime.utcnow()).isoformat(),
        "agent_id": agent_id or row.agent_id,
        "metadata": dict(metadata or {}),
    }
    turns.append(entry)
    row.transcript_turns = turns
    row.transcript_reference = f"transcript://calls/{row.id}#turns={len(turns)}"
    return entry


class CallSessionManager:
    """Manages durable `TelephonyCallSession` rows and enforces state machine invariants."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_call_session(
        self,
        *,
        tenant_id: UUID,
        call_id: UUID,
    ) -> TelephonyCallSession:
        row = (
            await self.session.execute(
                select(TelephonyCallSession).where(
                    TelephonyCallSession.id == call_id,
                    TelephonyCallSession.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise CallSessionNotFoundError(
                f"Call session '{call_id}' not found in this organization.",
                detail={"call_id": str(call_id)},
            )
        return row

    async def find_by_provider_call_id(
        self,
        *,
        provider: str,
        provider_call_id: str,
        tenant_id: UUID | None = None,
    ) -> TelephonyCallSession | None:
        stmt = select(TelephonyCallSession).where(
            TelephonyCallSession.provider == provider.upper().strip(),
            TelephonyCallSession.provider_call_id == provider_call_id.strip(),
        )
        if tenant_id is not None:
            stmt = stmt.where(TelephonyCallSession.tenant_id == tenant_id)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_call_sessions(
        self,
        *,
        tenant_id: UUID,
        environment_id: UUID | None = None,
        status: str | None = None,
        direction: str | None = None,
        agent_id: str | None = None,
        limit: int = 50,
    ) -> CallSessionListResponse:
        stmt = select(TelephonyCallSession).where(
            TelephonyCallSession.tenant_id == tenant_id
        )
        if environment_id is not None:
            stmt = stmt.where(
                (TelephonyCallSession.environment_id == environment_id)
                | (TelephonyCallSession.environment_id.is_(None))
            )
        if status:
            stmt = stmt.where(TelephonyCallSession.status == status.upper().strip())
        if direction:
            stmt = stmt.where(TelephonyCallSession.direction == direction.upper().strip())
        if agent_id:
            stmt = stmt.where(TelephonyCallSession.agent_id == agent_id.strip())
        stmt = stmt.order_by(TelephonyCallSession.created_at.desc()).limit(
            max(1, min(limit, 200))
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        items = [serialize_call_session(r) for r in rows]
        return CallSessionListResponse(items=items, total=len(items))

    async def transition_state(
        self,
        row: TelephonyCallSession,
        target_state: TelephonyCallState | str,
        *,
        occurred_at: datetime | None = None,
        hangup_reason: str | None = None,
        provider_duration_seconds: int | None = None,
        recording_reference: str | None = None,
        event_detail: dict[str, Any] | None = None,
    ) -> TelephonyCallSession:
        now = occurred_at or datetime.utcnow()
        curr_enum, targ_enum, is_noop = validate_call_state_transition(
            row.status, target_state
        )

        if recording_reference:
            row.recording_reference = recording_reference
        if hangup_reason:
            row.hangup_reason = hangup_reason

        if is_noop:
            if targ_enum in TERMINAL_CALL_STATES and not row.usage_finalized:
                usage_svc = TelephonyUsageService(self.session)
                await usage_svc.finalize_call_usage(
                    call_session=row,
                    provider_duration_seconds=provider_duration_seconds,
                )
            await self.session.flush()
            return row

        row.status = targ_enum.value
        row.updated_at = now

        if targ_enum in {
            TelephonyCallState.DIALING,
            TelephonyCallState.RINGING,
        }:
            if row.started_at is None:
                row.started_at = now

        if targ_enum in {
            TelephonyCallState.ANSWERED,
            TelephonyCallState.IN_PROGRESS,
        }:
            if row.started_at is None:
                row.started_at = now
            if row.answered_at is None:
                row.answered_at = now

        if targ_enum in TERMINAL_CALL_STATES:
            if row.started_at is None:
                row.started_at = now
            if row.ended_at is None:
                row.ended_at = now
            row.media_state = MediaSessionState.DISCONNECTED.value

        event_map: dict[TelephonyCallState, InternalTelephonyEventType] = {
            TelephonyCallState.RINGING: InternalTelephonyEventType.CALL_RINGING,
            TelephonyCallState.ANSWERED: InternalTelephonyEventType.CALL_ANSWERED,
            TelephonyCallState.IN_PROGRESS: InternalTelephonyEventType.CALL_STARTED,
            TelephonyCallState.TRANSFERRING: InternalTelephonyEventType.CALL_TRANSFER_STARTED,
            TelephonyCallState.TRANSFERRED: InternalTelephonyEventType.CALL_TRANSFER_COMPLETED,
            TelephonyCallState.COMPLETED: InternalTelephonyEventType.CALL_ENDED,
            TelephonyCallState.FAILED: InternalTelephonyEventType.CALL_FAILED,
            TelephonyCallState.CANCELLED: InternalTelephonyEventType.CALL_ENDED,
            TelephonyCallState.BUSY: InternalTelephonyEventType.CALL_FAILED,
            TelephonyCallState.NO_ANSWER: InternalTelephonyEventType.CALL_FAILED,
            TelephonyCallState.VOICEMAIL: InternalTelephonyEventType.CALL_VOICEMAIL,
        }
        evt_type = event_map.get(targ_enum, InternalTelephonyEventType.CALL_STARTED)
        append_runtime_event(
            row,
            event_type=evt_type,
            detail={
                "from_state": curr_enum.value,
                "to_state": targ_enum.value,
                **(event_detail or {}),
            },
            occurred_at=now,
        )

        if targ_enum in TERMINAL_CALL_STATES:
            usage_svc = TelephonyUsageService(self.session)
            await usage_svc.finalize_call_usage(
                call_session=row,
                provider_duration_seconds=provider_duration_seconds,
            )

        await self.session.flush()
        return row
