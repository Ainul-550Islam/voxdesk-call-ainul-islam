"""Fail-closed campaign and per-recipient compliance prechecks."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import ContactCenterV2Error
from app.contact_center_v2.policy import (
    assert_contact_callable,
    prepare_contacts,
    validate_start,
)
from app.contact_center_v2.repository import utcnow
from app.db.contact_center_v2_models import DialerContact, DialerPolicy
from app.db.models import Lead


@dataclass(frozen=True)
class JurisdictionRule:
    affirmative_voice_consent: bool = True
    maximum_cap_without_override: str = "0.03"
    recipient_timezone_required: bool = True
    safe_harbor_required: bool = True


# Conservative operational defaults, not a claim that this short table is
# legal advice. Unknown jurisdictions fail closed until a tenant-specific rule
# and audited jurisdiction override are configured.
JURISDICTION_RULES: dict[str, JurisdictionRule] = {
    code: JurisdictionRule()
    for code in (
        "US", "US-CA", "US-FL", "US-NY", "US-TX", "US-WA",
        "CA", "GB", "AU", "NZ", "IE", "IN", "BD",
    )
}


@dataclass(frozen=True)
class PrecheckReason:
    code: str
    message: str
    lead_id: str | None = None


@dataclass(frozen=True)
class PrecheckResult:
    allowed: bool
    reasons: tuple[PrecheckReason, ...] = field(default_factory=tuple)
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reasons": [
                {"code": item.code, "message": item.message, "lead_id": item.lead_id}
                for item in self.reasons
            ],
            "evidence": self.evidence,
        }


def lead_jurisdiction(policy: DialerPolicy, lead: Lead) -> str:
    fields = dict(lead.custom_fields or {})
    country = str(fields.get("country") or "").strip().upper()
    state = str(fields.get("state") or "").strip().upper()
    if country and state and country == "US":
        return f"US-{state}"
    return country or str(policy.jurisdiction or "").strip().upper()


def jurisdiction_block_reason(policy: DialerPolicy, lead: Lead) -> str | None:
    from app.contact_center_v2.dialer.modes import capabilities

    jurisdiction = lead_jurisdiction(policy, lead)
    rule = JURISDICTION_RULES.get(jurisdiction)
    if rule is None:
        return "jurisdiction_rule_missing"
    if rule.affirmative_voice_consent and not capabilities(policy.mode).affirmative_voice_consent_required:
        raise ContactCenterV2Error("consent_required", "This dialing mode requires affirmative voice consent")
    if rule.affirmative_voice_consent and not policy.consent_required:
        return "consent_required"
    if rule.recipient_timezone_required and not str((lead.custom_fields or {}).get("timezone") or "").strip():
        return "recipient_timezone_required"
    if rule.safe_harbor_required and not policy.safe_harbor_message.strip():
        return "safe_harbor_required"
    return None


async def precheck_contact(
    session: AsyncSession,
    *,
    policy: DialerPolicy,
    contact: DialerContact,
    lead: Lead,
    now: datetime | None = None,
) -> PrecheckResult:
    jurisdictional = jurisdiction_block_reason(policy, lead)
    if jurisdictional:
        return PrecheckResult(False, (PrecheckReason(jurisdictional, "Recipient jurisdiction evidence is incomplete", str(lead.id)),))
    try:
        await assert_contact_callable(session, policy, contact, lead, now=now or utcnow())
    except ContactCenterV2Error as exc:
        return PrecheckResult(False, (PrecheckReason(exc.code, exc.message, str(lead.id)),), dict(exc.detail or {}))
    return PrecheckResult(True, evidence={
        "lead_id": str(lead.id),
        "dnc_scrub_version": contact.dnc_scrub_version,
        "recipient_timezone": contact.recipient_timezone,
        "consent_version": contact.consent_version,
        "jurisdiction": lead_jurisdiction(policy, lead),
    })


async def campaign_precheck(
    session: AsyncSession,
    policy: DialerPolicy,
    *,
    now: datetime | None = None,
    refresh: bool = False,
) -> PrecheckResult:
    moment = now or utcnow()
    preparation: dict[str, int] = {}
    if refresh:
        preparation = await prepare_contacts(session, policy, now=moment)
    reasons: list[PrecheckReason] = []
    leads = (
        (
            await session.execute(
                select(Lead).where(
                    Lead.tenant_id == policy.tenant_id,
                    Lead.environment_id == policy.environment_id,
                    Lead.campaign_id == policy.campaign_id,
                )
            )
        )
        .scalars()
        .all()
    )
    for lead in leads:
        reason = jurisdiction_block_reason(policy, lead)
        if reason:
            reasons.append(PrecheckReason(reason, "Recipient jurisdiction evidence is incomplete", str(lead.id)))
    try:
        await validate_start(session, policy, now=moment, require_agents=False)
    except ContactCenterV2Error as exc:
        reasons.append(PrecheckReason(exc.code, exc.message))
    return PrecheckResult(
        not reasons,
        tuple(reasons),
        {
            "policy_id": str(policy.id),
            "campaign_id": str(policy.campaign_id),
            "dnc_scrubbed_at": policy.dnc_scrubbed_at.isoformat() if policy.dnc_scrubbed_at else None,
            "dnc_scrub_version": policy.dnc_scrub_version,
            "preparation": preparation,
            "checked_leads": len(leads),
        },
    )


__all__ = [
    "JURISDICTION_RULES",
    "JurisdictionRule",
    "PrecheckReason",
    "PrecheckResult",
    "campaign_precheck",
    "jurisdiction_block_reason",
    "lead_jurisdiction",
    "precheck_contact",
]
