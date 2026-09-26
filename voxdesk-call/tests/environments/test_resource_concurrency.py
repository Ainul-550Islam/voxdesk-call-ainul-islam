"""Binding races are closed by the insert hook and the usage idempotency key."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from datetime import datetime, timezone

from app.billing.metering import record_usage
from app.billing.periods import BillingPeriod
from app.db.models import Environment, Lead, UsageEventType, UsageMetric
from app.environments.resource_binding import ImmutableEnvironment

pytestmark = pytest.mark.asyncio


async def test_two_creates_bind_the_same_production_row_and_cannot_be_rebound(db, tenant_a):
    production = (await db.execute(
        select(Environment).where(
            Environment.tenant_id == tenant_a.id, Environment.kind == "production",
        )
    )).scalar_one()
    first = Lead(tenant_id=tenant_a.id, name="One", phone="+15554000001")
    second = Lead(tenant_id=tenant_a.id, name="Two", phone="+15554000002")
    db.add_all([first, second])
    await db.flush()
    assert first.environment_id == second.environment_id == production.id
    first.environment_id = uuid.uuid4()
    with pytest.raises(ImmutableEnvironment):
        await db.flush()
    await db.rollback()


async def test_a_suspended_environment_racing_a_create_rejects_the_insert(db, tenant_a):
    production = (await db.execute(
        select(Environment).where(
            Environment.tenant_id == tenant_a.id, Environment.kind == "production",
        )
    )).scalar_one()
    production.status = "suspended"
    await db.flush()
    db.add(Lead(tenant_id=tenant_a.id, name="Late", phone="+15554000003"))
    from app.tenancy.isolation import LifecycleDenied
    with pytest.raises(LifecycleDenied):
        await db.flush()
    await db.rollback()


async def test_duplicate_usage_events_do_not_double_charge(db, tenant_a):
    period = BillingPeriod(
        label="2026-09",
        start=datetime(2026, 9, 1, tzinfo=timezone.utc),
        end=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    key = "call-usage-" + uuid.uuid4().hex
    first = await record_usage(
        db, tenant_id=tenant_a.id, metric=UsageMetric.VOICE_MINUTE, quantity=15,
        idempotency_key=key, period=period, event_type=UsageEventType.VOICE_MINUTE_USED,
    )
    second = await record_usage(
        db, tenant_id=tenant_a.id, metric=UsageMetric.VOICE_MINUTE, quantity=15,
        idempotency_key=key, period=period, event_type=UsageEventType.VOICE_MINUTE_USED,
    )
    assert first.created is True
    assert second.duplicate is True
    assert second.event.id == first.event.id
    assert first.event.environment_id is not None
    assert first.event.quantity == 15
