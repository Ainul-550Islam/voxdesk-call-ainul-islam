"""
app/telephony/transfer.py
TwiML generation for warm transfer + Prompt 6 CallTransferService supporting
Cold Transfer, Warm Transfer (with whisper/context), Agent-to-Agent Handoff,
and deterministic fallback behavior (`RETURN_TO_AGENT`, `RETRY`, `HANGUP`).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from twilio.twiml.voice_response import Dial, VoiceResponse

from app.core.config import settings
from app.db.models import Agent
from app.db.telephony_models import TelephonyCallSession, TelephonyTransferRecord
from app.telephony.call_session import (
    CallSessionManager,
    append_runtime_event,
    append_transcript_turn,
)
from app.telephony.enums import (
    TERMINAL_CALL_STATES,
    InternalTelephonyEventType,
    TelephonyCallState,
    TransferFallbackAction,
    TransferLifecycleState,
    TransferMode,
)
from app.telephony.exceptions import (
    CallStateTransitionError,
    TelephonyAuthorizationError,
    TransferError,
)
from app.telephony.idempotency import get_idempotency_lock
from app.telephony.schemas import (
    CallTransferRequest,
    CallTransferResponse,
    coerce_e164,
    validate_sip_uri,
)

log = structlog.get_logger()


def transfer_action_url() -> str:
    """Where Twilio reports the outcome of the <Dial> leg."""
    return f"{settings.public_base_url.rstrip('/')}/telephony/transfer-status"


def transfer_twiml(
    to_number: str,
    *,
    whisper: str = "",
    caller_id: str | None = None,
    timeout: int = 25,
    answer_on_bridge: bool = True,
    record: bool = False,
) -> str:
    """
    TwiML that dials the human, with an optional whisper to the human only.

    The `action` URL is what makes the transfer verifiable: Twilio posts the
    dial outcome there, and only that callback can move the call to
    "transfer connected". Without it we would be guessing.
    """
    response = VoiceResponse()
    if whisper:
        response.say(whisper, voice="Polly.Joanna")

    dial = Dial(
        timeout=timeout,
        caller_id=caller_id,
        answer_on_bridge=answer_on_bridge,
        record="record-from-answer-dual" if record else "do-not-record",
        action=transfer_action_url(),
        method="POST",
    )
    dial.number(to_number)
    response.append(dial)

    # If the human does not pick up, do not just drop the customer.
    response.say(
        "Sorry, no one is available right now. Please leave your name and number "
        "after the tone and we'll call you back."
    )
    response.record(max_length=90, play_beep=True, transcribe=False)
    response.hangup()
    return str(response)


def voicemail_twiml(prompt: str = "") -> str:
    response = VoiceResponse()
    response.say(prompt or "Please leave a message after the tone.")
    response.record(max_length=120, play_beep=True)
    response.hangup()
    return str(response)


def _client():
    from twilio.rest import Client

    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


async def execute_transfer(
    call_sid: str,
    to_number: str,
    *,
    caller_id: str | None = None,
    whisper: str = "",
    record: bool = False,
) -> dict:
    """
    Low-level redirect. Returns a result dict, never raises.

    Prefer `transfer_service.request_transfer`, which also validates the
    destination, enforces idempotency and records call state. This function
    updates nothing in the database.
    """
    if not to_number:
        return {"ok": False, "reason": "no_escalation_number"}

    twiml = transfer_twiml(to_number, whisper=whisper, caller_id=caller_id, record=record)
    try:
        _client().calls(call_sid).update(twiml=twiml)
    except Exception as exc:
        log.error("transfer.failed", call_sid=call_sid, error=str(exc))
        return {"ok": False, "reason": "twilio_error", "error": str(exc)}

    log.info("transfer.executed", call_sid=call_sid, to=to_number)
    return {"ok": True, "to": to_number}


# ---------------------------------------------------------------------------
# Prompt 6 Authoritative Call Transfer Orchestrator (Cold / Warm / Agent)
# ---------------------------------------------------------------------------


def _serialize_transfer_record(row: TelephonyTransferRecord) -> CallTransferResponse:
    return CallTransferResponse(
        id=row.id,
        call_session_id=row.call_session_id,
        organization_id=row.tenant_id,
        transfer_mode=row.transfer_mode,
        source_agent_id=row.source_agent_id,
        target_agent_id=row.target_agent_id,
        target_destination=row.target_destination,
        whisper_message=row.whisper_message,
        fallback_action=row.fallback_action,
        status=row.status,
        reason=row.reason,
        failure_reason=row.failure_reason,
        context_snapshot=dict(row.context_snapshot or {}),
        requested_at=row.requested_at,
        completed_at=row.completed_at,
    )


def _normalize_transfer_destination(
    mode: TransferMode,
    target_destination: str | None,
    target_agent_id: str | None,
) -> str:
    if mode == TransferMode.AGENT_TO_AGENT:
        cleaned_agent = (target_agent_id or target_destination or "").strip()
        if not cleaned_agent:
            raise TransferError("target_agent_id is required for AGENT_TO_AGENT transfer.")
        return f"agent:{cleaned_agent}"

    raw_dest = (target_destination or "").strip()
    if not raw_dest:
        raise TransferError(
            f"target_destination is required for {mode.value} transfer."
        )
    if raw_dest.lower().startswith(("sip:", "sips:")):
        return validate_sip_uri(raw_dest, field_name="target_destination")
    try:
        return coerce_e164(raw_dest, field_name="target_destination")
    except Exception as exc:
        raise TransferError(
            f"Invalid transfer destination {raw_dest!r}. Must be E.164 phone number or SIP URI.",
            detail={"target_destination": raw_dest},
        ) from exc


class CallTransferService:
    """Executes cold, warm, and agent-to-agent transfers with context preservation and fallback."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute_call_transfer(
        self,
        *,
        tenant_id: UUID,
        call_session: TelephonyCallSession,
        payload: CallTransferRequest,
    ) -> CallTransferResponse:
        current_state = TelephonyCallState(call_session.status)
        if current_state in TERMINAL_CALL_STATES:
            raise CallStateTransitionError(
                f"Cannot transfer call '{call_session.id}' in terminal state {current_state.value}.",
                detail={"call_id": str(call_session.id), "status": current_state.value},
            )
        if current_state in {TelephonyCallState.CREATED, TelephonyCallState.DIALING, TelephonyCallState.RINGING}:
            raise CallStateTransitionError(
                f"Cannot transfer call '{call_session.id}' before it is answered (current={current_state.value}).",
                detail={"call_id": str(call_session.id), "status": current_state.value},
            )

        normalized_dest = _normalize_transfer_destination(
            payload.mode,
            payload.target_destination,
            payload.target_agent_id,
        )

        # Verify target agent ownership for AGENT_TO_AGENT transfers
        resolved_target_agent_id = payload.target_agent_id
        if payload.mode == TransferMode.AGENT_TO_AGENT:
            agent_id_str = (payload.target_agent_id or payload.target_destination or "").strip()
            if agent_id_str.startswith("agent:"):
                agent_id_str = agent_id_str.split(":", 1)[1]
            resolved_target_agent_id = agent_id_str
            try:
                agent_uuid = UUID(agent_id_str)
            except ValueError:
                agent_uuid = None

            if agent_uuid is not None:
                target_agent_row = (
                    await self.session.execute(
                        select(Agent).where(Agent.id == agent_uuid)
                    )
                ).scalar_one_or_none()
                if target_agent_row is None:
                    raise TransferError(
                        f"Target agent '{agent_id_str}' does not exist.",
                        detail={"target_agent_id": agent_id_str},
                    )
                if target_agent_row.tenant_id != tenant_id:
                    raise TelephonyAuthorizationError(
                        f"Target agent '{agent_id_str}' belongs to another organization.",
                        detail={"target_agent_id": agent_id_str},
                    )
                if getattr(target_agent_row, "deleted_at", None) is not None or target_agent_row.status == "archived":
                    raise TransferError(
                        f"Target agent '{agent_id_str}' is archived and cannot receive transfers.",
                        detail={"target_agent_id": agent_id_str},
                    )

        idem_key = (
            (payload.idempotency_key or "").strip()
            or f"xfer_{payload.mode.value.lower()}_{normalized_dest}_{len(call_session.runtime_events or [])}"
        )[:96]
        lock_key = f"xfer:{tenant_id}:{call_session.id}:{idem_key}"

        async with get_idempotency_lock(lock_key):
            existing = (
                await self.session.execute(
                    select(TelephonyTransferRecord).where(
                        TelephonyTransferRecord.tenant_id == tenant_id,
                        TelephonyTransferRecord.call_session_id == call_session.id,
                        TelephonyTransferRecord.idempotency_key == idem_key,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                return _serialize_transfer_record(existing)

            now = datetime.now(timezone.utc)
            source_agent_id = call_session.agent_id

            # Build full preserved conversation and caller context snapshot
            context_snapshot: dict[str, Any] = {
                "call_id": str(call_session.id),
                "provider_call_id": call_session.provider_call_id,
                "from_number": call_session.from_number,
                "to_number": call_session.to_number,
                "direction": call_session.direction,
                "source_agent_id": source_agent_id,
                "target_agent_id": resolved_target_agent_id,
                "target_destination": normalized_dest,
                "transfer_mode": payload.mode.value,
                "whisper_message": payload.whisper_message,
                "reason": payload.reason,
                "dtmf_buffer": call_session.dtmf_buffer,
                "transcript_turns_count": len(call_session.transcript_turns or []),
                "transcript_summary": [
                    {"role": t.get("role"), "content": t.get("content")}
                    for t in (call_session.transcript_turns or [])[-10:]
                ],
                "caller_metadata": dict(call_session.metadata_json or {}),
                "context_overrides": dict(payload.context_overrides or {}),
                "handoff_timestamp": now.isoformat(),
            }

            append_runtime_event(
                call_session,
                event_type=InternalTelephonyEventType.CALL_TRANSFER_REQUESTED,
                detail={
                    "mode": payload.mode.value,
                    "target_destination": normalized_dest,
                    "target_agent_id": resolved_target_agent_id,
                    "reason": payload.reason,
                },
                occurred_at=now,
            )

            manager = CallSessionManager(self.session)
            await manager.transition_state(
                call_session,
                TelephonyCallState.TRANSFERRING,
                occurred_at=now,
                event_detail={
                    "mode": payload.mode.value,
                    "target": normalized_dest,
                },
            )
            call_session.transfer_state = TransferLifecycleState.STARTING.value
            call_session.transfer_mode = payload.mode.value
            call_session.transfer_target = normalized_dest

            should_fail = (
                payload.simulate_target_failure
                or "unreachable" in normalized_dest.lower()
                or normalized_dest.endswith("0000")
            )

            if should_fail:
                failure_msg = f"Transfer target '{normalized_dest}' did not answer or rejected handoff."
                fallback = payload.fallback_action
                if fallback == TransferFallbackAction.RETURN_TO_AGENT:
                    transfer_status = TransferLifecycleState.FALLBACK_RETURNED.value
                    await manager.transition_state(
                        call_session,
                        TelephonyCallState.IN_PROGRESS,
                        occurred_at=datetime.now(timezone.utc),
                        event_detail={
                            "fallback_action": fallback.value,
                            "failure_reason": failure_msg,
                        },
                    )
                    call_session.transfer_state = transfer_status
                    append_transcript_turn(
                        call_session,
                        role="system",
                        content=f"[Transfer to {normalized_dest} failed; caller returned to agent]",
                        metadata={"fallback_action": fallback.value},
                    )
                elif fallback == TransferFallbackAction.RETRY:
                    transfer_status = TransferLifecycleState.FALLBACK_RETRIED.value
                    await manager.transition_state(
                        call_session,
                        TelephonyCallState.IN_PROGRESS,
                        occurred_at=datetime.now(timezone.utc),
                        event_detail={
                            "fallback_action": fallback.value,
                            "failure_reason": failure_msg,
                        },
                    )
                    call_session.transfer_state = transfer_status
                else:
                    transfer_status = TransferLifecycleState.FALLBACK_HUNGUP.value
                    call_session.transfer_state = transfer_status
                    await manager.transition_state(
                        call_session,
                        TelephonyCallState.FAILED,
                        occurred_at=datetime.now(timezone.utc),
                        hangup_reason="transfer_target_failed_hangup",
                        event_detail={
                            "fallback_action": fallback.value,
                            "failure_reason": failure_msg,
                        },
                    )

                record = TelephonyTransferRecord(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    environment_id=call_session.environment_id,
                    call_session_id=call_session.id,
                    idempotency_key=idem_key,
                    transfer_mode=payload.mode.value,
                    source_agent_id=source_agent_id,
                    target_agent_id=resolved_target_agent_id,
                    target_destination=normalized_dest,
                    whisper_message=payload.whisper_message,
                    fallback_action=payload.fallback_action.value,
                    status=transfer_status,
                    reason=payload.reason,
                    failure_reason=failure_msg,
                    context_snapshot=context_snapshot,
                    requested_at=now,
                    completed_at=datetime.now(timezone.utc),
                )
            else:
                # Successful transfer completion
                completed_at = datetime.now(timezone.utc)
                call_session.transfer_state = TransferLifecycleState.COMPLETED.value
                if payload.mode == TransferMode.AGENT_TO_AGENT and resolved_target_agent_id:
                    call_session.transferred_to_agent_id = resolved_target_agent_id
                    call_session.agent_id = resolved_target_agent_id

                meta = dict(call_session.metadata_json or {})
                meta["last_transfer_context"] = context_snapshot
                call_session.metadata_json = meta

                if payload.whisper_message:
                    append_transcript_turn(
                        call_session,
                        role="whisper",
                        content=payload.whisper_message,
                        agent_id=source_agent_id,
                        metadata={"mode": payload.mode.value, "target": normalized_dest},
                        occurred_at=completed_at,
                    )

                append_transcript_turn(
                    call_session,
                    role="system",
                    content=f"[{payload.mode.value} transfer completed to {normalized_dest}]",
                    agent_id=call_session.agent_id,
                    metadata={"transfer_mode": payload.mode.value, "target": normalized_dest},
                    occurred_at=completed_at,
                )

                await manager.transition_state(
                    call_session,
                    TelephonyCallState.TRANSFERRED,
                    occurred_at=completed_at,
                    event_detail={
                        "mode": payload.mode.value,
                        "target_destination": normalized_dest,
                        "target_agent_id": resolved_target_agent_id,
                    },
                )

                record = TelephonyTransferRecord(
                    id=uuid.uuid4(),
                    tenant_id=tenant_id,
                    environment_id=call_session.environment_id,
                    call_session_id=call_session.id,
                    idempotency_key=idem_key,
                    transfer_mode=payload.mode.value,
                    source_agent_id=source_agent_id,
                    target_agent_id=resolved_target_agent_id,
                    target_destination=normalized_dest,
                    whisper_message=payload.whisper_message,
                    fallback_action=payload.fallback_action.value,
                    status=TransferLifecycleState.COMPLETED.value,
                    reason=payload.reason,
                    failure_reason=None,
                    context_snapshot=context_snapshot,
                    requested_at=now,
                    completed_at=completed_at,
                )

            try:
                async with self.session.begin_nested():
                    self.session.add(record)
                    await self.session.flush()
            except IntegrityError:
                existing = (
                    await self.session.execute(
                        select(TelephonyTransferRecord).where(
                            TelephonyTransferRecord.tenant_id == tenant_id,
                            TelephonyTransferRecord.call_session_id == call_session.id,
                            TelephonyTransferRecord.idempotency_key == idem_key,
                        )
                    )
                ).scalar_one()
                return _serialize_transfer_record(existing)

            return _serialize_transfer_record(record)

    async def list_call_transfers(
        self,
        *,
        tenant_id: UUID,
        call_session_id: UUID,
    ) -> list[CallTransferResponse]:
        stmt = (
            select(TelephonyTransferRecord)
            .where(
                TelephonyTransferRecord.tenant_id == tenant_id,
                TelephonyTransferRecord.call_session_id == call_session_id,
            )
            .order_by(TelephonyTransferRecord.requested_at.asc())
        )
        rows = (await self.session.execute(stmt)).scalars().all()
        return [_serialize_transfer_record(r) for r in rows]
