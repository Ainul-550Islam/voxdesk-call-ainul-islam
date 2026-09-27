"""Tenant- and environment-scoped lead queries. No query here omits the tenant."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Campaign, Environment, Lead, User
from app.leads.models import (
    LeadActivity,
    LeadConsent,
    LeadIdentity,
    LeadScoreSnapshot,
    LeadSegment,
    LeadStatusHistory,
    LeadTask,
)
from app.tenancy.isolation import BoundaryDenied, NotFound


async def production_environment_id(session: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID:
    found = (
        await session.execute(
            select(Environment.id).where(
                Environment.tenant_id == tenant_id,
                Environment.kind == "production",
                Environment.status == "active",
            )
        )
    ).scalar_one_or_none()
    if found is None:
        raise NotFound()
    return found


async def require_environment(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None,
) -> uuid.UUID:
    if environment_id is None:
        return await production_environment_id(session, tenant_id)
    row = await session.get(Environment, environment_id)
    if row is None or row.tenant_id != tenant_id:
        raise BoundaryDenied()
    return row.id


async def get_lead(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
) -> Lead:
    lead = await session.get(Lead, lead_id)
    if lead is None or lead.tenant_id != tenant_id:
        raise NotFound()
    if environment_id is not None and lead.environment_id != environment_id:
        raise NotFound()
    return lead


async def get_lead_for_update(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
) -> Lead:
    """Scoped row-lock load for callers that serialize on one lead.

    ``SELECT ... FOR UPDATE`` on PostgreSQL; the SQLite dialect omits the
    clause, where the compare-and-set inside ``lifecycle.transition`` remains
    the race arbitration. Scope filters live in the query itself, so a lock
    can never be taken on another tenant's or environment's row.
    """
    stmt = select(Lead).where(
        Lead.id == lead_id,
        Lead.tenant_id == tenant_id,
    )
    if environment_id is not None:
        stmt = stmt.where(Lead.environment_id == environment_id)
    lead = (await session.execute(stmt.with_for_update())).scalars().first()
    if lead is None:
        raise NotFound()
    return lead


async def find_lead_by_phone(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    phone: str,
) -> Lead | None:
    """Raw-phone lead lookup for headless paths (messaging, agent tools).

    Scoped to tenant + environment: a phone number in another environment is
    a different lead and must never be returned (or mutated) here.
    """
    return (
        await session.execute(
            select(Lead)
            .where(
                Lead.tenant_id == tenant_id,
                Lead.environment_id == environment_id,
                Lead.phone == phone,
            )
            .order_by(Lead.created_at)
            .limit(1)
        )
    ).scalars().first()


async def campaign_in_scope(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None,
    campaign_id: uuid.UUID,
) -> Campaign | None:
    """A campaign owned by this tenant and (when checked) this environment.

    Returns ``None`` for missing, foreign-tenant and cross-environment rows
    alike, so callers cannot leak which boundary failed.
    """
    row = await session.get(Campaign, campaign_id)
    if row is None or row.tenant_id != tenant_id:
        return None
    if environment_id is not None and row.environment_id != environment_id:
        return None
    return row


async def get_identity(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
) -> LeadIdentity | None:
    stmt = select(LeadIdentity).where(
        LeadIdentity.tenant_id == tenant_id,
        LeadIdentity.lead_id == lead_id,
    )
    if environment_id is not None:
        stmt = stmt.where(LeadIdentity.environment_id == environment_id)
    return (await session.execute(stmt)).scalar_one_or_none()


async def find_by_phone(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    phone_normalized: str,
) -> LeadIdentity | None:
    return (
        await session.execute(
            select(LeadIdentity).where(
                LeadIdentity.tenant_id == tenant_id,
                LeadIdentity.environment_id == environment_id,
                LeadIdentity.phone_normalized == phone_normalized,
                LeadIdentity.merged_into_id.is_(None),
            )
        )
    ).scalar_one_or_none()


async def find_by_email(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    email_normalized: str,
) -> LeadIdentity | None:
    return (
        await session.execute(
            select(LeadIdentity).where(
                LeadIdentity.tenant_id == tenant_id,
                LeadIdentity.environment_id == environment_id,
                LeadIdentity.email_normalized == email_normalized,
                LeadIdentity.merged_into_id.is_(None),
            )
        )
    ).scalar_one_or_none()


async def next_history_sequence(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    environment_id: uuid.UUID | None = None,
) -> int:
    stmt = select(func.max(LeadStatusHistory.sequence)).where(
        LeadStatusHistory.tenant_id == tenant_id,
        LeadStatusHistory.lead_id == lead_id,
    )
    if environment_id is not None:
        stmt = stmt.where(LeadStatusHistory.environment_id == environment_id)
    current = (await session.execute(stmt)).scalar_one_or_none()
    return int(current or 0) + 1


async def history_for(
    session: AsyncSession, tenant_id: uuid.UUID, environment_id: uuid.UUID, lead_id: uuid.UUID
) -> list[LeadStatusHistory]:
    rows = (
        await session.execute(
            select(LeadStatusHistory)
            .where(
                LeadStatusHistory.tenant_id == tenant_id,
                LeadStatusHistory.environment_id == environment_id,
                LeadStatusHistory.lead_id == lead_id,
            )
            .order_by(LeadStatusHistory.sequence)
        )
    ).scalars().all()
    return list(rows)


async def user_in_tenant(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> User:
    user = await session.get(User, user_id)
    if user is None or user.tenant_id != tenant_id:
        raise NotFound()
    return user


async def get_segment(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    segment_id: uuid.UUID,
) -> LeadSegment:
    row = await session.get(LeadSegment, segment_id)
    if row is None or row.tenant_id != tenant_id or row.environment_id != environment_id:
        raise NotFound()
    return row


async def latest_consent(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    lead_id: uuid.UUID,
    channel: str,
    environment_id: uuid.UUID | None = None,
) -> LeadConsent | None:
    stmt = select(LeadConsent).where(
        LeadConsent.tenant_id == tenant_id,
        LeadConsent.lead_id == lead_id,
        LeadConsent.channel == channel,
    )
    if environment_id is not None:
        stmt = stmt.where(LeadConsent.environment_id == environment_id)
    return (
        await session.execute(stmt.order_by(LeadConsent.version.desc()).limit(1))
    ).scalar_one_or_none()


async def get_task(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    task_id: uuid.UUID,
) -> LeadTask:
    row = await session.get(LeadTask, task_id)
    if row is None or row.tenant_id != tenant_id or row.environment_id != environment_id:
        raise NotFound()
    return row


def touch(identity: LeadIdentity, moment: datetime | None = None) -> None:
    identity.updated_at = moment or datetime.utcnow()


async def latest_score(
    session: AsyncSession, tenant_id: uuid.UUID, lead_id: uuid.UUID
) -> LeadScoreSnapshot | None:
    return (
        await session.execute(
            select(LeadScoreSnapshot)
            .where(
                LeadScoreSnapshot.tenant_id == tenant_id,
                LeadScoreSnapshot.lead_id == lead_id,
            )
            .order_by(LeadScoreSnapshot.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def activities_for(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    lead_id: uuid.UUID,
    *,
    limit: int,
    offset: int,
) -> list[LeadActivity]:
    rows = (
        await session.execute(
            select(LeadActivity)
            .where(
                LeadActivity.tenant_id == tenant_id,
                LeadActivity.environment_id == environment_id,
                LeadActivity.lead_id == lead_id,
            )
            .order_by(LeadActivity.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return list(rows)
