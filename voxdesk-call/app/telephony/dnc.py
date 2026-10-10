"""Centralized pre-dial Do-Not-Call (DNC) and voice-consent enforcement (Part 1B / Gate G1).

Single source of truth checked by:
- ``app/telephony/outbound.py:dial_lead`` and ``run_campaign_step``
- ``app/api/outbound_call_routes.py:create_outbound_call``
- ``app/api/batch_call_routes.py`` (at recipient ingestion and immediately before dial)
- ``app/api/call_search_export_routes.py:check_dnc``
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enterprise_models import CallPolicy, DncEntry
from app.db.models import Lead, LeadStatus
from app.leads.consent import voice_denied
from app.telephony import phone as phone_util


def normalize_phone(raw: str | None) -> str:
    """Normalize a phone number using ``app.telephony.phone.normalize``.

    Accepts standard E.164 inputs (including punctuation like ``+1 (555) 234-5678``)
    as well as 10/11-digit NANP strings when no country prefix was supplied.
    """
    if not raw:
        raise phone_util.InvalidPhoneNumber("no destination configured")
    cleaned = raw.strip()
    try:
        return phone_util.normalize(cleaned)
    except phone_util.InvalidPhoneNumber:
        digits = re.sub(r"[\s\-().\u00a0\u2010-\u2015]", "", cleaned)
        if len(digits) == 10 and digits.isdigit():
            return phone_util.normalize(f"+1{digits}")
        if len(digits) == 11 and digits.startswith("1") and digits.isdigit():
            return phone_util.normalize(f"+{digits}")
        raise


@event.listens_for(DncEntry, "before_insert")
@event.listens_for(DncEntry, "before_update")
def _normalize_dnc_entry_phone(_mapper: Any, _connection: Any, target: DncEntry) -> None:
    if target.phone:
        try:
            target.phone = normalize_phone(target.phone)
        except phone_util.InvalidPhoneNumber:
            target.phone = target.phone.strip()


@dataclass(frozen=True)
class DncVerdict:
    blocked: bool
    reason: str | None
    normalized_phone: str
    dnc_entry: dict[str, Any] | None = None
    lead_dnc: bool = False
    consent_denied: bool = False
    policy_blocked: bool = False


async def evaluate_dnc(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    phone: str,
    *,
    lead_id: uuid.UUID | None = None,
    environment_id: uuid.UUID | None = None,
    agent_id: str | None = None,
) -> DncVerdict:
    """Evaluate all DNC and voice-consent sources for ``(tenant_id, phone, lead_id)``."""
    try:
        norm = normalize_phone(phone)
    except phone_util.InvalidPhoneNumber:
        norm = (phone or "").strip()

    # 1. Explicit DncEntry table (exact normalized match + fallback scan for legacy formatting)
    entry = (
        await db.execute(
            select(DncEntry)
            .where(DncEntry.tenant_id == tenant_id, DncEntry.phone == norm)
            .limit(1)
        )
    ).scalar_one_or_none()
    if entry is None and norm:
        all_entries = (
            await db.execute(select(DncEntry).where(DncEntry.tenant_id == tenant_id))
        ).scalars().all()
        for candidate in all_entries:
            try:
                if normalize_phone(candidate.phone) == norm:
                    entry = candidate
                    break
            except phone_util.InvalidPhoneNumber:
                continue

    # 2. Lead status == LeadStatus.DNC and voice_denied consent check
    lead_dnc = False
    consent_is_denied = False
    matched_leads: list[Lead] = []

    if lead_id is not None:
        lead_row = await db.get(Lead, lead_id)
        if lead_row is not None and lead_row.tenant_id == tenant_id:
            if environment_id is None or lead_row.environment_id == environment_id:
                matched_leads.append(lead_row)

    if norm:
        lead_stmt = select(Lead).where(Lead.tenant_id == tenant_id)
        if environment_id is not None:
            lead_stmt = lead_stmt.where(Lead.environment_id == environment_id)
        tenant_leads = (await db.execute(lead_stmt)).scalars().all()
        seen_ids = {lead_row.id for lead_row in matched_leads}
        for cand in tenant_leads:
            if cand.id in seen_ids:
                continue
            try:
                cand_norm = normalize_phone(cand.phone)
            except phone_util.InvalidPhoneNumber:
                cand_norm = (cand.phone or "").strip()
            if cand_norm == norm:
                matched_leads.append(cand)
                seen_ids.add(cand.id)

    for lead in matched_leads:
        if lead.status == LeadStatus.DNC:
            lead_dnc = True
        if await voice_denied(db, tenant_id, lead.id, environment_id=lead.environment_id):
            consent_is_denied = True

    # 3. CallPolicy(policy_type="dnc")
    policy_blocked = False
    dnc_policies = (
        await db.execute(
            select(CallPolicy).where(
                CallPolicy.tenant_id == tenant_id,
                CallPolicy.policy_type == "dnc",
                CallPolicy.is_enabled.is_(True),
            )
        )
    ).scalars().all()
    for pol in dnc_policies:
        if agent_id and pol.agent_id and pol.agent_id != agent_id:
            continue
        cfg = pol.config if isinstance(pol.config, dict) else {}
        blocked_numbers = cfg.get("blocked_numbers") or cfg.get("phones") or []
        for bnum in blocked_numbers:
            try:
                if normalize_phone(str(bnum)) == norm:
                    policy_blocked = True
                    break
            except phone_util.InvalidPhoneNumber:
                if str(bnum).strip() == norm:
                    policy_blocked = True
                    break
        blocked_prefixes = cfg.get("blocked_prefixes") or []
        for prefix in blocked_prefixes:
            if prefix and norm.startswith(str(prefix).strip()):
                policy_blocked = True
                break

    reason: str | None = None
    if entry is not None:
        reason = f"dnc_entry:{entry.reason or 'listed'}"
    elif lead_dnc:
        reason = "lead_dnc"
    elif consent_is_denied:
        reason = "consent_denied"
    elif policy_blocked:
        reason = "dnc_policy"

    return DncVerdict(
        blocked=reason is not None,
        reason=reason,
        normalized_phone=norm,
        dnc_entry=entry.as_dict() if entry is not None else None,
        lead_dnc=lead_dnc,
        consent_denied=consent_is_denied,
        policy_blocked=policy_blocked,
    )


async def is_blocked(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    phone: str,
    lead_id: uuid.UUID | None = None,
    *,
    environment_id: uuid.UUID | None = None,
    agent_id: str | None = None,
) -> tuple[bool, str | None]:
    """Return ``(blocked, reason)`` for pre-dial DNC and consent enforcement."""
    verdict = await evaluate_dnc(
        db,
        tenant_id,
        phone,
        lead_id=lead_id,
        environment_id=environment_id,
        agent_id=agent_id,
    )
    return verdict.blocked, verdict.reason
