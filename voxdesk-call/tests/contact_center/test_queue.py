"""Queue configuration. Membership is not availability."""

from __future__ import annotations

import uuid

import pytest

from app.contact_center.exceptions import QueueUnavailable
from app.contact_center.models import AgentPresence
from app.contact_center.queues import add_member, create_queue
from app.contact_center.repository import presence_for
from app.tenancy.isolation import NotFound
from sqlalchemy import select
from tests.acd_support import production
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_create_queue_and_member_does_not_mark_available(db, tenant_a, agent_a):
    env = await production(db, tenant_a)
    queue = await create_queue(db, tenant_id=tenant_a.id, name="Support", environment_id=env.id)
    member = await add_member(db, tenant_id=tenant_a.id, queue_id=queue.id, user_id=agent_a.id)
    await db.commit()
    presence = await presence_for(db, tenant_a.id, agent_a.id)
    assert member.enabled is True
    assert presence is None or presence.state != "available"
    assert queue.environment_id == env.id
    assert queue.overflow_policy["kind"] == "none"


@pytest.mark.asyncio
async def test_duplicate_queue_name_is_rejected(db, tenant_a):
    await create_queue(db, tenant_id=tenant_a.id, name="Support")
    await db.commit()
    with pytest.raises(QueueUnavailable):
        await create_queue(db, tenant_id=tenant_a.id, name="Support")


@pytest.mark.asyncio
async def test_escalate_policy_requires_a_number(db, tenant_a):
    with pytest.raises(QueueUnavailable):
        await create_queue(
            db,
            tenant_id=tenant_a.id,
            name="Escalations",
            overflow_policy={"kind": "escalate", "escalation_number": ""},
        )


@pytest.mark.asyncio
async def test_client_tenant_cannot_override_queue_create(client, manager_a, tenant_b):
    headers = await auth_headers(client, manager_a)
    response = await client.post(
        "/api/queues",
        json={"name": "Stolen", "tenant_id": str(tenant_b.id)},
        headers=headers,
    )
    assert response.status_code == 404
    listed = await client.get("/api/queues", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["queues"] == []


@pytest.mark.asyncio
async def test_other_tenant_queue_is_missing(db, tenant_a, tenant_b):
    queue = await create_queue(db, tenant_id=tenant_a.id, name="Support")
    await db.commit()
    with pytest.raises(NotFound):
        from app.contact_center.repository import get_queue

        await get_queue(db, tenant_b.id, queue.id)


@pytest.mark.asyncio
async def test_unknown_user_cannot_join(db, tenant_a):
    queue = await create_queue(db, tenant_id=tenant_a.id, name="Support")
    await db.commit()
    with pytest.raises(Exception):
        await add_member(db, tenant_id=tenant_a.id, queue_id=queue.id, user_id=uuid.uuid4())
    rows = (await db.execute(select(AgentPresence))).scalars().all()
    assert rows == []
