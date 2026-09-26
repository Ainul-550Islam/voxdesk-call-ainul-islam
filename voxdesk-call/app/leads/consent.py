"""Consent on the existing lead. Voice denial uses LeadStatus.DNC; it does not add a second list."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead, LeadStatus
from app.leads.exceptions import InvalidTransition
from app.leads.lifecycle import transition
from app.leads.models import LeadConsent
from app.leads.repository import latest_consent

CHANNELS = frozenset({"voice", "sms", "email"})
DECISIONS = frozenset({"granted", "denied", "unknown"})


async def record_consent(
    session: AsyncSession,
    lead: Lead,
    *,
    channel: str,
    decision: str,
    source: str,
    actor_id: uuid.UUID | None = None,
) -> LeadConsent:
    channel_name = channel.strip().lower()
    decision_name = decision.strip().lower()
    if channel_name not in CHANNELS:
        raise InvalidTransition("Consent channel must be voice, sms, or email")
    if decision_name not in DECISIONS:
        raise InvalidTransition("Consent decision must be granted, denied, or unknown")
    if channel_name == "voice" and decision_name == "granted" and lead.status is LeadStatus.DNC:
        raise InvalidTransition("Do-not-call cannot be cleared by granting voice consent")
    previous = await latest_consent(session, lead.tenant_id, lead.id, channel_name)
    version = 1 if previous is None else previous.version + 1
    row = LeadConsent(
        lead_id=lead.id,
        tenant_id=lead.tenant_id,
        environment_id=lead.environment_id,
        channel=channel_name,
        decision=decision_name,
        source=(source or "")[:64],
        version=version,
    )
    session.add(row)
    await session.flush()
    if channel_name == "voice" and decision_name == "denied" and lead.status is not LeadStatus.DNC:
        await transition(
            session,
            lead,
            LeadStatus.DNC.value,
            reason="consent:voice:denied",
            source="consent",
            actor_id=actor_id,
        )
    return row


async def voice_denied(session: AsyncSession, tenant_id: uuid.UUID, lead_id: uuid.UUID) -> bool:
    current = await latest_consent(session, tenant_id, lead_id, "voice")
    return current is not None and current.decision == "denied"
