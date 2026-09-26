"""Automation durability: environment isolation and one action receipt."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.automation.durable_repository import claim_action
from app.automation.executor import resolve_execution
from app.automation.retry import backoff_seconds, classify
from app.db.models import Automation, Environment
from app.jobs.models import PermanentJobError
from app.tenancy.isolation import BoundaryDenied


async def _production(db, tenant) -> Environment:
    return (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id, Environment.kind == "production"
            )
        )
    ).scalar_one()


async def _staging(db, tenant) -> Environment:
    row = Environment(
        tenant_id=tenant.id, name="Staging", slug="staging", kind="staging", status="active"
    )
    db.add(row)
    await db.flush()
    return row


async def _automation(db, tenant, environment, automation_id="auto-1") -> Automation:
    row = Automation(
        id=automation_id,
        tenant_id=tenant.id,
        environment_id=environment.id,
        name="Notify",
        event="lead_created",
        status="enabled",
        actions=[{"name": "enqueue_notification", "params": {}}],
    )
    db.add(row)
    await db.flush()
    return row


async def test_staging_automation_cannot_target_production(db, tenant_a):
    production = await _production(db, tenant_a)
    staging = await _staging(db, tenant_a)
    await _automation(db, tenant_a, staging)
    with pytest.raises(PermanentJobError) as caught:
        await resolve_execution(
            db,
            tenant_id=tenant_a.id,
            automation_id="auto-1",
            target_environment_id=production.id,
        )
    assert caught.value.category == "environment_mismatch"


async def test_other_tenant_automation_is_not_found(db, tenant_a, tenant_b):
    production = await _production(db, tenant_a)
    await _automation(db, tenant_a, production)
    with pytest.raises(PermanentJobError):
        await resolve_execution(
            db,
            tenant_id=tenant_b.id,
            automation_id="auto-1",
            target_environment_id=production.id,
        )


async def test_action_receipt_is_unique(db, tenant_a):
    production = await _production(db, tenant_a)
    first, created = await claim_action(
        db,
        tenant_id=tenant_a.id,
        environment_id=production.id,
        organization_id=None,
        automation_id="auto-1",
        business_event_id="lead-9",
        action_id="0:enqueue_notification",
    )
    second, again = await claim_action(
        db,
        tenant_id=tenant_a.id,
        environment_id=production.id,
        organization_id=None,
        automation_id="auto-1",
        business_event_id="lead-9",
        action_id="0:enqueue_notification",
    )
    assert created is True and again is False
    assert first.id == second.id


async def test_cross_tenant_receipt_does_not_match(db, tenant_a, tenant_b):
    production = await _production(db, tenant_a)
    other = await _production(db, tenant_b)
    await claim_action(
        db,
        tenant_id=tenant_a.id,
        environment_id=production.id,
        organization_id=None,
        automation_id="auto-1",
        business_event_id="lead-9",
        action_id="0:enqueue_notification",
    )
    _row, created = await claim_action(
        db,
        tenant_id=tenant_b.id,
        environment_id=other.id,
        organization_id=None,
        automation_id="auto-1",
        business_event_id="lead-9",
        action_id="0:enqueue_notification",
    )
    assert created is True


async def test_retry_classification_and_backoff():
    assert classify("validation_error") == "permanent"
    assert classify("timeout") == "retryable"
    assert classify("provider_4xx") == "permanent"
    first = backoff_seconds(1, key="a")
    later = backoff_seconds(4, key="a")
    assert 1 <= first <= 30
    assert later <= 3600
    assert later >= first


async def test_missing_automation_is_not_a_cross_tenant_existence_leak(db, tenant_a, tenant_b):
    production = await _production(db, tenant_b)
    with pytest.raises((PermanentJobError, BoundaryDenied)):
        await resolve_execution(
            db,
            tenant_id=tenant_a.id,
            automation_id="missing",
            target_environment_id=production.id,
        )
