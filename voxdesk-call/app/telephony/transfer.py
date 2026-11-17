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
from app.db.models import Agent, Tenant
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


def transfer_whisper_url(call_id: UUID | str) -> str:
    """Where Twilio fetches private, AMD-gated instructions for the human leg."""
    return (
        f"{settings.public_base_url.rstrip('/')}/telephony/transfer-whisper"
        f"?call_id={call_id}"
    )


def transfer_session_target_url(call_session_id: UUID | str, transfer_id: UUID | str) -> str:
    """TwiML URL for a transfer belonging to the production call-session runtime."""
    return (
        f"{settings.public_base_url.rstrip('/')}/telephony/transfer-session-target"
        f"?call_session_id={call_session_id}&transfer_id={transfer_id}"
    )


def transfer_session_status_url(call_session_id: UUID | str, transfer_id: UUID | str) -> str:
    """Signed Twilio <Dial action> endpoint for a production call-session transfer."""
    return (
        f"{settings.public_base_url.rstrip('/')}/telephony/transfer-session-status"
        f"?call_session_id={call_session_id}&transfer_id={transfer_id}"
    )


def transfer_session_target_status_url(
    call_session_id: UUID | str, transfer_id: UUID | str
) -> str:
    """Progress callback for the separately-created warm-transfer target leg."""
    return (
        f"{settings.public_base_url.rstrip('/')}/telephony/transfer-session-target-status"
        f"?call_session_id={call_session_id}&transfer_id={transfer_id}"
    )


def transfer_session_conference_url(
    call_session_id: UUID | str, transfer_id: UUID | str
) -> str:
    """Conference participant lifecycle callback for a production transfer."""
    return (
        f"{settings.public_base_url.rstrip('/')}/telephony/transfer-session-conference"
        f"?call_session_id={call_session_id}&transfer_id={transfer_id}"
    )


def transfer_hold_music_url() -> str:
    """Public WAV served by the signed-independent Twilio hold-music asset route."""
    return f"{settings.public_base_url.rstrip('/')}/telephony/transfer-hold-music"


def session_transfer_twiml(
    to_number: str,
    *,
    call_session_id: UUID | str,
    transfer_id: UUID | str,
    caller_id: str | None,
    require_human: bool = True,
    timeout: int = 25,
) -> str:
    """Direct Twilio transfer TwiML with a private AMD-gated target webhook.

    The callback response controls the customer leg after the Dial finishes;
    it must return TwiML (not a plain-text acknowledgement) so a failed attempt
    can resume the existing media stream.
    """
    if not to_number:
        raise ValueError("transfer destination is required")
    response = VoiceResponse()
    dial = Dial(
        timeout=timeout,
        caller_id=caller_id,
        answer_on_bridge=True,
        action=transfer_session_status_url(call_session_id, transfer_id),
        method="POST",
    )
    dial.number(
        to_number,
        url=transfer_session_target_url(call_session_id, transfer_id),
        method="POST",
        machine_detection="Enable" if require_human else None,
    )
    response.append(dial)
    return str(response)


def conference_caller_twiml(
    conference_name: str,
    *,
    wait_url: str,
    status_callback_url: str,
) -> str:
    """Redirect the customer leg into a moderated conference with hold treatment."""
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", conference_name or ""):
        raise ValueError("invalid conference name")
    response = VoiceResponse()
    dial = Dial()
    dial.conference(
        conference_name,
        start_conference_on_enter=False,
        end_conference_on_exit=True,
        wait_url=wait_url or "",
        wait_method="GET",
        beep="false",
        participant_label="caller",
        status_callback=status_callback_url,
        status_callback_event="start end join leave",
        status_callback_method="POST",
    )
    response.append(dial)
    return str(response)


def conference_target_twiml(
    conference_name: str,
    *,
    briefing_text: str = "",
    status_callback_url: str,
) -> str:
    """Brief only the target, then join the customer's conference."""
    import re

    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", conference_name or ""):
        raise ValueError("invalid conference name")
    response = VoiceResponse()
    if briefing_text:
        response.say(briefing_text[:500], voice="Polly.Joanna")
    dial = Dial()
    dial.conference(
        conference_name,
        start_conference_on_enter=True,
        end_conference_on_exit=False,
        wait_url="",
        wait_method="GET",
        beep="false",
        participant_label="transfer-target",
        status_callback=status_callback_url,
        status_callback_event="start end join leave",
        status_callback_method="POST",
    )
    response.append(dial)
    return str(response)


def transfer_twiml(
    to_number: str,
    *,
    whisper: str = "",
    caller_id: str | None = None,
    timeout: int = 25,
    answer_on_bridge: bool = True,
    record: bool = False,
    whisper_url: str | None = None,
    require_human: bool = False,
) -> str:
    """
    TwiML that dials the human and, when configured, privately briefs that leg.

    Twilio fetches ``whisper_url`` after the callee answers and before it
    bridges the two parties. Human-required transfers fail closed unless that
    endpoint is present, because AMD must be checked before the caller joins.
    The ``action`` URL reports the dial outcome to our webhook.
    """
    response = VoiceResponse()
    if require_human and not whisper_url:
        raise ValueError("human verification requires a called-party TwiML URL")
    if whisper and not whisper_url:
        raise ValueError("private transfer whisper requires a called-party TwiML URL")

    dial = Dial(
        timeout=timeout,
        caller_id=caller_id,
        answer_on_bridge=answer_on_bridge,
        record="record-from-answer-dual" if record else "do-not-record",
        action=transfer_action_url(),
        method="POST",
    )
    dial.number(
        to_number,
        url=whisper_url,
        method="POST" if whisper_url else None,
        machine_detection="Enable" if require_human else None,
    )
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

    try:
        twiml = transfer_twiml(
            to_number, whisper=whisper, caller_id=caller_id, record=record
        )
    except ValueError:
        # This low-level helper has no Call row from which to build the signed
        # called-party URL; never put a private whisper on the caller's leg.
        return {"ok": False, "reason": "private_whisper_unavailable"}

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

    async def _execute_production_twilio_transfer_v2(
        self,
        *,
        tenant_id: UUID,
        call_session: TelephonyCallSession,
        payload: CallTransferRequest,
        normalized_dest: str,
        resolved_target_agent_id: str | None,
        idem_key: str,
    ) -> CallTransferResponse:
        """Execute the supported Twilio subset for a real CallSession runtime call.

        Simulated sessions continue through the deterministic path below. Live
        sessions never receive a fabricated transfer success: Twilio must accept
        each redirect/dial, and signed callbacks complete the transfer record.
        """
        from app.telephony.transfer_service import (
            TransferDestinationError,
            plan_transfer_v2,
        )
        from app.telephony.providers.twilio import TwilioAdapter

        if (call_session.provider or "").strip().upper() != "TWILIO":
            raise TransferError(
                "Production transfer-v2 is not implemented for this provider.",
                code="TRANSFER_PROVIDER_UNSUPPORTED",
                detail={"provider": call_session.provider},
            )
        if payload.mode == TransferMode.AGENT_TO_AGENT:
            raise TransferError(
                "Production agent-to-agent routing needs a tenant-owned agent destination mapping.",
                code="AGENT_TRANSFER_DESTINATION_UNAVAILABLE",
            )
        if payload.transfer_style == "three_way":
            raise TransferError(
                "A three-way AI bridge is unavailable until the assistant has a conference media participant.",
                code="THREE_WAY_AI_PARTICIPANT_UNSUPPORTED",
            )
        if payload.fallback_action == TransferFallbackAction.RETRY:
            raise TransferError(
                "Automatic transfer retry is not implemented for live provider calls.",
                code="TRANSFER_RETRY_UNSUPPORTED",
            )
        if payload.simulate_target_failure:
            raise TransferError(
                "Failure simulation is only valid for simulated call sessions.",
                code="SIMULATION_FLAG_ON_PRODUCTION_CALL",
            )

        tenant = await self.session.get(Tenant, tenant_id)
        if tenant is None:
            raise TelephonyAuthorizationError("The transfer tenant no longer exists.")
        try:
            plan = plan_transfer_v2(
                tenant,
                call_session,
                destination=normalized_dest,
                style=payload.transfer_style,
                caller_id_mode=payload.caller_id_mode,
                hold_music=payload.hold_music,
                require_human=payload.require_human,
                whisper=payload.whisper_message,
                three_way_message=payload.three_way_message,
                reason=payload.reason or "customer requested a person",
            )
        except TransferDestinationError as exc:
            raise TransferError(
                str(exc), code=exc.code, detail={"target_destination": normalized_dest}
            ) from exc

        # The currently deployed Twilio endpoints accept a Number/E.164 target.
        # SIP URIs and directory extensions remain planner-only until the
        # tenant-scoped routing path is implemented and live-tested.
        if plan.destination.kind != "e164":
            raise TransferError(
                "This transfer destination type is not available on the production Twilio path.",
                code="TRANSFER_DESTINATION_TYPE_UNSUPPORTED",
                detail={"kind": plan.destination.kind},
            )

        now = datetime.now(timezone.utc)
        record_id = uuid.uuid4()
        conference_name = (
            f"voxdesk-xfer-{call_session.id.hex[:12]}-{record_id.hex[:8]}"
            if payload.mode == TransferMode.WARM
            else None
        )
        v2_context: dict[str, Any] = {
            "style": plan.style,
            "plan": plan.as_dict(),
            "human_verified": False,
            "kind": "conference" if conference_name else "direct",
            "conference_name": conference_name,
            "fallback_action": payload.fallback_action.value,
            "provider_events": [],
        }
        context_snapshot: dict[str, Any] = {
            "call_id": str(call_session.id),
            "provider_call_id": call_session.provider_call_id,
            "from_number": call_session.from_number,
            "to_number": call_session.to_number,
            "direction": call_session.direction,
            "source_agent_id": call_session.agent_id,
            "target_agent_id": resolved_target_agent_id,
            "target_destination": plan.destination.resolved,
            "transfer_mode": payload.mode.value,
            # Only the spoken-safe form is persisted on the transfer record.
            "whisper_message": plan.whisper,
            "reason": payload.reason,
            "dtmf_buffer": call_session.dtmf_buffer,
            "transcript_turns_count": len(call_session.transcript_turns or []),
            "transcript_summary": [
                {"role": turn.get("role"), "content": turn.get("content")}
                for turn in (call_session.transcript_turns or [])[-10:]
            ],
            "context_overrides": dict(payload.context_overrides or {}),
            "handoff_timestamp": now.isoformat(),
            # Use a detached JSON object: the provider call below mutates the
            # local plan state after this initial durable commit.
            "transfer_v2": dict(v2_context),
        }

        manager = CallSessionManager(self.session)
        append_runtime_event(
            call_session,
            event_type=InternalTelephonyEventType.CALL_TRANSFER_REQUESTED,
            detail={
                "mode": payload.mode.value,
                "target_destination": plan.destination.resolved,
                "reason": payload.reason,
                "transfer_v2": True,
            },
            occurred_at=now,
        )
        await manager.transition_state(
            call_session,
            TelephonyCallState.TRANSFERRING,
            occurred_at=now,
            event_detail={
                "mode": payload.mode.value,
                "target": plan.destination.resolved,
                "transfer_v2": True,
            },
        )
        call_session.transfer_state = TransferLifecycleState.STARTING.value
        call_session.transfer_mode = payload.mode.value
        call_session.transfer_target = plan.destination.resolved

        record = TelephonyTransferRecord(
            id=record_id,
            tenant_id=tenant_id,
            environment_id=call_session.environment_id,
            call_session_id=call_session.id,
            idempotency_key=idem_key,
            transfer_mode=payload.mode.value,
            source_agent_id=call_session.agent_id,
            target_agent_id=resolved_target_agent_id,
            target_destination=plan.destination.resolved,
            whisper_message=plan.whisper,
            fallback_action=payload.fallback_action.value,
            status=TransferLifecycleState.STARTING.value,
            reason=payload.reason,
            failure_reason=None,
            context_snapshot=context_snapshot,
            requested_at=now,
            completed_at=None,
        )
        self.session.add(record)
        await self.session.flush()
        # Persist intent before contacting the carrier, so a callback or worker
        # retry can find the operation and cannot accidentally place a duplicate.
        await self.session.commit()

        adapter = TwilioAdapter()
        try:
            if conference_name:
                from app.telephony.transfer import (
                    conference_caller_twiml,
                    transfer_hold_music_url,
                    transfer_session_conference_url,
                    transfer_session_target_status_url,
                    transfer_session_target_url,
                )

                conference_callback = transfer_session_conference_url(
                    call_session.id, record.id
                )
                caller_twiml = conference_caller_twiml(
                    conference_name,
                    wait_url=transfer_hold_music_url() if plan.hold_music else "",
                    status_callback_url=conference_callback,
                )
                await adapter.redirect_transfer_twiml(
                    call_session.provider_call_id, caller_twiml
                )
                target_result = await adapter.create_transfer_target(
                    target_number=plan.destination.resolved,
                    from_number=plan.caller_id or call_session.to_number,
                    answer_url=transfer_session_target_url(call_session.id, record.id),
                    status_callback_url=transfer_session_target_status_url(
                        call_session.id, record.id
                    ),
                    machine_detection=payload.require_human,
                )
                v2_context["target_call_sid"] = target_result.external_id
                v2_context["target_provider_status"] = target_result.status
            else:
                from app.telephony.transfer import session_transfer_twiml

                twiml = session_transfer_twiml(
                    plan.destination.resolved,
                    call_session_id=call_session.id,
                    transfer_id=record.id,
                    caller_id=plan.caller_id,
                    require_human=payload.require_human,
                )
                await adapter.redirect_transfer_twiml(
                    call_session.provider_call_id, twiml
                )
        except Exception as exc:
            failure_category = type(exc).__name__[:64]
            # A warm call may already be parked. Attempt to return it to the AI
            # before reporting a definite failure; if that provider redirect is
            # also uncertain, leave the session TRANSFERRING for reconciliation.
            resumed = False
            if conference_name:
                try:
                    await adapter.resume_call(call_session.provider_call_id)
                    resumed = True
                except Exception:
                    resumed = False
            record.context_snapshot = {
                **context_snapshot,
                "transfer_v2": {
                    **v2_context,
                    "provider_outcome": "unknown",
                    "provider_error_category": failure_category,
                    "caller_resume_confirmed": resumed,
                },
            }
            if resumed:
                record.status = TransferLifecycleState.FAILED.value
                record.failure_reason = "provider_transfer_not_accepted"
                call_session.transfer_state = TransferLifecycleState.FAILED.value
                await manager.transition_state(
                    call_session,
                    TelephonyCallState.IN_PROGRESS,
                    occurred_at=datetime.now(timezone.utc),
                    event_detail={
                        "transfer_v2": True,
                        "fallback_action": TransferFallbackAction.RETURN_TO_AGENT.value,
                    },
                )
                append_runtime_event(
                    call_session,
                    event_type="transfer.cancelled",
                    detail={"reason": "provider_transfer_not_accepted"},
                )
                record.completed_at = datetime.now(timezone.utc)
            else:
                record.failure_reason = "provider_outcome_unknown"
                append_runtime_event(
                    call_session,
                    event_type="transfer.provider_outcome_unknown",
                    detail={"error_category": failure_category},
                )
            await self.session.commit()
            return _serialize_transfer_record(record)

        record.status = TransferLifecycleState.RINGING_TARGET.value
        # The JSON column is not mutable-tracked; assign a fresh nested dict so
        # SQLAlchemy notices the target SID/status update after the first commit.
        record.context_snapshot = {
            **context_snapshot,
            "transfer_v2": dict(v2_context),
        }
        call_session.transfer_state = TransferLifecycleState.RINGING_TARGET.value
        call_session.metadata_json = {
            **dict(call_session.metadata_json or {}),
            "active_transfer_record_id": str(record.id),
        }
        append_runtime_event(
            call_session,
            event_type="transfer.started",
            detail={
                "transfer_id": str(record.id),
                "kind": v2_context["kind"],
                "target": plan.destination.resolved,
                "hold_music": bool(conference_name and plan.hold_music),
            },
        )
        await self.session.commit()
        return _serialize_transfer_record(record)

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

        if normalized_dest.startswith("+"):
            from app.telephony.country_policy import decide_for_session

            decision = await decide_for_session(
                self.session,
                tenant_id,
                normalized_dest,
                stage="transfer",
                number_id=(
                    str(call_session.phone_number_id)
                    if call_session.phone_number_id is not None
                    else None
                ),
                agent_id=call_session.agent_id,
            )
            if not decision.allowed:
                raise TransferError(
                    "This transfer destination country is not allowed for this account.",
                    code="COUNTRY_NOT_ALLOWED",
                    detail={
                        "country": decision.country,
                        "matched_scope": decision.matched_scope,
                        "reason": decision.reason,
                    },
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

            if not bool(call_session.is_simulation) and str(
                call_session.execution_kind or "PRODUCTION"
            ).strip().upper() != "SIMULATION":
                return await self._execute_production_twilio_transfer_v2(
                    tenant_id=tenant_id,
                    call_session=call_session,
                    payload=payload,
                    normalized_dest=normalized_dest,
                    resolved_target_agent_id=resolved_target_agent_id,
                    idem_key=idem_key,
                )

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
