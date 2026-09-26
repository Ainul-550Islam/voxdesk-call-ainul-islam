"""Presence transitions. A heartbeat is not a login."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.contact_center.agent_state import legal
from app.contact_center.exceptions import AgentUnavailable
from app.contact_center.policies import presence_reason
from app.contact_center.presence import heartbeat
from app.contact_center.queues import add_member, create_queue
from app.contact_center.service import enqueue, set_state
from tests.acd_support import go_available, live_call, production


def test_illegal_and_duplicate_transitions():
    assert legal("offline", "busy") is False
    assert legal("offline", "available") is True
    assert legal("available", "available") is True
    assert legal("available", "disabled", supervisor=False) is False
    assert legal("available", "disabled", supervisor=True) is True
    assert legal("disabled", "available", supervisor=True) is False


@pytest.mark.asyncio
async def test_heartbeat_does_not_make_offline_available(db, tenant_a, agent_a):
    row, outcome = await set_state(
        db,
        tenant_id=tenant_a.id,
        user_id=agent_a.id,
        target="offline",
        actor_id=agent_a.id,
    )
    assert outcome == "duplicate"
    assert row.state == "offline"
    beaten = await heartbeat(db, tenant_a.id, agent_a.id)
    await db.commit()
    assert beaten.state == "offline"
    assert beaten.last_seen is None


@pytest.mark.asyncio
async def test_stale_available_agent_is_not_offered_work(db, tenant_a, agent_a):
    env = await production(db, tenant_a)
    queue = await create_queue(db, tenant_id=tenant_a.id, name="Support", environment_id=env.id)
    await add_member(db, tenant_id=tenant_a.id, queue_id=queue.id, user_id=agent_a.id)
    presence, _outcome = await go_available(db, tenant_a, agent_a)
    presence.last_seen = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db.commit()
    assert presence_reason(presence) == "stale"
    call = await live_call(db, tenant_a, env)
    result = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=call.id)
    await db.commit()
    assert result.outcome == "waiting"
    assert result.assignment is None
    assert result.decision.rejected[0]["reason"] == "stale"


@pytest.mark.asyncio
async def test_agent_cannot_disable_self(db, tenant_a, agent_a):
    await go_available(db, tenant_a, agent_a)
    with pytest.raises(AgentUnavailable):
        await set_state(
            db,
            tenant_id=tenant_a.id,
            user_id=agent_a.id,
            target="disabled",
            actor_id=agent_a.id,
            supervisor=False,
        )


@pytest.mark.asyncio
async def test_same_state_is_a_duplicate(db, tenant_a, agent_a):
    await go_available(db, tenant_a, agent_a)
    _row, outcome = await set_state(
        db,
        tenant_id=tenant_a.id,
        user_id=agent_a.id,
        target="available",
        actor_id=agent_a.id,
    )
    assert outcome == "duplicate"
