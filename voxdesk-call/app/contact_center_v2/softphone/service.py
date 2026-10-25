"""Twilio Voice SDK sessions, provider conferences, and signed-event reconciliation.

REST acknowledgements authorize only the requested transition. Customer and
agent legs become connected from signed webhooks, never from local row creation.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from app.auth.dependencies import TenantContext
from app.contact_center.agent_state import transition
from app.contact_center.repository import presence_for
from app.contact_center_v2.dialer import finalize_attempt
from app.contact_center_v2.errors import (
    Conflict,
    ContactCenterV2Error,
    NotConfigured,
    NotFound,
    PermissionDenied,
    ProviderAmbiguous,
    ProviderFailure,
    Validation,
)
from app.contact_center_v2.occupancy import record_work_mode, release_agent, reserve_agent
from app.contact_center_v2.policy import latest_consent, verified_caller_id
from app.contact_center_v2.repository import as_utc, utcnow
from app.contact_center_v2.settings import ContactCenterV2Settings, settings
from app.contact_center_v2.softphone.ice_config import ice_servers
from app.contact_center_v2.twiml import safe_harbor_twiml
from app.core.config import settings as core_settings
from app.core.logging import log
from app.db.contact_center_v2_models import (
    AgentReservation,
    DialerAttempt,
    DialerPolicy,
    SoftphoneCall,
    SoftphoneLeg,
    SoftphoneSession,
)
from app.db.models import (
    Call,
    CallDirection,
    CallStatus,
    Environment,
    Lead,
    RequestIdempotencyReceipt,
    TransferState,
)
from app.resilience.idempotency import (
    claim_request,
    complete_request,
    fail_request,
    request_digest,
)
from app.telephony import dnc, phone
from app.telephony.providers.twilio import TwilioAdapter

CALL_ACTIVE = ("pending", "dialing", "waiting_for_agent", "in_progress", "held")
CALL_TERMINAL = ("completed", "failed", "cancelled", "transferred")
LEG_TERMINAL = {"completed", "failed", "cancelled"}


def _adapter() -> TwilioAdapter:
    return TwilioAdapter()


async def _bounded(awaitable: Any) -> Any:
    """Bound provider waits without claiming that a timed-out thread stopped."""
    return await asyncio.wait_for(
        awaitable, timeout=settings.softphone_provider_timeout_seconds
    )


async def run_provider(method: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Run an async provider adapter off the event loop with a hard wait bound.

    The Twilio SDK is synchronous inside several async adapters. A separate
    thread plus a fresh event loop prevents that SDK from blocking request or
    scheduler loops. Timeout/cancellation is an ambiguous provider outcome;
    callers must preserve their durable pre-provider intent.
    """
    return await _bounded(
        asyncio.to_thread(lambda: asyncio.run(method(*args, **kwargs)))
    )


def _conference_name(call_id: uuid.UUID) -> str:
    return f"voxdesk-{call_id}"


def _public_url(path: str) -> str:
    base = (core_settings.public_base_url or "").strip().rstrip("/")
    if not base.startswith(("https://", "http://")):
        raise NotConfigured("A public base URL is required for softphone webhooks")
    return f"{base}{path}"


def _key_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def access_token(
    row: SoftphoneSession, config: ContactCenterV2Settings = settings
) -> tuple[str, int]:
    account_sid = (core_settings.twilio_account_sid or "").strip()
    api_key = config.softphone_twilio_api_key_sid.strip()
    api_secret = config.softphone_twilio_api_key_secret.strip()
    application_sid = config.softphone_twilio_twiml_app_sid.strip()
    if not all((account_sid, api_key, api_secret, application_sid)):
        raise NotConfigured(
            "Twilio account SID, Voice API key/secret, and TwiML App SID are required",
            missing=[
                name
                for name, value in (
                    ("TWILIO_ACCOUNT_SID", account_sid),
                    ("CC2_SOFTPHONE_TWILIO_API_KEY_SID", api_key),
                    ("CC2_SOFTPHONE_TWILIO_API_KEY_SECRET", api_secret),
                    ("CC2_SOFTPHONE_TWILIO_TWIML_APP_SID", application_sid),
                )
                if not value
            ],
        )
    try:
        from twilio.jwt.access_token import AccessToken
        from twilio.jwt.access_token.grants import VoiceGrant
    except ImportError as exc:
        raise NotConfigured("The Twilio Python SDK is not installed") from exc
    remaining = max(1, int((as_utc(row.expires_at) - utcnow()).total_seconds()))
    ttl = min(config.softphone_token_ttl_seconds, remaining)
    token = AccessToken(account_sid, api_key, api_secret, identity=row.client_identity, ttl=ttl)
    token.add_grant(VoiceGrant(outgoing_application_sid=application_sid, incoming_allow=True))
    return token.to_jwt(), ttl


def session_payload(
    row: SoftphoneSession,
    config: ContactCenterV2Settings = settings,
    *,
    include_token: bool = True,
) -> dict[str, Any]:
    payload = row.as_dict()
    payload["identity"] = row.client_identity
    payload["ice_servers"] = ice_servers(row.user_id, config)
    if include_token:
        token, ttl = access_token(row, config)
        payload["token"] = token
        payload["token_ttl_seconds"] = ttl
    ticket = getattr(row, "_cc2_websocket_ticket", None)
    if ticket:
        payload["websocket_ticket"] = ticket
        payload["signaling_url"] = "/api/cc2/softphone/signaling"
    return payload


async def _environment(
    session: AsyncSession, context: TenantContext, environment_id: uuid.UUID
) -> Environment:
    row = await session.get(Environment, environment_id)
    if row is None or row.tenant_id != context.tenant_id:
        raise NotFound("environment")
    if row.status != "active" or not row.is_default or row.kind != "production":
        raise PermissionDenied("Softphone calls require the active default production environment")
    return row


async def register_session(
    session: AsyncSession,
    context: TenantContext,
    *,
    environment_id: uuid.UUID,
    device_label: str,
    idempotency_key: str,
    config: ContactCenterV2Settings = settings,
    device_fingerprint: str | None = None,
    device_kind: str = "browser",
) -> SoftphoneSession:
    environment = await _environment(session, context, environment_id)
    now = utcnow()
    identity = f"agent-{context.user_id}"
    from app.contact_center_v2.softphone.devices import register_device

    device = await register_device(
        session,
        tenant_id=context.tenant_id,
        environment_id=environment.id,
        user_id=context.user_id,
        label=device_label,
        fingerprint=device_fingerprint or f"{identity}:{device_label.strip()}",
        kind=device_kind,
        now=now,
    )
    probe = SoftphoneSession(
        tenant_id=context.tenant_id,
        environment_id=environment.id,
        user_id=context.user_id,
        device_id=device.id,
        active_device_key=str(device.id),
        client_identity=identity,
        registration_key="probe",
        request_digest="probe",
        device_label=device_label,
        status="active",
        expires_at=now + timedelta(seconds=config.softphone_session_ttl_seconds),
    )
    access_token(probe, config)
    ice_servers(context.user_id, config)
    digest = request_digest(
        {
            "environment_id": str(environment.id),
            "identity": identity,
            "device_label": device_label,
        }
    )
    key = _key_hash(idempotency_key)
    existing = (
        await session.execute(
            select(SoftphoneSession).where(
                SoftphoneSession.tenant_id == context.tenant_id,
                SoftphoneSession.registration_key == key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_digest != digest or existing.user_id != context.user_id:
            raise Conflict("Idempotency key was already used for another registration")
        if existing.status != "active" or as_utc(existing.expires_at) <= now:
            raise Conflict("The idempotent registration expired; use a new idempotency key")
        return existing
    # One human has one current client identity. Re-registration replaces only
    # an inactive registration; active sessions must be heartbeated or revoked.
    current = (
        await session.execute(
            select(SoftphoneSession).where(SoftphoneSession.client_identity == identity)
        )
    ).scalar_one_or_none()
    if current is not None:
        if current.status == "active" and as_utc(current.expires_at) > now:
            if current.user_id != context.user_id or current.tenant_id != context.tenant_id:
                raise PermissionDenied("The softphone identity is already registered")
            current.device_label = device_label
            current.device_id = device.id
            current.active_device_key = str(device.id)
            current.last_heartbeat_at = now
            current.expires_at = probe.expires_at
            await session.flush()
            return current
        current.status = "revoked"
        current.revoked_at = now
        current.active_device_key = None
        current.ws_ticket_hash = ""
        await session.flush()
    row = SoftphoneSession(
        tenant_id=context.tenant_id,
        environment_id=environment.id,
        user_id=context.user_id,
        device_id=device.id,
        active_device_key=str(device.id),
        client_identity=identity,
        registration_key=key,
        request_digest=digest,
        device_label=device_label,
        status="active",
        expires_at=probe.expires_at,
        last_heartbeat_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def heartbeat_session(
    session: AsyncSession,
    context: TenantContext,
    session_id: uuid.UUID,
    config: ContactCenterV2Settings = settings,
) -> SoftphoneSession:
    row = await session.get(SoftphoneSession, session_id)
    if row is None or row.tenant_id != context.tenant_id:
        raise NotFound("softphone session")
    if row.user_id != context.user_id:
        raise PermissionDenied("Only the registering agent can heartbeat this session")
    if row.status != "active" or as_utc(row.expires_at) <= utcnow():
        raise Conflict("Softphone session is not active")
    moment = utcnow()
    result = await session.execute(
        update(SoftphoneSession)
        .where(
            SoftphoneSession.id == row.id,
            SoftphoneSession.tenant_id == context.tenant_id,
            SoftphoneSession.status == "active",
        )
        .values(
            last_heartbeat_at=moment,
            expires_at=moment + timedelta(seconds=config.softphone_session_ttl_seconds),
        )
    )
    if result.rowcount != 1:
        raise Conflict("Softphone session changed concurrently")
    await session.refresh(row)
    if row.device_id is not None:
        from app.contact_center_v2.softphone.devices import heartbeat_device

        await heartbeat_device(
            session, tenant_id=context.tenant_id, user_id=context.user_id,
            device_id=row.device_id, now=moment,
        )
    return row


async def revoke_session(
    session: AsyncSession, context: TenantContext, session_id: uuid.UUID
) -> SoftphoneSession:
    row = await session.get(SoftphoneSession, session_id)
    if row is None or row.tenant_id != context.tenant_id:
        raise NotFound("softphone session")
    if row.user_id != context.user_id:
        raise PermissionDenied("Only the registering agent can revoke this session")
    active = (
        await session.execute(
            select(SoftphoneCall.id).where(
                SoftphoneCall.tenant_id == context.tenant_id,
                SoftphoneCall.session_id == row.id,
                SoftphoneCall.status.in_(CALL_ACTIVE),
            )
        )
    ).first()
    if active is not None:
        raise Conflict("End active calls before revoking the softphone session")
    if row.status == "active":
        row.status = "revoked"
        row.revoked_at = utcnow()
        row.active_device_key = None
        row.ws_ticket_hash = ""
        await session.flush()
    return row


async def _owned_session(
    session: AsyncSession, context: TenantContext, session_id: uuid.UUID
) -> SoftphoneSession:
    row = await session.get(SoftphoneSession, session_id)
    if row is None or row.tenant_id != context.tenant_id:
        raise NotFound("softphone session")
    if row.user_id != context.user_id:
        raise PermissionDenied("This softphone session belongs to another agent")
    if row.status != "active" or as_utc(row.expires_at) <= utcnow():
        raise Conflict("Softphone session is expired or revoked")
    return row


async def _require_direct_consent(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    destination: str,
) -> None:
    leads = (
        await session.execute(
            select(Lead).where(
                Lead.tenant_id == tenant_id,
                Lead.environment_id == environment_id,
                Lead.phone == destination,
            )
        )
    ).scalars().all()
    for lead in leads:
        consent = await latest_consent(session, lead)
        if (
            consent is not None
            and consent.decision == "granted"
            and consent.source
            and consent.recorded_at is not None
        ):
            return
    raise PermissionDenied("Direct outbound dialing requires current affirmative voice consent")


async def create_outbound_call(
    session: AsyncSession,
    context: TenantContext,
    *,
    softphone_session_id: uuid.UUID,
    destination: str,
    caller_id_number: str,
    idempotency_key: str,
    adapter: TwilioAdapter | None = None,
) -> tuple[SoftphoneCall, bool]:
    adapter = adapter or _adapter()
    if not adapter.configured():
        raise NotConfigured("Twilio account SID and auth token are required")
    registration = await _owned_session(session, context, softphone_session_id)
    try:
        to_number = phone.normalize(destination)
        from_number = phone.normalize(caller_id_number)
    except ValueError as exc:
        raise Validation(str(exc)) from exc
    environment = await _environment(session, context, registration.environment_id)
    blocked, _ = await dnc.is_blocked(
        session, context.tenant_id, to_number, environment_id=environment.id
    )
    if blocked:
        raise PermissionDenied("Destination is suppressed by the DNC registry")
    await _require_direct_consent(
        session,
        tenant_id=context.tenant_id,
        environment_id=environment.id,
        destination=to_number,
    )
    policy_probe = DialerPolicy(
        tenant_id=context.tenant_id,
        environment_id=environment.id,
        caller_id_number=from_number,
    )
    await verified_caller_id(session, policy_probe)
    payload = {
        "session_id": str(registration.id),
        "destination": to_number,
        "caller_id_number": from_number,
    }
    claim = await claim_request(
        session,
        tenant_id=context.tenant_id,
        environment_id=environment.id,
        operation="contact_center_v2.softphone.outbound",
        key=idempotency_key,
        request_data=payload,
    )
    receipt = claim.receipt
    if not claim.created:
        existing = await session.get(SoftphoneCall, uuid.UUID(receipt.resource_id))
        if existing is None or existing.tenant_id != context.tenant_id:
            raise NotFound("softphone call")
        return existing, True
    call = SoftphoneCall(
        tenant_id=context.tenant_id,
        environment_id=environment.id,
        session_id=registration.id,
        agent_user_id=context.user_id,
        origin="agent_outbound",
        status="dialing",
        conference_name="pending",
        idempotency_key=_key_hash(idempotency_key),
    )
    session.add(call)
    await session.flush()
    call.conference_name = _conference_name(call.id)
    await reserve_agent(
        session,
        tenant_id=context.tenant_id,
        environment_id=environment.id,
        user_id=context.user_id,
        kind="softphone",
        resource_id=call.id,
        require_softphone=True,
    )
    answer_url = _public_url(f"/telephony/voice?cc2=softphone-customer&call_id={call.id}")
    # Receipt, intent, and reservation become durable atomically before the
    # carrier side effect. A pre-provider validation error rolls back all three.
    await session.commit()
    receipt_id = receipt.id
    call_id = call.id
    try:
        result = await run_provider(
            adapter.create_outbound,
            to_number=to_number,
            from_number=from_number,
            answer_url=answer_url,
        )
    except Exception as exc:
        try:
            await session.rollback()
            await _mark_outbound_ambiguous(
                session,
                tenant_id=context.tenant_id,
                call_id=call_id,
                receipt_id=receipt_id,
                category="provider_outcome_ambiguous",
            )
        except Exception as persistence_exc:
            log.error(
                "cc2_softphone_provider_ambiguity_persistence_failed",
                call_id=str(call_id),
                error_type=type(persistence_exc).__name__,
            )
        raise ProviderAmbiguous() from exc

    # The provider returned a SID. Persistence failures below are never
    # reclassified as a rejected call and never release the agent for a redial.
    try:
        canonical = Call(
            tenant_id=context.tenant_id,
            environment_id=environment.id,
            call_sid=result.call_id,
            direction=CallDirection.OUTBOUND,
            from_number=result.from_number or from_number,
            to_number=result.to_number or to_number,
            status=CallStatus.RINGING,
            started_at=utcnow(),
        )
        session.add(canonical)
        await session.flush()
        call.call_id = canonical.id
        leg = SoftphoneLeg(
            tenant_id=context.tenant_id,
            environment_id=environment.id,
            softphone_call_id=call.id,
            call_id=canonical.id,
            kind="customer",
            provider_call_id=result.call_id,
            idempotency_key=f"customer:{call.id}",
            status="ringing",
            destination_redacted=phone.redact(to_number),
        )
        session.add(leg)
        current_receipt = await session.get(RequestIdempotencyReceipt, receipt_id)
        if current_receipt is None:
            raise LookupError("softphone idempotency receipt disappeared")
        await complete_request(
            session, current_receipt, resource_type="softphone_call", resource_id=call.id
        )
        await session.commit()
        return call, False
    except Exception as exc:
        try:
            await session.rollback()
            await _mark_outbound_ambiguous(
                session,
                tenant_id=context.tenant_id,
                call_id=call_id,
                receipt_id=receipt_id,
                category="post_provider_persistence_ambiguous",
            )
        except Exception as persistence_exc:
            # The committed dialing row/receipt still fail closed even if this
            # secondary marker cannot be written. Never turn this into a retry.
            log.error(
                "cc2_softphone_ambiguity_persistence_failed",
                call_id=str(call_id),
                error_type=type(persistence_exc).__name__,
            )
        raise ProviderAmbiguous(
            "Twilio returned a call SID but local persistence is ambiguous; reconcile before retrying"
        ) from exc


async def _mark_outbound_ambiguous(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    receipt_id: uuid.UUID,
    category: str,
) -> None:
    current = await session.get(SoftphoneCall, call_id)
    if current is not None and current.tenant_id == tenant_id and current.status == "dialing":
        current.failure_category = category
        current.updated_at = utcnow()
        # Keep status=dialing and the reservation. The provider may have a live
        # leg, so local cleanup or another attempt would be unsafe.
        await session.flush()
    receipt = await session.get(RequestIdempotencyReceipt, receipt_id)
    if receipt is not None and receipt.tenant_id == tenant_id:
        await fail_request(session, receipt, category=category)
    await session.commit()

async def _reservation(
    session: AsyncSession, tenant_id: uuid.UUID, kind: str, resource_id: uuid.UUID
) -> AgentReservation | None:
    return (
        await session.execute(
            select(AgentReservation).where(
                AgentReservation.tenant_id == tenant_id,
                AgentReservation.kind == kind,
                AgentReservation.resource_id == resource_id,
                AgentReservation.status == "active",
            )
        )
    ).scalar_one_or_none()


async def get_call(
    session: AsyncSession,
    context: TenantContext,
    call_id: uuid.UUID,
    *,
    supervisor: bool = False,
) -> SoftphoneCall:
    row = await session.get(SoftphoneCall, call_id)
    if row is None or row.tenant_id != context.tenant_id:
        raise NotFound("softphone call")
    if not supervisor and row.agent_user_id != context.user_id:
        raise PermissionDenied("This call belongs to another agent")
    return row


async def leg_rows(session: AsyncSession, call: SoftphoneCall) -> list[SoftphoneLeg]:
    return (
        await session.execute(
            select(SoftphoneLeg)
            .where(
                SoftphoneLeg.tenant_id == call.tenant_id,
                SoftphoneLeg.softphone_call_id == call.id,
            )
            .order_by(SoftphoneLeg.created_at, SoftphoneLeg.id)
        )
    ).scalars().all()


async def call_payload(session: AsyncSession, row: SoftphoneCall) -> dict[str, Any]:
    payload = row.as_dict()
    legs = await leg_rows(session, row)
    payload["legs"] = [leg.as_dict() for leg in legs]
    agent = next((leg for leg in legs if leg.kind == "agent"), None)
    payload["agent_joined_at"] = agent.answered_at.isoformat() if agent and agent.answered_at else None
    canonical = await session.get(Call, row.call_id) if row.call_id else None
    if canonical is not None and canonical.tenant_id != row.tenant_id:
        canonical = None
    from app.contact_center_v2.softphone.media_bridge import customer_context, warm_transfer_briefing

    payload["warm_transfer_briefing"] = warm_transfer_briefing(canonical)
    payload["customer_context"] = await customer_context(session, row, canonical)
    recording = dict((canonical.transfer_context or {}).get("cc2_recording") or {}) if canonical else {}
    payload["recording_paused"] = recording.get("paused") if isinstance(recording.get("paused"), bool) else None
    return payload


async def prepare_answered_dialer_call(
    session: AsyncSession,
    *,
    attempt: DialerAttempt,
    registration: SoftphoneSession,
    provider_call_id: str,
    callback_id: uuid.UUID | None = None,
) -> SoftphoneCall:
    existing = (
        await session.execute(
            select(SoftphoneCall).where(
                SoftphoneCall.tenant_id == attempt.tenant_id,
                SoftphoneCall.dialer_attempt_id == attempt.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    canonical = await session.get(Call, attempt.call_id) if attempt.call_id else None
    if canonical is None or canonical.tenant_id != attempt.tenant_id:
        raise NotFound("canonical dialer call")
    if (
        registration.tenant_id != attempt.tenant_id
        or registration.environment_id != attempt.environment_id
        or registration.user_id != attempt.agent_user_id
        or registration.status != "active"
        or as_utc(registration.expires_at) <= utcnow()
    ):
        raise PermissionDenied("The reserved agent has no active softphone session")
    now = utcnow()
    call = SoftphoneCall(
        tenant_id=attempt.tenant_id,
        environment_id=attempt.environment_id,
        session_id=registration.id,
        agent_user_id=registration.user_id,
        origin="callback" if callback_id else "dialer",
        status="waiting_for_agent",
        call_id=canonical.id,
        dialer_attempt_id=attempt.id,
        callback_id=callback_id,
        conference_name="pending",
        idempotency_key=f"dialer:{attempt.id}",
        answered_at=now,
        agent_join_deadline=now + timedelta(seconds=settings.dialer_agent_join_timeout_seconds),
    )
    session.add(call)
    await session.flush()
    call.conference_name = _conference_name(call.id)
    leg = SoftphoneLeg(
        tenant_id=attempt.tenant_id,
        environment_id=attempt.environment_id,
        softphone_call_id=call.id,
        call_id=canonical.id,
        kind="customer",
        provider_call_id=provider_call_id,
        idempotency_key=f"customer:{call.id}",
        status="in_progress",
        destination_redacted=phone.redact(canonical.to_number),
        answered_at=now,
    )
    session.add(leg)
    attempt.outcome = "answered"
    attempt.answered_at = attempt.answered_at or now
    attempt.updated_at = now
    await session.flush()
    return call


async def prepare_acd_call(
    session: AsyncSession,
    *,
    entry_id: uuid.UUID,
    agent_user_id: uuid.UUID,
    adapter: TwilioAdapter | None = None,
) -> SoftphoneCall:
    """Redirect an existing live ACD call to the assigned agent's conference."""
    from app.contact_center.models import QueueEntry
    from app.contact_center_v2.occupancy import active_softphone_session

    adapter = adapter or _adapter()
    entry = await session.get(QueueEntry, entry_id)
    if entry is None:
        raise NotFound("queue entry")
    registration = await active_softphone_session(
        session, entry.tenant_id, agent_user_id
    )
    if registration is None or registration.environment_id != entry.environment_id:
        raise PermissionDenied("The assigned agent has no active softphone in this environment")
    canonical = await session.get(Call, entry.call_id)
    if (
        canonical is None
        or canonical.tenant_id != entry.tenant_id
        or canonical.status is not CallStatus.IN_PROGRESS
    ):
        raise Conflict("ACD softphone bridging requires a live canonical call")
    existing = (
        await session.execute(
            select(SoftphoneCall).where(
                SoftphoneCall.tenant_id == entry.tenant_id,
                SoftphoneCall.acd_entry_id == entry.id,
                SoftphoneCall.status.in_(CALL_ACTIVE),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    now = utcnow()
    call = SoftphoneCall(
        tenant_id=entry.tenant_id,
        environment_id=entry.environment_id,
        session_id=registration.id,
        agent_user_id=agent_user_id,
        origin="acd",
        status="waiting_for_agent",
        call_id=canonical.id,
        acd_entry_id=entry.id,
        conference_name="pending",
        idempotency_key=f"acd:{entry.id}",
        answered_at=now,
        agent_join_deadline=now + timedelta(seconds=settings.dialer_agent_join_timeout_seconds),
    )
    session.add(call)
    await session.flush()
    call.conference_name = _conference_name(call.id)
    session.add(
        SoftphoneLeg(
            tenant_id=call.tenant_id,
            environment_id=call.environment_id,
            softphone_call_id=call.id,
            call_id=canonical.id,
            kind="customer",
            provider_call_id=canonical.call_sid,
            idempotency_key=f"customer:{call.id}",
            status="in_progress",
            destination_redacted=phone.redact(canonical.from_number),
            answered_at=now,
        )
    )
    await session.commit()
    try:
        await run_provider(adapter.bridge_to, 
            external_id=canonical.call_sid,
            destination="",
            conference_name=call.conference_name,
            record=False,
        )
    except Exception as exc:
        # The redirect may have reached Twilio. Keep the active conference row
        # so a successful agent join remains possible and no second redirect is
        # issued; the join deadline supplies the safe-harbor fallback otherwise.
        call.failure_category = "provider_outcome_ambiguous"
        call.updated_at = utcnow()
        await session.commit()
        raise ProviderAmbiguous("The ACD bridge outcome is ambiguous; join reconciliation is required") from exc
    return call


async def record_agent_leg(
    session: AsyncSession,
    *,
    call_id: uuid.UUID,
    softphone_session_id: uuid.UUID,
    provider_call_id: str,
    from_uri: str,
) -> SoftphoneLeg:
    call = await session.get(SoftphoneCall, call_id)
    registration = await session.get(SoftphoneSession, softphone_session_id)
    if (
        call is None
        or registration is None
        or call.tenant_id != registration.tenant_id
        or call.session_id != registration.id
        or call.agent_user_id != registration.user_id
        or registration.status != "active"
        or as_utc(registration.expires_at) <= utcnow()
    ):
        raise PermissionDenied("Agent webhook does not match the reserved softphone session")
    expected_identity = f"client:{registration.client_identity}"
    if from_uri and from_uri != expected_identity:
        raise PermissionDenied("Agent client identity does not match the reservation")
    existing = (
        await session.execute(
            select(SoftphoneLeg).where(SoftphoneLeg.provider_call_id == provider_call_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.softphone_call_id != call.id or existing.kind != "agent":
            raise Conflict("Provider call SID is already attached to another leg")
        return existing
    if call.status in CALL_TERMINAL:
        raise Conflict("Cannot join a terminal call")
    if call.agent_join_deadline is not None and as_utc(call.agent_join_deadline) < utcnow():
        raise Conflict("Agent join deadline has expired")
    agent_legs = [leg for leg in await leg_rows(session, call) if leg.kind == "agent"]
    if agent_legs:
        raise Conflict("A different agent leg already joined this call")
    now = utcnow()
    leg = SoftphoneLeg(
        tenant_id=call.tenant_id,
        environment_id=call.environment_id,
        softphone_call_id=call.id,
        kind="agent",
        provider_call_id=provider_call_id,
        idempotency_key=f"agent:{call.id}",
        status="in_progress",
        destination_redacted=registration.client_identity,
        answered_at=now,
    )
    session.add(leg)
    call.status = "in_progress"
    presence = await presence_for(session, call.tenant_id, call.agent_user_id)
    if presence is not None and presence.state == "ringing":
        await transition(session, presence, "busy")
    await record_work_mode(
        session,
        tenant_id=call.tenant_id,
        environment_id=call.environment_id,
        user_id=call.agent_user_id,
        mode="busy",
        source_id=f"softphone:{call.id}",
    )
    if call.dialer_attempt_id is not None:
        attempt = await session.get(DialerAttempt, call.dialer_attempt_id)
        if attempt is not None and attempt.tenant_id == call.tenant_id:
            attempt.agent_connected_at = attempt.agent_connected_at or now
            attempt.outcome = "connected"
            attempt.updated_at = now
    await session.flush()
    return leg


async def _leg(session: AsyncSession, call: SoftphoneCall, kind: str) -> SoftphoneLeg:
    stmt = select(SoftphoneLeg).where(
        SoftphoneLeg.tenant_id == call.tenant_id,
        SoftphoneLeg.softphone_call_id == call.id,
        SoftphoneLeg.kind == kind,
    )
    if kind == "consult":
        stmt = stmt.where(SoftphoneLeg.status.not_in(LEG_TERMINAL))
    leg = (
        await session.execute(stmt.order_by(SoftphoneLeg.created_at.desc()).limit(1))
    ).scalar_one_or_none()
    if leg is None or not leg.provider_call_id:
        raise Conflict(f"Call has no active {kind} leg")
    return leg


async def _conference_sid(
    session: AsyncSession, call: SoftphoneCall, adapter: TwilioAdapter
) -> str:
    if call.provider_conference_id:
        return call.provider_conference_id
    adapter.require_configured()

    def lookup() -> str:
        conferences = adapter._client().conferences.list(
            friendly_name=call.conference_name, status="in-progress", limit=2
        )
        if len(conferences) != 1:
            raise ProviderFailure("Twilio did not identify one active conference")
        sid = getattr(conferences[0], "sid", "") or ""
        if not sid:
            raise ProviderFailure("Twilio returned no conference SID")
        return sid

    sid = await _bounded(asyncio.to_thread(lookup))
    call.provider_conference_id = sid
    await session.flush()
    return sid


async def control_call(
    session: AsyncSession,
    context: TenantContext,
    *,
    call_id: uuid.UUID,
    action: str,
    enabled: bool,
    supervisor: bool = False,
    adapter: TwilioAdapter | None = None,
) -> SoftphoneCall:
    adapter = adapter or _adapter()
    call = await get_call(session, context, call_id, supervisor=supervisor)
    if action not in {"mute", "hold"}:
        raise Validation("Control action must be mute or hold")
    # Repeating the desired state is a true replay: do not cross the provider
    # boundary again and do not emit a second occupancy transition.
    if action == "mute" and call.muted is enabled:
        return call
    if action == "hold" and call.on_hold is enabled:
        return call
    if call.status not in CALL_ACTIVE:
        raise Conflict("Call is not active")
    leg = await _leg(session, call, "agent" if action == "mute" else "customer")
    conference_sid = await _conference_sid(session, call, adapter)
    adapter.require_configured()

    def mutate() -> None:
        participant = (
            adapter._client()
            .conferences(conference_sid)
            .participants(leg.provider_call_id)
            .update(muted=enabled)
        )
        if participant is None:
            raise ProviderFailure("Twilio returned no participant acknowledgement")

    try:
        await _bounded(asyncio.to_thread(mutate))
    except ContactCenterV2Error:
        raise
    except Exception as exc:
        raise ProviderFailure(f"Twilio refused to {action} the participant") from exc
    if action == "mute":
        call.muted = enabled
    else:
        call.on_hold = enabled
        call.status = "held" if enabled else "in_progress"
        await record_work_mode(
            session,
            tenant_id=call.tenant_id,
            environment_id=call.environment_id,
            user_id=call.agent_user_id,
            mode="hold" if enabled else "busy",
            source_id=f"softphone:{call.id}",
        )
    call.updated_at = utcnow()
    await session.flush()
    return call


async def set_recording_pause(
    session: AsyncSession,
    context: TenantContext,
    *,
    call_id: uuid.UUID,
    paused: bool,
    supervisor: bool = False,
    adapter: TwilioAdapter | None = None,
) -> SoftphoneCall:
    """Pause/resume the real customer recording for PCI-sensitive input."""
    adapter = adapter or _adapter()
    call = await get_call(session, context, call_id, supervisor=supervisor)
    if call.status not in CALL_ACTIVE:
        raise Conflict("Recording control requires an active call")
    customer = await _leg(session, call, "customer")
    canonical = await session.get(Call, call.call_id) if call.call_id else None
    if canonical is None or canonical.tenant_id != call.tenant_id:
        raise Conflict("The customer leg has no canonical recording scope")
    context_data = dict(canonical.transfer_context or {})
    marker = dict(context_data.get("cc2_recording") or {})
    if marker.get("paused") is paused:
        return call
    adapter.require_configured()
    desired = "paused" if paused else "in-progress"
    previous = "in-progress" if paused else "paused"

    def mutate() -> int:
        recordings = list(adapter._client().calls(customer.provider_call_id).recordings.list(limit=20))
        if any(str(item.status) == desired for item in recordings):
            return 0
        candidates = [item for item in recordings if str(item.status) == previous]
        if not candidates:
            raise Conflict("No active provider recording matches the requested pause state")
        for recording in candidates:
            if recording.update(status=desired) is None:
                raise ProviderFailure("Twilio returned no recording acknowledgement")
        return len(candidates)

    try:
        await _bounded(asyncio.to_thread(mutate))
    except ContactCenterV2Error:
        raise
    except Exception as exc:
        raise ProviderFailure("Twilio refused the recording pause state") from exc
    context_data["cc2_recording"] = {"paused": paused, "updated_at": utcnow().isoformat()}
    canonical.transfer_context = context_data
    call.updated_at = utcnow()
    await session.flush()
    return call


async def transfer_call(
    session: AsyncSession,
    context: TenantContext,
    *,
    call_id: uuid.UUID,
    destination: str,
    mode: str,
    supervisor: bool = False,
    adapter: TwilioAdapter | None = None,
) -> tuple[SoftphoneCall, SoftphoneLeg | None]:
    adapter = adapter or _adapter()
    call = await get_call(session, context, call_id, supervisor=supervisor)
    if mode not in {"blind", "warm"}:
        raise Validation("Transfer requires blind or warm mode")
    target = phone.normalize(destination)
    if call.status == "transferred" and mode == "blind":
        canonical = await session.get(Call, call.call_id) if call.call_id else None
        if canonical is not None and canonical.transfer_destination == target:
            return call, None
        raise Conflict("The call was already transferred to a different destination")
    if call.status not in CALL_ACTIVE:
        raise Conflict("Transfer requires an active call")
    customer = await _leg(session, call, "customer")
    agent = await _leg(session, call, "agent")
    if mode == "warm":
        active_consult = [
            leg
            for leg in await leg_rows(session, call)
            if leg.kind == "consult" and leg.status not in LEG_TERMINAL
        ]
        if active_consult:
            consult = active_consult[0]
            if consult.destination_redacted == phone.redact(target):
                return call, consult
            raise Conflict("A consultation for a different destination is already active")
        canonical = await session.get(Call, call.call_id) if call.call_id else None
        if canonical is None:
            raise Conflict("The customer leg has no verified caller ID")
        consult = SoftphoneLeg(
            tenant_id=call.tenant_id,
            environment_id=call.environment_id,
            softphone_call_id=call.id,
            kind="consult",
            provider_call_id=None,
            idempotency_key=f"consult:{call.id}:{uuid.uuid4()}",
            status="pending",
            destination_redacted=phone.redact(target),
        )
        session.add(consult)
        await session.flush()
        answer_url = _public_url(
            f"/telephony/voice?cc2=softphone-consult&call_id={call.id}&leg_id={consult.id}"
        )
        # A durable pending consult blocks a second provider attempt when the
        # first outcome is unknown. The signed consult webhook binds its SID.
        await session.commit()
        try:
            result = await run_provider(
                adapter.create_outbound,
                to_number=target,
                from_number=canonical.from_number,
                answer_url=answer_url,
            )
        except Exception as exc:
            raise ProviderAmbiguous(
                "The consultation outcome is ambiguous; reconcile the pending leg before retrying"
            ) from exc
        consult.provider_call_id = result.call_id
        consult.status = "ringing"
        await session.flush()
        return call, consult
    canonical = await session.get(Call, call.call_id) if call.call_id else None
    if (
        canonical is None
        or canonical.tenant_id != call.tenant_id
        or canonical.call_sid != customer.provider_call_id
    ):
        raise Conflict("The customer leg is not bound to its canonical call")
    from app.telephony.transfer_service import request_transfer

    try:
        result = await request_transfer(
            session,
            context.tenant,
            canonical,
            reason="softphone blind transfer",
            destination_override=target,
        )
    except Exception as exc:
        # request_transfer commits REQUESTED before the provider boundary and
        # therefore blocks a blind second redirect after an ambiguous failure.
        raise ProviderAmbiguous("The blind transfer outcome is ambiguous") from exc
    if not result.ok:
        raise ProviderFailure("Twilio refused the blind transfer")
    if result.state is TransferState.REQUESTED:
        raise ProviderAmbiguous("The blind transfer is still awaiting provider reconciliation")
    call.status = "transferred"
    call.ended_at = utcnow()
    call.updated_at = utcnow()
    try:
        await run_provider(adapter.hangup, agent.provider_call_id)
    except Exception:
        # The customer transfer is already carrier-acknowledged. Preserve that
        # truth and surface the independent agent-leg cleanup failure.
        call.failure_category = "agent_hangup_failed"
    else:
        agent.status = "completed"
        agent.ended_at = utcnow()
    await _finish_local_call(session, call, outcome="transferred")
    return call, None


async def complete_warm_transfer(
    session: AsyncSession,
    context: TenantContext,
    *,
    call_id: uuid.UUID,
    supervisor: bool = False,
    adapter: TwilioAdapter | None = None,
) -> SoftphoneCall:
    adapter = adapter or _adapter()
    call = await get_call(session, context, call_id, supervisor=supervisor)
    consult = await _leg(session, call, "consult")
    if consult.status != "in_progress":
        raise Conflict("Consultation party has not answered")
    agent = await _leg(session, call, "agent")
    try:
        await run_provider(adapter.hangup, agent.provider_call_id)
    except Exception as exc:
        raise ProviderFailure("Twilio refused to complete the warm transfer") from exc
    agent.status = "completed"
    agent.ended_at = utcnow()
    call.status = "transferred"
    call.ended_at = utcnow()
    call.updated_at = utcnow()
    await _finish_local_call(session, call, outcome="transferred")
    return call


async def cancel_consultation(
    session: AsyncSession,
    context: TenantContext,
    *,
    call_id: uuid.UUID,
    supervisor: bool = False,
    adapter: TwilioAdapter | None = None,
) -> SoftphoneCall:
    adapter = adapter or _adapter()
    call = await get_call(session, context, call_id, supervisor=supervisor)
    consult = await _leg(session, call, "consult")
    try:
        await run_provider(adapter.hangup, consult.provider_call_id)
    except Exception as exc:
        raise ProviderFailure("Twilio refused to cancel the consultation") from exc
    consult.status = "completed"
    consult.ended_at = utcnow()
    await session.flush()
    return call


async def end_call(
    session: AsyncSession,
    context: TenantContext,
    *,
    call_id: uuid.UUID,
    supervisor: bool = False,
    adapter: TwilioAdapter | None = None,
) -> SoftphoneCall:
    adapter = adapter or _adapter()
    call = await get_call(session, context, call_id, supervisor=supervisor)
    if call.status in CALL_TERMINAL:
        return call
    customer = await _leg(session, call, "customer")
    try:
        await run_provider(adapter.hangup, customer.provider_call_id)
    except Exception as exc:
        raise ProviderFailure("Twilio refused to end the call") from exc
    customer.status = "completed"
    customer.ended_at = utcnow()
    call.status = "completed"
    call.ended_at = utcnow()
    call.updated_at = utcnow()
    await _finish_local_call(session, call, outcome="completed")
    return call


async def _finish_local_call(
    session: AsyncSession, call: SoftphoneCall, *, outcome: str
) -> None:
    if call.origin == "agent_outbound":
        reservation = await _reservation(session, call.tenant_id, "softphone", call.id)
        if reservation is not None:
            await release_agent(session, reservation, wrap_up=outcome == "completed")
    if call.origin == "callback" and call.callback_id:
        from app.contact_center_v2.acd.callbacks import finish_callback

        await finish_callback(session, callback_id=call.callback_id, outcome=outcome)
    if call.origin == "acd" and call.acd_entry_id:
        from app.contact_center.models import RoutingAssignment
        from app.contact_center.service import complete_work, release_assignment

        assignment = (
            await session.execute(
                select(RoutingAssignment).where(
                    RoutingAssignment.tenant_id == call.tenant_id,
                    RoutingAssignment.entry_id == call.acd_entry_id,
                    RoutingAssignment.status == "active",
                )
            )
        ).scalar_one_or_none()
        if assignment is not None:
            if outcome in {"completed", "transferred"}:
                await complete_work(
                    session,
                    tenant_id=call.tenant_id,
                    assignment_id=assignment.id,
                    actor_id=assignment.user_id,
                )
            else:
                await release_assignment(session, assignment, requeue=False)
    if call.dialer_attempt_id:
        attempt = await session.get(DialerAttempt, call.dialer_attempt_id)
        if attempt is not None and attempt.tenant_id == call.tenant_id:
            await finalize_attempt(session, attempt=attempt, outcome=outcome)


async def reconcile_status(
    session: AsyncSession,
    *,
    provider_call_id: str,
    provider_status: str,
    duration: int | None = None,
    answered_by: str | None = None,
) -> SoftphoneCall | None:
    leg = (
        await session.execute(
            select(SoftphoneLeg).where(SoftphoneLeg.provider_call_id == provider_call_id)
        )
    ).scalar_one_or_none()
    if leg is None:
        return None
    call = await session.get(SoftphoneCall, leg.softphone_call_id)
    if call is None or call.tenant_id != leg.tenant_id:
        return None
    status_map = {
        "queued": "pending",
        "initiated": "pending",
        "ringing": "ringing",
        "in-progress": "in_progress",
        "completed": "completed",
        "busy": "failed",
        "no-answer": "failed",
        "failed": "failed",
        "canceled": "cancelled",
    }
    normalized = status_map.get(provider_status.lower())
    if normalized is None:
        return call
    leg.status = normalized
    now = utcnow()
    if normalized == "in_progress":
        leg.answered_at = leg.answered_at or now
        if leg.kind == "customer":
            call.answered_at = call.answered_at or now
            if call.status == "dialing":
                call.status = "waiting_for_agent"
                call.agent_join_deadline = call.agent_join_deadline or (
                    now + timedelta(seconds=settings.dialer_agent_join_timeout_seconds)
                )
            if call.call_id:
                canonical = await session.get(Call, call.call_id)
                if canonical is not None:
                    canonical.status = CallStatus.IN_PROGRESS
    if normalized in LEG_TERMINAL:
        leg.ended_at = leg.ended_at or now
        if leg.call_id:
            canonical = await session.get(Call, leg.call_id)
            if canonical is not None:
                canonical.ended_at = leg.ended_at
                canonical.duration_seconds = float(max(0, duration or 0))
                canonical.status = {
                    "completed": CallStatus.COMPLETED,
                    "failed": CallStatus.FAILED if provider_status.lower() != "no-answer" else CallStatus.NO_ANSWER,
                    "cancelled": CallStatus.CANCELLED,
                }[normalized]
        if leg.kind == "consult":
            await session.flush()
            return call
        if leg.kind == "agent" and call.status not in CALL_TERMINAL:
            await session.flush()
            return call
        outcome = {
            "completed": "completed",
            "failed": "failed",
            "cancelled": "cancelled",
        }[normalized]
        agent_joined = any(
            item.kind == "agent" and item.answered_at is not None
            for item in await leg_rows(session, call)
        )
        if call.dialer_attempt_id:
            attempt = await session.get(DialerAttempt, call.dialer_attempt_id)
            if attempt is not None:
                if answered_by:
                    attempt.answered_by = answered_by
                if answered_by and answered_by.startswith("machine"):
                    outcome = "machine"
                elif call.answered_at and attempt.agent_connected_at is None:
                    outcome = "abandoned"
        elif call.answered_at and not agent_joined and outcome == "completed":
            outcome = "failed"
            call.failure_category = "agent_not_connected"
        call.status = "failed" if outcome in {"failed", "machine"} else (
            "cancelled" if outcome == "cancelled" else "completed"
        )
        if outcome == "completed":
            call.failure_category = ""
        elif call.failure_category != "agent_not_connected":
            call.failure_category = provider_status.lower()[:64]
        call.ended_at = leg.ended_at
        call.updated_at = now
        await _finish_local_call(session, call, outcome=outcome)
    await session.flush()
    return call


async def sweep_agent_join_timeouts(
    session: AsyncSession, *, adapter: TwilioAdapter | None = None
) -> int:
    adapter = adapter or _adapter()
    moment = utcnow()
    rows = (
        await session.execute(
            select(SoftphoneCall).where(
                SoftphoneCall.status == "waiting_for_agent",
                SoftphoneCall.agent_join_deadline.is_not(None),
                SoftphoneCall.agent_join_deadline <= moment,
            )
        )
    ).scalars().all()
    changed = 0
    for call in rows:
        try:
            customer = await _leg(session, call, "customer")
        except Conflict:
            continue
        attempt = (
            await session.get(DialerAttempt, call.dialer_attempt_id)
            if call.dialer_attempt_id
            else None
        )
        message = "No agent is available. Please try your call again later."
        if attempt is not None:
            policy = await session.get(DialerPolicy, attempt.policy_id)
            if policy is not None and policy.tenant_id == call.tenant_id:
                message = policy.safe_harbor_message
        adapter.require_configured()

        def redirect() -> Any:
            return adapter._client().calls(customer.provider_call_id).update(
                twiml=safe_harbor_twiml(message)
            )

        try:
            result = await _bounded(asyncio.to_thread(redirect))
            if result is None:
                continue
        except Exception:
            # Retry the past-due deadline later. A REST timeout is not evidence
            # that the safe-harbor message played.
            continue
        if attempt is not None:
            attempt.safe_harbor_requested_at = moment
            attempt.abandoned_at = moment
            attempt.abandonment_counted = True
            attempt.outcome = "answered"
            attempt.updated_at = moment
            reservation = await _reservation(session, call.tenant_id, "dialer", attempt.id)
            if reservation is not None:
                await release_agent(session, reservation, wrap_up=False, now=moment)
        elif call.origin == "agent_outbound":
            reservation = await _reservation(session, call.tenant_id, "softphone", call.id)
            if reservation is not None:
                await release_agent(session, reservation, wrap_up=False, now=moment)
        call.agent_join_deadline = None
        call.updated_at = moment
        changed += 1
    await session.flush()
    return changed


async def expire_sessions(session: AsyncSession) -> int:
    result = await session.execute(
        update(SoftphoneSession)
        .where(SoftphoneSession.status == "active", SoftphoneSession.expires_at <= utcnow())
        .values(status="expired", active_device_key=None)
    )
    return int(result.rowcount or 0)


async def list_calls(
    session: AsyncSession,
    context: TenantContext,
    *,
    supervisor: bool,
    limit: int = 100,
) -> list[SoftphoneCall]:
    stmt = select(SoftphoneCall).where(SoftphoneCall.tenant_id == context.tenant_id)
    if not supervisor:
        stmt = stmt.where(SoftphoneCall.agent_user_id == context.user_id)
    return (
        await session.execute(stmt.order_by(SoftphoneCall.created_at.desc()).limit(limit))
    ).scalars().all()
