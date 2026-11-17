"""Twilio webhooks.

Flow:
  1. Someone dials the tenant's number.
  2. Twilio POSTs /telephony/voice  -> we resolve the Tenant and bound Agent/AgentVersion
     via :func:`resolve_runtime_config` (Sub-Phase 2E) and answer with TwiML containing <Stream>.
  3. Twilio opens a WebSocket to /telephony/ws and pumps raw audio both ways.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from twilio.twiml.voice_response import Connect, VoiceResponse

from app.agent.errors import ProviderError
from app.agent.provider_observability import record_provider_error
from app.billing import hooks as billing_hooks
from app.core import metrics
from app.core.config import settings
from app.core.logging import log
from app.db.models import (
    Call,
    CallDirection,
    CallStatus,
    Lead,
    LeadStatus,
    Tenant,
    TransferState,
)
from app.db.telephony_models import TelephonyCallSession, TelephonyTransferRecord
from app.telephony.call_session import (
    CallSessionManager,
    append_runtime_event,
    append_transcript_turn,
)
from app.telephony.exceptions import CallStateTransitionError
from app.telephony.enums import (
    TERMINAL_CALL_STATES,
    TelephonyCallState,
    TransferLifecycleState,
)
from app.telephony.providers.twilio import TwilioAdapter
from app.db.session import get_session, get_sessionmaker
from app.integrations import crm
from app.integrations.crm import hooks as crm_hooks
from app.realtime import events as realtime_events
from app.runtime.agent_config_resolver import resolve_runtime_config
from app.telephony import call_state, e2e_guard, phone, transfer_service
from app.telephony.ivr import DEFAULT_FLOW, next_node, render_node
from app.telephony.number_provisioning import find_tenant_for_called_number
from app.telephony.stream_auth import (
    create_stream_token,
    verify_stream_token,
    verify_twilio_request,
)
from app.webhooks.call_event_bridge import publish_call_event

router = APIRouter(prefix="/telephony", tags=["telephony"])


# Single shared implementation -- see app/telephony/stream_auth.py.
_verify_twilio = verify_twilio_request


# --- TwiML builders -------------------------------------------------------
#
# Every TwiML document the live call path can be redirected to is built here so
# there is exactly one place that knows the shape of each. Call control
# (pause/resume/transfer-cancel) re-applies these documents with
# ``calls(sid).update(twiml=...)`` rather than hand-writing XML at the call
# site, which is how a stray quote in a hand-built string turns into a dropped
# call.

#: The queue a held caller is parked in. One queue per call SID so a resume can
#: never pull another call out of hold by mistake.
HOLD_QUEUE_PREFIX = "voxdesk-hold"
DEFAULT_HOLD_MESSAGE = "Please hold. We will be right back with you."
CALL_CONTROL_TWIML_KINDS = ("media_stream", "hold")


def media_stream_url(call_sid: str, *, campaign_id: str = "", lead_id: str = "") -> str:
    """The signed media-stream URL one live call runs on."""
    url = f"{settings.ws_base_url}/telephony/ws" f"?token={create_stream_token(call_sid)}"
    if campaign_id:
        url += f"&campaign_id={campaign_id}"
    if lead_id:
        url += f"&lead_id={lead_id}"
    return url


def build_media_stream_twiml(
    call_sid: str, *, campaign_id: str = "", lead_id: str = "", greeting_text: str = ""
) -> str:
    """TwiML that hands the call back to the AI media stream.

    This is the document an answered call runs on, so it is also the document a
    resume (or a transfer cancel) re-applies. Rebuilding it from the same
    helper the call was answered with is what makes "resume" mean "the same
    stream", not "some stream".
    """
    response = VoiceResponse()
    if greeting_text:
        response.say(greeting_text)
    connect = Connect()
    connect.stream(url=media_stream_url(call_sid, campaign_id=campaign_id, lead_id=lead_id))
    response.append(connect)
    return str(response)


def build_hold_twiml(call_sid: str, *, message: str = DEFAULT_HOLD_MESSAGE) -> str:
    """TwiML that parks a live caller on hold.

    ``<Enqueue>`` is used rather than a long ``<Pause>`` because a pause runs
    out and then ends the call: a caller whose hold expired would be hung up on
    mid-sentence. A queue plays Twilio's hold treatment until something moves
    the call elsewhere, and moving it elsewhere is exactly what resume does.
    """
    response = VoiceResponse()
    response.say(message, voice="Polly.Joanna")
    response.enqueue(name=f"{HOLD_QUEUE_PREFIX}-{call_sid}")
    return str(response)


def build_dtmf_twiml(digits: str) -> str:
    """TwiML that plays one digit run on a live call.

    Built with the SDK rather than an f-string so a digit run can never break
    out of the ``digits`` attribute and inject a second TwiML verb.
    """
    response = VoiceResponse()
    response.play("", digits=digits)
    return str(response)


def build_whisper_twiml(message: str) -> str:
    """TwiML that speaks one briefing to a single leg and nothing else."""
    response = VoiceResponse()
    response.say(message, voice="Polly.Joanna")
    return str(response)


def build_call_control_twiml(
    kind: str,
    *,
    call_sid: str,
    stream_url: str = "",
    campaign_id: str = "",
    lead_id: str = "",
    hold_message: str = DEFAULT_HOLD_MESSAGE,
) -> str:
    """Build one of the call-control TwiML documents by name.

    ``stream_url`` lets a caller re-apply an already-minted stream URL instead
    of minting a new token; an empty value mints a fresh signed URL.
    """
    normalized = (kind or "").strip().lower()
    if normalized == "hold":
        return build_hold_twiml(call_sid, message=hold_message)
    if normalized == "media_stream":
        if stream_url:
            response = VoiceResponse()
            connect = Connect()
            connect.stream(url=stream_url)
            response.append(connect)
            return str(response)
        return build_media_stream_twiml(
            call_sid, campaign_id=campaign_id, lead_id=lead_id
        )
    raise ValueError(
        f"unknown call-control TwiML kind {kind!r}; expected one of {CALL_CONTROL_TWIML_KINDS}"
    )


async def _apply_session_status_callback(
    session: AsyncSession,
    *,
    call_sid: str,
    raw_status: str,
    duration_seconds: int | None = None,
    call_session_id: uuid.UUID | None = None,
) -> TelephonyCallSession | None:
    """Mirror Twilio call progress into the authoritative call-session state."""
    stmt = select(TelephonyCallSession).where(
        TelephonyCallSession.provider == "TWILIO"
    )
    if call_session_id is not None:
        stmt = stmt.where(TelephonyCallSession.id == call_session_id)
    else:
        stmt = stmt.where(TelephonyCallSession.provider_call_id == call_sid)
    call_session = (
        await session.execute(
            stmt.with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if call_session is None:
        return None

    existing_sid = str(call_session.provider_call_id or "")
    if existing_sid != call_sid:
        if not existing_sid.startswith("pending_") or not call_sid:
            log.warning(
                "twilio.runtime_status_sid_mismatch",
                call_session_id=str(call_session.id),
                provider_call_id=call_sid,
            )
            return None
        duplicate = await session.scalar(
            select(TelephonyCallSession.id).where(
                TelephonyCallSession.provider == "TWILIO",
                TelephonyCallSession.provider_call_id == call_sid,
                TelephonyCallSession.id != call_session.id,
            )
        )
        if duplicate is not None:
            return None
        call_session.provider_call_id = call_sid

    mapped = {
        "initiated": TelephonyCallState.DIALING,
        "queued": TelephonyCallState.DIALING,
        "ringing": TelephonyCallState.RINGING,
        "answered": TelephonyCallState.IN_PROGRESS,
        "in-progress": TelephonyCallState.IN_PROGRESS,
        "completed": TelephonyCallState.COMPLETED,
        "failed": TelephonyCallState.FAILED,
        "busy": TelephonyCallState.BUSY,
        "no-answer": TelephonyCallState.NO_ANSWER,
        "canceled": TelephonyCallState.CANCELLED,
        "cancelled": TelephonyCallState.CANCELLED,
    }
    target = mapped.get((raw_status or "").strip().lower())
    if target is None:
        return call_session
    current = TelephonyCallState(call_session.status)
    if target == TelephonyCallState.IN_PROGRESS and current in {
        TelephonyCallState.TRANSFERRING,
        TelephonyCallState.TRANSFERRED,
    }:
        # A delayed "in-progress" parent-call callback must not regress a
        # transfer that already moved the call into its conference.
        return call_session
    if current in TERMINAL_CALL_STATES:
        return call_session
    try:
        await CallSessionManager(session).transition_state(
            call_session,
            target,
            occurred_at=datetime.now(timezone.utc),
            provider_duration_seconds=duration_seconds,
            hangup_reason=(
                raw_status[:120]
                if target in TERMINAL_CALL_STATES
                else None
            ),
            event_detail={"provider": "TWILIO", "provider_status": raw_status[:64]},
        )
    except CallStateTransitionError as exc:
        # Call-session state has its own monotonic state machine. A late carrier
        # event is acknowledged without regressing it.
        log.info(
            "twilio.runtime_status_ignored",
            call_sid=call_sid,
            status=raw_status,
            error_category=type(exc).__name__[:64],
        )
    return call_session


async def _bind_twilio_legacy_call(
    session: AsyncSession,
    call_session: TelephonyCallSession,
    call_sid: str,
) -> Call | None:
    """Bind a session-based outbound call to the legacy row used by `/ws`."""
    if not call_sid or call_session.environment_id is None:
        return None
    existing_sid = str(call_session.provider_call_id or "")
    if existing_sid != call_sid:
        if not existing_sid.startswith("pending_"):
            return None
        duplicate = await session.scalar(
            select(TelephonyCallSession.id).where(
                TelephonyCallSession.provider == "TWILIO",
                TelephonyCallSession.provider_call_id == call_sid,
                TelephonyCallSession.id != call_session.id,
            )
        )
        if duplicate is not None:
            return None
        call_session.provider_call_id = call_sid

    legacy_call = (
        await session.execute(
            select(Call)
            .where(Call.call_sid == call_sid)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if legacy_call is not None and (
        legacy_call.tenant_id != call_session.tenant_id
        or legacy_call.environment_id != call_session.environment_id
    ):
        return None

    if legacy_call is None:
        metadata = dict(call_session.metadata_json or {})
        agent_id = None
        version_id = None
        try:
            if call_session.agent_id:
                agent_id = uuid.UUID(str(call_session.agent_id))
        except (ValueError, TypeError):
            agent_id = None
        try:
            version_ref = metadata.get("resolved_agent_version_id")
            if version_ref:
                version_id = uuid.UUID(str(version_ref))
        except (ValueError, TypeError):
            version_id = None
        now = datetime.now(timezone.utc)
        legacy_call = Call(
            id=uuid.uuid4(),
            tenant_id=call_session.tenant_id,
            environment_id=call_session.environment_id,
            call_sid=call_sid,
            from_number=call_session.from_number,
            to_number=call_session.to_number,
            status=CallStatus.RINGING,
            direction=CallDirection.OUTBOUND,
            agent_id=agent_id,
            agent_version_id=version_id,
            experiment_id=call_session.experiment_id,
            variant_id=call_session.variant_id,
            started_at=call_session.started_at or now,
        )
        session.add(legacy_call)
        await session.flush()

    if call_session.legacy_call_id and call_session.legacy_call_id != legacy_call.id:
        linked_call = await session.get(Call, call_session.legacy_call_id)
        if linked_call is not None:
            return None
    call_session.legacy_call_id = legacy_call.id
    return legacy_call


@router.post("/voice", response_class=PlainTextResponse)
async def incoming_call(
    request: Request,
    CallSid: str = Form(...),
    From: str = Form(...),
    To: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    from app.core.graceful_shutdown import is_draining

    if is_draining():
        log.warning("call.rejected_node_draining", call_sid=CallSid)
        return PlainTextResponse(
            '<?xml version="1.0" encoding="UTF-8"?><Response><Reject reason="busy"/></Response>',
            status_code=503,
            media_type="application/xml",
            headers={"Retry-After": "5", "X-VoxDesk-Drain": "draining"},
        )

    if not await _verify_twilio(request):
        return PlainTextResponse("forbidden", status_code=403)

    # BATCH06: route the signed contact-center-v2 branches (softphone agent /
    # customer legs, callbacks, consults) through their own handler. It is a
    # no-op for ordinary inbound calls (no ``cc2`` query param -> returns None),
    # and it must run only AFTER the Twilio signature has been verified so an
    # unauthenticated caller can never reach the conference/softphone logic.
    from app.contact_center_v2.telephony import handle_voice

    cc2_response = await handle_voice(
        request, session, call_sid=CallSid, from_uri=From
    )
    if cc2_response is not None:
        return cc2_response

    tenant = await find_tenant_for_called_number(session, To)
    if tenant is None:
        tenant = (
            await session.execute(select(Tenant).where(Tenant.twilio_number == To))
        ).scalar_one_or_none()

    # Step 5 (scale-compliance): the real-call E2E guard. A no-op unless the
    # operator armed E2E mode; when armed, only an explicit, allowlisted,
    # test-tenant call may pass. Rejections happen before any Call row exists,
    # so a refused caller can never create a session, stream, usage event, or
    # audit record. See app/telephony/e2e_guard.py.
    try:
        e2e_ctx = e2e_guard.check_inbound(settings, from_number=From, to_number=To, tenant=tenant)
    except e2e_guard.E2ERejected as exc:
        log.warning(
            "call.e2e_rejected_hangup",
            reason=str(exc),
            from_=phone.redact(From),
            to=phone.redact(To),
        )
        rejected = VoiceResponse()
        rejected.say("This number is in test mode and this caller is not authorized. Goodbye.")
        rejected.hangup()
        return PlainTextResponse(str(rejected), media_type="application/xml")
    except e2e_guard.E2EConfigurationError:
        # Fail-closed: refuse traffic rather than answer without the guard.
        raise HTTPException(status_code=503, detail="E2E configuration error")

    response = VoiceResponse()

    if tenant is None or not tenant.is_active:
        response.say("This number is not configured. Goodbye.")
        response.hangup()
        return PlainTextResponse(str(response), media_type="application/xml")

    # Idempotent call creation: Twilio may re-deliver /voice for the same
    # CallSid (network retry). The unique index on call_sid already prevents a
    # duplicate row, but the naive insert turned that into a 500 and another
    # retry. Answering an already-known CallSid with fresh TwiML is correct and
    # creates no second call/session/billing/usage/audit record.
    call = (
        await session.execute(select(Call).where(Call.call_sid == CallSid))
    ).scalar_one_or_none()
    created_call = False
    if call is None:
        call = Call(
            tenant_id=tenant.id,
            call_sid=CallSid,
            from_number=From,
            to_number=To,
            status=CallStatus.IN_PROGRESS,
        )
        session.add(call)
        try:
            await session.flush()
            raw_overrides = request.query_params.get("voxdesk_overrides")
            if not raw_overrides:
                form_data = await request.form()
                raw_overrides = form_data.get("voxdesk_overrides")
            if raw_overrides:
                from app.telephony.webhooks import apply_inbound_overrides_to_call

                apply_inbound_overrides_to_call(call, raw_overrides)
            await resolve_runtime_config(session, call, tenant)
            await publish_call_event(session, call, "call_started")
            await session.commit()
            created_call = True
        except IntegrityError:
            # Lost a race with a concurrent duplicate; roll back and reuse the
            # row the other request created.
            await session.rollback()
            await session.refresh(tenant)
            call = (
                await session.execute(select(Call).where(Call.call_sid == CallSid))
            ).scalar_one_or_none()
            if call is None:
                # Not a duplicate CallSid: preserve publication/DB failures.
                raise
    else:
        log.info("call.incoming_duplicate", call_sid=CallSid, tenant=tenant.name)

    if call is not None and call.tenant_id != tenant.id:
        # A retried SID must not mint stream access under another tenant's To.
        raise HTTPException(status_code=404, detail="Call not found")

    # Realtime: announce a genuinely-new call once, AFTER its row is durable.
    # A redelivered /voice (Twilio retry) does not re-announce — that is the
    # whole point of created_call tracking instead of "emit on every POST".
    if created_call and call is not None:
        await realtime_events.emit_call_event(call, kind="call.created")

    # Short-lived signed token so the media WebSocket is not open to anyone
    # who learns a call SID. See app/telephony/stream_auth.py.
    ws_url = f"{settings.ws_base_url}/telephony/ws" f"?token={create_stream_token(CallSid)}"

    # IVR চালু থাকলে আগে মেনু, তারপর AI। ফ্লো ভাঙা থাকলে চুপচাপ AI-তে যাবে।
    if tenant.ivr_enabled:
        flow = tenant.ivr_flow or DEFAULT_FLOW
        start = flow.get("start", "start")
        log.info("call.incoming.ivr", tenant=tenant.name, node=start, e2e_test=bool(e2e_ctx))
        return PlainTextResponse(
            render_node(flow, start, tenant, ws_url=ws_url),
            media_type="application/xml",
        )

    connect = Connect()
    connect.stream(url=ws_url)
    response.append(connect)
    log.info(
        "call.incoming",
        from_=phone.redact(From),
        to=phone.redact(To),
        tenant=tenant.name,
        call_sid=CallSid,
        agent_id=str(call.agent_id) if (call and call.agent_id) else None,
        agent_version_id=str(call.agent_version_id) if (call and call.agent_version_id) else None,
        e2e_test=bool(e2e_ctx),
    )
    return PlainTextResponse(str(response), media_type="application/xml")


@router.post("/ivr", response_class=PlainTextResponse)
async def ivr_step(
    request: Request,
    node: str = "",
    To: str = Form(...),
    CallSid: str = Form(...),
    Digits: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    """Every Gather in a flow posts back here with the pressed digit."""
    if not await _verify_twilio(request):
        return PlainTextResponse("forbidden", status_code=403)

    tenant = (
        await session.execute(select(Tenant).where(Tenant.twilio_number == To))
    ).scalar_one_or_none()
    if tenant is None:
        return PlainTextResponse("<Response><Hangup/></Response>", media_type="application/xml")

    call = (await session.execute(select(Call).where(
        Call.call_sid == CallSid, Call.tenant_id == tenant.id,
    ))).scalar_one_or_none()
    if call is None:
        raise HTTPException(status_code=404, detail="Call not found")
    if Digits:
        if len(Digits) > 64 or any(digit not in "0123456789ABCD*#" for digit in Digits):
            raise HTTPException(status_code=422, detail="Invalid DTMF input")
        # Version one records occurrence, not raw keys or per-key occurrences.
        # The database event key absorbs retries; nothing private is persisted.
        await publish_call_event(session, call, "dtmf_received")
        await session.commit()

    flow = tenant.ivr_flow or DEFAULT_FLOW
    target = next_node(flow, node, Digits) if Digits else (node or flow.get("start"))
    # Short-lived signed token so the media WebSocket is not open to anyone
    # who learns a call SID. See app/telephony/stream_auth.py.
    ws_url = f"{settings.ws_base_url}/telephony/ws" f"?token={create_stream_token(CallSid)}"
    log.info("ivr.step", node=node, digit_present=bool(Digits), next=target)
    return PlainTextResponse(
        render_node(flow, target, tenant, ws_url=ws_url), media_type="application/xml"
    )


@router.post("/outbound-answer", response_class=PlainTextResponse)
async def outbound_answer(
    request: Request,
    campaign_id: str = "",
    lead_id: str = "",
    AnsweredBy: str = Form(""),
    CallSid: str = Form(""),
    call_session_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
):
    """
    Twilio hits this when an outbound call is picked up. If the answer machine
    detector says it is voicemail we hang up immediately -- talking to an
    answering machine burns money and annoys people.

    Like the other Twilio webhooks this authenticates by HMAC signature, not
    by a user token. It was the last route in the telephony path still missing
    that check: without it anyone could mint a media-stream URL for an
    arbitrary call SID.
    """
    from twilio.twiml.voice_response import VoiceResponse as VR

    if not await _verify_twilio(request):
        return PlainTextResponse("forbidden", status_code=403)

    response = VR()
    runtime_call = None
    legacy_call = None
    if call_session_id is not None:
        runtime_call = (
            await session.execute(
                select(TelephonyCallSession)
                .where(
                    TelephonyCallSession.id == call_session_id,
                    TelephonyCallSession.provider == "TWILIO",
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if runtime_call is None or not CallSid:
            log.warning(
                "outbound.session_callback_unmatched",
                call_session_id=str(call_session_id),
                has_call_sid=bool(CallSid),
            )
            response.hangup()
            return PlainTextResponse(str(response), media_type="application/xml")
        legacy_call = await _bind_twilio_legacy_call(session, runtime_call, CallSid)
        if legacy_call is None:
            log.error(
                "outbound.session_legacy_call_link_failed",
                call_session_id=str(call_session_id),
                call_sid=CallSid,
            )
            response.hangup()
            return PlainTextResponse(str(response), media_type="application/xml")
    else:
        if CallSid:
            runtime_call = (
                await session.execute(
                    select(TelephonyCallSession)
                    .where(
                        TelephonyCallSession.provider == "TWILIO",
                        TelephonyCallSession.provider_call_id == CallSid,
                    )
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()
            legacy_call = (
                await session.execute(
                    select(Call).where(Call.call_sid == CallSid)
                    .with_for_update().execution_options(populate_existing=True)
                )
            ).scalar_one_or_none()

    if runtime_call is not None and runtime_call.status in {
        state.value for state in TERMINAL_CALL_STATES
    }:
        response.hangup()
        if legacy_call is not None:
            await session.commit()
        return PlainTextResponse(str(response), media_type="application/xml")

    if AnsweredBy.startswith("machine"):
        # Only documented provider verdicts are customer-facing facts. Unknown
        # machine-prefixed values retain the old hangup behavior, not a verdict.
        recognized_machine = AnsweredBy in {
            "machine_start", "machine_end_beep", "machine_end_silence", "machine_end_other",
        }
        if runtime_call is not None and runtime_call.status in {
            TelephonyCallState.CREATED.value,
            TelephonyCallState.DIALING.value,
            TelephonyCallState.RINGING.value,
            TelephonyCallState.ANSWERED.value,
        }:
            await CallSessionManager(session).transition_state(
                runtime_call,
                TelephonyCallState.VOICEMAIL,
                occurred_at=datetime.now(timezone.utc),
                hangup_reason="answering_machine_detected",
                event_detail={"answered_by": "machine"},
            )
        if legacy_call is not None:
            transition = call_state.apply_status(
                legacy_call,
                CallStatus.NO_ANSWER,
                reason="answering machine detected",
                source="twilio_outbound_answer",
            )
            await call_state.publish_transition(session, legacy_call, transition)
        if recognized_machine and legacy_call is not None:
            await publish_call_event(session, legacy_call, "voicemail_detected")
        if runtime_call is not None or legacy_call is not None:
            await session.commit()
        log.info("outbound.machine_hangup", lead=lead_id, recognized=recognized_machine)
        response.hangup()
        return PlainTextResponse(str(response), media_type="application/xml")

    if runtime_call is not None and runtime_call.status in {
        TelephonyCallState.CREATED.value,
        TelephonyCallState.DIALING.value,
        TelephonyCallState.RINGING.value,
        TelephonyCallState.ANSWERED.value,
    }:
        await CallSessionManager(session).transition_state(
            runtime_call,
            TelephonyCallState.IN_PROGRESS,
            occurred_at=datetime.now(timezone.utc),
            event_detail={"answer_source": "twilio_outbound_answer"},
        )
    if legacy_call is not None:
        transition = call_state.apply_status(
            legacy_call,
            CallStatus.IN_PROGRESS,
            source="twilio_outbound_answer",
        )
        await call_state.publish_transition(session, legacy_call, transition)
    if runtime_call is not None or legacy_call is not None:
        await session.commit()

    connect = Connect()
    connect.stream(
        url=media_stream_url(CallSid, campaign_id=campaign_id, lead_id=lead_id)
    )
    response.append(connect)
    log.info("outbound.answered", lead=lead_id, campaign=campaign_id)
    return PlainTextResponse(str(response), media_type="application/xml")


async def _safe_close(websocket, code: int) -> None:
    """Close a WebSocket at most once, never raising.

    A close sent after the peer has gone, or after the ASGI response has
    completed, raises RuntimeError or WebSocketDisconnect in the server.
    That is harmless for the client and only adds noise, so it is logged
    at debug level and swallowed.
    """
    try:
        await websocket.close(code=code)
    except (RuntimeError, WebSocketDisconnect) as exc:
        log.debug("ws.close_after_completion", code=code, error=str(exc))


@router.websocket("/ws")
async def media_stream(websocket: WebSocket):
    """
    Twilio Media Stream. Machine-to-machine: authenticated by the signed
    token we minted into the <Stream> URL, NOT by a user JWT.
    """
    token = websocket.query_params.get("token")
    from app.core.graceful_shutdown import is_draining, track_active_call

    if is_draining():
        await websocket.accept()
        log.warning("ws.rejected_node_draining")
        await _safe_close(websocket, 1012)
        return

    await websocket.accept()

    # Twilio sends "connected" then "start" before any audio. Bound this with
    # a timeout: a socket that connects and never speaks must not hold a
    # database session and a pipeline slot open forever. The bound is a
    # handshake bound only (generous by default and disable-able via
    # STREAM_HANDSHAKE_TIMEOUT_SECONDS=0) -- it never touches the live
    # conversation, which runs off the WebSocket, not a wall clock.
    async def _await_start():
        await websocket.receive_text()  # connected
        return json.loads(await websocket.receive_text())  # start

    try:
        timeout = settings.stream_handshake_timeout_seconds
        start_msg = await (
            asyncio.wait_for(_await_start(), timeout) if timeout and timeout > 0 else _await_start()
        )
    except WebSocketDisconnect:
        # The client already closed the socket. Closing it again raises
        # "response already completed" in the ASGI server.
        log.warning("ws.handshake_incomplete", reason="client_disconnected")
        return
    except asyncio.TimeoutError:
        log.warning("ws.handshake_incomplete", reason="timeout")
        await _safe_close(websocket, 1008)  # policy violation
        return
    except (ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        # Malformed frame: non-JSON text, a binary frame (no "text" key),
        # or a non-object payload. Reject once with 1008 and stop; never
        # let the exception escape after accept().
        log.warning("ws.handshake_incomplete", reason="malformed_frame")
        await _safe_close(websocket, 1008)
        return

    start = start_msg.get("start", {})
    stream_sid = start.get("streamSid")
    call_sid = start.get("callSid")

    # The token is bound to this call SID, so a token captured from one call
    # cannot be replayed to listen in on another.
    # Imported here, not at module scope: the pipecat stack is heavy and is
    # only needed once a real media stream arrives. Keeping it lazy lets the
    # HTTP API, the webhooks and the test suite boot without it.
    from app.agent.pipeline import run_voice_agent

    if not verify_stream_token(call_sid, token):
        log.warning("ws.rejected_bad_stream_token", call_sid=call_sid)
        await _safe_close(websocket, 1008)  # policy violation
        return

    async with get_sessionmaker()() as session:
        call = (
            await session.execute(select(Call).where(Call.call_sid == call_sid))
        ).scalar_one_or_none()
        if call is None:
            await _safe_close(websocket, 1000)
            return
        tenant = await session.get(Tenant, call.tenant_id)

        # Step 7 observability: the gauge counts live media-stream pipelines,
        # which is what "calls in flight" means to an operator watching the
        # dashboard. Bounded (a bare gauge), and cleared in `finally` even if
        # the pipeline crashes.
        metrics.ACTIVE_CALLS.inc()
        try:
            with track_active_call(call_sid):
                await run_voice_agent(
                    websocket=websocket,
                    stream_sid=stream_sid,
                    call_sid=call_sid,
                    session=session,
                    tenant=tenant,
                    call=call,
                )
        except Exception as exc:
            if isinstance(exc, ProviderError):
                record_provider_error(exc.provider, exc.category)
                log.error(
                    "call.crashed",
                    call_sid=call_sid,
                    tenant_id=str(call.tenant_id),
                    provider=exc.provider,
                    category=exc.category,
                    retryable=exc.retryable,
                    error=exc.safe_message,
                )
            else:
                log.error(
                    "call.crashed", call_sid=call_sid, tenant_id=str(call.tenant_id), error=str(exc)
                )
            await session.refresh(call)
            if call.transfer_state is TransferState.NONE:
                transition = call_state.apply_status(
                    call,
                    CallStatus.FAILED,
                    reason="media stream error",
                    source="media_stream",
                )
                await call_state.publish_transition(session, call, transition)
                await session.commit()
            else:
                log.info(
                    "call.stream_closed_for_transfer",
                    call_sid=call_sid,
                    transfer_state=call.transfer_state.value,
                )
        finally:
            metrics.ACTIVE_CALLS.dec()


@router.post("/status", response_class=PlainTextResponse)
async def call_status(
    request: Request,
    CallSid: str = Form(...),
    CallStatus_: str = Form(alias="CallStatus", default=""),
    CallDuration: str = Form(default="0"),
    call_session_id: uuid.UUID | None = None,
    session: AsyncSession = Depends(get_session),
):
    """
    Twilio's call status callback.

    Twilio retries this webhook, and for a transferred call the parent leg and
    the dial leg can report out of order, so everything here must be safe to
    run twice and must never regress a terminal state. All of that logic lives
    in `app.telephony.call_state`; this function only decides what a *changed*
    status means for billing, leads and the CRM.
    """
    if not await _verify_twilio(request):
        return PlainTextResponse("forbidden", status_code=403)

    try:
        duration = float(CallDuration or 0)
    except ValueError:
        duration = 0.0
    runtime_call = await _apply_session_status_callback(
        session,
        call_sid=CallSid,
        raw_status=CallStatus_,
        duration_seconds=max(0, int(duration)),
        call_session_id=call_session_id,
    )
    call = (
        await session.execute(
            select(Call).where(Call.call_sid == CallSid)
            .with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if call is None and runtime_call is not None:
        # A very fast status callback can beat the runtime's post-origination
        # persistence. Recreate the websocket-facing row from the authoritative
        # call session, then apply this callback normally below.
        call = await _bind_twilio_legacy_call(session, runtime_call, CallSid)
    if call is None:
        if runtime_call is None:
            log.info("status.unknown_call_sid", call_sid=CallSid)
        await session.commit()
        return PlainTextResponse("ok")

    result = call_state.apply_provider_status(
        call, CallStatus_, duration_seconds=duration, source="twilio_status"
    )

    await call_state.publish_transition(session, call, result)

    tenant = await session.get(Tenant, call.tenant_id)

    # A transfer that was still dialling when the call ended never connected.
    if result.applied and call_state.is_terminal(call.status):
        if call.transfer_state in (TransferState.REQUESTED, TransferState.DIALING):
            await transfer_service.mark_transfer_failed(
                session,
                call,
                f"{transfer_service.INFERRED_PREFIX}"
                f"call ended while {call.transfer_state.value}",
            )
        elif call.transfer_state is TransferState.CONNECTED and dict(
            (call.transfer_context or {}).get("transfer_v2") or {}
        ):
            await transfer_service.record_transfer_v2_event(session, call, "ended")

    if result.applied and call_state.is_terminal(call.status) and tenant:
        await billing_hooks.on_call_finalized(session, tenant, call)

    if result.applied and call.lead_id:
        lead = await session.get(Lead, call.lead_id)
        if lead is not None and (
            lead.tenant_id != call.tenant_id
            or lead.environment_id != call.environment_id
        ):
            log.warning(
                "status.lead_scope_mismatch",
                call_sid=CallSid, lead=str(lead.id),
            )
        elif lead and lead.status is not LeadStatus.DNC:
            from app.leads import lifecycle
            from app.leads.exceptions import ClaimConflict, InvalidTransition

            target = None
            if call.status is CallStatus.COMPLETED and (call.duration_seconds or 0) > 10:
                target = (
                    LeadStatus.QUALIFIED if (call.lead_score or 0) >= 50 else LeadStatus.CALLED
                )
                lead.score = call.lead_score
            elif lead.attempts >= (tenant.max_call_attempts if tenant else 3):
                target = LeadStatus.FAILED
            if target is not None:
                try:
                    await lifecycle.transition(
                        session,
                        lead,
                        target.value,
                        reason="twilio_status_callback",
                        source="telephony",
                        expected=lead.status,
                        environment_id=call.environment_id,
                    )
                except (InvalidTransition, ClaimConflict):
                    log.info(
                        "status.lead_grade_skipped",
                        call_sid=CallSid, lead=str(lead.id),
                    )

    if result.applied and call_state.is_terminal(call.status) and not call.crm_synced:
        if call.status is CallStatus.COMPLETED:
            emitted = await crm_hooks.on_call_completed(session, tenant, call)
        else:
            emitted = await crm_hooks.on_call_missed(session, tenant, call)
        call.crm_synced = call.crm_synced or emitted

    await session.commit()

    if result.applied:
        await realtime_events.emit_call_event(call, kind="call.updated")

    return PlainTextResponse("ok")


@router.get("/transfer-hold-music")
async def transfer_hold_music() -> Response:
    """Serve the licensed, Twilio-compatible WAV used by <Conference waitUrl>."""
    path = transfer_service.HOLD_MUSIC_PATH
    if not transfer_service.hold_music_available() or path.suffix.lower() != ".wav":
        raise HTTPException(status_code=404, detail="Hold music is not available")
    return FileResponse(
        path,
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=86400"},
    )


async def _load_session_transfer(
    session: AsyncSession,
    *,
    call_session_id: uuid.UUID,
    transfer_id: uuid.UUID,
) -> tuple[TelephonyCallSession | None, TelephonyTransferRecord | None]:
    """Load one signed Twilio transfer callback within its call-session tenant."""
    call_session = (
        await session.execute(
            select(TelephonyCallSession)
            .where(TelephonyCallSession.id == call_session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if call_session is None or (call_session.provider or "").upper() != "TWILIO":
        return None, None
    transfer = (
        await session.execute(
            select(TelephonyTransferRecord)
            .where(
                TelephonyTransferRecord.id == transfer_id,
                TelephonyTransferRecord.call_session_id == call_session.id,
                TelephonyTransferRecord.tenant_id == call_session.tenant_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    return call_session, transfer


def _session_transfer_parts(
    transfer: TelephonyTransferRecord,
) -> tuple[dict, dict, dict]:
    context = dict(transfer.context_snapshot or {})
    v2 = dict(context.get("transfer_v2") or {})
    plan = dict(v2.get("plan") or {})
    return context, v2, plan


def _save_session_transfer_parts(
    transfer: TelephonyTransferRecord,
    context: dict,
    v2: dict,
) -> None:
    transfer.context_snapshot = {**context, "transfer_v2": v2}


def _hangup_twiml() -> Response:
    voice = VoiceResponse()
    voice.hangup()
    return Response(str(voice), media_type="application/xml")


@router.post("/transfer-session-target")
async def transfer_session_target(
    request: Request,
    call_session_id: uuid.UUID,
    transfer_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """AMD-gated called-party TwiML for the production TelephonyCallSession flow."""
    if not await _verify_twilio(request):
        return PlainTextResponse("forbidden", status_code=403)
    form = await request.form()
    target_call_sid = str(form.get("CallSid") or "").strip()
    call_session, transfer = await _load_session_transfer(
        session,
        call_session_id=call_session_id,
        transfer_id=transfer_id,
    )
    if (
        call_session is None
        or transfer is None
        or not target_call_sid
        or call_session.status not in {"TRANSFERRING", "TRANSFERRED"}
        or transfer.status not in {
            TransferLifecycleState.STARTING.value,
            TransferLifecycleState.RINGING_TARGET.value,
            TransferLifecycleState.WHISPERING.value,
        }
    ):
        return _hangup_twiml()

    context, v2, plan = _session_transfer_parts(transfer)
    previous_target = str(v2.get("target_call_sid") or "")
    if previous_target and previous_target != target_call_sid:
        return _hangup_twiml()

    require_human = bool(plan.get("require_human", True))
    answered_by = str(form.get("AnsweredBy") or "").strip()
    allowed, verdict = transfer_service.human_leg_verdict(
        {"AnsweredBy": answered_by} if answered_by else None,
        require_human=require_human,
    )
    v2.update(
        {
            "target_call_sid": target_call_sid,
            "human_verified": allowed,
            "amd_verdict": verdict,
        }
    )
    if allowed:
        transfer.status = TransferLifecycleState.WHISPERING.value
        call_session.transfer_state = TransferLifecycleState.WHISPERING.value
        append_runtime_event(
            call_session,
            event_type="transfer.human_verified",
            detail={"transfer_id": str(transfer.id), "amd_verdict": verdict},
        )
    else:
        append_runtime_event(
            call_session,
            event_type="transfer.human_rejected",
            detail={"transfer_id": str(transfer.id), "amd_verdict": verdict},
        )
    _save_session_transfer_parts(transfer, context, v2)
    await session.commit()

    voice = VoiceResponse()
    if not allowed:
        # Fail closed without telling the target or caller the AMD verdict.
        voice.hangup()
    elif v2.get("kind") == "conference":
        from app.telephony.transfer import (
            conference_target_twiml,
            transfer_session_conference_url,
        )

        whisper = transfer_service._safe_whisper_reason(str(plan.get("whisper") or ""))
        twiml = conference_target_twiml(
            str(v2.get("conference_name") or ""),
            briefing_text=whisper,
            status_callback_url=transfer_session_conference_url(
                call_session.id, transfer.id
            ),
        )
        return Response(twiml, media_type="application/xml")
    else:
        whisper = transfer_service._safe_whisper_reason(str(plan.get("whisper") or ""))
        if whisper:
            voice.say(whisper, voice="Polly.Joanna")
    return Response(str(voice), media_type="application/xml")


@router.post("/transfer-session-status")
async def transfer_session_status(
    request: Request,
    call_session_id: uuid.UUID,
    transfer_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Complete a direct <Dial> transfer and return the caller to its media stream."""
    if not await _verify_twilio(request):
        return PlainTextResponse("forbidden", status_code=403)
    form = await request.form()
    call_sid = str(form.get("CallSid") or "").strip()
    outcome = str(form.get("DialCallStatus") or "").strip().lower()
    call_session, transfer = await _load_session_transfer(
        session,
        call_session_id=call_session_id,
        transfer_id=transfer_id,
    )
    if (
        call_session is None
        or transfer is None
        or call_session.provider_call_id != call_sid
        or call_session.status not in {"TRANSFERRING", "TRANSFERRED"}
    ):
        return _hangup_twiml()
    context, v2, plan = _session_transfer_parts(transfer)
    if v2.get("kind") != "direct":
        return _hangup_twiml()

    successful = outcome in {"answered", "completed"} and (
        not bool(plan.get("require_human", True))
        or v2.get("human_verified") is True
    )
    manager = CallSessionManager(session)
    now = datetime.now(timezone.utc)
    if successful:
        transfer.status = TransferLifecycleState.COMPLETED.value
        transfer.failure_reason = None
        v2["provider_events"] = list(v2.get("provider_events") or []) + [
            {"event": "bridged", "at": now.isoformat()},
            {"event": "ended", "at": now.isoformat()},
        ]
        call_session.transfer_state = TransferLifecycleState.COMPLETED.value
        append_runtime_event(
            call_session,
            event_type="transfer.bridged",
            detail={"transfer_id": str(transfer.id), "target": transfer.target_destination},
            occurred_at=now,
        )
        append_runtime_event(
            call_session,
            event_type="transfer.ended",
            detail={"transfer_id": str(transfer.id)},
            occurred_at=now,
        )
        append_transcript_turn(
            call_session,
            role="system",
            content="[Human transfer ended; the assistant is available again]",
            metadata={"transfer_id": str(transfer.id)},
            occurred_at=now,
        )
        if call_session.status in {"TRANSFERRING", "TRANSFERRED"}:
            await manager.transition_state(
                call_session,
                TelephonyCallState.IN_PROGRESS,
                occurred_at=now,
                event_detail={"transfer_v2": True, "resumed_after_transfer": True},
            )
        transfer.completed_at = now
        greeting = "The transfer has ended. I can help you further."
    else:
        failure = (
            str(v2.get("amd_verdict") or "human_not_confirmed")
            if outcome in {"answered", "completed"}
            else (outcome or "transfer_failed")
        )
        transfer.status = TransferLifecycleState.FAILED.value
        transfer.failure_reason = failure[:400]
        v2["provider_events"] = list(v2.get("provider_events") or []) + [
            {"event": "cancelled", "at": now.isoformat(), "reason": failure}
        ]
        call_session.transfer_state = TransferLifecycleState.FAILED.value
        append_runtime_event(
            call_session,
            event_type="transfer.cancelled",
            detail={"transfer_id": str(transfer.id), "reason": failure},
            occurred_at=now,
        )
        transfer.completed_at = now
        if transfer.fallback_action == "HANGUP":
            await manager.transition_state(
                call_session,
                TelephonyCallState.COMPLETED,
                occurred_at=now,
                hangup_reason="transfer_target_failed_hangup",
                event_detail={"transfer_v2": True, "reason": failure},
            )
            _save_session_transfer_parts(transfer, context, v2)
            await session.commit()
            voice = VoiceResponse()
            voice.say("I couldn't reach that team. Goodbye.")
            voice.hangup()
            return Response(str(voice), media_type="application/xml")
        if call_session.status in {"TRANSFERRING", "TRANSFERRED"}:
            await manager.transition_state(
                call_session,
                TelephonyCallState.IN_PROGRESS,
                occurred_at=now,
                event_detail={"transfer_v2": True, "fallback_action": "RETURN_TO_AGENT"},
            )
        greeting = "I wasn't able to reach them, but I can keep helping."

    _save_session_transfer_parts(transfer, context, v2)
    await session.commit()
    return Response(
        build_media_stream_twiml(call_sid, greeting_text=greeting),
        media_type="application/xml",
    )


@router.post("/transfer-session-target-status", response_class=PlainTextResponse)
async def transfer_session_target_status(
    request: Request,
    call_session_id: uuid.UUID,
    transfer_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Observe target-call progress and recover a parked caller if dialing fails."""
    if not await _verify_twilio(request):
        return PlainTextResponse("forbidden", status_code=403)
    form = await request.form()
    target_call_sid = str(form.get("CallSid") or "").strip()
    call_status = str(form.get("CallStatus") or "").strip().lower()
    call_session, transfer = await _load_session_transfer(
        session,
        call_session_id=call_session_id,
        transfer_id=transfer_id,
    )
    if call_session is None or transfer is None or not target_call_sid:
        return PlainTextResponse("ok")
    context, v2, plan = _session_transfer_parts(transfer)
    if v2.get("kind") != "conference":
        return PlainTextResponse("ok")
    known_target_sid = str(v2.get("target_call_sid") or "")
    if known_target_sid and known_target_sid != target_call_sid:
        return PlainTextResponse("ok")
    v2["target_call_sid"] = target_call_sid
    events = list(v2.get("provider_events") or [])
    events.append({"event": call_status[:32], "at": datetime.now(timezone.utc).isoformat()})
    v2["provider_events"] = events[-20:]

    terminal_target = call_status in {"busy", "failed", "no-answer", "canceled", "cancelled", "completed"}
    if terminal_target and transfer.status != TransferLifecycleState.COMPLETED.value:
        resumed = False
        try:
            await TwilioAdapter().resume_call(call_session.provider_call_id)
            resumed = True
        except Exception:
            resumed = False
        now = datetime.now(timezone.utc)
        transfer.status = TransferLifecycleState.FAILED.value
        transfer.failure_reason = "target_not_connected"
        transfer.completed_at = now
        call_session.transfer_state = TransferLifecycleState.FAILED.value
        v2["caller_resume_confirmed"] = resumed
        append_runtime_event(
            call_session,
            event_type=("transfer.cancelled" if resumed else "transfer.resume_unconfirmed"),
            detail={
                "transfer_id": str(transfer.id),
                "reason": "target_not_connected",
                "caller_resume_confirmed": resumed,
            },
            occurred_at=now,
        )
        if resumed and call_session.status in {"TRANSFERRING", "TRANSFERRED"}:
            await CallSessionManager(session).transition_state(
                call_session,
                TelephonyCallState.IN_PROGRESS,
                occurred_at=now,
                event_detail={"transfer_v2": True, "fallback_action": "RETURN_TO_AGENT"},
            )
    _save_session_transfer_parts(transfer, context, v2)
    await session.commit()
    return PlainTextResponse("ok")


@router.post("/transfer-session-conference", response_class=PlainTextResponse)
async def transfer_session_conference(
    request: Request,
    call_session_id: uuid.UUID,
    transfer_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> PlainTextResponse:
    """Track verified target joins/leaves and return the caller to AI after consult."""
    if not await _verify_twilio(request):
        return PlainTextResponse("forbidden", status_code=403)
    form = await request.form()
    event = str(form.get("StatusCallbackEvent") or "").strip().lower()
    participant_call_sid = str(form.get("CallSid") or "").strip()
    friendly_name = str(form.get("FriendlyName") or "").strip()
    call_session, transfer = await _load_session_transfer(
        session,
        call_session_id=call_session_id,
        transfer_id=transfer_id,
    )
    if call_session is None or transfer is None:
        return PlainTextResponse("ok")
    context, v2, _plan = _session_transfer_parts(transfer)
    if (
        v2.get("kind") != "conference"
        or friendly_name != str(v2.get("conference_name") or "")
        or not participant_call_sid
    ):
        return PlainTextResponse("ok")

    target_sid = str(v2.get("target_call_sid") or "")
    parent_sid = call_session.provider_call_id
    now = datetime.now(timezone.utc)
    manager = CallSessionManager(session)
    if event in {"participant-join", "join"} and participant_call_sid == target_sid:
        if v2.get("human_verified") is True and transfer.status != TransferLifecycleState.COMPLETED.value:
            transfer.status = TransferLifecycleState.COMPLETED.value
            call_session.transfer_state = TransferLifecycleState.COMPLETED.value
            transfer.completed_at = now
            if call_session.status == TelephonyCallState.TRANSFERRING.value:
                await manager.transition_state(
                    call_session,
                    TelephonyCallState.TRANSFERRED,
                    occurred_at=now,
                    event_detail={"transfer_v2": True, "conference_joined": True},
                )
            append_runtime_event(
                call_session,
                event_type="transfer.bridged",
                detail={"transfer_id": str(transfer.id), "conference_name": friendly_name},
                occurred_at=now,
            )
            append_transcript_turn(
                call_session,
                role="system",
                content=f"[{transfer.transfer_mode} transfer connected to {transfer.target_destination}]",
                metadata={"transfer_id": str(transfer.id)},
                occurred_at=now,
            )
    elif event in {"participant-leave", "leave"}:
        if participant_call_sid == target_sid and transfer.status == TransferLifecycleState.COMPLETED.value:
            resumed = False
            try:
                await TwilioAdapter().resume_call(parent_sid)
                resumed = True
            except Exception:
                resumed = False
            v2["caller_resume_confirmed"] = resumed
            v2["ended_at"] = now.isoformat()
            append_runtime_event(
                call_session,
                event_type="transfer.ended" if resumed else "transfer.resume_unconfirmed",
                detail={"transfer_id": str(transfer.id), "caller_resume_confirmed": resumed},
                occurred_at=now,
            )
            if resumed and call_session.status == TelephonyCallState.TRANSFERRED.value:
                await manager.transition_state(
                    call_session,
                    TelephonyCallState.IN_PROGRESS,
                    occurred_at=now,
                    event_detail={"transfer_v2": True, "resumed_after_conference": True},
                )
        elif participant_call_sid == parent_sid and call_session.status not in {
            TelephonyCallState.COMPLETED.value,
            TelephonyCallState.FAILED.value,
            TelephonyCallState.CANCELLED.value,
        }:
            v2["ended_at"] = now.isoformat()
            await manager.transition_state(
                call_session,
                TelephonyCallState.COMPLETED,
                occurred_at=now,
                hangup_reason="caller_left_transfer_conference",
                event_detail={"transfer_v2": True},
            )
            append_runtime_event(
                call_session,
                event_type="transfer.ended",
                detail={"transfer_id": str(transfer.id), "caller_left": True},
                occurred_at=now,
            )
    elif event in {"conference-end", "end"}:
        v2["conference_ended_at"] = now.isoformat()
        if transfer.completed_at is None:
            transfer.completed_at = now

    _save_session_transfer_parts(transfer, context, v2)
    await session.commit()
    return PlainTextResponse("ok")


@router.post("/transfer-whisper")
async def transfer_whisper(
    request: Request,
    call_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Privately brief a verified human before Twilio connects the caller.

    Twilio invokes the <Number url> only after its AMD result is available and
    before bridging the two call legs. Missing/unknown AMD results fail closed:
    the callee is hung up, while the caller remains on the parent <Dial> path.
    """
    if not await _verify_twilio(request):
        return PlainTextResponse("forbidden", status_code=403)

    call = (
        await session.execute(
            select(Call).where(Call.id == call_id)
            .with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    voice = VoiceResponse()
    if call is None:
        voice.hangup()
        return Response(str(voice), media_type="application/xml")

    transfer_context = dict(call.transfer_context or {})
    plan = dict(transfer_context.get("transfer_v2") or {})
    # This endpoint is only valid for a live plan created by the transfer
    # service. An absent/stale plan is not permission to bridge a party.
    if (
        call_state.is_terminal(call.status)
        or call.transfer_state not in (TransferState.REQUESTED, TransferState.DIALING)
        or not plan
        or not plan.get("require_human")
    ):
        voice.hangup()
        return Response(str(voice), media_type="application/xml")

    form = await request.form()
    answered_by = str(form.get("AnsweredBy") or "").strip()
    allowed, verdict = transfer_service.human_leg_verdict(
        {"AnsweredBy": answered_by} if answered_by else None,
        require_human=True,
    )
    plan["human_verified"] = allowed
    plan["amd_verdict"] = verdict
    transfer_context["transfer_v2"] = plan
    call.transfer_context = transfer_context
    await session.commit()

    if not allowed:
        # Do not disclose the machine-detection result to either party.
        voice.hangup()
        log.info(
            "transfer_amd_rejected",
            call_id=str(call.id),
            verdict=verdict,
        )
    else:
        whisper = transfer_service._safe_whisper_reason(
            str(plan.get("whisper") or "")
        )
        if whisper:
            voice.say(whisper, voice="Polly.Joanna")

    return Response(str(voice), media_type="application/xml")


@router.post("/transfer-status", response_class=PlainTextResponse)
async def transfer_status(
    request: Request,
    CallSid: str = Form(...),
    DialCallStatus: str = Form(default=""),
    DialCallDuration: str = Form(default="0"),
    session: AsyncSession = Depends(get_session),
):
    """
    Outcome of the <Dial> leg to the human.
    """
    if not await _verify_twilio(request):
        return PlainTextResponse("forbidden", status_code=403)

    call = (
        await session.execute(
            select(Call).where(Call.call_sid == CallSid)
            .with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if call is None:
        log.info("transfer_callback", result="unknown_call_sid", call_sid=CallSid)
        return PlainTextResponse("ok")

    outcome = (DialCallStatus or "").strip().lower()
    log.info(
        "transfer_callback",
        call_id=str(call.id),
        call_sid=CallSid,
        tenant_id=str(call.tenant_id),
        dial_status=outcome,
        transfer_state=call.transfer_state.value,
    )

    connected = False
    transfer_outcome = outcome
    if outcome in ("answered", "completed"):
        v2_plan = dict((call.transfer_context or {}).get("transfer_v2") or {})
        if v2_plan.get("require_human") and v2_plan.get("human_verified") is not True:
            failure_reason = str(v2_plan.get("amd_verdict") or "human_not_confirmed")
            inferred_failure = (
                call.transfer_state is TransferState.FAILED
                and (call.transfer_error or "").startswith(transfer_service.INFERRED_PREFIX)
            )
            changed = await transfer_service.mark_transfer_failed(
                session, call, failure_reason
            )
            if inferred_failure:
                call.transfer_error = failure_reason
                await publish_call_event(session, call, "transfer_failed")
                await transfer_service.record_transfer_v2_event(
                    session, call, "cancelled", detail=failure_reason
                )
                changed = True
            transfer_outcome = failure_reason
        else:
            changed = await transfer_service.mark_transfer_connected(session, call)
            connected = changed or call.transfer_state is TransferState.CONNECTED
            transfer_outcome = "connected" if connected else outcome
            if changed:
                tenant = await session.get(Tenant, call.tenant_id)
                await crm_hooks.on_transfer_completed(session, tenant, call)
    elif outcome in ("busy", "no-answer", "failed", "canceled", "cancelled"):
        inferred_failure = (
            call.transfer_state is TransferState.FAILED
            and (call.transfer_error or "").startswith(transfer_service.INFERRED_PREFIX)
        )
        changed = await transfer_service.mark_transfer_failed(session, call, outcome)
        if inferred_failure:
            call.transfer_error = outcome
            await publish_call_event(session, call, "transfer_failed")
            if dict((call.transfer_context or {}).get("transfer_v2") or {}):
                await transfer_service.record_transfer_v2_event(
                    session, call, "cancelled", detail=outcome
                )
            changed = True
    elif outcome in ("initiated", "ringing") and call.transfer_state in (
        TransferState.REQUESTED, TransferState.DIALING,
    ):
        await publish_call_event(session, call, "transfer_started")
        changed = False
    else:
        changed = False
        log.warning(
            "invalid_transfer_state",
            reason="unknown_dial_status",
            call_id=str(call.id),
            dial_status=outcome,
        )

    await session.commit()

    if outcome in ("answered", "completed"):
        if changed:
            await realtime_events.emit_call_event(
                call,
                kind="call.transfer",
                extra={"transfer_outcome": transfer_outcome},
            )
    elif outcome in ("busy", "no-answer", "failed", "canceled", "cancelled"):
        if changed:
            await realtime_events.emit_call_event(
                call, kind="call.transfer", extra={"transfer_outcome": outcome}
            )

    return PlainTextResponse("<Response/>", media_type="application/xml")


async def _push_to_crm(tenant: Tenant, call: Call) -> None:
    """Fire-and-forget CRM sync. Logged, never raised."""
    payload = crm.build_payload(
        tenant_name=tenant.name,
        call_id=str(call.id),
        direction=call.direction.value,
        from_number=call.from_number,
        to_number=call.to_number,
        duration_seconds=call.duration_seconds,
        intent=call.intent,
        summary=call.summary,
        booked=call.booked,
        escalated=call.escalated,
        lead_score=call.lead_score,
        recording_url=call.recording_url,
    )
    ok = await crm.push(
        webhook_url=tenant.crm_webhook_url,
        payload=payload,
        crm_type=tenant.crm_type,
        api_key=tenant.crm_api_key,
    )
    log.info("crm.sync", call=str(call.id), ok=ok)
