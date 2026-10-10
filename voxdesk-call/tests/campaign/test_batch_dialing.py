"""End-to-end BatchCall -> Campaign -> Lead -> Call runtime bridge test (Part 1B / Gate G1)."""

from __future__ import annotations

import uuid
from datetime import time
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.api.batch_call_routes import (
    BatchCallCreate,
    RecipientInput,
    create_batch_call,
    get_batch_analytics,
    get_batch_call,
    list_recipients,
    start_batch_call,
)
from app.auth.dependencies import TenantContext
from app.db.enterprise_models import (
    BatchCall,
    BatchRecipientStatus,
    BatchStatus,
)
from app.db.models import (
    Call,
    CallStatus,
    Campaign,
    Environment,
    Lead,
    User,
    UserRole,
)
from app.telephony import outbound
from tests.conftest import make_tenant


@pytest.mark.asyncio
async def test_batch_call_creates_campaign_leads_and_dials_real_calls(
    db, monkeypatch
):
    tenant = await make_tenant(db, "Batch Dial Co")
    tenant.outbound_enabled = True
    tenant.outbound_caller_id = "+15550007777"
    tenant.timezone = "UTC"
    tenant.outbound_window_open = time(0, 0)
    tenant.outbound_window_close = time(23, 59, 59)
    user = User(
        tenant_id=tenant.id,
        email=f"batch-{uuid.uuid4().hex[:6]}@example.com",
        password_hash="x",
        role=UserRole.OWNER,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(tenant)
    await db.refresh(user)

    production = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()

    ctx = TenantContext(
        user=user,
        tenant=tenant,
        auth_method="session",
        environment_id=production.id,
    )

    dialed_sids: list[str] = []

    class _FakeCalls:
        def create(self, **kwargs):
            sid = f"CA{uuid.uuid4().hex[:16]}"
            dialed_sids.append(sid)
            return SimpleNamespace(sid=sid)

    monkeypatch.setattr(
        outbound, "_twilio", lambda: SimpleNamespace(calls=_FakeCalls())
    )

    created = await create_batch_call(
        BatchCallCreate(
            name="E2E Batch 3 Recipients",
            agent_id="agent-batch-e2e",
            concurrency=5,
            recipients=[
                RecipientInput(phone="+15550200001", name="Alice"),
                RecipientInput(phone="+15550200002", name="Bob"),
                RecipientInput(phone="+15550200003", name="Carol"),
            ],
        ),
        ctx=ctx,
        session=db,
        x_idempotency_key="batch-e2e-idem-001",
    )
    batch_id = uuid.UUID(created.id)
    assert created.total_recipients == 3

    # Verify backing Campaign and 3 Lead rows were created
    batch_row = await db.get(BatchCall, batch_id)
    assert batch_row is not None
    assert batch_row.campaign_id is not None
    campaign = await db.get(Campaign, batch_row.campaign_id)
    assert campaign is not None
    assert campaign.batch_call_id == batch_id

    leads = (
        await db.execute(
            select(Lead).where(Lead.campaign_id == campaign.id)
        )
    ).scalars().all()
    assert len(leads) == 3

    # Start the batch; start_batch_call activates the campaign and runs run_campaign_step
    started = await start_batch_call(batch_id, ctx=ctx, session=db)
    assert started.status == BatchStatus.RUNNING.value

    # Assert 3 Call rows exist and all 3 BatchRecipient rows have call_id populated
    calls = (
        await db.execute(
            select(Call).where(Call.tenant_id == tenant.id)
        )
    ).scalars().all()
    assert len(calls) == 3
    assert len(dialed_sids) == 3

    recipient_list = await list_recipients(
        batch_id, status=None, limit=50, offset=0, ctx=ctx, session=db
    )
    assert len(recipient_list.recipients) == 3
    for rec in recipient_list.recipients:
        assert rec.call_id is not None
        assert rec.lead_id is not None
        assert rec.status == BatchRecipientStatus.DIALING.value

    # Mark 2 calls COMPLETED and 1 call FAILED, then read batch status & analytics
    calls[0].status = CallStatus.COMPLETED
    calls[1].status = CallStatus.COMPLETED
    calls[2].status = CallStatus.FAILED
    await db.commit()

    refreshed = await get_batch_call(batch_id, ctx=ctx, session=db)
    assert refreshed.status == BatchStatus.COMPLETED.value
    assert refreshed.completed_recipients == 2
    assert refreshed.failed_recipients == 1

    analytics = await get_batch_analytics(batch_id, ctx=ctx, session=db)
    assert analytics.total_recipients == 3
    assert analytics.completed == 2
    assert analytics.failed == 1
