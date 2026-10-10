"""Verify that audit writes across enterprise routes are durable and atomic with the primary mutation (Part 1E / Gate G2)."""

from __future__ import annotations

import base64
import os

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.api import (
    batch_call_routes,
    live_monitoring_routes,
    retention_routes,
    webhook_lifecycle_routes,
)
from app.core.config import settings
from app.db.enterprise_models import BatchCall, LiveCallSession, RetentionPolicy
from app.db.models import CallStatus, WebhookSubscription
from tests.conftest import auth_headers, make_call

pytestmark = pytest.mark.asyncio


def _raise_integrity_error(*args, **kwargs):
    raise IntegrityError(
        "INSERT INTO audit_logs ...", {}, RuntimeError("forced audit failure")
    )


async def test_retention_policy_rolls_back_when_audit_insert_fails(
    client, db, tenant_a, owner_a, monkeypatch
):
    headers = await auth_headers(client, owner_a)

    async def _fail_emit(*args, **kwargs):
        _raise_integrity_error()

    monkeypatch.setattr(retention_routes, "emit", _fail_emit)
    before = await db.scalar(
        select(func.count(RetentionPolicy.id)).where(
            RetentionPolicy.tenant_id == tenant_a.id
        )
    )
    resp = await client.post(
        "/api/retention/policies",
        headers=headers,
        json={
            "agent_id": "agent-rollback-check",
            "resource_type": "call",
            "retention_days": 30,
            "purge_after_days": 90,
            "legal_hold": False,
        },
    )
    assert resp.status_code >= 500
    after = await db.scalar(
        select(func.count(RetentionPolicy.id)).where(
            RetentionPolicy.tenant_id == tenant_a.id
        )
    )
    assert after == before


async def test_live_monitoring_rolls_back_when_audit_insert_fails(
    client, db, tenant_a, owner_a, monkeypatch
):
    call = await make_call(db, tenant_a, status=CallStatus.IN_PROGRESS)
    await db.commit()
    headers = await auth_headers(client, owner_a)

    async def _fail_audit(*args, **kwargs):
        _raise_integrity_error()

    monkeypatch.setattr(
        live_monitoring_routes, "record_enterprise_audit", _fail_audit
    )
    before = await db.scalar(
        select(func.count(LiveCallSession.id)).where(
            LiveCallSession.tenant_id == tenant_a.id
        )
    )
    resp = await client.post(
        f"/api/calls/{call.id}/monitor",
        headers=headers,
        json={"mode": "listen", "reason": "QA check"},
    )
    assert resp.status_code >= 500
    after = await db.scalar(
        select(func.count(LiveCallSession.id)).where(
            LiveCallSession.tenant_id == tenant_a.id
        )
    )
    assert after == before


async def test_batch_call_rolls_back_when_audit_insert_fails(
    client, db, tenant_a, owner_a, monkeypatch
):
    headers = await auth_headers(client, owner_a)

    async def _fail_audit(*args, **kwargs):
        _raise_integrity_error()

    monkeypatch.setattr(batch_call_routes, "record_enterprise_audit", _fail_audit)
    before = await db.scalar(
        select(func.count(BatchCall.id)).where(BatchCall.tenant_id == tenant_a.id)
    )
    resp = await client.post(
        "/api/batch-calls",
        headers=headers,
        json={
            "name": "Audit Atomic Campaign",
            "agent_id": "agent-1",
            "recipients": [{"phone": "+15552345678", "name": "Alice"}],
        },
    )
    assert resp.status_code >= 500
    after = await db.scalar(
        select(func.count(BatchCall.id)).where(BatchCall.tenant_id == tenant_a.id)
    )
    assert after == before


async def test_webhook_subscription_rolls_back_when_audit_insert_fails(
    client, db, tenant_a, owner_a, monkeypatch
):
    monkeypatch.setattr(
        settings,
        "identity_encryption_keys",
        "test:" + base64.urlsafe_b64encode(os.urandom(32)).decode(),
    )

    async def _allow_rate(*args, **kwargs):
        return True

    monkeypatch.setattr(
        webhook_lifecycle_routes, "rate_limit", _allow_rate
    )
    monkeypatch.setattr(
        webhook_lifecycle_routes, "validate_outbound_url", lambda *a, **kw: None
    )

    headers = await auth_headers(client, owner_a)

    async def _fail_audit(*args, **kwargs):
        _raise_integrity_error()

    monkeypatch.setattr(webhook_lifecycle_routes, "record_event", _fail_audit)
    before = await db.scalar(
        select(func.count(WebhookSubscription.id)).where(
            WebhookSubscription.tenant_id == tenant_a.id
        )
    )
    resp = await client.post(
        "/api/webhooks",
        headers=headers,
        json={
            "url": "https://hooks.example.com/atomic-test",
            "events": ["call_started"],
        },
    )
    assert resp.status_code >= 500
    after = await db.scalar(
        select(func.count(WebhookSubscription.id)).where(
            WebhookSubscription.tenant_id == tenant_a.id
        )
    )
    assert after == before
