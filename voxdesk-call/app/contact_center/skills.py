"""Tenant skills and per-agent proficiency.

A skill is a name plus an integer proficiency from 1 to 5. There is no VIP
flag and no inferred financial value.
"""

from __future__ import annotations

import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.exceptions import InvalidSkill
from app.contact_center.models import AgentSkill, Skill
from app.contact_center.policies import user_eligible
from app.contact_center.repository import agent_skills, get_skill, skill_by_name


async def create_skill(session: AsyncSession, *, tenant_id: uuid.UUID, name: str) -> Skill:
    label = name.strip().lower()
    if not label:
        raise InvalidSkill("Skill name is required")
    row = Skill(tenant_id=tenant_id, name=label[:64])
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        raise InvalidSkill("Skill already exists") from exc
    return row


async def set_enabled(session: AsyncSession, skill: Skill, enabled: bool) -> Skill:
    skill.enabled = bool(enabled)
    await session.flush()
    return skill


async def assign_skill(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    skill_id: uuid.UUID,
    proficiency: int = 1,
    weight: int = 0,
) -> AgentSkill:
    reason = await user_eligible(session, tenant_id, user_id)
    if reason:
        raise InvalidSkill(reason)
    skill = await get_skill(session, tenant_id, skill_id)
    if not skill.enabled:
        raise InvalidSkill("Skill is disabled")
    level = int(proficiency)
    if level < 1 or level > 5:
        raise InvalidSkill("Proficiency must be 1 to 5")
    held = await agent_skills(session, tenant_id, user_id)
    existing = held.get(skill.id)
    if existing is not None:
        existing.proficiency = level
        existing.weight = int(weight)
        existing.enabled = True
        await session.flush()
        return existing
    row = AgentSkill(
        tenant_id=tenant_id,
        user_id=user_id,
        skill_id=skill.id,
        proficiency=level,
        weight=int(weight),
    )
    session.add(row)
    await session.flush()
    return row


async def require_named(session: AsyncSession, tenant_id: uuid.UUID, names: list[str]) -> list[Skill]:
    found: list[Skill] = []
    for name in names:
        row = await skill_by_name(session, tenant_id, name.strip().lower())
        if row is None or not row.enabled:
            raise InvalidSkill(f"Unknown skill {name}")
        found.append(row)
    return found
