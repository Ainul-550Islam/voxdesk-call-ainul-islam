"""
Outbound calling: cold-call campaigns, follow-ups and appointment reminders.

This is the half of the product the inbound receptionist does not cover, and
the half most Fiverr gigs charge extra for ("AI Cold Calling Agent",
"Lead Qualification & Follow-Up Agent", "Appointment Reminders").

Compliance is not optional here. US TCPA rules restrict *when* you may call and
require you to honour do-not-call. `is_call_window_open()` and the DNC check in
`next_callable_leads()` are the guardrails -- do not bypass them to hit a quota.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.metrics import record_side_effect
from app.db.models import (
    Call,
    CallDirection,
    CallStatus,
    Campaign,
    Lead,
    LeadStatus,
    Tenant,
)
from app.telephony import phone

log = structlog.get_logger()

# Back off longer after each failed try instead of hammering the same number.
RETRY_BACKOFF_HOURS = (1, 4, 24)


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
    """Leads that are due, under the attempt cap, and not on do-not-call.

    Batch 06: selection is restricted to the campaign's own environment —
    ``Lead.environment_id == campaign.environment_id`` — so a campaign can
    never pick up another environment's leads even if a legacy row points
    ``campaign_id`` at it. A campaign without an environment (which the
    schema no longer allows) selects nothing: fail closed.
    """
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
    return [
        lead
        for lead in rows
        if lead.status is not LeadStatus.DNC
        and (lead.next_attempt_at is None or lead.next_attempt_at <= now)
    ]


# ------------------------------------------------------------------ dialer ---


def _twilio_client():
    """Imported lazily so the package is not needed for tests."""
    from twilio.rest import Client

    return Client(settings.twilio_account_sid, settings.twilio_auth_token)


def build_outbound_twiml_url(campaign_id: str, lead_id: str) -> str:
    """Twilio fetches this when the callee picks up; it returns the <Connect>."""
    base = settings.public_base_url.rstrip("/")
    return f"{base}/telephony/outbound-answer?campaign_id={campaign_id}&lead_id={lead_id}"


async def _claim_attempt(
    session: AsyncSession,
    lead: Lead,
    tenant: Tenant,
    *,
    now: datetime,
) -> bool:
    """
    Atomically reserve the next dial attempt, or lose the race.

    Step 6 (scale-compliance). The old code did `lead.attempts += 1` in memory
    and only persisted it *after* dialing, so two overlapping campaign ticks
    (or a crash between Twilio accepting the call and the commit) would dial
    the same lead twice. A single conditional UPDATE arbitrates the race: the
    `attempts == :seen` guard is a compare-and-set, so exactly one worker can
    ever increment past the value it read, and the attempt is durably recorded
    *before* the phone rings.
    """
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
        # The caller mirrors these fields on the ORM object exactly once;
        # letting the session "evaluate" the increment back would double it.
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return result.rowcount == 1


async def _enterprise_block(session: AsyncSession, lead: Lead) -> str | None:
    """Merged leads and voice-consent denials stay undialed. DNC status is checked by the caller."""
    from app.leads.consent import voice_denied
    from app.leads.repository import get_identity

    if await voice_denied(session, lead.tenant_id, lead.id):
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
    """Dial one lead. Records the attempt whether or not Twilio succeeds.

    Batch 06 scope gate: tenant, campaign and lead must all agree —
    ``campaign.tenant_id == lead.tenant_id == tenant.id`` **and**
    ``campaign.environment_id == lead.environment_id``. A lead from another
    environment of the same tenant is refused exactly like a foreign-tenant
    lead. DNC, call-window, attempt-cap, backoff and machine detection are
    unchanged and cannot be weakened here.
    """
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

    now = datetime.utcnow()
    if not await _claim_attempt(session, lead, tenant, now=now):
        # Another worker (or a concurrent tick) owns this lead, or it hit the
        # attempt cap between our check and the claim. Do not dial. The
        # conditional UPDATE is the race arbitration: same campaign + same
        # environment + same lead can only ever be claimed once.
        return {"ok": False, "reason": "claimed_by_another"}

    from app.leads.lifecycle import record_dial_claim

    # Audit the claim while the in-memory status still holds the pre-claim
    # value, so the history row records the true from-status (the database
    # row is already QUEUED — the atomic claim set it).
    await record_dial_claim(session, lead, campaign_id=campaign.id)
    # Reload what the atomic claim committed (attempts, QUEUED status,
    # timestamps) instead of assigning any of it by hand: this function no
    # longer contains a direct ``lead.status = ...`` write.
    await session.refresh(lead)

    caller_id = tenant.outbound_caller_id or tenant.twilio_number

    if dry_run:
        return {"ok": True, "dry_run": True, "to": lead.phone, "from": caller_id}

    record_side_effect("outbound_call", "attempt")
    try:
        client = _twilio_client()
        tw_call = client.calls.create(
            to=lead.phone,
            from_=caller_id,
            url=build_outbound_twiml_url(str(campaign.id), str(lead.id)),
            status_callback=f"{settings.public_base_url.rstrip('/')}/telephony/status",
            status_callback_event=["completed", "no-answer", "busy", "failed"],
            machine_detection="Enable",  # do not talk to voicemail
            timeout=25,
        )
    except Exception as exc:
        from app.leads import lifecycle
        from app.leads.exceptions import ClaimConflict, InvalidTransition

        lead.next_attempt_at = now + backoff_for(lead.attempts)
        try:
            # Canonical lifecycle: QUEUED -> FAILED with history. A racing
            # callback that already graded this lead wins; the attempt and
            # backoff stay recorded either way.
            await lifecycle.transition(
                session, lead, LeadStatus.FAILED.value,
                reason="dial_failed", source="outbound",
                expected=LeadStatus.QUEUED,
            )
        except (InvalidTransition, ClaimConflict) as conflict:
            log.warning(
                "outbound.dial_failed_grade_conflict",
                lead=str(lead.id), error=type(conflict).__name__,
            )
        await session.commit()
        # Provider detail stays out of the logs: the exception string can
        # quote a request body, and the number is redacted regardless. The
        # API return keeps the original message for the operator.
        log.error("outbound.dial_failed", lead=str(lead.id), error=type(exc).__name__)
        record_side_effect("outbound_call", "failure")
        return {"ok": False, "reason": "dial_failed", "error": str(exc)}

    session.add(
        Call(
            tenant_id=tenant.id,
            call_sid=tw_call.sid,
            from_number=caller_id,
            to_number=lead.phone,
            status=CallStatus.RINGING,
            direction=CallDirection.OUTBOUND,
            lead_id=lead.id,
        )
    )
    lead.next_attempt_at = now + backoff_for(lead.attempts)
    await session.commit()
    log.info(
        "outbound.dialed", to=phone.redact(lead.phone), campaign=campaign.name, sid=tw_call.sid
    )
    record_side_effect("outbound_call", "success")
    return {"ok": True, "call_sid": tw_call.sid, "to": lead.phone}


async def run_campaign_tick(
    session: AsyncSession, tenant: Tenant, campaign: Campaign, *, dry_run: bool = False
) -> dict:
    """One throttled batch. Call this on a schedule (cron / APScheduler)."""
    if not campaign.is_active:
        return {"dialed": 0, "reason": "campaign_inactive"}
    if not is_call_window_open(tenant):
        return {"dialed": 0, "reason": "outside_window"}

    leads = await next_callable_leads(session, tenant, campaign, limit=campaign.calls_per_minute)
    results = [await place_call(session, tenant, campaign, lead, dry_run=dry_run) for lead in leads]
    return {"dialed": sum(1 for r in results if r.get("ok")), "results": results}
