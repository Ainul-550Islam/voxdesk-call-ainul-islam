"""Coaching is a workflow. It does not discipline the employee."""

from __future__ import annotations

import uuid

import pytest

from app.db.models import UserRole
from app.qa.coaching import create_signal, transition
from app.qa.exceptions import ReviewerNotAuthorized, StaleReview
from app.tenancy.isolation import NotFound
from tests.acd_support import live_call, production


@pytest.mark.asyncio
async def test_acknowledgement_is_the_agent_or_a_supervisor_and_does_not_change_employment(
    db, tenant_a, agent_a, manager_a
):
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    signal = await create_signal(
        db,
        tenant_id=tenant_a.id,
        call_id=call.id,
        agent_user_id=agent_a.id,
        category="opening",
        recommendation="Repeat the recording notice.",
    )
    assert signal.status == "created"
    assigned, outcome = await transition(
        db, signal, "assigned", actor_id=manager_a.id, supervisor=True, assignee_id=manager_a.id
    )
    assert outcome == "applied"
    assert assigned.status == "assigned"
    with pytest.raises(ReviewerNotAuthorized):
        await transition(db, signal, "acknowledged", actor_id=manager_a.id, supervisor=False)
    acknowledged, acked = await transition(db, signal, "acknowledged", actor_id=agent_a.id)
    assert acked == "applied"
    assert acknowledged.acknowledged_at is not None
    duplicate, again = await transition(db, signal, "acknowledged", actor_id=agent_a.id)
    assert again == "duplicate"
    assert duplicate.version == acknowledged.version
    completed, done = await transition(
        db, signal, "completed", actor_id=manager_a.id, supervisor=True, notes="discussed"
    )
    assert done == "applied"
    assert completed.status == "completed"
    await db.refresh(agent_a)
    assert agent_a.role == UserRole.AGENT
    assert agent_a.is_active is True
    assert not hasattr(completed, "discipline_action")


@pytest.mark.asyncio
async def test_stale_acknowledgement_and_foreign_agent_are_rejected(db, tenant_a, tenant_b, agent_a, manager_a):
    env = await production(db, tenant_a)
    call = await live_call(db, tenant_a, env)
    signal = await create_signal(
        db,
        tenant_id=tenant_a.id,
        call_id=call.id,
        agent_user_id=agent_a.id,
        category="tone",
        recommendation="Slow down.",
    )
    await transition(db, signal, "assigned", actor_id=manager_a.id, supervisor=True)
    with pytest.raises(StaleReview):
        await transition(
            db,
            signal,
            "acknowledged",
            actor_id=agent_a.id,
            expected_version=signal.version + 5,
        )
    with pytest.raises(NotFound):
        await create_signal(
            db,
            tenant_id=tenant_a.id,
            call_id=call.id,
            agent_user_id=uuid.uuid4(),
            category="tone",
            recommendation="no",
        )
    foreign_env = await production(db, tenant_b)
    assert foreign_env.tenant_id != tenant_a.id
