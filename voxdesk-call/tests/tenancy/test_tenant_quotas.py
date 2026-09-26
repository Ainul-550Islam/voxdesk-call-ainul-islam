"""Quota checks stay on the existing resolver. The race uses one UPDATE."""

from __future__ import annotations

import asyncio

import pytest

from app.db.models import Tenant
from app.quotas.models import QuotaKey
from app.quotas.service import set_quota
from app.tenancy.limits import inspect
from app.tenancy.quota import compare_and_reserve
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


async def test_negative_unknown_and_hard_limit(client, db, tenant_a, owner_a):
    headers = await auth_headers(client, owner_a)
    negative = await client.post(
        f"/api/tenants/{tenant_a.id}/usage/check",
        json={"key": "users", "used": -1},
        headers=headers,
    )
    assert negative.status_code == 422, negative.text

    unknown = await client.post(
        f"/api/tenants/{tenant_a.id}/usage/check",
        json={"key": "not-a-quota"},
        headers=headers,
    )
    assert unknown.status_code == 422, unknown.text

    unset = await inspect(db, tenant_a, QuotaKey.WEBHOOK_EVENTS_PER_MINUTE, used=1)
    assert unset.mode == "unknown"
    assert unset.allowed is True
    assert unset.limit is None

    await set_quota(
        db,
        scope_kind="tenant",
        scope_id=tenant_a.id,
        key=QuotaKey.USERS,
        mode="hard",
        limit_value=1,
    )
    denied = await client.post(
        f"/api/tenants/{tenant_a.id}/usage/check",
        json={"key": "users", "used": 2},
        headers=headers,
    )
    assert denied.status_code == 200, denied.text
    body = denied.json()
    assert body["allowed"] is False
    assert body["source"] in {"hierarchy", "billing"}
    assert body["limit"] == 1
    summary = await client.get(f"/api/tenants/{tenant_a.id}/usage", headers=headers)
    assert summary.status_code == 200, summary.text
    assert summary.json()["tenant_id"] == str(tenant_a.id)
    assert any(item["key"] == "users" for item in summary.json()["decisions"])


async def test_foreign_tenant_usage_is_not_found(client, tenant_a, tenant_b, owner_a):
    headers = await auth_headers(client, owner_a)
    response = await client.get(f"/api/tenants/{tenant_b.id}/usage", headers=headers)
    assert response.status_code == 404, response.text
    forged = await client.post(
        f"/api/tenants/{tenant_a.id}/usage/check",
        json={"key": "users", "used": 0, "tenant_id": str(tenant_b.id)},
        headers=headers,
    )
    assert forged.status_code == 404, forged.text


async def test_compare_and_reserve_allows_one_winner(concurrent_sessionmaker):
    async with concurrent_sessionmaker() as db:
        tenant = Tenant(
            name="Quota Race",
            twilio_number="+15550001111",
            max_call_attempts=0,
        )
        db.add(tenant)
        await db.commit()
        tenant_id = tenant.id

    async def once() -> bool:
        async with concurrent_sessionmaker() as db:
            won = await compare_and_reserve(
                db,
                tenant_id,
                counter="max_call_attempts",
                limit=1,
                adding=1,
            )
            await db.commit()
            return won

    results = await asyncio.gather(once(), once())
    assert results.count(True) == 1
    assert results.count(False) == 1
    async with concurrent_sessionmaker() as db:
        stored = await db.get(Tenant, tenant_id)
        assert stored is not None
        assert stored.max_call_attempts == 1
