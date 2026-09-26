"""Supervisor mutations are authorized, audited and idempotent."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.contact_center.queues import add_member, create_queue
from app.contact_center.service import enqueue, release_assignment, set_state
from app.db.models import AuditLog
from tests.acd_support import go_available, live_call, production
from tests.conftest import auth_headers


@pytest.mark.asyncio
async def test_viewer_cannot_force_state(client, viewer_a, agent_a):
    headers = await auth_headers(client, viewer_a)
    response = await client.post(
        f"/api/supervisor/agents/{agent_a.id}/state",
        json={"state": "disabled"},
        headers=headers,
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_disable_releases_work_and_repeat_release_is_duplicate(db, tenant_a, agent_a, admin_a):
    env = await production(db, tenant_a)
    queue = await create_queue(db, tenant_id=tenant_a.id, name="Support", environment_id=env.id)
    await add_member(db, tenant_id=tenant_a.id, queue_id=queue.id, user_id=agent_a.id)
    await go_available(db, tenant_a, agent_a)
    call = await live_call(db, tenant_a, env)
    assigned = await enqueue(db, tenant_id=tenant_a.id, queue_id=queue.id, call_id=call.id)
    await db.commit()
    assert assigned.outcome == "assigned"
    _row, outcome = await set_state(
        db,
        tenant_id=tenant_a.id,
        user_id=agent_a.id,
        target="disabled",
        actor_id=admin_a.id,
        supervisor=True,
    )
    await db.commit()
    assert outcome == "applied"
    await db.refresh(assigned.entry)
    assert assigned.entry.status == "waiting"
    again = await release_assignment(db, assigned.assignment)
    assert again == "duplicate"
    audits = (
        await db.execute(select(AuditLog).where(AuditLog.tenant_id == tenant_a.id))
    ).scalars().all()
    operations = [row.detail.get("operation") for row in audits]
    assert "acd_agent_state" in operations


@pytest.mark.asyncio
async def test_manager_can_read_supervisor_monitor_but_not_write(client, manager_a, db, tenant_a):
    queue = await create_queue(db, tenant_id=tenant_a.id, name="Support")
    await db.commit()
    headers = await auth_headers(client, manager_a)
    read = await client.get(f"/api/supervisor/queues/{queue.id}", headers=headers)
    assert read.status_code == 200
    assert read.json()["metrics"]["waiting"] == 0
    write = await client.post(
        f"/api/supervisor/agents/{manager_a.id}/state",
        json={"state": "offline"},
        headers=headers,
    )
    assert write.status_code == 403
