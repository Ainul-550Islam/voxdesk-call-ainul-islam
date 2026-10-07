"""Automation dead-letter quarantine and replay.

Replay keeps organization, tenant and environment. It does not accept a new
scope. A replay budget stops an infinite loop.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import DurableJob, UserRole
from app.jobs.repository import get_job, replay_dead_letter
from app.resources.exceptions import ResourceUnauthorized
from app.tenancy.isolation import BoundaryDenied


def operator_may_replay(role: UserRole | None) -> bool:
    if role is None:
        return False
    return has_permission(role, Permission.CAMPAIGN_RUN)


async def quarantine(session: AsyncSession, job: DurableJob, *, category: str) -> DurableJob:
    from app.jobs.repository import move_to_dlq

    return await move_to_dlq(session, job, category=category)


async def replay(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    role: UserRole | None,
) -> DurableJob:
    if not operator_may_replay(role):
        raise ResourceUnauthorized()
    job = await get_job(session, job_id, tenant_id=tenant_id)
    if job is None or job.tenant_id != tenant_id:
        raise BoundaryDenied()
    if job.job_type != "automation":
        raise BoundaryDenied()
    return await replay_dead_letter(session, job)
