"""Skill catalog, proficiency requirements and canonical-router synchronization."""
from __future__ import annotations

import uuid
from typing import Iterable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.models import AgentSkill, Queue, QueueMember, Skill
from app.contact_center_v2.errors import NotFound, Validation
from app.contact_center_v2.repository import owned
from app.db.contact_center_v2_models import QueueSkillRequirement
from app.db.models import User


async def upsert_skill(
    session: AsyncSession, *, tenant_id: uuid.UUID, name: str, enabled: bool = True
) -> Skill:
    clean = name.strip()
    if not clean or len(clean) > 64:
        raise Validation("Skill name must contain 1–64 characters")
    row = (
        await session.execute(
            select(Skill).where(Skill.tenant_id == tenant_id, Skill.name == clean)
        )
    ).scalar_one_or_none()
    if row is None:
        row = Skill(tenant_id=tenant_id, name=clean, enabled=enabled)
        session.add(row)
    else:
        row.enabled = enabled
    await session.flush()
    return row


async def set_agent_skill(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    skill_id: uuid.UUID,
    proficiency: int,
    weight: int = 0,
    enabled: bool = True,
) -> AgentSkill:
    if not 1 <= int(proficiency) <= 5:
        raise Validation("Skill proficiency must be between 1 and 5")
    user = await session.get(User, user_id)
    skill = await session.get(Skill, skill_id)
    if user is None or user.tenant_id != tenant_id or skill is None or skill.tenant_id != tenant_id:
        raise NotFound("agent skill scope")
    row = (
        await session.execute(
            select(AgentSkill).where(AgentSkill.skill_id == skill_id, AgentSkill.user_id == user_id)
        )
    ).scalar_one_or_none()
    if row is None:
        row = AgentSkill(tenant_id=tenant_id, skill_id=skill_id, user_id=user_id)
        session.add(row)
    row.proficiency = int(proficiency)
    row.weight = int(weight)
    row.enabled = bool(enabled)
    await session.flush()
    return row


async def set_queue_requirements(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    queue_id: uuid.UUID,
    requirements: Iterable[dict],
) -> list[QueueSkillRequirement]:
    queue = await owned(session, Queue, queue_id, tenant_id, for_update=True)
    values = list(requirements)
    if len(values) > 50:
        raise Validation("A queue cannot have more than 50 skill requirements")
    skill_ids: set[uuid.UUID] = set()
    normalized: list[tuple[Skill, dict]] = []
    for value in values:
        try:
            skill_id = value["skill_id"] if isinstance(value["skill_id"], uuid.UUID) else uuid.UUID(str(value["skill_id"]))
        except (KeyError, ValueError, TypeError) as exc:
            raise Validation("Each queue requirement needs a valid skill_id") from exc
        if skill_id in skill_ids:
            raise Validation("Duplicate queue skill requirement")
        skill_ids.add(skill_id)
        skill = await session.get(Skill, skill_id)
        if skill is None or skill.tenant_id != tenant_id or not skill.enabled:
            raise NotFound("enabled queue skill")
        proficiency = int(value.get("min_proficiency", 1))
        if not 1 <= proficiency <= 5:
            raise Validation("Minimum skill proficiency must be between 1 and 5")
        normalized.append((skill, value))
    await session.execute(
        delete(QueueSkillRequirement).where(
            QueueSkillRequirement.tenant_id == tenant_id,
            QueueSkillRequirement.queue_id == queue_id,
        )
    )
    rows: list[QueueSkillRequirement] = []
    required_names: list[str] = []
    for skill, value in normalized:
        required = bool(value.get("required", True))
        row = QueueSkillRequirement(
            tenant_id=tenant_id,
            environment_id=queue.environment_id,
            queue_id=queue.id,
            skill_id=skill.id,
            min_proficiency=int(value.get("min_proficiency", 1)),
            required=required,
            weight=int(value.get("weight", 0)),
        )
        session.add(row)
        rows.append(row)
        if required:
            required_names.append(skill.name)
    # The existing router remains authoritative for assignment. Its canonical
    # name-based skill gate is synchronized with the durable v2 requirements.
    queue.required_skills = required_names
    overflow_policy = dict(queue.overflow_policy or {})
    overflow_policy["cc2_min_proficiency"] = {
        str(row.skill_id): row.min_proficiency for row in rows if row.required
    }
    queue.overflow_policy = overflow_policy
    await session.flush()
    return rows


async def requirements_for_queue(
    session: AsyncSession, *, tenant_id: uuid.UUID, queue_id: uuid.UUID
) -> list[QueueSkillRequirement]:
    return list(
        (
            await session.execute(
                select(QueueSkillRequirement).where(
                    QueueSkillRequirement.tenant_id == tenant_id,
                    QueueSkillRequirement.queue_id == queue_id,
                )
            )
        )
        .scalars()
        .all()
    )


async def agent_skill_levels(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    rows = (
        (
            await session.execute(
                select(AgentSkill).where(
                    AgentSkill.tenant_id == tenant_id,
                    AgentSkill.user_id == user_id,
                    AgentSkill.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.skill_id: int(row.proficiency) for row in rows}


async def eligible_agents(
    session: AsyncSession, *, tenant_id: uuid.UUID, queue_id: uuid.UUID
) -> list[uuid.UUID]:
    requirements = await requirements_for_queue(session, tenant_id=tenant_id, queue_id=queue_id)
    members = (
        (
            await session.execute(
                select(QueueMember).where(
                    QueueMember.tenant_id == tenant_id,
                    QueueMember.queue_id == queue_id,
                    QueueMember.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    eligible: list[uuid.UUID] = []
    for member in members:
        levels = await agent_skill_levels(session, tenant_id=tenant_id, user_id=member.user_id)
        if all(
            not requirement.required or levels.get(requirement.skill_id, 0) >= requirement.min_proficiency
            for requirement in requirements
        ):
            eligible.append(member.user_id)
    return eligible


async def synchronize_queue_skills(
    session: AsyncSession, *, tenant_id: uuid.UUID, queue_id: uuid.UUID
) -> list[QueueSkillRequirement]:
    requirements = await requirements_for_queue(session, tenant_id=tenant_id, queue_id=queue_id)
    if requirements:
        queue = await owned(session, Queue, queue_id, tenant_id)
        skills = (
            (
                await session.execute(
                    select(Skill).where(Skill.tenant_id == tenant_id, Skill.id.in_([row.skill_id for row in requirements]))
                )
            )
            .scalars()
            .all()
        )
        by_id = {row.id: row for row in skills}
        queue.required_skills = [by_id[row.skill_id].name for row in requirements if row.required and row.skill_id in by_id]
        overflow_policy = dict(queue.overflow_policy or {})
        overflow_policy["cc2_min_proficiency"] = {
            str(row.skill_id): row.min_proficiency for row in requirements if row.required
        }
        queue.overflow_policy = overflow_policy
        await session.flush()
    return requirements


__all__ = [
    "agent_skill_levels",
    "eligible_agents",
    "requirements_for_queue",
    "set_agent_skill",
    "set_queue_requirements",
    "synchronize_queue_skills",
    "upsert_skill",
]
