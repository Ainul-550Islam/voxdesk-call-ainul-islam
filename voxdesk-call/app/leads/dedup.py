"""Deterministic duplicate detection. Fuzzy name similarity never merges."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead, LeadStatus
from app.leads.exceptions import MergeRejected
from app.leads.models import LeadIdentity, LeadMerge
from app.leads.repository import find_by_email, find_by_phone, get_identity, get_lead
from app.telephony.phone import InvalidPhoneNumber, normalize

_EMAIL_MAX = 254


def normalize_phone(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    try:
        cleaned = normalize(value)
    except InvalidPhoneNumber:
        return None
    if cleaned.startswith(("client:", "sip:")):
        return None
    return cleaned


def normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if "@" not in text or "." not in text.split("@", 1)[1]:
        return None
    if len(text) > _EMAIL_MAX:
        return None
    return text


def name_similarity(left: str | None, right: str | None) -> float:
    """Token overlap in ``[0, 1]``. Informational. Never an authorization check."""
    a = {part for part in (left or "").lower().split() if part}
    b = {part for part in (right or "").lower().split() if part}
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 4)


async def exact_matches(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    *,
    phone: str | None,
    email: str | None,
    exclude_lead_id: uuid.UUID | None = None,
) -> list[LeadIdentity]:
    found: list[LeadIdentity] = []
    seen: set[uuid.UUID] = set()
    phone_key = normalize_phone(phone)
    email_key = normalize_email(email)
    if phone_key:
        row = await find_by_phone(session, tenant_id, environment_id, phone_key)
        if row is not None and row.lead_id != exclude_lead_id:
            found.append(row)
            seen.add(row.lead_id)
    if email_key:
        row = await find_by_email(session, tenant_id, environment_id, email_key)
        if row is not None and row.lead_id not in seen and row.lead_id != exclude_lead_id:
            found.append(row)
    return found


async def informational_name_matches(
    session: AsyncSession,
    lead: Lead,
    *,
    threshold: float = 0.8,
    limit: int = 20,
) -> list[dict]:
    """Similar names in the same tenant and environment. Not merge candidates."""
    rows = (
        await session.execute(
            select(Lead)
            .where(
                Lead.tenant_id == lead.tenant_id,
                Lead.environment_id == lead.environment_id,
                Lead.id != lead.id,
            )
            .limit(200)
        )
    ).scalars().all()
    matches = []
    for other in rows:
        score = name_similarity(lead.name, other.name)
        if score < threshold:
            continue
        matches.append({
            "lead_id": str(other.id),
            "name": other.name,
            "similarity": score,
            "mergeable": False,
            "reason": "name_similarity_is_informational",
        })
    matches.sort(key=lambda item: item["similarity"], reverse=True)
    return matches[:limit]


async def merge(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    survivor_id: uuid.UUID,
    duplicate_id: uuid.UUID,
    reason: str,
    actor_id: uuid.UUID | None = None,
) -> LeadMerge:
    """Explicit merge. Refuses fuzzy, cross-tenant, and do-not-call laundering."""
    if survivor_id == duplicate_id:
        raise MergeRejected("A lead cannot be merged into itself")
    survivor = await get_lead(session, tenant_id, survivor_id, environment_id)
    duplicate = await get_lead(session, tenant_id, duplicate_id, environment_id)
    if survivor.environment_id != duplicate.environment_id:
        raise MergeRejected("Leads in different environments cannot be merged")
    survivor_identity = await get_identity(session, tenant_id, survivor.id)
    duplicate_identity = await get_identity(session, tenant_id, duplicate.id)
    if duplicate_identity is not None and duplicate_identity.merged_into_id is not None:
        raise MergeRejected("That lead was already merged")
    if survivor_identity is not None and survivor_identity.merged_into_id is not None:
        raise MergeRejected("The survivor was already merged into another lead")
    # A callable survivor must not absorb a do-not-call number and keep dialing.
    if duplicate.status is LeadStatus.DNC and survivor.status is not LeadStatus.DNC:
        from app.leads.lifecycle import transition

        await transition(
            session,
            survivor,
            LeadStatus.DNC.value,
            reason="merge_propagated_do_not_call",
            source="merge",
            actor_id=actor_id,
        )
    if duplicate_identity is None:
        duplicate_identity = LeadIdentity(
            lead_id=duplicate.id,
            tenant_id=tenant_id,
            environment_id=environment_id,
            source="merge",
        )
        session.add(duplicate_identity)
        await session.flush()
    duplicate_identity.merged_into_id = survivor.id
    row = LeadMerge(
        tenant_id=tenant_id,
        environment_id=environment_id,
        survivor_lead_id=survivor.id,
        duplicate_lead_id=duplicate.id,
        reason=reason[:200] or "explicit_merge",
        actor_id=actor_id,
    )
    session.add(row)
    from app.leads.lifecycle import append_history

    await append_history(
        session,
        duplicate,
        from_status=duplicate.status.value,
        to_status=duplicate.status.value,
        reason=f"merged_into:{survivor.id}",
        source="merge",
        actor_id=actor_id,
    )
    await session.flush()
    return row
