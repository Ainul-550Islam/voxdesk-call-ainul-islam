"""Eligibility rules.

A missing tenant-membership row is the legacy allow used everywhere else.
A suspended, revoked or expired row is a deny. A disabled user, a disabled
queue member, a stale heartbeat or a missing required skill is ineligible.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import EnvironmentMembership, TenantMembership, User, UserRole
from app.contact_center.models import AgentPresence, AgentSkill, QueueMember, Skill
from app.contact_center.presence import is_fresh


def may_supervise(role: UserRole | None) -> bool:
    return role is not None and has_permission(role, Permission.SUPERVISOR_WRITE)


def may_write_queue(role: UserRole | None) -> bool:
    return role is not None and has_permission(role, Permission.QUEUE_WRITE)


async def user_eligible(session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID) -> str:
    user = await session.get(User, user_id)
    if user is None or user.tenant_id != tenant_id:
        return "foreign_user"
    if not user.is_active:
        return "disabled_user"
    membership = (
        await session.execute(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if membership is not None and membership.status != "active":
        return f"membership_{membership.status}"
    return ""


async def environment_eligible(
    session: AsyncSession, environment_id: uuid.UUID, user_id: uuid.UUID
) -> str:
    row = (
        await session.execute(
            select(EnvironmentMembership).where(
                EnvironmentMembership.environment_id == environment_id,
                EnvironmentMembership.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return ""
    if row.status != "active":
        return f"environment_{row.status}"
    return ""


def presence_reason(row: AgentPresence | None, *, now: datetime | None = None) -> str:
    if row is None:
        return "no_presence"
    if row.state in {"offline", "away", "paused", "disabled", "wrap_up"}:
        return row.state
    if row.state not in {"available", "ringing", "busy"}:
        return row.state
    if row.state == "available" and not is_fresh(row, now=now):
        return "stale"
    if row.active_count >= row.capacity:
        return "at_capacity"
    return ""


def skill_reason(
    required: list[str],
    held: dict[uuid.UUID, AgentSkill],
    skills_by_name: dict[str, Skill],
) -> str:
    for name in required:
        skill = skills_by_name.get(name)
        if skill is None or not skill.enabled:
            return "missing_skill"
        match = held.get(skill.id)
        if match is None or not match.enabled:
            return "missing_skill"
    return ""


def skill_score(
    required: list[str],
    held: dict[uuid.UUID, AgentSkill],
    skills_by_name: dict[str, Skill],
) -> tuple[int, int]:
    """Max proficiency and how many required skills are held. Not a VIP score."""
    if not required:
        levels = [row.proficiency + row.weight for row in held.values() if row.enabled]
        return (max(levels) if levels else 0, len(levels))
    levels: list[int] = []
    for name in required:
        skill = skills_by_name.get(name)
        if skill is None:
            continue
        row = held.get(skill.id)
        if row is not None and row.enabled:
            levels.append(row.proficiency + row.weight)
    return (max(levels) if levels else 0, len(levels))


def member_reason(member: QueueMember | None) -> str:
    if member is None or not member.enabled:
        return "not_a_member"
    return ""
