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

from fastapi import APIRouter, Depends, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
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
    CallStatus,
    Lead,
    LeadStatus,
    Tenant,
    TransferState,
)
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
    if AnsweredBy.startswith("machine"):
        # Only documented provider verdicts are customer-facing facts. Unknown
        # machine-prefixed values retain the old hangup behavior, not a verdict.
        recognized_machine = AnsweredBy in {
            "machine_start", "machine_end_beep", "machine_end_silence", "machine_end_other",
        }
        if recognized_machine:
            call = (await session.execute(select(Call).where(
                Call.call_sid == CallSid,
            ))).scalar_one_or_none()
            if call is not None:
                await publish_call_event(session, call, "voicemail_detected")
                await session.commit()
        log.info("outbound.machine_hangup", lead=lead_id, recognized=recognized_machine)
        response.hangup()
        return PlainTextResponse(str(response), media_type="application/xml")

    connect = Connect()
    connect.stream(
        url=(
            f"{settings.ws_base_url}/telephony/ws"
            f"?token={create_stream_token(CallSid)}"
            f"&campaign_id={campaign_id}&lead_id={lead_id}"
        )
    )
    response.append(connect)
    log.info("outbound.answered", lead=lead_id, campaign=campaign_id)
    return PlainTextResponse(str(response), media_type="application/xml")


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
        await websocket.close(code=1012)
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
    except (WebSocketDisconnect, asyncio.TimeoutError):
        log.warning("ws.handshake_incomplete")
        await websocket.close(code=1008)  # policy violation
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
        await websocket.close(code=1008)  # policy violation
        return

    async with get_sessionmaker()() as session:
        call = (
            await session.execute(select(Call).where(Call.call_sid == call_sid))
        ).scalar_one_or_none()
        if call is None:
            await websocket.close()
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

    call = (
        await session.execute(
            select(Call).where(Call.call_sid == CallSid)
            .with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if call is None:
        # Unknown SID: acknowledge so Twilio stops retrying, but do nothing.
        log.info("status.unknown_call_sid", call_sid=CallSid)
        return PlainTextResponse("ok")

    try:
        duration = float(CallDuration or 0)
    except ValueError:
        duration = 0.0

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

    if outcome in ("answered", "completed"):
        changed = await transfer_service.mark_transfer_connected(session, call)
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
                call, kind="call.transfer", extra={"transfer_outcome": "connected"}
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
