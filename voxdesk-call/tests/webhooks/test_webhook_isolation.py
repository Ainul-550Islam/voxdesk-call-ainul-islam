"""Repository boundary and transactional CRUD contracts, not HTTP API coverage."""
import pytest
from sqlalchemy import select

from app.db.models import Environment, WebhookDelivery
from app.tenancy.isolation import BoundaryDenied
from app.webhooks import repository as repo


async def production(db, tenant):
    return await db.scalar(select(Environment.id).where(
        Environment.tenant_id == tenant.id, Environment.kind == "production",
    ))


async def subscribe(db, tenant, environment=None):
    return await repo.create_subscription(
        db, tenant_id=tenant.id, environment_id=environment,
        endpoint="https://example.com/hooks", secret="test-only-secret",
        event_types=["call_ended"],
    )


@pytest.mark.asyncio
async def test_crud_and_rotation_are_durable_and_rollback_safe(db, tenant_a):
    row = await subscribe(db, tenant_a)
    row_id = row.id
    scope = dict(tenant_id=tenant_a.id, environment_id=None, subscription_id=row_id)
    await db.commit()
    await repo.update_subscription(db, **scope, enabled=False, event_types=["call_started"])
    await repo.rotate_secret(db, **scope, secret="new-test-secret")
    assert row.secret_version == 2
    assert repo.open_secret(tenant_a.id, row.secret_envelope) == "new-test-secret"
    await db.rollback()
    await db.refresh(row)
    assert row.enabled is True
    assert row.secret_version == 1
    assert row.event_types == ["call_ended"]
    await repo.rotate_secret(db, **scope, secret="committed-test-secret")
    await db.commit()
    db.expire_all()
    stored = await repo.get_subscription(db, **scope)
    assert stored.secret_version == 2
    assert repo.open_secret(scope["tenant_id"], stored.secret_envelope) == "committed-test-secret"
    await repo.delete_subscription(db, **scope)
    await db.commit()
    assert await repo.get_subscription(db, **scope) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["update", "rotate", "delete"])
async def test_mutation_conceals_cross_tenant_and_environment(db, tenant_a, tenant_b, action):
    env = await production(db, tenant_a)
    row = await subscribe(db, tenant_a, env)
    fn, args = {
        "update": (repo.update_subscription, {"enabled": False}),
        "rotate": (repo.rotate_secret, {"secret": "replacement-test-secret"}),
        "delete": (repo.delete_subscription, {}),
    }[action]
    for tenant_id, environment_id in [(tenant_b.id, env), (tenant_a.id, None)]:
        with pytest.raises(BoundaryDenied) as caught:
            await fn(db, tenant_id=tenant_id, environment_id=environment_id,
                     subscription_id=row.id, **args)
        assert caught.value.status_code == 404
    assert row.enabled
    assert row.secret_version == 1


@pytest.mark.asyncio
async def test_get_and_list_require_exact_supplied_scope(db, tenant_a, tenant_b):
    env = await production(db, tenant_a)
    row = await subscribe(db, tenant_a, env)
    assert await repo.get_subscription(db, tenant_id=tenant_b.id, subscription_id=row.id) is None
    assert await repo.get_subscription(db, tenant_id=tenant_a.id, environment_id=None,
                                       subscription_id=row.id) is None
    assert await repo.list_subscriptions(db, tenant_id=tenant_a.id, environment_id=None) == []
    assert await repo.list_subscriptions(db, tenant_id=tenant_b.id, environment_id=env) == []
    assert await repo.list_subscriptions(db, tenant_id=tenant_a.id, environment_id=env) == [row]


@pytest.mark.asyncio
async def test_cross_tenant_environment_cannot_be_attached(db, tenant_a, tenant_b):
    other = await production(db, tenant_b)
    with pytest.raises(BoundaryDenied):
        await subscribe(db, tenant_a, other)


@pytest.mark.asyncio
async def test_delivery_cannot_claim_another_subscription_or_environment(db, tenant_a, tenant_b):
    env = await production(db, tenant_a)
    row = await subscribe(db, tenant_a, env)
    for tenant_id, environment in [(tenant_b.id, env), (tenant_a.id, None)]:
        with pytest.raises(BoundaryDenied):
            await repo.create_delivery(db, tenant_id=tenant_id, environment_id=environment,
                                       subscription_id=row.id, event_id="evt-1")
    assert list((await db.scalars(select(WebhookDelivery))).all()) == []


@pytest.mark.asyncio
async def test_tenantwide_subscription_accepts_owned_environment_delivery(db, tenant_a, tenant_b):
    row = await subscribe(db, tenant_a)
    env = await production(db, tenant_a)
    args = dict(tenant_id=tenant_a.id, environment_id=env,
                subscription_id=row.id, event_id="event-1")
    delivery, created = await repo.create_delivery(db, **args)
    duplicate, repeated = await repo.create_delivery(db, **args)
    assert created and not repeated and delivery.id == duplicate.id
    with pytest.raises(BoundaryDenied):
        await repo.create_delivery(db, tenant_id=tenant_a.id,
                                   environment_id=await production(db, tenant_b),
                                   subscription_id=row.id, event_id="event-2")


@pytest.mark.asyncio
async def test_validation_does_not_partially_change_subscription(db, tenant_a):
    row = await subscribe(db, tenant_a)
    with pytest.raises(ValueError):
        await repo.update_subscription(db, tenant_id=tenant_a.id, environment_id=None,
                                       subscription_id=row.id, enabled=False, event_types=[""])
    assert row.enabled
    assert row.event_types == ["call_ended"]


@pytest.mark.asyncio
async def test_http_crud_idempotency_rotation_and_audit(client, db, owner_a, webhook_configuration):
    from tests.conftest import auth_headers
    from app.db.models import WebhookSubscription, AuditLog
    import uuid
    headers = await auth_headers(client, owner_a)
    key = {**headers, "Idempotency-Key": str(uuid.uuid4())}
    body = {"url": "https://8.8.8.8/hooks", "description": "Primary endpoint",
            "events": ["call_ended"], "secret": "customer-owned-signing-secret",
            "headers": {"X-Customer-Key": "private-customer-key"}, "timeout_seconds": 2}
    first = await client.post("/api/webhooks", json=body, headers=key)
    assert first.status_code == 201, first.text
    second = await client.post("/api/webhooks", json=body, headers=key)
    assert second.status_code == 201 and first.json()["id"] == second.json()["id"]
    conflict = await client.post("/api/webhooks", json={**body, "description": "Changed"}, headers=key)
    assert conflict.status_code == 409
    identifier = first.json()["id"]
    row = await db.get(WebhookSubscription, uuid.UUID(identifier))
    assert row.secret_envelope.startswith("v1.")
    assert "private-customer-key" not in row.headers_envelope
    assert first.json()["headers"] == {"X-Customer-Key": "***"}
    rotated = await client.post(f"/api/webhooks/{identifier}/rotate-secret", json={}, headers=headers)
    assert rotated.status_code == 200
    assert rotated.json()["secret_version"] == 2
    await db.refresh(row)
    assert repo.open_secret(owner_a.tenant_id, row.secret_envelope) == rotated.json()["secret"]
    changed = await client.patch(f"/api/webhooks/{identifier}", json={"is_active": False}, headers=headers)
    assert changed.status_code == 200 and changed.json()["is_active"] is False
    events = (await db.scalars(select(AuditLog.event_type).where(AuditLog.resource_id == identifier))).all()
    assert {"webhook.created", "webhook.updated", "webhook.secret_rotated"} <= set(events)
    deleted = await client.delete(f"/api/webhooks/{identifier}", headers=headers)
    assert deleted.status_code == 200
    assert (await client.get(f"/api/webhooks/{identifier}", headers=headers)).status_code == 404


FOREIGN_OPERATIONS = [
    ("GET", "", None), ("PATCH", "", {"description": "foreign"}), ("DELETE", "", None),
    ("POST", "/enable", None), ("POST", "/disable", None),
    ("POST", "/rotate-secret", {}), ("GET", "/events", None),
    ("PUT", "/events", ["call_ended"]), ("POST", "/events/call_ended", None),
    ("DELETE", "/events/call_ended", None), ("GET", "/deliveries", None),
    ("GET", "/deliveries/{delivery}", None), ("POST", "/deliveries/{delivery}/retry", None),
    ("POST", "/test", {}), ("POST", "/replay", {"delivery_id": "delivery"}),
    ("GET", "/dlq", None), ("POST", "/dlq/replay-all", None),
    ("GET", "/stats", None),
    ("POST", "/verify-signature?payload=x&timestamp=1&signature=x", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,suffix,body", FOREIGN_OPERATIONS)
async def test_each_endpoint_route_conceals_foreign_tenant(client, db, tenant_a, owner_b,
                                                          webhook_configuration, method, suffix, body):
    from tests.conftest import auth_headers
    import uuid
    row = await subscribe(db, tenant_a)
    await db.commit()
    delivery_id = str(uuid.uuid4())
    suffix = suffix.replace("{delivery}", delivery_id)
    if body == {"delivery_id": "delivery"}:
        body = {"delivery_id": delivery_id}
    response = await client.request(method, f"/api/webhooks/{row.id}{suffix}", json=body,
                                    headers=await auth_headers(client, owner_b))
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["enable", "disable", "delete"])
async def test_bulk_mutation_is_atomic_and_foreign_ids_are_404(client, db, tenant_a, tenant_b,
                                                             owner_a, webhook_configuration, action):
    from tests.conftest import auth_headers
    own = await subscribe(db, tenant_a)
    foreign = await subscribe(db, tenant_b)
    await db.commit()
    response = await client.post(f"/api/webhooks/bulk/{action}", json=[str(own.id), str(foreign.id)],
                                 headers=await auth_headers(client, owner_a))
    assert response.status_code == 404
    await db.refresh(own)
    assert own.enabled is True


@pytest.mark.asyncio
async def test_lists_summaries_receipts_do_not_leak_tenants(client, db, tenant_a, owner_b, webhook_configuration):
    from tests.conftest import auth_headers
    row = await subscribe(db, tenant_a)
    await db.commit()
    headers = await auth_headers(client, owner_b)
    for path in ["", "/stats/summary", "/health", "/deliveries/recent", "/idempotency/stats"]:
        response = await client.get("/api/webhooks" + path, headers=headers)
        assert response.status_code == 200, response.text
        assert str(row.id) not in response.text
        assert str(tenant_a.id) not in response.text
    response = await client.get("/api/webhooks", headers=headers)
    assert response.json()["total"] == 0


@pytest.mark.asyncio
async def test_audit_failure_rolls_back_api_creation(client, db, owner_a, webhook_configuration, monkeypatch):
    from app.api import webhook_lifecycle_routes as routes
    from app.db.models import WebhookSubscription
    from tests.conftest import auth_headers
    async def broken(*args, **kwargs):
        raise RuntimeError("audit storage failed")
    monkeypatch.setattr(routes, "record_event", broken)
    with pytest.raises(RuntimeError, match="audit storage failed"):
        await client.post("/api/webhooks", json={"url": "https://8.8.8.8/hooks"},
                          headers=await auth_headers(client, owner_a))
    assert list((await db.scalars(select(WebhookSubscription))).all()) == []


@pytest.mark.asyncio
async def test_viewer_cannot_write_and_unconfigured_secrets_fail_closed(client, owner_a, viewer_a,
                                                                      webhook_configuration, monkeypatch):
    from tests.conftest import auth_headers
    from app.core.config import settings
    body = {"url": "https://8.8.8.8/hooks"}
    forbidden = await client.post("/api/webhooks", json=body, headers=await auth_headers(client, viewer_a))
    assert forbidden.status_code == 403
    monkeypatch.setattr(settings, "identity_encryption_keys", "")
    monkeypatch.setattr(settings, "crm_encryption_keys", "")
    unavailable = await client.post("/api/webhooks", json=body, headers=await auth_headers(client, owner_a))
    assert unavailable.status_code == 501
    assert unavailable.json()["detail"]["code"] == "NOT_CONFIGURED"
