"""Notifications, webhooks, email and inbox durability."""

from __future__ import annotations

import pytest
from tests.webhooks.conftest import webhook_crypto  # noqa: F401 - explicit AES fixture
import httpx
from sqlalchemy import select

from app.core.config import settings
from app.db.models import Environment, NotificationRow, UserRole
from app.email.delivery import (
    ConfiguredEmailProvider,
    classify_provider_error,
    recipient_hash,
    record,
    validate_recipient,
)
from app.inbox.assignment import claim
from app.inbox.concurrency import compare_and_set
from app.inbox.service import add_note
from app.inbox.sla import evaluate, start
from app.notifications.dead_letter import move_to_dlq, replay
from app.notifications.delivery import SmsAdapter, dispatch
from app.notifications.preferences import allows, get_or_create
from app.notifications.retry import classify, should_retry
from app.resources.exceptions import ResourceUnauthorized
from app.tenancy.isolation import BoundaryDenied, Conflict
from app.webhooks.delivery import deliver
from app.webhooks.replay import replay_delivery
from app.webhooks.repository import (
    create_delivery,
    create_subscription,
    get_subscription,
    open_secret,
)
from app.webhooks.retry import classify_status, should_retry as webhook_should_retry
from app.webhooks.signing import sign, verify
from tests.conftest import make_call


async def _production(db, tenant) -> Environment:
    return (
        await db.execute(
            select(Environment).where(
                Environment.tenant_id == tenant.id, Environment.kind == "production"
            )
        )
    ).scalar_one()


def test_webhook_signature_round_trip():
    header = sign("top-secret", b'{"ok":true}', timestamp=1_700_000_000)
    assert verify("top-secret", b'{"ok":true}', header, now=1_700_000_000)
    assert verify("other", b'{"ok":true}', header, now=1_700_000_000) is False
    assert "top-secret" not in header


def test_permanent_400_is_not_retried_and_500_429_are_bounded():
    assert classify_status(400) == "permanent"
    assert webhook_should_retry(classify_status(400), 1) is False
    assert classify_status(302) == "permanent"
    assert classify_status(500) == "retryable"
    assert classify_status(429) == "retryable"
    assert webhook_should_retry("retryable", 7) is True
    assert webhook_should_retry("retryable", 8) is False
    assert classify("provider_4xx") == "permanent"
    assert should_retry("provider_5xx", 4) is True
    assert should_retry("provider_5xx", 5) is False
    assert classify_provider_error("provider_4xx") == "permanent"
    assert classify_provider_error("timeout") == "retryable"


async def test_ssrf_and_redirect_are_rejected(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/secret"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        blocked = await deliver(
            url="http://169.254.169.254/latest/meta-data",
            body=b"{}",
            secret="do-not-log",
            event_id="evt-ssrf",
            client=client,
        )
        redirected = await deliver(
            url="https://example.com/hooks",
            body=b"{}",
            secret="do-not-log",
            event_id="evt-redirect",
            client=client,
        )
    assert blocked.kind == "permanent" and blocked.category == "invalid_endpoint"
    assert calls["n"] == 1
    assert redirected.kind == "permanent" and redirected.category == "redirect_rejected"


async def test_permanent_400_and_retryable_500(monkeypatch):
    captured = []

    def info(event, **kwargs):
        captured.append((event, kwargs))

    monkeypatch.setattr("app.webhooks.delivery.log.info", info)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bad"):
            return httpx.Response(400)
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        denied = await deliver(
            url="https://example.com/bad",
            body=b"{}",
            secret="super-secret-value",
            event_id="evt-400",
            client=client,
        )
        failed = await deliver(
            url="https://example.com/down",
            body=b"{}",
            secret="super-secret-value",
            event_id="evt-500",
            client=client,
        )
    assert denied.kind == "permanent" and denied.status == 400
    assert failed.kind == "retryable" and failed.status == 500
    blob = str(captured)
    assert "super-secret-value" not in blob


async def test_webhook_scope_and_replay_authorization(db, tenant_a, tenant_b):
    production = await _production(db, tenant_a)
    secret = "whsec-not-for-logs"
    subscription = await create_subscription(
        db,
        tenant_id=tenant_a.id,
        endpoint="https://example.com/hooks",
        secret=secret,
        event_types=["lead.created"],
        environment_id=production.id,
    )
    assert subscription.secret_envelope != secret
    assert open_secret(tenant_a.id, subscription.secret_envelope) == secret
    assert (
        await get_subscription(db, tenant_id=tenant_b.id, subscription_id=subscription.id) is None
    )
    from app.outbox.publisher import publish
    event, _ = await publish(db, tenant_id=tenant_a.id, environment_id=production.id,
                             event_type="lead.created", idempotency_key="replay-contract")
    event.status = "dead_letter"
    delivery, created = await create_delivery(
        db,
        tenant_id=tenant_a.id,
        subscription_id=subscription.id,
        event_id=str(event.id),
        environment_id=production.id,
    )
    again, second = await create_delivery(
        db,
        tenant_id=tenant_a.id,
        subscription_id=subscription.id,
        event_id=str(event.id),
        environment_id=production.id,
    )
    assert created is True and second is False and again.id == delivery.id
    delivery.status = "dead_letter"
    with pytest.raises(ResourceUnauthorized):
        await replay_delivery(
            db,
            tenant_id=tenant_a.id,
            delivery_id=delivery.id,
            subscription_id=subscription.id,
            role=UserRole.VIEWER,
        )
    with pytest.raises(BoundaryDenied):
        await replay_delivery(
            db,
            tenant_id=tenant_b.id,
            delivery_id=delivery.id,
            subscription_id=subscription.id,
            role=UserRole.OWNER,
        )
    replayed = await replay_delivery(
        db,
        tenant_id=tenant_a.id,
        delivery_id=delivery.id,
        subscription_id=subscription.id,
        role=UserRole.OWNER,
    )
    assert replayed.tenant_id == tenant_a.id
    assert replayed.subscription_id == subscription.id
    assert replayed.status == "queued"


async def test_notification_replay_keeps_tenant_and_environment(db, tenant_a, tenant_b, owner_a):
    production = await _production(db, tenant_a)
    other = await _production(db, tenant_b)
    row = NotificationRow(
        id="note-1",
        tenant_id=tenant_a.id,
        environment_id=production.id,
        template_id="tmpl",
        channel="email",
        event_source="system",
        dedupe_key="note-1",
        delivery_state="pending",
    )
    db.add(row)
    await db.flush()
    await move_to_dlq(db, row, category="provider_4xx")
    with pytest.raises(ResourceUnauthorized):
        await replay(db, tenant_id=tenant_a.id, notification_id=row.id, role=UserRole.VIEWER)
    with pytest.raises(BoundaryDenied):
        await replay(
            db,
            tenant_id=tenant_b.id,
            notification_id=row.id,
            role=UserRole.OWNER,
            environment_id=other.id,
        )
    replayed = await replay(db, tenant_id=tenant_a.id, notification_id=row.id, role=UserRole.OWNER)
    assert replayed.tenant_id == tenant_a.id
    assert replayed.environment_id == production.id
    assert replayed.delivery_state == "pending"
    preference = await get_or_create(db, tenant_id=tenant_a.id, user_id=owner_a.id)
    preference.email_enabled = False
    assert allows(preference, channel="email", category="operational") is False
    assert allows(preference, channel="email", category="security") is True


async def test_sms_invalid_recipient_does_not_call_a_provider():
    result = await SmsAdapter().send(recipient="not-a-number", body="hello")
    assert result.outcome == "permanent_failure"
    missing = await dispatch("fax", recipient="x", body="y")
    assert missing.category == "unsupported_channel"


async def test_production_log_transport_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "email_transport", "log")
    with pytest.raises(RuntimeError):
        ConfiguredEmailProvider()


async def test_email_record_stores_a_hash_not_the_address(db, tenant_a):
    address = "person@example.com"
    row = await record(
        db, tenant_id=tenant_a.id, recipient=address, status="accepted", template_name="hello"
    )
    assert row.recipient_hash == recipient_hash(address)
    assert address not in row.recipient_hash
    assert validate_recipient(address) is True
    assert validate_recipient("not-an-email") is False


async def test_inbox_claim_conflict_and_sla(db, tenant_a, tenant_b):
    production = await _production(db, tenant_a)
    call = await make_call(db, tenant_a)
    from app.db.models import InboxThreadState

    row = InboxThreadState(
        tenant_id=tenant_a.id, environment_id=production.id, call_id=call.id, version=1
    )
    db.add(row)
    await db.flush()
    claimed = await claim(
        db,
        tenant_id=tenant_a.id,
        environment_id=production.id,
        call_id=call.id,
        agent_id="agent-1",
    )
    assert claimed.assignee_id == "agent-1"
    assert claimed.version == 2
    with pytest.raises(Conflict):
        await claim(
            db,
            tenant_id=tenant_a.id,
            environment_id=production.id,
            call_id=call.id,
            agent_id="agent-2",
        )

    class Stale:
        def __init__(self, current):
            self.id = current.id
            self.version = 1

    with pytest.raises(Conflict):
        await compare_and_set(
            db,
            Stale(claimed),
            tenant_id=tenant_a.id,
            environment_id=production.id,
            values={"priority": "high"},
        )
    other = await _production(db, tenant_b)
    with pytest.raises(BoundaryDenied):
        await claim(
            db,
            tenant_id=tenant_b.id,
            environment_id=other.id,
            call_id=call.id,
            agent_id="agent-3",
        )
    await db.refresh(claimed)
    noted = await add_note(
        db,
        claimed,
        tenant_id=tenant_a.id,
        environment_id=production.id,
        body="internal",
        author="agent-1",
        at="2026-09-23T00:00:00+00:00",
    )
    started = await start(db, tenant_id=tenant_a.id, environment_id=production.id, call_id=call.id)
    from datetime import datetime, timedelta, timezone

    breached = await evaluate(db, started, now=datetime.now(timezone.utc) + timedelta(hours=48))
    assert noted.notes[-1]["body"] == "internal"
    assert breached.sla_state == "breached"


async def test_plaintext_webhook_secret_is_refused_in_production(monkeypatch, db, tenant_a):
    monkeypatch.setattr("app.auth.identity.secrets.encryption_available", lambda: False)
    monkeypatch.setattr(settings, "app_env", "production")
    from app.auth.identity.exceptions import IdentitySecretsUnavailable

    with pytest.raises(IdentitySecretsUnavailable):
        await create_subscription(
            db,
            tenant_id=tenant_a.id,
            endpoint="https://example.com/hooks",
            secret="plain",
            event_types=["lead.created"],
        )
