"""Dialer limits tests: calling_window CallPolicy, concurrency CallPolicy, and retry scheduling (Part 1B / Gate G1)."""

from __future__ import annotations

import uuid
from datetime import datetime, time, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.enterprise_models import (
    BatchCall,
    BatchStatus,
    CallPolicy,
)
from app.db.models import (
    CallStatus,
    Campaign,
    Environment,
    LeadStatus,
)
from app.telephony import dialer_limits, outbound
from tests.conftest import make_lead, make_tenant


async def _seed_tenant_and_campaign(db):
    tenant = await make_tenant(db, "Limits Co")
    tenant.outbound_enabled = True
    tenant.outbound_caller_id = "+15550008888"
    tenant.timezone = "UTC"
    tenant.outbound_window_open = time(0, 0)
    tenant.outbound_window_close = time(23, 59, 59)
    await db.commit()

    production = (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id,
                Environment.kind == "production",
            )
        )
    ).scalar_one()

    campaign = Campaign(
        tenant_id=tenant.id,
        environment_id=production.id,
        name="Limits Campaign",
        is_active=True,
        calls_per_minute=10,
    )
    db.add(campaign)
    await db.commit()
    await db.refresh(campaign)
    return tenant, production, campaign


@pytest.mark.asyncio
async def test_calling_window_policy_blocks_outside_hours_and_computes_next_window(
    db,
):
    tenant, _, _ = await _seed_tenant_and_campaign(db)

    window_cfg = CallPolicy(
        tenant_id=tenant.id,
        agent_id="agent-win",
        policy_type="calling_window",
        config={
            "timezone": "UTC",
            "windows": [
                {"day": d, "start": "09:00", "end": "17:00", "enabled": d < 5}
                for d in range(7)
            ],
        },
        is_enabled=True,
    )
    db.add(window_cfg)
    await db.commit()

    # Inside window: Wednesday 2026-10-07 12:00 UTC (weekday 2)
    inside_dt = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    v_inside = await dialer_limits.check_calling_window(
        db, tenant.id, agent_id="agent-win", now=inside_dt
    )
    assert v_inside.allowed is True

    # Outside hours: Wednesday 2026-10-07 20:00 UTC
    after_dt = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)
    v_after = await dialer_limits.check_calling_window(
        db, tenant.id, agent_id="agent-win", now=after_dt
    )
    assert v_after.allowed is False
    assert v_after.reason is not None and "outside_calling_window" in v_after.reason
    assert v_after.next_window_start is not None
    assert v_after.next_window_start == datetime(2026, 10, 8, 9, 0, tzinfo=timezone.utc)

    # Disabled day: Saturday 2026-10-10 12:00 UTC (weekday 5)
    weekend_dt = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    v_weekend = await dialer_limits.check_calling_window(
        db, tenant.id, agent_id="agent-win", now=weekend_dt
    )
    assert v_weekend.allowed is False
    assert v_weekend.next_window_start == datetime(
        2026, 10, 12, 9, 0, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
async def test_concurrency_policy_limit_and_retry_schedule(
    db, monkeypatch
):
    tenant, production, campaign = await _seed_tenant_and_campaign(db)

    conc_policy = CallPolicy(
        tenant_id=tenant.id,
        agent_id="agent-pol",
        policy_type="concurrency",
        config={"max_concurrent_calls": 1, "max_calls_per_minute": 10},
        is_enabled=True,
    )
    retry_policy = CallPolicy(
        tenant_id=tenant.id,
        agent_id="agent-pol",
        policy_type="retry",
        config={
            "max_attempts": 3,
            "retry_delay_seconds": 120,
            "backoff_multiplier": 2.0,
            "max_delay_seconds": 3600,
            "retry_on": ["no_answer", "busy", "failed"],
        },
        is_enabled=True,
    )
    db.add_all([conc_policy, retry_policy])
    batch = BatchCall(
        tenant_id=tenant.id,
        campaign_id=campaign.id,
        name="Policy Batch",
        agent_id="agent-pol",
        status=BatchStatus.RUNNING.value,
        concurrency=1,
    )
    db.add(batch)
    await db.flush()
    campaign.batch_call_id = batch.id
    await db.commit()

    lead1 = await make_lead(
        db,
        tenant,
        phone="+15550100101",
        campaign_id=campaign.id,
        environment_id=production.id,
    )
    lead2 = await make_lead(
        db,
        tenant,
        phone="+15550100102",
        campaign_id=campaign.id,
        environment_id=production.id,
    )

    class _FakeCalls:
        def create(self, **kwargs):
            return SimpleNamespace(sid=f"CA{uuid.uuid4().hex[:16]}")

    monkeypatch.setattr(
        outbound, "_twilio", lambda: SimpleNamespace(calls=_FakeCalls())
    )

    # First lead dials and leaves a RINGING Call row
    call1 = await outbound.dial_lead(
        db, lead1, campaign=campaign, batch=batch
    )
    assert call1 is not None
    assert call1.status == CallStatus.RINGING

    # Second lead is deferred because max_concurrent_calls == 1 and 1 Call is RINGING
    call2 = await outbound.dial_lead(
        db, lead2, campaign=campaign, batch=batch
    )
    assert call2 is None
    assert lead2.status == LeadStatus.NEW

    # Once call1 completes, lead2 can dial
    call1.status = CallStatus.COMPLETED
    await db.commit()

    call2_retry = await outbound.dial_lead(
        db, lead2, campaign=campaign, batch=batch
    )
    assert call2_retry is not None

    # Verify retry schedule uses CallPolicy(policy_type="retry")
    now_dt = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    r_dec = await dialer_limits.compute_retry_schedule(
        db,
        tenant.id,
        agent_id="agent-pol",
        attempt=1,
        outcome="failed",
        batch=batch,
        now=now_dt,
    )
    assert r_dec.should_retry is True
    assert r_dec.next_attempt_at == datetime(2026, 10, 7, 12, 2, tzinfo=timezone.utc)

    r_exhausted = await dialer_limits.compute_retry_schedule(
        db,
        tenant.id,
        agent_id="agent-pol",
        attempt=3,
        outcome="failed",
        batch=batch,
        now=now_dt,
    )
    assert r_exhausted.should_retry is False
    assert r_exhausted.reason is not None and "max_attempts_reached" in r_exhausted.reason
