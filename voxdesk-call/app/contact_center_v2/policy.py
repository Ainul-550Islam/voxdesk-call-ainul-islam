"""Dialer compliance policy, evidence preparation and abandonment measurement."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import ContactCenterV2Error, NotFound
from app.contact_center_v2.occupancy import available_agent_ids
from app.contact_center_v2.pacing import (
    AbandonmentMeasurement,
    calling_window_open,
    effective_cap,
    next_calling_window,
    timezone_for_contact,
)
from app.contact_center_v2.repository import as_utc, owned, utcnow
from app.contact_center_v2.settings import settings
from app.db.contact_center_v2_models import (
    DialerContact,
    DialerJurisdictionOverride,
    DialerPolicy,
)
from app.db.models import Call, CallDirection, Campaign, Lead, LeadStatus, Tenant
from app.leads.consent import voice_denied
from app.leads.models import LeadConsent
from app.telephony import dnc, phone
from app.telephony.number_provisioning import PhoneNumber
from app.telephony.number_trust import NumberTrustProfile


def _mode_capabilities(mode: str):
    from app.contact_center_v2.dialer.modes import capabilities

    return capabilities(mode)


async def campaign_for(
    session: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID
) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None or campaign.tenant_id != tenant_id or campaign.environment_id is None:
        raise NotFound("campaign")
    return campaign


async def policy_for(
    session: AsyncSession, tenant_id: uuid.UUID, policy_id: uuid.UUID, *, for_update: bool = False
) -> DialerPolicy:
    return await owned(session, DialerPolicy, policy_id, tenant_id, for_update=for_update)


async def policy_for_campaign(
    session: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID
) -> DialerPolicy:
    row = (
        await session.execute(
            select(DialerPolicy).where(
                DialerPolicy.tenant_id == tenant_id,
                DialerPolicy.campaign_id == campaign_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("dialer policy")
    return row


async def active_override(
    session: AsyncSession,
    policy: DialerPolicy,
    *,
    now: datetime | None = None,
) -> DialerJurisdictionOverride | None:
    moment = now or utcnow()
    return (
        await session.execute(
            select(DialerJurisdictionOverride)
            .where(
                DialerJurisdictionOverride.tenant_id == policy.tenant_id,
                DialerJurisdictionOverride.policy_id == policy.id,
                DialerJurisdictionOverride.jurisdiction == policy.jurisdiction,
                DialerJurisdictionOverride.revoked_at.is_(None),
                DialerJurisdictionOverride.expires_at > moment,
                DialerJurisdictionOverride.abandonment_cap >= policy.abandonment_cap,
            )
            .order_by(DialerJurisdictionOverride.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def validated_cap(
    session: AsyncSession, policy: DialerPolicy, *, now: datetime | None = None
) -> Decimal:
    override = await active_override(session, policy, now=now)
    value = effective_cap(
        Decimal(policy.abandonment_cap),
        None if override is None else Decimal(override.abandonment_cap),
    )
    if value > Decimal(settings.dialer_max_abandon_cap_with_override):
        raise ContactCenterV2Error("invalid_abandonment_cap", "The jurisdiction cap exceeds the hard safety limit")
    return value


async def verified_caller_id(
    session: AsyncSession,
    policy: DialerPolicy,
    *,
    now: datetime | None = None,
) -> PhoneNumber:
    moment = now or utcnow()
    try:
        normalized = phone.normalize(policy.caller_id_number)
    except Exception as exc:
        raise ContactCenterV2Error("caller_id_invalid", "Caller ID must be a valid E.164 number") from exc
    row = (
        await session.execute(
            select(PhoneNumber, NumberTrustProfile)
            .join(NumberTrustProfile, NumberTrustProfile.phone_number_id == PhoneNumber.id)
            .where(
                PhoneNumber.tenant_id == policy.tenant_id,
                PhoneNumber.e164 == normalized,
                or_(
                    PhoneNumber.environment_id == policy.environment_id,
                    PhoneNumber.environment_id.is_(None),
                ),
                PhoneNumber.status.in_(("active", "assigned", "provisioned")),
                NumberTrustProfile.tenant_id == policy.tenant_id,
                NumberTrustProfile.e164 == normalized,
                NumberTrustProfile.last_checked.is_not(None),
            )
            .order_by(NumberTrustProfile.last_checked.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        raise ContactCenterV2Error(
            "caller_id_unverified",
            "The caller ID must belong to this tenant and have a provider-sourced trust profile",
        )
    number, trust = row
    if trust.last_checked is None or as_utc(trust.last_checked) < moment - timedelta(
        hours=settings.dialer_trust_max_age_hours
    ):
        raise ContactCenterV2Error("caller_id_stale", "The caller ID trust evidence is stale")
    if trust.shaken_attestation in (None, "unverified"):
        raise ContactCenterV2Error("caller_id_unverified", "The caller ID lacks verified attestation")
    capabilities = dict(number.capabilities or {})
    if capabilities.get("voice") is not True:
        raise ContactCenterV2Error("caller_id_voice_disabled", "The verified caller ID is not voice capable")
    return number


def validate_safe_harbor(policy: DialerPolicy) -> None:
    message = " ".join(policy.safe_harbor_message.split())
    if len(message) < 10 or len(message) > 1200:
        raise ContactCenterV2Error(
            "safe_harbor_required",
            "A bounded safe-harbor message is required before automated dialing",
        )


def scrub_fingerprint(lead: Lead) -> str:
    """Bind scrub evidence to the recipient identity, number, and local zone.

    The hash is not a secret or a substitute for the live DNC check. It makes a
    changed phone number or recipient timezone ineligible until preparation
    snapshots fresh evidence for that exact recipient.
    """
    canonical = json.dumps(
        {
            "tenant_id": str(lead.tenant_id),
            "environment_id": str(lead.environment_id),
            "lead_id": str(lead.id),
            "phone": phone.normalize(lead.phone),
            "recipient_timezone": str((lead.custom_fields or {}).get("timezone") or "").strip(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


async def latest_consent(
    session: AsyncSession, lead: Lead
) -> LeadConsent | None:
    return (
        await session.execute(
            select(LeadConsent)
            .where(
                LeadConsent.tenant_id == lead.tenant_id,
                LeadConsent.environment_id == lead.environment_id,
                LeadConsent.lead_id == lead.id,
                LeadConsent.channel == "voice",
            )
            .order_by(LeadConsent.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def prepare_contacts(
    session: AsyncSession,
    policy: DialerPolicy,
    *,
    now: datetime | None = None,
) -> dict[str, int]:
    """Scrub every current lead and snapshot the evidence used for eligibility."""
    moment = now or utcnow()
    campaign = await campaign_for(session, policy.tenant_id, policy.campaign_id)
    leads = (
        await session.execute(
            select(Lead)
            .where(
                Lead.tenant_id == policy.tenant_id,
                Lead.environment_id == policy.environment_id,
                Lead.campaign_id == campaign.id,
            )
            .order_by(Lead.created_at, Lead.id)
        )
    ).scalars().all()
    version = f"scrub-{uuid.uuid4().hex[:24]}"
    existing = {
        row.lead_id: row
        for row in (
            await session.execute(
                select(DialerContact).where(
                    DialerContact.tenant_id == policy.tenant_id,
                    DialerContact.policy_id == policy.id,
                )
            )
        ).scalars().all()
    }
    eligible = blocked = 0
    for lead in leads:
        contact = existing.get(lead.id)
        if contact is None:
            contact = DialerContact(
                tenant_id=policy.tenant_id,
                environment_id=policy.environment_id,
                policy_id=policy.id,
                lead_id=lead.id,
            )
            session.add(contact)
        raw_timezone = str((lead.custom_fields or {}).get("timezone") or "")
        timezone_for_contact(raw_timezone)
        blocked_reason = ""
        if lead.status is LeadStatus.DNC:
            blocked_reason = "lead_dnc"
        is_blocked, dnc_reason = await dnc.is_blocked(
            session,
            policy.tenant_id,
            lead.phone,
            lead_id=lead.id,
            environment_id=lead.environment_id,
        )
        if is_blocked:
            blocked_reason = dnc_reason or "dnc"
        consent = await latest_consent(session, lead)
        denied = await voice_denied(
            session, policy.tenant_id, lead.id, environment_id=lead.environment_id
        )
        if denied or (consent is not None and consent.decision == "denied"):
            blocked_reason = "consent_denied"
        elif not blocked_reason and (policy.consent_required or _mode_capabilities(policy.mode).affirmative_voice_consent_required) and (
            consent is None or consent.decision != "granted" or not consent.source or consent.recorded_at is None
        ):
            blocked_reason = "consent_required"
        contact.recipient_timezone = raw_timezone
        contact.dnc_scrubbed_at = moment
        contact.dnc_scrub_version = version
        contact.scrub_fingerprint = scrub_fingerprint(lead)
        contact.consent_version = None if consent is None else consent.version
        contact.consent_recorded_at = None if consent is None else consent.recorded_at
        contact.block_reason = blocked_reason
        if contact.state not in {"dialing", "completed", "skipped"}:
            contact.state = "blocked" if blocked_reason else "eligible"
        contact.updated_at = moment
        if blocked_reason:
            blocked += 1
        else:
            eligible += 1
    policy.dnc_scrubbed_at = moment
    policy.dnc_scrub_version = version
    policy.prepared_lead_count = len(leads)
    policy.updated_at = moment
    await session.flush()
    return {"leads": len(leads), "eligible": eligible, "blocked": blocked}


async def preparation_summary(
    session: AsyncSession, policy: DialerPolicy
) -> dict[str, int]:
    """Read current preparation counts for an idempotent replay response."""
    lead_count = int(
        await session.scalar(
            select(func.count())
            .select_from(Lead)
            .where(
                Lead.tenant_id == policy.tenant_id,
                Lead.environment_id == policy.environment_id,
                Lead.campaign_id == policy.campaign_id,
            )
        )
        or 0
    )
    contacts = (
        (
            await session.execute(
                select(DialerContact).where(
                    DialerContact.tenant_id == policy.tenant_id,
                    DialerContact.policy_id == policy.id,
                )
            )
        )
        .scalars()
        .all()
    )
    blocked = sum(bool(row.block_reason) for row in contacts)
    return {
        "leads": lead_count,
        "eligible": max(0, len(contacts) - blocked),
        "blocked": blocked,
    }


async def validate_start(
    session: AsyncSession,
    policy: DialerPolicy,
    *,
    now: datetime | None = None,
    require_agents: bool = True,
) -> None:
    moment = now or utcnow()
    if policy.status in {"completed", "stopped"}:
        raise ContactCenterV2Error("campaign_closed", "A closed dialer policy cannot be restarted")
    if policy.status == "running":
        raise ContactCenterV2Error("already_running", "The dialer is already running")
    validate_safe_harbor(policy)
    if policy.mode == "ai_agent_blast":
        ratio = Decimal(policy.pacing_ratio)
        if ratio < 1 or ratio != ratio.to_integral_value():
            raise ContactCenterV2Error(
                "ai_pacing_invalid",
                "AI blasting uses a whole number of new attempts per tick (minimum 1)",
                status_code=422,
            )
    if Decimal(policy.pacing_ratio) > Decimal(settings.dialer_max_pacing_ratio):
        raise ContactCenterV2Error("pacing_ratio_invalid", "Pacing ratio exceeds the configured safety limit")
    if policy.mode == "preview" and Decimal(policy.pacing_ratio) != Decimal("1"):
        raise ContactCenterV2Error("preview_pacing_invalid", "Preview dialing never uses a pacing ratio")
    await validated_cap(session, policy, now=moment)
    await verified_caller_id(session, policy, now=moment)
    tenant = await session.get(Tenant, policy.tenant_id)
    if tenant is None or not tenant.is_active or not tenant.outbound_enabled:
        raise ContactCenterV2Error("outbound_disabled", "Outbound calling is disabled for this tenant")
    if policy.mode == "ai_agent_blast":
        from app.runtime.agent_config_resolver import resolve_runtime_config

        probe = Call(
            tenant_id=policy.tenant_id,
            environment_id=policy.environment_id,
            call_sid=f"cc2-ai-precheck:{policy.id}",
            from_number=policy.caller_id_number,
            to_number="",
            direction=CallDirection.OUTBOUND,
        )
        try:
            await resolve_runtime_config(
                session, probe, tenant=tenant, environment=policy.environment_id
            )
        except (ValueError, LookupError) as exc:
            raise ContactCenterV2Error(
                "ai_agent_unavailable",
                "AI blasting requires a resolvable AI agent runtime for this campaign",
            ) from exc
    if not policy.dnc_scrubbed_at or not policy.dnc_scrub_version:
        raise ContactCenterV2Error("dnc_scrub_required", "A current DNC scrub is required")
    eligible = (
        await session.execute(
            select(func.count())
            .select_from(DialerContact)
            .where(
                DialerContact.tenant_id == policy.tenant_id,
                DialerContact.policy_id == policy.id,
                DialerContact.dnc_scrub_version == policy.dnc_scrub_version,
                DialerContact.state.in_(("eligible", "retry_scheduled", "dialing")),
            )
        )
    ).scalar_one()
    if int(eligible or 0) == 0:
        raise ContactCenterV2Error("no_eligible_contacts", "No contacts passed DNC, consent and evidence checks")
    if require_agents and policy.mode in {"predictive", "progressive"}:
        agents = await available_agent_ids(
            session, policy.tenant_id, now=moment, require_softphone=True,
            environment_id=policy.environment_id,
        )
        if not agents:
            raise ContactCenterV2Error(
                "no_available_agent",
                "At least one available agent with an active softphone is required",
            )


async def assert_contact_callable(
    session: AsyncSession,
    policy: DialerPolicy,
    contact: DialerContact,
    lead: Lead,
    *,
    now: datetime | None = None,
) -> None:
    moment = now or utcnow()
    if lead.tenant_id != policy.tenant_id or lead.environment_id != policy.environment_id:
        raise NotFound("lead")
    if contact.dnc_scrubbed_at is None or contact.dnc_scrub_version != policy.dnc_scrub_version:
        raise ContactCenterV2Error("dnc_scrub_required", "The contact needs a current DNC scrub")
    if contact.scrub_fingerprint != scrub_fingerprint(lead):
        raise ContactCenterV2Error(
            "dnc_scrub_required",
            "Recipient identity, number, or timezone changed; prepare a fresh DNC scrub",
        )
    if contact.block_reason or lead.status is LeadStatus.DNC:
        raise ContactCenterV2Error("contact_blocked", contact.block_reason or "lead_dnc")
    is_blocked, reason = await dnc.is_blocked(
        session, policy.tenant_id, lead.phone, lead_id=lead.id, environment_id=lead.environment_id
    )
    if is_blocked:
        contact.state = "blocked"
        contact.block_reason = reason or "dnc"
        await session.flush()
        raise ContactCenterV2Error("contact_blocked", "The contact is on a do-not-call list")
    consent = await latest_consent(session, lead)
    if await voice_denied(session, policy.tenant_id, lead.id, environment_id=lead.environment_id):
        raise ContactCenterV2Error("consent_required", "Voice consent is denied")
    if (policy.consent_required or _mode_capabilities(policy.mode).affirmative_voice_consent_required) and (
        consent is None
        or consent.decision != "granted"
        or not consent.source
        or consent.recorded_at is None
        or consent.version != contact.consent_version
    ):
        raise ContactCenterV2Error("consent_required", "Current affirmative voice consent is required")
    if not calling_window_open(
        recipient_timezone=contact.recipient_timezone,
        windows=list(policy.calling_windows or []),
        holiday_dates=list(policy.holiday_dates or []),
        now=moment,
    ):
        retry = next_calling_window(
            recipient_timezone=contact.recipient_timezone,
            windows=list(policy.calling_windows or []),
            holiday_dates=list(policy.holiday_dates or []),
            now=moment,
        )
        contact.next_attempt_at = retry
        contact.state = "retry_scheduled"
        await session.flush()
        raise ContactCenterV2Error(
            "outside_recipient_window",
            "The recipient is outside the permitted local calling window",
            detail={"next_attempt_at": retry.isoformat()},
        )
    await verified_caller_id(session, policy, now=moment)
    validate_safe_harbor(policy)


async def abandonment_measurement(
    session: AsyncSession,
    policy: DialerPolicy,
    *,
    now: datetime | None = None,
) -> AbandonmentMeasurement:
    moment = now or utcnow()
    cap = await validated_cap(session, policy, now=moment)
    from app.contact_center_v2.dialer.abandon_tracker import AbandonTracker

    return await AbandonTracker(session).measurement(
        policy,
        cap=cap,
        minimum_sample=settings.dialer_min_abandon_sample,
        now=moment,
    )
