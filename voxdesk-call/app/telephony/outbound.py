"""
Outbound calling: cold-call campaigns, BatchCall runtime bridge, follow-ups, and appointment reminders (Part 1B / Gate G1).

Compliance is not optional here. US TCPA rules restrict *when* you may call and
require you to honour do-not-call. ``dnc.is_blocked()``, ``dialer_limits``,
``is_call_window_open()``, and the DNC check in ``next_callable_leads()`` are
the guardrails -- do not bypass them to hit a quota.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from twilio.base.exceptions import TwilioRestException

from app.core.config import settings
from app.core.metrics import record_side_effect
from app.db.enterprise_models import BatchCall, BatchRecipient, BatchRecipientStatus, BatchStatus
from app.db.models import (
    Call,
    CallDirection,
    CallStatus,
    Campaign,
    Lead,
    LeadStatus,
    Tenant,
)
from app.db.telephony_models import TelephonyCallSession
from app.leads.consent import voice_denied
from app.telephony import dialer_limits, dnc, phone
from app.telephony import phone as phone_util

log = structlog.get_logger()

RETRY_BACKOFF_HOURS = (1, 4, 24)
MAX_ATTEMPTS = 3
RETRY_BACKOFF = [timedelta(minutes=15), timedelta(hours=2), timedelta(hours=24)]


# ------------------------------------------------------------------ timing ---


def is_call_window_open(tenant: Tenant, now: datetime | None = None) -> bool:
    """True only inside the tenant's outbound window, in the tenant's timezone."""
    tz = ZoneInfo(tenant.timezone)
    now = now.astimezone(tz) if now else datetime.now(tz)
    open_t: dtime = tenant.outbound_window_open
    close_t: dtime = tenant.outbound_window_close
    return open_t <= now.time() <= close_t


def next_window_start(tenant: Tenant, now: datetime | None = None) -> datetime:
    """When the window next opens -- used to schedule instead of drop."""
    tz = ZoneInfo(tenant.timezone)
    now = now.astimezone(tz) if now else datetime.now(tz)
    today_open = now.replace(
        hour=tenant.outbound_window_open.hour,
        minute=tenant.outbound_window_open.minute,
        second=0,
        microsecond=0,
    )
    if now < today_open:
        return today_open
    if now.time() <= tenant.outbound_window_close:
        return now
    return today_open + timedelta(days=1)


def backoff_for(attempts: int) -> timedelta:
    idx = min(max(attempts - 1, 0), len(RETRY_BACKOFF_HOURS) - 1)
    return timedelta(hours=RETRY_BACKOFF_HOURS[idx])


# ------------------------------------------------------------------- queue ---


async def next_callable_leads(
    session: AsyncSession, tenant: Tenant, campaign: Campaign, limit: int = 10
) -> list[Lead]:
    """Leads that are due, under the attempt cap, and not on do-not-call."""
    now = datetime.utcnow()
    from app.leads.models import LeadConsent, LeadIdentity

    if campaign.environment_id is None:
        return []
    merged = select(LeadIdentity.lead_id).where(
        LeadIdentity.tenant_id == tenant.id,
        LeadIdentity.merged_into_id.is_not(None),
    )
    denied = select(LeadConsent.lead_id).where(
        LeadConsent.tenant_id == tenant.id,
        LeadConsent.channel == "voice",
        LeadConsent.decision == "denied",
    )
    stmt = (
        select(Lead)
        .where(
            Lead.tenant_id == tenant.id,
            Lead.environment_id == campaign.environment_id,
            Lead.campaign_id == campaign.id,
            Lead.status.in_([LeadStatus.NEW, LeadStatus.QUEUED]),
            Lead.attempts < tenant.max_call_attempts,
            Lead.status != LeadStatus.DNC,
            Lead.id.not_in(merged),
            Lead.id.not_in(denied),
        )
        .order_by(Lead.next_attempt_at.is_(None).desc(), Lead.next_attempt_at)
        .limit(limit)
    )
    rows = (await session.execute(stmt)).scalars().all()
    eligible: list[Lead] = []
    for lead in rows:
        if lead.status is LeadStatus.DNC:
            continue
        if lead.next_attempt_at is not None and lead.next_attempt_at > now:
            continue
        is_blk, _ = await dnc.is_blocked(
            session, tenant.id, lead.phone, lead_id=lead.id, environment_id=lead.environment_id
        )
        if is_blk:
            setattr(lead, "status", LeadStatus.DNC)
            continue
        eligible.append(lead)
    return eligible


# ------------------------------------------------------------------ dialer ---


def _twilio_client():
    """Imported lazily so the package is not needed for tests."""
    from twilio.rest import Client

    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def _twilio():
    """Compatibility alias for ``_twilio_client``."""
    return _twilio_client()


def build_outbound_twiml_url(campaign_id: str, lead_id: str) -> str:
    """Twilio fetches this when the callee picks up; it returns the <Connect>."""
    base = settings.public_base_url.rstrip("/")
    return f"{base}/telephony/outbound-answer?campaign_id={campaign_id}&lead_id={lead_id}"


async def _find_batch_for_campaign(
    session: AsyncSession, campaign: Campaign | None
) -> BatchCall | None:
    if campaign is None:
        return None
    if getattr(campaign, "batch_call_id", None):
        row = await session.get(BatchCall, campaign.batch_call_id)
        if row is not None and row.tenant_id == campaign.tenant_id:
            return row
    return (
        await session.execute(
            select(BatchCall).where(
                BatchCall.tenant_id == campaign.tenant_id,
                BatchCall.campaign_id == campaign.id,
            )
        )
    ).scalar_one_or_none()


async def _find_recipient_for_lead(
    session: AsyncSession,
    lead: Lead,
    batch: BatchCall | None = None,
) -> BatchRecipient | None:
    if batch is not None:
        rec = (
            await session.execute(
                select(BatchRecipient).where(
                    BatchRecipient.batch_id == batch.id,
                    or_(
                        BatchRecipient.lead_id == lead.id,
                        BatchRecipient.phone == lead.phone,
                    ),
                )
            )
        ).scalars().first()
        if rec is not None:
            return rec
    return (
        await session.execute(
            select(BatchRecipient).where(
                BatchRecipient.tenant_id == lead.tenant_id,
                BatchRecipient.lead_id == lead.id,
            )
        )
    ).scalars().first()


async def sync_batch_from_calls(
    session: AsyncSession,
    batch: BatchCall,
) -> BatchCall:
    """Recompute ``BatchRecipient`` and ``BatchCall`` counters from real ``Call`` rows."""
    recipients = list(
        (
            await session.execute(
                select(BatchRecipient).where(BatchRecipient.batch_id == batch.id)
            )
        ).scalars().all()
    )
    call_ids = [r.call_id for r in recipients if r.call_id is not None]
    calls_by_id: dict[uuid.UUID, Call] = {}
    if call_ids:
        call_rows = (
            await session.execute(
                select(Call).where(
                    Call.tenant_id == batch.tenant_id,
                    Call.id.in_(call_ids),
                )
            )
        ).scalars().all()
        calls_by_id = {c.id: c for c in call_rows}

    now_utc = datetime.now(timezone.utc)
    completed = 0
    failed = 0
    active_or_pending = 0

    for rec in recipients:
        if rec.call_id and rec.call_id in calls_by_id:
            call_row = calls_by_id[rec.call_id]
            st = call_row.status.value if hasattr(call_row.status, "value") else str(call_row.status)
            if st in (CallStatus.COMPLETED.value, CallStatus.TRANSFERRED.value):
                rec.status = BatchRecipientStatus.COMPLETED.value
                rec.updated_at = now_utc
            elif st == CallStatus.NO_ANSWER.value:
                rec.status = BatchRecipientStatus.NO_ANSWER.value
                rec.updated_at = now_utc
            elif st == "busy":
                rec.status = BatchRecipientStatus.BUSY.value
                rec.updated_at = now_utc
            elif st in (CallStatus.FAILED.value, CallStatus.CANCELLED.value):
                rec.status = BatchRecipientStatus.FAILED.value
                rec.updated_at = now_utc
            elif st in (CallStatus.RINGING.value, CallStatus.IN_PROGRESS.value):
                if rec.status not in (
                    BatchRecipientStatus.COMPLETED.value,
                    BatchRecipientStatus.FAILED.value,
                ):
                    rec.status = BatchRecipientStatus.DIALING.value

        if rec.status == BatchRecipientStatus.COMPLETED.value:
            completed += 1
        elif rec.status in (
            BatchRecipientStatus.FAILED.value,
            BatchRecipientStatus.NO_ANSWER.value,
            BatchRecipientStatus.BUSY.value,
            BatchRecipientStatus.DNC_BLOCKED.value,
        ):
            failed += 1
        elif rec.status in (
            BatchRecipientStatus.PENDING.value,
            BatchRecipientStatus.QUEUED.value,
            BatchRecipientStatus.DIALING.value,
            BatchRecipientStatus.RETRY_SCHEDULED.value,
            BatchRecipientStatus.WINDOW_BLOCKED.value,
        ):
            active_or_pending += 1

    batch.total_recipients = len(recipients)
    batch.completed_recipients = completed
    batch.failed_recipients = failed
    batch.updated_at = now_utc

    if (
        batch.status == BatchStatus.RUNNING.value
        and len(recipients) > 0
        and active_or_pending == 0
    ):
        batch.status = BatchStatus.COMPLETED.value
        batch.completed_at = batch.completed_at or now_utc
        if batch.campaign_id:
            camp = await session.get(Campaign, batch.campaign_id)
            if camp is not None and camp.tenant_id == batch.tenant_id:
                camp.is_active = False

    await session.flush()
    return batch


async def _claim_attempt(
    session: AsyncSession,
    lead: Lead,
    tenant: Tenant,
    *,
    now: datetime,
) -> bool:
    """Atomically reserve the next dial attempt, or lose the race."""
    result = await session.execute(
        update(Lead)
        .where(
            Lead.id == lead.id,
            Lead.tenant_id == tenant.id,
            Lead.attempts == lead.attempts,
            Lead.attempts < tenant.max_call_attempts,
            Lead.status.in_([LeadStatus.NEW, LeadStatus.QUEUED]),
            Lead.status != LeadStatus.DNC,
        )
        .values(
            attempts=lead.attempts + 1,
            status=LeadStatus.QUEUED,
            last_attempt_at=now,
            next_attempt_at=now + backoff_for(lead.attempts + 1),
        )
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return result.rowcount == 1


async def _enterprise_block(session: AsyncSession, lead: Lead) -> str | None:
    """Merged leads, DNC entries, and voice-consent denials stay undialed."""
    from app.leads.repository import get_identity

    if await voice_denied(session, lead.tenant_id, lead.id):
        return "do_not_call"
    is_dnc, _ = await dnc.is_blocked(
        session, lead.tenant_id, lead.phone, lead_id=lead.id, environment_id=lead.environment_id
    )
    if is_dnc:
        return "do_not_call"
    identity = await get_identity(session, lead.tenant_id, lead.id)
    if identity is not None and identity.merged_into_id is not None:
        return "merged"
    return None


async def place_call(
    session: AsyncSession,
    tenant: Tenant,
    campaign: Campaign,
    lead: Lead,
    *,
    dry_run: bool = False,
) -> dict:
    """Dial one lead in a campaign. Records the attempt whether or not Twilio succeeds."""
    if lead.tenant_id != tenant.id:
        return {"ok": False, "reason": "tenant_mismatch"}
    if campaign.tenant_id != tenant.id:
        return {"ok": False, "reason": "tenant_mismatch"}
    if campaign.environment_id is None or lead.environment_id != campaign.environment_id:
        return {"ok": False, "reason": "environment_mismatch"}
    if not tenant.outbound_enabled:
        return {"ok": False, "reason": "outbound_disabled"}
    if lead.status is LeadStatus.DNC:
        return {"ok": False, "reason": "do_not_call"}
    blocked = await _enterprise_block(session, lead)
    if blocked:
        return {"ok": False, "reason": blocked}
    if lead.attempts >= tenant.max_call_attempts:
        return {"ok": False, "reason": "max_attempts"}
    if not is_call_window_open(tenant):
        lead.next_attempt_at = next_window_start(tenant).replace(tzinfo=None)
        await session.commit()
        return {
            "ok": False,
            "reason": "outside_window",
            "retry_at": lead.next_attempt_at.isoformat(),
        }

    batch = await _find_batch_for_campaign(session, campaign)
    agent_id = (batch.agent_id if batch is not None else "") or ""

    win_verdict = await dialer_limits.check_calling_window(
        session, tenant.id, agent_id=agent_id, batch=batch
    )
    if not win_verdict.allowed:
        if win_verdict.next_window_start is not None:
            lead.next_attempt_at = win_verdict.next_window_start.replace(tzinfo=None)
            await session.commit()
        return {
            "ok": False,
            "reason": "outside_window",
            "retry_at": lead.next_attempt_at.isoformat() if lead.next_attempt_at else None,
        }

    conc_verdict = await dialer_limits.check_concurrency_and_rate(
        session, tenant.id, agent_id=agent_id, campaign=campaign, batch=batch
    )
    if not conc_verdict.allowed:
        return {"ok": False, "reason": conc_verdict.reason or "concurrency_limit"}

    if not dry_run:
        from app.billing.hooks import may_place_outbound_call

        allowed, billing_reason = await may_place_outbound_call(session, tenant)
        if not allowed:
            return {
                "ok": False,
                "reason": "billing_entitlement_denied",
                "billing_reason": billing_reason,
            }

    now = datetime.utcnow()
    if not await _claim_attempt(session, lead, tenant, now=now):
        return {"ok": False, "reason": "claimed_by_another"}

    from app.leads.lifecycle import record_dial_claim

    await record_dial_claim(session, lead, campaign_id=campaign.id)
    await session.refresh(lead)

    caller_id = tenant.outbound_caller_id or tenant.twilio_number

    if dry_run:
        return {"ok": True, "dry_run": True, "to": lead.phone, "from": caller_id}

    recipient = await _find_recipient_for_lead(session, lead, batch=batch)
    record_side_effect("outbound_call", "attempt")
    try:
        client = _twilio()
        tw_call = client.calls.create(
            to=lead.phone,
            from_=caller_id,
            url=build_outbound_twiml_url(str(campaign.id), str(lead.id)),
            status_callback=f"{settings.public_base_url.rstrip('/')}/telephony/status",
            status_callback_event=["completed", "no-answer", "busy", "failed"],
            machine_detection="Enable",
            timeout=25,
        )
    except Exception as exc:
        from app.leads import lifecycle
        from app.leads.exceptions import ClaimConflict, InvalidTransition

        lead.next_attempt_at = now + backoff_for(lead.attempts)
        try:
            await lifecycle.transition(
                session,
                lead,
                LeadStatus.FAILED.value,
                reason="dial_failed",
                source="outbound",
                expected=LeadStatus.QUEUED,
            )
        except (InvalidTransition, ClaimConflict) as conflict:
            log.warning(
                "outbound.dial_failed_grade_conflict",
                lead=str(lead.id),
                error=type(conflict).__name__,
            )
        if recipient is not None:
            recipient.status = BatchRecipientStatus.FAILED.value
            recipient.attempts = lead.attempts
            recipient.last_error = str(exc)[:500]
        if batch is not None:
            await sync_batch_from_calls(session, batch)
        await session.commit()
        log.error("outbound.dial_failed", lead=str(lead.id), error=type(exc).__name__)
        record_side_effect("outbound_call", "failure")
        return {"ok": False, "reason": "dial_failed", "error": str(exc)}

    call = Call(
        tenant_id=tenant.id,
        environment_id=lead.environment_id,
        call_sid=tw_call.sid,
        from_number=caller_id,
        to_number=lead.phone,
        status=CallStatus.RINGING,
        direction=CallDirection.OUTBOUND,
        lead_id=lead.id,
    )
    session.add(call)
    await session.flush()

    if agent_id:
        session.add(
            TelephonyCallSession(
                id=call.id,
                tenant_id=tenant.id,
                agent_id=agent_id,
                provider="TWILIO",
                provider_call_id=tw_call.sid,
                direction="OUTBOUND",
                from_number=caller_id,
                to_number=lead.phone,
                status="RINGING",
            )
        )

    if recipient is not None:
        recipient.call_id = call.id
        recipient.attempts = lead.attempts
        recipient.status = BatchRecipientStatus.DIALING.value
        recipient.last_error = ""
        recipient.updated_at = datetime.now(timezone.utc)
    if batch is not None:
        await sync_batch_from_calls(session, batch)

    from app.webhooks.call_event_bridge import publish_call_event

    await publish_call_event(session, call, "call_started")
    lead.next_attempt_at = now + backoff_for(lead.attempts)
    await session.commit()
    log.info(
        "outbound.dialed",
        to=phone.redact(lead.phone),
        campaign=campaign.name,
        sid=tw_call.sid,
    )
    record_side_effect("outbound_call", "success")
    return {"ok": True, "call_sid": tw_call.sid, "call_id": str(call.id), "to": lead.phone}


async def dial_lead(
    session: AsyncSession,
    lead: Lead,
    *,
    campaign: Campaign | None = None,
    batch: BatchCall | None = None,
    now: datetime | None = None,
) -> Call | None:
    """Place one outbound call to ``lead``. Returns the ``Call`` row, or ``None`` if skipped."""
    if campaign is None and lead.campaign_id:
        campaign = await session.get(Campaign, lead.campaign_id)
    if batch is None and campaign is not None:
        batch = await _find_batch_for_campaign(session, campaign)
    recipient = await _find_recipient_for_lead(session, lead, batch=batch)
    agent_id = (batch.agent_id if batch is not None else "") or ""

    # 1. Centralized DNC + LeadStatus.DNC + voice_denied check
    if lead.status == LeadStatus.DNC:
        log.warning("outbound.blocked_dnc", lead_id=str(lead.id))
        if recipient is not None:
            recipient.status = BatchRecipientStatus.DNC_BLOCKED.value
            recipient.last_error = "lead_dnc"
        return None

    if await voice_denied(session, lead.tenant_id, lead.id, environment_id=lead.environment_id):
        setattr(lead, "status", LeadStatus.DNC)
        log.warning("outbound.blocked_consent", lead_id=str(lead.id))
        if recipient is not None:
            recipient.status = BatchRecipientStatus.DNC_BLOCKED.value
            recipient.last_error = "consent_denied"
        return None

    is_dnc, dnc_reason = await dnc.is_blocked(
        session,
        lead.tenant_id,
        lead.phone,
        lead_id=lead.id,
        environment_id=lead.environment_id,
        agent_id=agent_id,
    )
    if is_dnc:
        setattr(lead, "status", LeadStatus.DNC)
        log.warning("outbound.blocked_dnc_registry", lead_id=str(lead.id), reason=dnc_reason)
        if recipient is not None:
            recipient.status = BatchRecipientStatus.DNC_BLOCKED.value
            recipient.last_error = dnc_reason or "dnc_blocked"
        await session.flush()
        return None

    # 2. Centralized calling-window check
    win_verdict = await dialer_limits.check_calling_window(
        session,
        lead.tenant_id,
        agent_id=agent_id,
        batch=batch,
        now=now,
    )
    if not win_verdict.allowed:
        log.info(
            "outbound.deferred_calling_window",
            lead_id=str(lead.id),
            reason=win_verdict.reason,
        )
        if recipient is not None:
            recipient.status = BatchRecipientStatus.WINDOW_BLOCKED.value
            recipient.last_error = win_verdict.reason or "outside_calling_window"
            recipient.next_attempt_at = win_verdict.next_window_start
        if win_verdict.next_window_start is not None:
            lead.next_attempt_at = win_verdict.next_window_start.replace(tzinfo=None)
        await session.flush()
        return None

    # 3. Centralized concurrency & rate check
    conc_verdict = await dialer_limits.check_concurrency_and_rate(
        session,
        lead.tenant_id,
        agent_id=agent_id,
        campaign=campaign,
        batch=batch,
        now=now,
    )
    if not conc_verdict.allowed:
        log.info(
            "outbound.deferred_concurrency",
            lead_id=str(lead.id),
            reason=conc_verdict.reason,
        )
        return None

    tenant = await session.get(Tenant, lead.tenant_id)
    if not tenant:
        return None

    raw_from = tenant.outbound_caller_id or tenant.twilio_number
    try:
        from_number = phone_util.normalize(raw_from)
    except phone_util.InvalidPhoneNumber as exc:
        log.error("outbound.invalid_caller_id", tenant_id=str(tenant.id), reason=str(exc))
        return None

    try:
        to_number = phone_util.normalize(lead.phone)
    except phone_util.InvalidPhoneNumber as exc:
        setattr(lead, "status", LeadStatus.FAILED)
        if recipient is not None:
            recipient.status = BatchRecipientStatus.FAILED.value
            recipient.last_error = str(exc)
        await session.commit()
        log.warning("outbound.invalid_lead_phone", lead_id=str(lead.id), reason=str(exc))
        return None

    if lead.phone != to_number:
        lead.phone = to_number

    lead.attempts += 1
    lead.last_attempt_at = datetime.utcnow()

    answer_url = f"{settings.public_base_url}/telephony/outbound/answer?lead_id={lead.id}"
    status_url = f"{settings.public_base_url}/telephony/status"

    try:
        tw_call = _twilio().calls.create(
            to=to_number,
            from_=from_number,
            url=answer_url,
            status_callback=status_url,
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            machine_detection="Enable",
            timeout=25,
        )
    except (TwilioRestException, Exception) as exc:
        log.error(
            "outbound.dial_failed",
            lead_id=str(lead.id),
            to=phone_util.redact(to_number),
            error=type(exc).__name__,
        )
        retry_dec = await dialer_limits.compute_retry_schedule(
            session,
            lead.tenant_id,
            agent_id=agent_id,
            attempt=lead.attempts,
            outcome="failed",
            batch=batch,
            now=now,
        )
        if not retry_dec.should_retry:
            setattr(lead, "status", LeadStatus.FAILED)
            lead.next_attempt_at = None
            if recipient is not None:
                recipient.status = BatchRecipientStatus.FAILED.value
                recipient.attempts = lead.attempts
                recipient.last_error = str(exc)[:500]
                recipient.next_attempt_at = None
        else:
            setattr(lead, "status", LeadStatus.QUEUED)
            lead.next_attempt_at = (
                retry_dec.next_attempt_at.replace(tzinfo=None)
                if retry_dec.next_attempt_at
                else datetime.utcnow()
                + RETRY_BACKOFF[min(lead.attempts - 1, len(RETRY_BACKOFF) - 1)]
            )
            if recipient is not None:
                recipient.status = BatchRecipientStatus.RETRY_SCHEDULED.value
                recipient.attempts = lead.attempts
                recipient.last_error = str(exc)[:500]
                recipient.next_attempt_at = retry_dec.next_attempt_at
        await session.commit()
        return None

    call = Call(
        tenant_id=tenant.id,
        environment_id=lead.environment_id,
        lead_id=lead.id,
        call_sid=tw_call.sid,
        from_number=from_number,
        to_number=to_number,
        direction=CallDirection.OUTBOUND,
        status=CallStatus.RINGING,
    )
    session.add(call)
    await session.flush()

    if agent_id:
        session.add(
            TelephonyCallSession(
                id=call.id,
                tenant_id=tenant.id,
                agent_id=agent_id,
                provider="TWILIO",
                provider_call_id=tw_call.sid,
                direction="OUTBOUND",
                from_number=from_number,
                to_number=to_number,
                status="RINGING",
            )
        )

    setattr(lead, "status", LeadStatus.CALLED)
    lead.next_attempt_at = None

    if recipient is not None:
        recipient.call_id = call.id
        recipient.attempts = lead.attempts
        recipient.status = BatchRecipientStatus.DIALING.value
        recipient.last_error = ""
        recipient.next_attempt_at = None
        recipient.updated_at = datetime.now(timezone.utc)

    if batch is not None:
        await sync_batch_from_calls(session, batch)

    from app.webhooks.call_event_bridge import publish_call_event

    await publish_call_event(session, call, "call_started")
    await session.commit()
    log.info(
        "outbound.dialed",
        lead_id=str(lead.id),
        call_sid=tw_call.sid,
        to=phone_util.redact(to_number),
    )
    return call


async def run_campaign_step(
    session: AsyncSession,
    campaign: Campaign,
    *,
    now: datetime | None = None,
) -> int:
    """Called by the scheduler tick (or batch start). Dials up to the campaign/policy budget."""
    if not campaign.is_active:
        return 0

    batch = await _find_batch_for_campaign(session, campaign)
    agent_id = (batch.agent_id if batch is not None else "") or ""

    win_verdict = await dialer_limits.check_calling_window(
        session,
        campaign.tenant_id,
        agent_id=agent_id,
        batch=batch,
        now=now,
    )
    if not win_verdict.allowed:
        if batch is not None:
            pending_recs = (
                await session.execute(
                    select(BatchRecipient).where(
                        BatchRecipient.batch_id == batch.id,
                        BatchRecipient.status.in_(
                            [
                                BatchRecipientStatus.PENDING.value,
                                BatchRecipientStatus.QUEUED.value,
                            ]
                        ),
                    )
                )
            ).scalars().all()
            for rec in pending_recs:
                rec.status = BatchRecipientStatus.WINDOW_BLOCKED.value
                rec.last_error = win_verdict.reason or "outside_calling_window"
                rec.next_attempt_at = win_verdict.next_window_start
            await session.flush()
        return 0

    conc_verdict = await dialer_limits.check_concurrency_and_rate(
        session,
        campaign.tenant_id,
        agent_id=agent_id,
        campaign=campaign,
        batch=batch,
        now=now,
    )
    if not conc_verdict.allowed:
        return 0

    remaining_concurrency = max(0, conc_verdict.max_concurrent_calls - conc_verdict.active_calls)
    budget = max(1, min(campaign.calls_per_minute // 2 or 1, remaining_concurrency))
    if batch is not None and batch.concurrency:
        budget = max(1, min(int(batch.concurrency), remaining_concurrency))

    now_naive = dialer_limits._naive_utc(now)
    q = (
        select(Lead)
        .where(
            Lead.tenant_id == campaign.tenant_id,
            Lead.environment_id == campaign.environment_id,
            Lead.campaign_id == campaign.id,
            Lead.status.in_([LeadStatus.NEW, LeadStatus.QUEUED]),
            (Lead.next_attempt_at.is_(None)) | (Lead.next_attempt_at <= now_naive),
        )
        .order_by(Lead.score.desc().nullslast(), Lead.created_at)
        .limit(budget)
    )
    leads = (await session.execute(q)).scalars().all()

    dialed = 0
    for lead in leads:
        c = await dial_lead(session, lead, campaign=campaign, batch=batch, now=now)
        if c:
            dialed += 1

    if batch is not None:
        await sync_batch_from_calls(session, batch)
        await session.commit()

    return dialed


async def run_campaign_tick(
    session: AsyncSession, tenant: Tenant, campaign: Campaign, *, dry_run: bool = False
) -> dict:
    """One throttled batch. Call this on a schedule (cron / APScheduler)."""
    if not campaign.is_active:
        return {"dialed": 0, "reason": "campaign_inactive"}
    if not is_call_window_open(tenant):
        return {"dialed": 0, "reason": "outside_window"}

    batch = await _find_batch_for_campaign(session, campaign)
    agent_id = (batch.agent_id if batch is not None else "") or ""
    win_verdict = await dialer_limits.check_calling_window(
        session, tenant.id, agent_id=agent_id, batch=batch
    )
    if not win_verdict.allowed:
        return {"dialed": 0, "reason": "outside_window"}

    leads = await next_callable_leads(session, tenant, campaign, limit=campaign.calls_per_minute)
    results = [
        await place_call(session, tenant, campaign, lead, dry_run=dry_run) for lead in leads
    ]
    if batch is not None and not dry_run:
        await sync_batch_from_calls(session, batch)
        await session.commit()
    return {"dialed": sum(1 for r in results if r.get("ok")), "results": results}
