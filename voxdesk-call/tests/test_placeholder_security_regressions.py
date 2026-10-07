"""Regression checks for executable no-op authorization/ownership branches."""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.api.live_monitoring_routes import end_monitoring
from app.api.workflow_event_routes import TriggerCreate, create_trigger
from app.auth.dependencies import TenantContext
from app.db.enterprise_models import LiveCallSession, WorkflowTrigger
from app.db.models import UserRole, Workflow
from tests.acd_support import live_call, production
from tests.conftest import make_user


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["malformed", "missing", "foreign", "owned"])
async def test_trigger_requires_existing_tenant_workflow(db, tenant_a, tenant_b, owner_a, kind):
    workflow = Workflow(tenant_id=tenant_b.id if kind == "foreign" else tenant_a.id,
                        name="Trigger security regression")
    db.add(workflow)
    await db.commit()
    workflow_id = "invalid" if kind == "malformed" else str(uuid.uuid4()) if kind == "missing" else str(workflow.id)
    payload = TriggerCreate(workflow_id=workflow_id, event_type="after_call")
    ctx = TenantContext(user=owner_a, tenant=tenant_a)
    if kind == "owned":
        result = await create_trigger(payload, ctx, db)
        assert result.workflow_id == str(workflow.id)
        assert result.tenant_id == str(tenant_a.id)
    else:
        with pytest.raises(HTTPException) as caught:
            await create_trigger(payload, ctx, db)
        assert caught.value.status_code == 404
        assert (await db.execute(select(func.count()).select_from(WorkflowTrigger))).scalar_one() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("role,owns,allowed", [
    (UserRole.MANAGER, False, False),
    (UserRole.MANAGER, True, True),
    (UserRole.ADMIN, False, True),
    (UserRole.OWNER, False, True),
])
async def test_monitor_end_enforces_owner_or_effective_admin(db, tenant_a, owner_a, role, owns, allowed):
    environment = await production(db, tenant_a)
    call = await live_call(db, tenant_a, environment)
    actor = await make_user(db, tenant_a, role)
    row = LiveCallSession(tenant_id=tenant_a.id, call_id=call.id,
                          supervisor_id=actor.id if owns else owner_a.id,
                          status="active", mode="listen", meta={})
    db.add(row)
    await db.commit()
    ctx = TenantContext(user=actor, tenant=tenant_a)
    if allowed:
        result = await end_monitoring(call.id, row.id, ctx, db)
        assert result.status == "ended"
        assert result.meta["audit"][-1]["by"] == str(actor.id)
    else:
        with pytest.raises(HTTPException) as caught:
            await end_monitoring(call.id, row.id, ctx, db)
        assert caught.value.status_code == 403
        await db.refresh(row)
        assert row.status == "active"
        assert row.ended_at is None
