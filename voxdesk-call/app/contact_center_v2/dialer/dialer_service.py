"""Predictive, progressive and agent-clicked preview dialing execution."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import ContactCenterV2Error, NotFound
from app.contact_center_v2.occupancy import (
    available_agent_ids,
    release_agent,
    reserve_agent,
)
from app.contact_center_v2.pacing import PacingDecision
from app.contact_center_v2.policy import (
    abandonment_measurement,
    assert_contact_callable,
    campaign_for,
    policy_for,
    prepare_contacts,
    validate_start,
    validated_cap,
    verified_caller_id,
)
from app.contact_center_v2.repository import as_utc, compare_and_set, owned, utcnow
from app.contact_center_v2.settings import settings
from app.db.contact_center_v2_models import (
    AgentReservation,
    DialerAttempt,
    DialerContact,
    DialerJurisdictionOverride,
    DialerPolicy,
    PacingSnapshot,
    PreviewReservation,
)
from app.db.models import Campaign, Lead, Tenant
from app.jobs.repository import create_job
from app.jobs.types import ensure_payload_safe

ACTIVE_OUTCOMES = ("initiated", "ringing", "answered", "waiting_for_agent")
TERMINAL_OUTCOMES = ("machine", "abandoned", "failed", "completed", "cancelled")


def _policy_values(payload: dict) -> dict:
    allowed = {
        "mode", "caller_id_number", "pacing_ratio", "abandonment_cap", "max_inflight",
        "calling_windows", "holiday_dates", "safe_harbor_message", "consent_required", "jurisdiction",
    }
    values = {key: value for key, value in payload.items() if key in allowed and value is not None}
    if "calling_windows" in values:
        values["calling_windows"] = [
            item.model_dump() if hasattr(item, "model_dump") else dict(item)
            for item in values["calling_windows"]
        ]
    return values


async def upsert_policy(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    payload: dict,
    policy_id: uuid.UUID | None = None,
) -> DialerPolicy:
    if policy_id is None:
        campaign = await campaign_for(session, tenant_id, payload["campaign_id"])
        existing = (
            await session.execute(
                select(DialerPolicy).where(
                    DialerPolicy.tenant_id == tenant_id,
                    DialerPolicy.campaign_id == campaign.id,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ContactCenterV2Error("policy_exists", "This campaign already has a dialer policy")
        row = DialerPolicy(
            tenant_id=tenant_id,
            environment_id=campaign.environment_id,
            campaign_id=campaign.id,
            **_policy_values(payload),
        )
        session.add(row)
    else:
        row = await policy_for(session, tenant_id, policy_id, for_update=True)
        if row.status == "running":
            raise ContactCenterV2Error("policy_running", "Pause the dialer before changing its policy")
        for key, value in _policy_values(payload).items():
            setattr(row, key, value)
        row.version += 1
        row.dnc_scrubbed_at = None
        row.dnc_scrub_version = ""
    if row.mode == "ai_agent_blast":
        ratio = Decimal(row.pacing_ratio)
        if ratio < 1 or ratio != ratio.to_integral_value():
            raise ContactCenterV2Error(
                "ai_pacing_invalid",
                "AI blasting uses a whole number of new attempts per tick (minimum 1)",
                status_code=422,
            )
    row.updated_at = utcnow()
    await session.flush()
    return row


async def add_jurisdiction_override(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    policy_id: uuid.UUID,
    actor_id: uuid.UUID,
    jurisdiction: str,
    abandonment_cap: Decimal,
    legal_reference: str,
    reason: str,
    expires_at: datetime,
) -> DialerJurisdictionOverride:
    policy = await policy_for(session, tenant_id, policy_id)
    if as_utc(expires_at) <= utcnow():
        raise ContactCenterV2Error("override_expired", "The override expiry must be in the future", status_code=422)
    if jurisdiction != policy.jurisdiction:
        raise ContactCenterV2Error("jurisdiction_mismatch", "The override must name the policy jurisdiction")
    if Decimal(abandonment_cap) <= Decimal("0.03"):
        raise ContactCenterV2Error("override_not_required", "Caps at or below 3% do not use an override", status_code=422)
    if Decimal(abandonment_cap) > Decimal(settings.dialer_max_abandon_cap_with_override):
        raise ContactCenterV2Error("invalid_abandonment_cap", "The requested cap exceeds the hard safety limit")
    row = DialerJurisdictionOverride(
        tenant_id=tenant_id,
        environment_id=policy.environment_id,
        policy_id=policy.id,
        jurisdiction=jurisdiction,
        abandonment_cap=abandonment_cap,
        legal_reference=legal_reference,
        reason=reason,
        approved_by=actor_id,
        expires_at=expires_at,
    )
    session.add(row)
    await session.flush()
    return row


async def start_campaign(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    policy_id: uuid.UUID,
    actor_id: uuid.UUID,
    now: datetime | None = None,
) -> tuple[DialerPolicy, uuid.UUID]:
    del actor_id
    moment = now or utcnow()
    policy = await policy_for(session, tenant_id, policy_id, for_update=True)
    await prepare_contacts(session, policy, now=moment)
    from app.contact_center_v2.dialer.precheck import campaign_precheck

    precheck = await campaign_precheck(session, policy, now=moment, refresh=False)
    if not precheck.allowed:
        reason = precheck.reasons[0]
        raise ContactCenterV2Error(reason.code, reason.message, detail=precheck.as_dict())
    await validate_start(session, policy, now=moment, require_agents=True)
    measurement = await abandonment_measurement(session, policy, now=moment)
    if measurement.breached:
        raise ContactCenterV2Error(
            "abandonment_cap",
            "Predictive or progressive dialing is stopped at the rolling abandonment cap",
            detail=measurement.as_dict(),
        )
    policy.status = "running"
    policy.started_at = policy.started_at or moment
    policy.stopped_at = None
    policy.version += 1
    policy.updated_at = moment
    payload = {"policy_id": str(policy.id)}
    ensure_payload_safe(payload)
    job, _ = await create_job(
        session,
        tenant_id=tenant_id,
        environment_id=policy.environment_id,
        job_type="cc2.dialer.start",
        payload=payload,
        idempotency_key=f"cc2-dialer-start:{policy.id}:{policy.version}",
        priority=0,
    )
    await session.flush()
    return policy, job.id


async def pause_campaign(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    policy_id: uuid.UUID,
    stopped: bool = False,
) -> DialerPolicy:
    policy = await policy_for(session, tenant_id, policy_id, for_update=True)
    if policy.status in {"completed", "stopped"} and not stopped:
        return policy
    policy.status = "stopped" if stopped else "paused"
    policy.stopped_at = utcnow()
    policy.version += 1
    policy.updated_at = utcnow()
    await session.flush()
    return policy


async def _schedule_tick(
    session: AsyncSession,
    policy: DialerPolicy,
    *,
    now: datetime,
) -> None:
    if policy.mode == "preview" or policy.status != "running":
        return
    delay = float(settings.dialer_tick_seconds)
    available = now + timedelta(seconds=delay)
    bucket = int(now.timestamp() / delay)
    payload = {"policy_id": str(policy.id)}
    ensure_payload_safe(payload)
    await create_job(
        session,
        tenant_id=policy.tenant_id,
        environment_id=policy.environment_id,
        job_type="cc2.dialer.tick",
        payload=payload,
        idempotency_key=f"cc2-dialer-tick:{policy.id}:{bucket}",
        available_at=available,
        priority=0,
    )


async def _release_attempt_agent(
    session: AsyncSession,
    attempt: DialerAttempt,
    *,
    wrap_up: bool,
    now: datetime | None = None,
) -> None:
    reservation = (
        await session.execute(
            select(AgentReservation).where(
                AgentReservation.tenant_id == attempt.tenant_id,
                AgentReservation.kind == "dialer",
                AgentReservation.resource_id == attempt.id,
                AgentReservation.status == "active",
            )
        )
    ).scalar_one_or_none()
    if reservation is not None:
        await release_agent(session, reservation, wrap_up=wrap_up, now=now)


async def execute_contact(
    session: AsyncSession,
    *,
    policy: DialerPolicy,
    contact: DialerContact,
    actor_agent_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    now: datetime | None = None,
) -> DialerAttempt | None:
    """Claim a contact, reserve any required agent, then reuse ``place_call``.

    The existing outbound service remains the only lead-claim and carrier-dial
    path. An ambiguous provider failure blocks automatic retry so a second call
    cannot be placed when the first provider outcome is unknown.
    """
    moment = now or utcnow()
    lead = await session.get(Lead, contact.lead_id)
    if lead is None:
        raise NotFound("lead")
    from app.contact_center_v2.dialer.precheck import jurisdiction_block_reason

    jurisdiction_reason = jurisdiction_block_reason(policy, lead)
    if jurisdiction_reason:
        raise ContactCenterV2Error(jurisdiction_reason, "Recipient jurisdiction evidence is incomplete")
    await assert_contact_callable(session, policy, contact, lead, now=moment)
    attempt = DialerAttempt(
        tenant_id=policy.tenant_id,
        environment_id=policy.environment_id,
        policy_id=policy.id,
        contact_id=contact.id,
        lead_id=lead.id,
        mode=policy.mode,
        outcome="initiated",
        idempotency_key=(
            f"cc2-preview:{hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()}"
            if idempotency_key
            else f"cc2-attempt:{contact.id}:{lead.attempts + 1}"
        ),
        agent_user_id=actor_agent_id,
        created_at=moment,
        updated_at=moment,
    )
    session.add(attempt)
    await session.flush()
    expected_state = "preview_assigned" if policy.mode == "preview" else contact.state
    claimed = await compare_and_set(
        session,
        DialerContact,
        contact.id,
        policy.tenant_id,
        expected={"state": expected_state, "active_attempt_id": None, "version": contact.version},
        values={
            "state": "dialing",
            "active_attempt_id": attempt.id,
            "assigned_agent_id": actor_agent_id,
            "version": contact.version + 1,
            "updated_at": moment,
        },
    )
    if not claimed:
        attempt.outcome = "cancelled"
        attempt.ended_at = moment
        await session.flush()
        return None
    await session.refresh(contact)
    if policy.mode in {"progressive", "preview"}:
        if actor_agent_id is None:
            raise ContactCenterV2Error("agent_required", "This dialing mode requires a reserved agent")
        try:
            await reserve_agent(
                session,
                tenant_id=policy.tenant_id,
                environment_id=policy.environment_id,
                user_id=actor_agent_id,
                kind="dialer",
                resource_id=attempt.id,
                now=moment,
                require_softphone=True,
            )
        except Exception:
            contact.state = "eligible"
            contact.active_attempt_id = None
            contact.version += 1
            attempt.outcome = "cancelled"
            attempt.ended_at = moment
            await session.flush()
            raise
    attempt.agent_user_id = actor_agent_id
    tenant = await session.get(Tenant, policy.tenant_id)
    campaign = await session.get(Campaign, policy.campaign_id)
    if tenant is None or campaign is None:
        raise NotFound("campaign")
    from app.telephony.outbound import place_call

    answer_url = (
        # The existing signed Twilio webhook is extended by BATCH04 markers.
        # No unauthenticated provider callback is introduced. AI blast keeps
        # the canonical campaign id so the existing AI media stream is used.
        f"{__import__('app.core.config', fromlist=['settings']).settings.public_base_url.rstrip('/')}"
        + (
            f"/telephony/outbound-answer?campaign_id={campaign.id}&lead_id={lead.id}"
            if policy.mode == "ai_agent_blast"
            else f"/telephony/outbound-answer?campaign_id=dialer:{attempt.id}&lead_id={lead.id}"
        )
    )
    result = await place_call(
        session,
        tenant,
        campaign,
        lead,
        answer_url=answer_url,
        caller_id_number=policy.caller_id_number,
    )
    moment = utcnow()
    attempt.updated_at = moment
    if not result.get("ok"):
        reason = str(result.get("reason") or "dial_failed")
        attempt.outcome = "failed"
        attempt.failure_category = reason[:64]
        attempt.ended_at = moment
        contact.active_attempt_id = None
        contact.version += 1
        contact.updated_at = moment
        if reason in {"dial_failed", "provider_unavailable"}:
            contact.state = "blocked"
            contact.block_reason = "provider_ambiguous"
        elif reason in {"outside_window", "concurrency_limit", "rate_limit"}:
            contact.state = "retry_scheduled"
            contact.next_attempt_at = moment + timedelta(minutes=5)
        else:
            contact.state = "blocked"
            contact.block_reason = reason[:64]
        await _release_attempt_agent(session, attempt, wrap_up=False, now=moment)
        await session.commit()
        return attempt
    attempt.provider_call_id = str(result.get("call_sid") or "") or None
    attempt.call_id = uuid.UUID(str(result["call_id"]))
    attempt.outcome = "ringing"
    contact.next_attempt_at = None
    contact.updated_at = moment
    await session.commit()
    return attempt


async def run_tick(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    policy_id: uuid.UUID,
    now: datetime | None = None,
) -> dict:
    moment = now or utcnow()
    policy = await policy_for(session, tenant_id, policy_id, for_update=True)
    if policy.status != "running":
        return {"attempted": 0, "reason": "not_running"}
    if policy.mode == "preview":
        return {"attempted": 0, "reason": "agent_click_required"}
    # A running policy is revalidated without the start-transition check.
    from app.contact_center_v2.policy import validate_safe_harbor

    validate_safe_harbor(policy)
    await verified_caller_id(session, policy, now=moment)
    await validated_cap(session, policy, now=moment)
    measurement = await abandonment_measurement(session, policy, now=moment)
    inflight = int(
        await session.scalar(
            select(func.count())
            .select_from(DialerAttempt)
            .where(
                DialerAttempt.tenant_id == tenant_id,
                DialerAttempt.policy_id == policy.id,
                DialerAttempt.outcome.in_(ACTIVE_OUTCOMES),
            )
        ) or 0
    )
    from app.contact_center_v2.dialer.agent_availability import estimate_agent_availability
    from app.contact_center_v2.dialer.pacing_engine import (
        answer_rate_from_history,
        compute_pacing_plan,
    )

    availability = await estimate_agent_availability(
        session, tenant_id=tenant_id, environment_id=policy.environment_id, now=moment
    )
    answered = int(
        await session.scalar(
            select(func.count()).select_from(DialerAttempt).where(
                DialerAttempt.tenant_id == tenant_id,
                DialerAttempt.policy_id == policy.id,
                DialerAttempt.answered_at.is_not(None),
            )
        ) or 0
    )
    completed_attempts = int(
        await session.scalar(
            select(func.count()).select_from(DialerAttempt).where(
                DialerAttempt.tenant_id == tenant_id,
                DialerAttempt.policy_id == policy.id,
                DialerAttempt.outcome.in_(TERMINAL_OUTCOMES),
            )
        ) or 0
    )
    plan = compute_pacing_plan(
        mode=policy.mode,
        requested_ratio=Decimal(policy.pacing_ratio),
        measurement=measurement,
        availability=availability,
        inflight=inflight,
        max_inflight=policy.max_inflight,
        answer_rate=answer_rate_from_history(answered, completed_attempts),
        now=moment,
    )
    decision = PacingDecision(
        plan.allowed, plan.reason, plan.target_inflight,
        plan.allowed if policy.mode == "progressive" else 0,
    )
    session.add(PacingSnapshot(
        tenant_id=tenant_id,
        environment_id=policy.environment_id,
        campaign_id=policy.campaign_id,
        policy_id=policy.id,
        mode=policy.mode,
        answer_rate=plan.answer_rate,
        occupancy=plan.occupancy,
        dial_ratio=plan.dial_ratio,
        projected_abandon_rate=plan.projected_rate,
        available_agents=plan.available_agents,
        soon_available_agents=plan.soon_available_agents,
        sample_size=plan.sample_size,
        throttled_to_progressive=plan.throttled_to_progressive,
        reason=plan.reason,
        created_at=moment,
    ))
    if decision.reason == "abandonment_cap":
        policy.status = "paused"
        policy.updated_at = moment
        await session.flush()
        return {
            "attempted": 0, "reason": decision.reason,
            "measurement": measurement.as_dict(), "pacing_plan": plan.as_dict(),
        }
    attempted = 0
    if decision.allowed:
        contacts = (
            await session.execute(
                select(DialerContact)
                .join(Lead, Lead.id == DialerContact.lead_id)
                .where(
                    DialerContact.tenant_id == tenant_id,
                    DialerContact.policy_id == policy.id,
                    DialerContact.state.in_(("eligible", "retry_scheduled")),
                    DialerContact.active_attempt_id.is_(None),
                    Lead.campaign_id == policy.campaign_id,
                    Lead.environment_id == policy.environment_id,
                )
                .order_by(DialerContact.next_attempt_at.asc().nullsfirst(), DialerContact.created_at, DialerContact.id)
                .limit(min(settings.dialer_max_attempts_per_tick, decision.allowed))
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        from .dial_worker import acquire_cps_permit

        for contact in contacts:
            if attempted >= decision.allowed:
                break
            if contact.next_attempt_at is not None and as_utc(contact.next_attempt_at) > moment:
                continue
            agent_id = None
            if policy.mode == "progressive":
                remaining = await available_agent_ids(
        session, tenant_id, now=moment, require_softphone=True, environment_id=policy.environment_id
    )
                if not remaining:
                    break
                agent_id = remaining[0]
            if not await acquire_cps_permit(
                tenant_id=tenant_id,
                policy_id=policy.id,
                rate=Decimal(settings.dialer_cps),
                burst=settings.dialer_cps_burst,
                now=moment,
            ):
                break
            try:
                attempt = await execute_contact(
                    session, policy=policy, contact=contact, actor_agent_id=agent_id, now=moment
                )
            except ContactCenterV2Error as exc:
                if exc.code in {"outside_recipient_window", "contact_blocked", "consent_required"}:
                    continue
                raise
            if attempt is not None and attempt.outcome == "ringing":
                attempted += 1
    await _schedule_tick(session, policy, now=utcnow())
    await session.flush()
    return {
        "attempted": attempted,
        "reason": decision.reason,
        "decision": decision.as_dict(),
        "measurement": measurement.as_dict(),
        "pacing_plan": plan.as_dict(),
    }


async def _expire_preview_reservations(
    session: AsyncSession, *, policy: DialerPolicy, now: datetime
) -> int:
    rows = (
        (
            await session.execute(
                select(PreviewReservation)
                .where(
                    PreviewReservation.tenant_id == policy.tenant_id,
                    PreviewReservation.policy_id == policy.id,
                    PreviewReservation.status == "active",
                    PreviewReservation.expires_at <= now,
                )
                .order_by(PreviewReservation.expires_at, PreviewReservation.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    for reservation in rows:
        reservation.status = "expired"
        reservation.active_key = None
        reservation.closed_at = now
        contact = await session.get(DialerContact, reservation.contact_id)
        if (
            contact is not None
            and contact.state == "preview_assigned"
            and contact.assigned_agent_id == reservation.agent_user_id
            and contact.active_attempt_id is None
        ):
            contact.state = "eligible"
            contact.assigned_agent_id = None
            contact.preview_lease_until = None
            contact.version += 1
            contact.updated_at = now
    if rows:
        await session.flush()
    return len(rows)


async def expire_preview_reservations(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """Release expired preview leases across bounded, locked policy batches."""
    moment = now or utcnow()
    policies = (
        await session.execute(
            select(DialerPolicy)
            .where(DialerPolicy.mode == "preview")
            .order_by(DialerPolicy.updated_at, DialerPolicy.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    expired = 0
    for policy in policies:
        expired += await _expire_preview_reservations(session, policy=policy, now=moment)
    return expired


async def _preview_reservation(
    session: AsyncSession, *, contact: DialerContact, policy: DialerPolicy,
    agent_id: uuid.UUID, expires_at: datetime, now: datetime,
) -> PreviewReservation:
    row = (
        await session.execute(
            select(PreviewReservation).where(
                PreviewReservation.tenant_id == policy.tenant_id,
                PreviewReservation.contact_id == contact.id,
                PreviewReservation.status == "active",
            )
        )
    ).scalar_one_or_none()
    if row is None:
        row = PreviewReservation(
            tenant_id=policy.tenant_id,
            environment_id=policy.environment_id,
            policy_id=policy.id,
            contact_id=contact.id,
            lead_id=contact.lead_id,
            agent_user_id=agent_id,
            status="active",
            active_key=str(contact.id),
            expires_at=expires_at,
            created_at=now,
        )
        session.add(row)
    else:
        if row.agent_user_id != agent_id:
            raise ContactCenterV2Error("preview_claimed", "Another agent claimed the preview contact")
        row.expires_at = expires_at
        row.active_key = str(contact.id)
    await session.flush()
    return row


async def preview_next(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    policy_id: uuid.UUID,
    agent_id: uuid.UUID,
    now: datetime | None = None,
) -> tuple[DialerContact, Lead]:
    moment = now or utcnow()
    policy = await policy_for(session, tenant_id, policy_id)
    if policy.mode != "preview" or policy.status != "running":
        raise ContactCenterV2Error("preview_not_running", "Preview dialing is not running")
    await _expire_preview_reservations(session, policy=policy, now=moment)
    existing = (
        await session.execute(
            select(DialerContact).where(
                DialerContact.tenant_id == tenant_id,
                DialerContact.policy_id == policy.id,
                DialerContact.assigned_agent_id == agent_id,
                DialerContact.state == "preview_assigned",
                DialerContact.preview_lease_until > moment,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        lead = await session.get(Lead, existing.lead_id)
        if lead is not None:
            await _preview_reservation(
                session, contact=existing, policy=policy, agent_id=agent_id,
                expires_at=as_utc(existing.preview_lease_until), now=moment,
            )
            return existing, lead
    contact = (
        await session.execute(
            select(DialerContact)
            .join(Lead, Lead.id == DialerContact.lead_id)
            .where(
                DialerContact.tenant_id == tenant_id,
                DialerContact.policy_id == policy.id,
                DialerContact.state.in_(("eligible", "retry_scheduled")),
                DialerContact.active_attempt_id.is_(None),
                or_(
                    DialerContact.next_attempt_at.is_(None),
                    DialerContact.next_attempt_at <= moment,
                ),
                Lead.campaign_id == policy.campaign_id,
            )
            .order_by(DialerContact.next_attempt_at.asc().nullsfirst(), DialerContact.created_at, DialerContact.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if contact is None:
        raise NotFound("preview contact")
    lease = moment + timedelta(seconds=settings.dialer_preview_lease_seconds)
    claimed = await compare_and_set(
        session,
        DialerContact,
        contact.id,
        tenant_id,
        expected={"state": contact.state, "version": contact.version, "active_attempt_id": None},
        values={
            "state": "preview_assigned",
            "assigned_agent_id": agent_id,
            "preview_lease_until": lease,
            "version": contact.version + 1,
            "updated_at": moment,
        },
    )
    if not claimed:
        raise ContactCenterV2Error("preview_claimed", "Another agent claimed the preview contact")
    await session.refresh(contact)
    lead = await session.get(Lead, contact.lead_id)
    if lead is None:
        raise NotFound("lead")
    await _preview_reservation(
        session, contact=contact, policy=policy, agent_id=agent_id,
        expires_at=lease, now=moment,
    )
    return contact, lead


async def preview_dial(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    contact_id: uuid.UUID,
    agent_id: uuid.UUID,
    idempotency_key: str,
    now: datetime | None = None,
) -> DialerAttempt | None:
    moment = now or utcnow()
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    existing = (
        await session.execute(
            select(DialerAttempt).where(
                DialerAttempt.tenant_id == tenant_id,
                DialerAttempt.idempotency_key == f"cc2-preview:{digest}",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.contact_id != contact_id or existing.agent_user_id != agent_id:
            raise ContactCenterV2Error("idempotency_conflict", "The idempotency key belongs to another attempt")
        return existing
    contact = await owned(session, DialerContact, contact_id, tenant_id, for_update=True)
    policy = await policy_for(session, tenant_id, contact.policy_id)
    if policy.mode != "preview" or policy.status != "running":
        raise ContactCenterV2Error("preview_not_running", "Preview dialing is not running")
    if (
        contact.state != "preview_assigned"
        or contact.assigned_agent_id != agent_id
        or contact.preview_lease_until is None
        or as_utc(contact.preview_lease_until) <= moment
    ):
        raise ContactCenterV2Error("preview_lease_lost", "This agent does not hold a current preview lease")
    attempt = await execute_contact(
        session,
        policy=policy,
        contact=contact,
        actor_agent_id=agent_id,
        idempotency_key=idempotency_key,
        now=moment,
    )
    reservation = (
        await session.execute(
            select(PreviewReservation).where(
                PreviewReservation.tenant_id == tenant_id,
                PreviewReservation.contact_id == contact_id,
                PreviewReservation.agent_user_id == agent_id,
                PreviewReservation.status == "active",
            )
        )
    ).scalar_one_or_none()
    if reservation is not None:
        reservation.status = "dialed" if attempt is not None else "released"
        reservation.active_key = None
        reservation.closed_at = utcnow()
        await session.flush()
    return attempt


async def preview_skip(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    contact_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> DialerContact:
    contact = await owned(session, DialerContact, contact_id, tenant_id, for_update=True)
    if contact.state != "preview_assigned" or contact.assigned_agent_id != agent_id:
        raise ContactCenterV2Error("preview_lease_lost", "This agent does not hold the preview lease")
    contact.state = "skipped"
    contact.preview_lease_until = None
    contact.version += 1
    contact.updated_at = utcnow()
    reservation = (
        await session.execute(
            select(PreviewReservation).where(
                PreviewReservation.tenant_id == tenant_id,
                PreviewReservation.contact_id == contact_id,
                PreviewReservation.agent_user_id == agent_id,
                PreviewReservation.status == "active",
            )
        )
    ).scalar_one_or_none()
    if reservation is not None:
        reservation.status = "skipped"
        reservation.active_key = None
        reservation.closed_at = contact.updated_at
    await session.flush()
    return contact


async def mark_agent_connected(
    session: AsyncSession,
    attempt: DialerAttempt,
    *,
    now: datetime | None = None,
) -> None:
    moment = now or utcnow()
    if attempt.agent_connected_at is None:
        attempt.agent_connected_at = moment
    attempt.outcome = "connected"
    attempt.updated_at = moment
    await session.flush()


async def finalize_attempt(
    session: AsyncSession,
    attempt: DialerAttempt,
    *,
    outcome: str,
    duration_seconds: int = 0,
    failure_category: str = "",
    now: datetime | None = None,
) -> None:
    if outcome not in TERMINAL_OUTCOMES:
        raise ContactCenterV2Error("invalid_attempt_outcome", "Invalid terminal attempt outcome", status_code=422)
    if attempt.outcome in TERMINAL_OUTCOMES:
        return
    moment = now or utcnow()
    attempt.outcome = outcome
    attempt.duration_seconds = max(0, int(duration_seconds))
    attempt.failure_category = failure_category[:64]
    attempt.ended_at = moment
    if outcome == "abandoned":
        attempt.abandoned_at = attempt.abandoned_at or moment
        attempt.abandonment_counted = True
    if outcome in {"completed", "abandoned"} and attempt.safe_harbor_requested_at and not attempt.safe_harbor_played_at:
        # Terminal provider status after the safe-harbor TwiML was accepted is
        # the only point at which this timestamp is recorded.
        attempt.safe_harbor_played_at = moment
    contact = await session.get(DialerContact, attempt.contact_id)
    if contact is not None:
        contact.active_attempt_id = None
        contact.version += 1
        contact.updated_at = moment
        if outcome == "completed":
            contact.state = "completed"
        elif outcome in {"machine", "failed"}:
            contact.state = "retry_scheduled"
            contact.next_attempt_at = moment + timedelta(hours=2)
        elif outcome == "abandoned":
            contact.state = "retry_scheduled"
            contact.next_attempt_at = moment + timedelta(hours=4)
        else:
            contact.state = "completed"
    if outcome == "completed" and attempt.agent_connected_at is not None:
        from app.contact_center_v2.dialer.wrapup_guard import ensure_wrapup_draft

        await ensure_wrapup_draft(session, attempt, now=moment)
    await _release_attempt_agent(
        session,
        attempt,
        wrap_up=outcome == "completed" and attempt.agent_connected_at is not None,
        now=moment,
    )
    await session.flush()
