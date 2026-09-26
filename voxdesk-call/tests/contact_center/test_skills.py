"""Skills are names and proficiency. No VIP."""

from __future__ import annotations

import pytest

from app.contact_center.exceptions import InvalidSkill
from app.contact_center.queues import add_member, create_queue
from app.contact_center.service import enqueue
from app.contact_center.skills import assign_skill, create_skill
from app.tenancy.isolation import NotFound
from tests.acd_support import go_available, live_call, production
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_proficiency_bounds_and_required_skill_routes(db, tenant_a, agent_a):
    skill = await create_skill(db, tenant_id=tenant_a.id, name="Billing")
    with pytest.raises(InvalidSkill):
        await assign_skill(
            db, tenant_id=tenant_a.id, user_id=agent_a.id, skill_id=skill.id, proficiency=9
        )
    held = await assign_skill(
        db, tenant_id=tenant_a.id, user_id=agent_a.id, skill_id=skill.id, proficiency=3
    )
    assert held.proficiency == 3
    env = await production(db, tenant_a)
    queue = await create_queue(
        db,
        tenant_id=tenant_a.id,
        name="Billing queue",
        environment_id=env.id,
        required_skills=["billing"],
        strategy="skill_first",
    )
    await add_member(db, tenant_id=tenant_a.id, queue_id=queue.id, user_id=agent_a.id)
    await go_available(db, tenant_a, agent_a)
    call = await live_call(db, tenant_a, env)
    result = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=call.id)
    await db.commit()
    assert result.outcome == "assigned"
    assert result.decision.matched_skills == ["billing"]


@pytest.mark.asyncio
async def test_disabled_skill_blocks_assignment(db, tenant_a, agent_a):
    skill = await create_skill(db, tenant_id=tenant_a.id, name="Billing")
    await assign_skill(db, tenant_id=tenant_a.id, user_id=agent_a.id, skill_id=skill.id)
    skill.enabled = False
    await db.commit()
    with pytest.raises(InvalidSkill):
        await assign_skill(db, tenant_id=tenant_a.id, user_id=agent_a.id, skill_id=skill.id)
    env = await production(db, tenant_a)
    queue = await create_queue(
        db,
        tenant_id=tenant_a.id,
        name="Billing queue",
        required_skills=["billing"],
        environment_id=env.id,
    )
    await add_member(db, tenant_id=tenant_a.id, queue_id=queue.id, user_id=agent_a.id)
    await go_available(db, tenant_a, agent_a)
    call = await live_call(db, tenant_a, env)
    result = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=call.id)
    await db.commit()
    assert result.outcome == "waiting"
    assert result.decision.rejected[0]["reason"] == "missing_skill"


@pytest.mark.asyncio
async def test_skill_is_tenant_scoped(db, tenant_a, tenant_b):
    skill = await create_skill(db, tenant_id=tenant_a.id, name="Billing")
    await db.commit()
    from app.contact_center.repository import get_skill

    with pytest.raises(NotFound):
        await get_skill(db, tenant_b.id, skill.id)


@pytest.mark.asyncio
async def test_viewer_cannot_create_a_skill(client, viewer_a):
    headers = await auth_headers(client, viewer_a)
    response = await client.post("/api/skills", json={"name": "billing"}, headers=headers)
    assert response.status_code == 403
