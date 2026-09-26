"""Deterministic routing. One owner. Inbox records the assignee."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.contact_center.models import Candidate
from app.contact_center.queues import add_member, create_queue
from app.contact_center.routing import choose
from app.contact_center.service import enqueue
from app.db.models import InboxThreadState
from app.inbox.repository import get_thread
from tests.acd_support import go_available, live_call, production
from tests.conftest import make_user
from app.db.models import UserRole


def test_least_loaded_is_stable_and_not_random():
    first = Candidate(
        user_id=__import__("uuid").UUID("00000000-0000-0000-0000-000000000002"),
        member_priority=0,
        proficiency=0,
        skill_count=0,
        active_count=1,
        last_assigned_at=None,
        eligible=True,
    )
    second = Candidate(
        user_id=__import__("uuid").UUID("00000000-0000-0000-0000-000000000001"),
        member_priority=0,
        proficiency=0,
        skill_count=0,
        active_count=0,
        last_assigned_at=None,
        eligible=True,
    )
    choice = choose([first, second], strategy="least_loaded", required_skills=[])
    again = choose([second, first], strategy="least_loaded", required_skills=[])
    assert choice.selected.user_id == second.user_id
    assert again.selected.user_id == second.user_id
    assert choice.selected.user_id == again.selected.user_id


def test_rejected_agent_is_explained():
    stale = Candidate(
        user_id=__import__("uuid").uuid4(),
        member_priority=0,
        proficiency=0,
        skill_count=0,
        active_count=0,
        last_assigned_at=None,
        eligible=False,
        reason="stale",
    )
    choice = choose([stale], strategy="least_loaded", required_skills=["billing"])
    assert choice.selected is None
    assert choice.rejected == [{"user_id": str(stale.user_id), "reason": "stale"}]


@pytest.mark.asyncio
async def test_enqueue_assigns_one_inbox_owner(db, tenant_a, agent_a):
    env = await production(db, tenant_a)
    queue = await create_queue(db, tenant_id=tenant_a.id, name="Support", environment_id=env.id)
    await add_member(db, tenant_id=tenant_a.id, queue_id=queue.id, user_id=agent_a.id)
    await go_available(db, tenant_a, agent_a)
    call = await live_call(db, tenant_a, env)
    result = await enqueue(
        db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=call.id, actor_id=agent_a.id
    )
    await db.commit()
    assert result.outcome == "assigned"
    assert result.assignment is not None
    assert result.assignment.user_id == agent_a.id
    thread = await get_thread(
        db, tenant_id=tenant_a.id, environment_id=env.id, call_id=call.id
    )
    assert thread is not None
    assert thread.assignee_id == str(agent_a.id)
    assert thread.first_response_deadline
    owners = (
        await db.execute(select(InboxThreadState).where(InboxThreadState.call_id == call.id))
    ).scalars().all()
    assert len(owners) == 1


@pytest.mark.asyncio
async def test_second_enqueue_does_not_create_a_second_owner(db, tenant_a, agent_a):
    env = await production(db, tenant_a)
    queue = await create_queue(db, tenant_id=tenant_a.id, name="Support", environment_id=env.id)
    await add_member(db, tenant_id=tenant_a.id, queue_id=queue.id, user_id=agent_a.id)
    await go_available(db, tenant_a, agent_a)
    call = await live_call(db, tenant_a, env)
    first = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=call.id)
    second = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=call.id)
    await db.commit()
    assert first.outcome == "assigned"
    assert second.outcome == "duplicate"
    assert second.assignment is None


@pytest.mark.asyncio
async def test_missing_skill_is_not_assigned(db, tenant_a, agent_a):
    env = await production(db, tenant_a)
    queue = await create_queue(
        db,
        tenant_id=tenant_a.id,
        name="Billing",
        environment_id=env.id,
        required_skills=["billing"],
    )
    await add_member(db, tenant_id=tenant_a.id, queue_id=queue.id, user_id=agent_a.id)
    await go_available(db, tenant_a, agent_a)
    call = await live_call(db, tenant_a, env)
    result = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=call.id)
    await db.commit()
    assert result.outcome == "waiting"
    assert result.assignment is None
    assert result.decision.applied is False
    assert result.decision.rejected[0]["reason"] == "missing_skill"


@pytest.mark.asyncio
async def test_least_loaded_prefers_the_idle_agent(db, tenant_a, agent_a):
    other = await make_user(db, tenant_a, UserRole.AGENT)
    env = await production(db, tenant_a)
    queue = await create_queue(
        db, tenant_id=tenant_a.id, name="Support", environment_id=env.id, strategy="least_loaded"
    )
    await add_member(db, tenant_id=tenant_a.id, queue_id=queue.id, user_id=agent_a.id, capacity=2)
    await add_member(db, tenant_id=tenant_a.id, queue_id=queue.id, user_id=other.id, capacity=2)
    await go_available(db, tenant_a, agent_a)
    await go_available(db, tenant_a, other)
    first_call = await live_call(db, tenant_a, env)
    first = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=first_call.id)
    await db.commit()
    second_call = await live_call(db, tenant_a, env)
    second = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=second_call.id)
    await db.commit()
    assert first.outcome == "assigned"
    assert second.outcome == "assigned"
    assert first.assignment.user_id != second.assignment.user_id
