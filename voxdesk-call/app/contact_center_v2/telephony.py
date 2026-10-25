"""Signed-Twilio webhook branches mounted into the existing telephony handlers."""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import Request
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from twilio.twiml.voice_response import VoiceResponse

from app.contact_center_v2.dialer import finalize_attempt
from app.contact_center_v2.dialer.abandoned_call_handler import handle_abandoned_call
from app.contact_center_v2.errors import ContactCenterV2Error
from app.contact_center_v2.occupancy import (
    active_softphone_session,
    available_agent_ids,
    reserve_agent,
)
from app.contact_center_v2.repository import utcnow
from app.contact_center_v2.settings import settings
from app.contact_center_v2.softphone import (
    prepare_answered_dialer_call,
    record_agent_leg,
    reconcile_status as reconcile_softphone_status,
)
from app.contact_center_v2.softphone.media_bridge import conference_media
from app.contact_center_v2.twiml import rejection_twiml
from app.db.contact_center_v2_models import (
    DialerAttempt,
    DialerPolicy,
    SoftphoneCall,
    SoftphoneLeg,
)
from app.db.models import Call, CallStatus


def _xml(body: str, status_code: int = 200) -> PlainTextResponse:
    return PlainTextResponse(body, status_code=status_code, media_type="application/xml")


def _pause_redirect(request: Request, wait: int) -> PlainTextResponse:
    response = VoiceResponse()
    response.pause(length=1)
    response.redirect(str(request.url.include_query_params(cc2_wait=wait + 1)), method="POST")
    return _xml(str(response))


def _wait_count(request: Request) -> int:
    try:
        return min(10, max(0, int(request.query_params.get("cc2_wait", "0"))))
    except ValueError:
        return 10


async def _customer_conference(
    session: AsyncSession, call: SoftphoneCall, provider_call_id: str
) -> PlainTextResponse:
    legs = (
        await session.execute(
            select(SoftphoneLeg).where(
                SoftphoneLeg.tenant_id == call.tenant_id,
                SoftphoneLeg.softphone_call_id == call.id,
                SoftphoneLeg.kind == "customer",
            )
        )
    ).scalars().all()
    leg = legs[0] if legs else None
    if leg is None or leg.provider_call_id != provider_call_id:
        return _xml(rejection_twiml())
    now = utcnow()
    leg.status = "in_progress"
    leg.answered_at = leg.answered_at or now
    call.answered_at = call.answered_at or now
    call.status = "waiting_for_agent"
    call.agent_join_deadline = call.agent_join_deadline or (
        now + timedelta(seconds=settings.dialer_agent_join_timeout_seconds)
    )
    if call.call_id:
        canonical = await session.get(Call, call.call_id)
        if canonical is not None and canonical.tenant_id == call.tenant_id:
            canonical.status = CallStatus.IN_PROGRESS
    await session.commit()
    return _xml(conference_media(call, customer=True))


async def handle_voice(
    request: Request,
    session: AsyncSession,
    *,
    call_sid: str,
    from_uri: str,
) -> PlainTextResponse | None:
    """Return None for ordinary inbound calls; handle only explicit cc2 branches."""
    kind = request.query_params.get("cc2", "")
    if not kind:
        return None
    try:
        if kind == "softphone-customer":
            call = await session.get(SoftphoneCall, uuid.UUID(request.query_params["call_id"]))
            if call is None:
                return _pause_redirect(request, _wait_count(request)) if _wait_count(request) < 1 else _xml(rejection_twiml())
            return await _customer_conference(session, call, call_sid)
        if kind == "callback-customer":
            callback_id = uuid.UUID(request.query_params["callback_id"])
            call = (
                await session.execute(
                    select(SoftphoneCall)
                    .where(
                        SoftphoneCall.callback_id == callback_id,
                        SoftphoneCall.origin == "callback",
                    )
                    .order_by(SoftphoneCall.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if call is None:
                return _pause_redirect(request, _wait_count(request)) if _wait_count(request) < 1 else _xml(rejection_twiml())
            return await _customer_conference(session, call, call_sid)
        if kind == "softphone-agent":
            # TwiML App custom connect parameters arrive in the signed POST
            # form; the static Voice URL carries only cc2=softphone-agent.
            form = await request.form()
            call_id = uuid.UUID(str(
                request.query_params.get("call_id") or form.get("call_id") or ""
            ))
            session_id = uuid.UUID(str(
                request.query_params.get("session_id") or form.get("session_id") or ""
            ))
            await record_agent_leg(
                session,
                call_id=call_id,
                softphone_session_id=session_id,
                provider_call_id=call_sid,
                from_uri=from_uri,
            )
            call = await session.get(SoftphoneCall, call_id)
            if call is None:
                return _xml(rejection_twiml())
            await session.commit()
            return _xml(conference_media(call, customer=False))
        if kind == "softphone-consult":
            call_id = uuid.UUID(request.query_params["call_id"])
            leg_id = uuid.UUID(request.query_params["leg_id"])
            call = await session.get(SoftphoneCall, call_id)
            leg = await session.get(SoftphoneLeg, leg_id)
            if (
                call is None
                or leg is None
                or leg.tenant_id != call.tenant_id
                or leg.softphone_call_id != call.id
                or leg.kind != "consult"
                or leg.provider_call_id != call_sid
            ):
                return _xml(rejection_twiml())
            leg.status = "in_progress"
            leg.answered_at = leg.answered_at or utcnow()
            await session.commit()
            return _xml(conference_media(call, customer=False))
        return _xml(rejection_twiml())
    except (ValueError, KeyError, ContactCenterV2Error):
        await session.rollback()
        return _xml(rejection_twiml())


async def handle_outbound_answer(
    request: Request,
    session: AsyncSession,
    *,
    campaign_id: str,
    call_sid: str,
    answered_by: str,
) -> PlainTextResponse | None:
    if not campaign_id.startswith("dialer:"):
        # AI blast uses the canonical campaign answer URL so the existing AI
        # media stream remains authoritative. Reconcile its CC2 ledger first.
        attempt = (
            await session.execute(
                select(DialerAttempt).where(
                    DialerAttempt.provider_call_id == call_sid,
                    DialerAttempt.mode == "ai_agent_blast",
                )
            )
        ).scalar_one_or_none()
        if attempt is None:
            return None
        policy = await session.get(DialerPolicy, attempt.policy_id)
        if (
            policy is None
            or policy.tenant_id != attempt.tenant_id
            or str(policy.campaign_id) != campaign_id
        ):
            return _xml(rejection_twiml())
        now = utcnow()
        attempt.answered_by = answered_by or "human"
        attempt.answered_at = attempt.answered_at or now
        if answered_by.startswith("machine"):
            await finalize_attempt(session, attempt=attempt, outcome="machine", failure_category="machine", now=now)
        elif attempt.outcome not in {"machine", "abandoned", "failed", "completed", "cancelled"}:
            attempt.agent_connected_at = attempt.agent_connected_at or now
            attempt.outcome = "answered"
            attempt.updated_at = now
        await session.commit()
        return None
    try:
        attempt_id = uuid.UUID(campaign_id.split(":", 1)[1])
    except ValueError:
        return _xml(rejection_twiml())
    attempt = await session.get(DialerAttempt, attempt_id)
    if attempt is None:
        return _pause_redirect(request, _wait_count(request)) if _wait_count(request) < 1 else _xml(rejection_twiml())
    if attempt.provider_call_id and attempt.provider_call_id != call_sid:
        return _xml(rejection_twiml())
    policy = await session.get(DialerPolicy, attempt.policy_id)
    if policy is None or policy.tenant_id != attempt.tenant_id:
        return _xml(rejection_twiml())
    if answered_by.startswith("machine"):
        attempt.answered_by = answered_by
        attempt.answered_at = attempt.answered_at or utcnow()
        await finalize_attempt(session, attempt=attempt, outcome="machine", failure_category="machine")
        await session.commit()
        response = VoiceResponse()
        response.hangup()
        return _xml(str(response))
    if attempt.call_id is None or attempt.provider_call_id is None:
        if _wait_count(request) < 1:
            return _pause_redirect(request, _wait_count(request))
        return await handle_abandoned_call(session, attempt=attempt, policy=policy)
    attempt.answered_by = answered_by or "human"
    attempt.answered_at = attempt.answered_at or utcnow()
    if attempt.agent_user_id is None:
        agents = await available_agent_ids(
            session, attempt.tenant_id, require_softphone=True, environment_id=attempt.environment_id
        )
        if not agents:
            return await handle_abandoned_call(session, attempt=attempt, policy=policy)
        attempt.agent_user_id = agents[0]
        try:
            await reserve_agent(
                session,
                tenant_id=attempt.tenant_id,
                environment_id=attempt.environment_id,
                user_id=agents[0],
                kind="dialer",
                resource_id=attempt.id,
                require_softphone=True,
            )
        except ContactCenterV2Error:
            return await handle_abandoned_call(session, attempt=attempt, policy=policy)
    registration = await active_softphone_session(
        session, attempt.tenant_id, attempt.agent_user_id
    )
    if registration is None or registration.environment_id != attempt.environment_id:
        return await handle_abandoned_call(session, attempt=attempt, policy=policy)
    try:
        call = await prepare_answered_dialer_call(
            session,
            attempt=attempt,
            registration=registration,
            provider_call_id=call_sid,
        )
    except ContactCenterV2Error:
        return await handle_abandoned_call(session, attempt=attempt, policy=policy)
    await session.commit()
    return _xml(conference_media(call, customer=True))


async def reconcile_status(
    session: AsyncSession,
    *,
    provider_call_id: str,
    provider_status: str,
    duration_seconds: int = 0,
    answered_by: str | None = None,
) -> SoftphoneCall | DialerAttempt | None:
    """Reconcile a signed status callback with a CC2 leg or a dialer-only attempt.

    Ordinary campaign calls return ``None`` and continue through the canonical
    telephony path. A recognized CC2 row is updated idempotently and is never
    inferred from a caller-supplied tenant or campaign identifier.
    """
    call = await reconcile_softphone_status(
        session,
        provider_call_id=provider_call_id,
        provider_status=provider_status,
        duration=max(0, int(duration_seconds)),
        answered_by=answered_by,
    )
    if call is not None:
        return call
    attempt = (
        await session.execute(
            select(DialerAttempt).where(DialerAttempt.provider_call_id == provider_call_id)
        )
    ).scalar_one_or_none()
    if attempt is None:
        return None
    normalized = provider_status.strip().lower()
    if answered_by:
        attempt.answered_by = answered_by
    if normalized not in {"completed", "busy", "no-answer", "failed", "canceled", "cancelled"}:
        if normalized in {"in-progress", "answered"}:
            attempt.answered_at = attempt.answered_at or utcnow()
            if attempt.outcome == "ringing":
                attempt.outcome = "answered"
                attempt.updated_at = utcnow()
        await session.flush()
        return attempt
    if answered_by and answered_by.startswith("machine"):
        outcome = "machine"
    elif normalized in {"canceled", "cancelled"}:
        outcome = "cancelled"
    elif normalized in {"busy", "no-answer", "failed"}:
        outcome = "failed"
    elif attempt.abandonment_counted or (
        attempt.answered_at is not None and attempt.agent_connected_at is None
    ):
        outcome = "abandoned"
    elif attempt.agent_connected_at is not None:
        outcome = "completed"
    else:
        outcome = "failed"
    await finalize_attempt(
        session,
        attempt=attempt,
        outcome=outcome,
        duration_seconds=max(0, int(duration_seconds)),
        failure_category=normalized[:64],
    )
    await session.flush()
    return attempt


__all__ = ["handle_outbound_answer", "handle_voice", "reconcile_status"]
