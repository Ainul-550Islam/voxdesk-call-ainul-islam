"""Existing tenant-only creates keep working and land on production."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db.models import (
    Automation,
    Call,
    CallStatus,
    Environment,
    InboxThreadState,
    Lead,
    NotificationRow,
    UsageEvent,
    UsageEventType,
    UsageMetric,
)
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def _production(db, tenant) -> Environment:
    return (await db.execute(
        select(Environment).where(
            Environment.tenant_id == tenant.id, Environment.kind == "production",
        )
    )).scalar_one()


async def test_legacy_lead_import_binds_production(client, db, tenant_a, owner_a):
    production = await _production(db, tenant_a)
    headers = await auth_headers(client, owner_a)
    response = await client.post(
        f"/api/tenants/{tenant_a.id}/leads",
        json={"leads": [{"name": "Ada", "phone": "+15553000001"}]},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    lead = (await db.execute(select(Lead).where(Lead.phone == "+15553000001"))).scalar_one()
    assert lead.tenant_id == tenant_a.id
    assert lead.environment_id == production.id


async def test_direct_constructors_bind_production_without_an_environment_argument(db, tenant_a):
    production = await _production(db, tenant_a)
    call = Call(
        tenant_id=tenant_a.id, call_sid=f"legacy-{uuid.uuid4().hex[:8]}",
        from_number="+15553000002", to_number=tenant_a.twilio_number,
        status=CallStatus.RINGING,
    )
    automation = Automation(
        id=uuid.uuid4().hex[:24], tenant_id=tenant_a.id, name="Follow up", event="call.ended",
    )
    notification = NotificationRow(
        id=uuid.uuid4().hex[:24], tenant_id=tenant_a.id, template_id="tmpl",
        channel="sms", event_source="call", dedupe_key=uuid.uuid4().hex[:24],
    )
    usage = UsageEvent(
        tenant_id=tenant_a.id, billing_period="2026-09", metric=UsageMetric.VOICE_MINUTE,
        event_type=UsageEventType.VOICE_MINUTE_USED, quantity=30, unit="minutes",
        idempotency_key=uuid.uuid4().hex,
    )
    db.add_all([call, automation, notification, usage])
    await db.flush()
    inbox = InboxThreadState(tenant_id=tenant_a.id, call_id=call.id)
    db.add(inbox)
    await db.flush()
    for row in (call, automation, notification, usage, inbox):
        assert row.environment_id == production.id
        assert row.tenant_id == tenant_a.id


async def test_usage_metric_contract_is_unchanged():
    assert UsageMetric.VOICE_MINUTE.value == "voice_minute"
