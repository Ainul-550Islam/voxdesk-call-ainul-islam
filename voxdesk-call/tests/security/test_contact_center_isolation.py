"""Cross-tenant and cross-environment access fails. A body tenant id cannot switch."""

from __future__ import annotations

import uuid

import pytest

from app.auth.jwt import create_access_token
from app.contact_center.queues import add_member, create_queue
from app.contact_center.service import enqueue
from app.db.models import Environment, EnvironmentMembership, UserRole
from app.tenancy.isolation import BoundaryDenied
from tests.acd_support import go_available, live_call, production
from tests.conftest import auth_headers, make_user


@pytest.mark.asyncio
async def test_other_tenant_cannot_read_queue_or_decision(client, db, tenant_a, tenant_b, manager_a):
    queue = await create_queue(db, tenant_id=tenant_a.id, name="Support")
    await db.commit()
    other = await make_user(db, tenant_b, UserRole.MANAGER)
    headers = await auth_headers(client, other)
    response = await client.get(f"/api/queues/{queue.id}", headers=headers)
    assert response.status_code == 404
    switched = await client.get(f"/api/tenants/{tenant_a.id}/queues", headers=headers)
    assert switched.status_code == 404


@pytest.mark.asyncio
async def test_body_tenant_id_does_not_enqueue_into_another_tenant(client, manager_a, tenant_b):
    headers = await auth_headers(client, manager_a)
    response = await client.post(
        "/api/queues",
        json={"name": "Nope", "tenant_id": str(tenant_b.id)},
        headers=headers,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_call_in_another_environment_is_rejected(db, tenant_a, agent_a):
    production_env = await production(db, tenant_a)
    staging = Environment(
        tenant_id=tenant_a.id,
        name="Staging",
        slug="staging",
        kind="staging",
        status="active",
    )
    db.add(staging)
    await db.commit()
    await db.refresh(staging)
    queue = await create_queue(
        db, tenant_id=tenant_a.id, name="Staging queue", environment_id=staging.id
    )
    await add_member(db, tenant_id=tenant_a.id, queue_id=queue.id, user_id=agent_a.id)
    await go_available(db, tenant_a, agent_a)
    call = await live_call(db, tenant_a, production_env)
    with pytest.raises(BoundaryDenied):
        await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=call.id)


@pytest.mark.asyncio
async def test_revoked_environment_membership_is_not_offered_work(db, tenant_a, agent_a):
    env = await production(db, tenant_a)
    db.add(
        EnvironmentMembership(
            environment_id=env.id,
            user_id=agent_a.id,
            role=UserRole.AGENT,
            status="revoked",
        )
    )
    await db.commit()
    queue = await create_queue(db, tenant_id=tenant_a.id, name="Support", environment_id=env.id)
    await add_member(db, tenant_id=tenant_a.id, queue_id=queue.id, user_id=agent_a.id)
    await go_available(db, tenant_a, agent_a)
    call = await live_call(db, tenant_a, env)
    result = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=call.id)
    await db.commit()
    assert result.assignment is None
    assert result.decision.rejected[0]["reason"] == "environment_membership_revoked"


@pytest.mark.asyncio
async def test_expired_token_cannot_read_queues(client, agent_a):
    token, _ttl = create_access_token(
        user_id=agent_a.id,
        tenant_id=agent_a.tenant_id,
        role=agent_a.role.value,
        token_version=agent_a.token_version,
        expires_minutes=-1,
    )
    response = await client.get("/api/queues", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_disabled_user_is_rejected(client, db, agent_a):
    agent_a.is_active = False
    await db.commit()
    token, _ttl = create_access_token(
        user_id=agent_a.id,
        tenant_id=agent_a.tenant_id,
        role=agent_a.role.value,
        token_version=agent_a.token_version,
    )
    response = await client.get("/api/queues", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert uuid.UUID(str(agent_a.id))
