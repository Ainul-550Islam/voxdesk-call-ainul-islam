"""CRM writeback & Salesforce error semantics behavioural tests (Capability 12).

Verifies:
- Invalid input -> HTTP 422
- Unconfigured CRM / Salesforce integration -> HTTP 409 (NOT_CONFIGURED)
- Upstream provider failure -> HTTP 502 (UPSTREAM_PROVIDER_ERROR)
- Zero HTTP 501 responses anywhere in crm_writeback_routes.py or salesforce_routes.py
"""
from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import crm_writeback_routes, salesforce_routes
from app.db.models import Call, CallStatus, CrmIntegration, CrmProviderType, Environment
from app.integrations.crm import crypto
from app.integrations.crm.base import Capability
from app.integrations.crm.errors import CrmTransientError
from sqlalchemy import select
from tests.conftest import auth_headers


def test_zero_501_in_crm_and_salesforce_route_modules() -> None:
    for mod in (crm_writeback_routes, salesforce_routes):
        src = inspect.getsource(mod)
        assert "status_code=501" not in src
        assert "HTTP_501" not in src


@pytest.mark.asyncio
async def test_crm_writeback_422_invalid_409_unconfigured_502_upstream_and_zero_501(
    client: AsyncClient,
    db: AsyncSession,
    tenant_a,
    owner_a,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = await auth_headers(client, owner_a)
    env = await db.scalar(
        select(Environment).where(
            Environment.tenant_id == tenant_a.id,
            Environment.kind == "production",
        )
    )
    call = Call(
        tenant_id=tenant_a.id,
        environment_id=env.id if env else None,
        call_sid=f"CA{uuid.uuid4().hex[:20]}",
        from_number="+14155550111",
        to_number="+14155550199",
        status=CallStatus.COMPLETED,
    )
    db.add(call)
    await db.commit()
    await db.refresh(call)

    # 1. Invalid payload -> 422
    bad = await client.post(
        f"/api/crm/calls/{call.id}/disposition",
        headers=headers,
        json={},  # missing required outcome
    )
    assert bad.status_code == 422

    # 2. Unconfigured CRM -> 409 NOT_CONFIGURED (never 501)
    unconf = await client.post(
        f"/api/crm/calls/{call.id}/disposition",
        headers=headers,
        json={"outcome": "booked_demo", "notes": "Qualified prospect"},
    )
    assert unconf.status_code == 409
    assert unconf.json()["detail"]["code"] == "NOT_CONFIGURED"

    # 3. Configured CRM with upstream failure -> 502 UPSTREAM_PROVIDER_ERROR
    key_ring = crypto.key_ring_from_settings()
    envelope, key_id = crypto.encrypt_credentials(
        {"access_token": "pat-na1-test-token"},
        tenant_id=str(tenant_a.id),
        provider="hubspot",
        key_ring=key_ring,
    )
    integration = CrmIntegration(
        id=uuid.uuid4(),
        tenant_id=tenant_a.id,
        provider=CrmProviderType.HUBSPOT,
        is_enabled=True,
        credentials_encrypted=envelope,
        credentials_key_id=key_id,
        config={"portal_id": "12345"},
    )
    db.add(integration)
    await db.commit()

    class _FailingAdapter:
        capabilities = frozenset({Capability.CREATE_ACTIVITY, Capability.UPSERT_CONTACT})

        async def upsert_contact(self, *_a, **_kw):
            raise CrmTransientError("HubSpot 503 upstream gateway timeout")

    monkeypatch.setattr(
        crm_writeback_routes,
        "build_crm_provider",
        lambda *_a, **_kw: _FailingAdapter(),
    )

    upstream = await client.post(
        f"/api/crm/calls/{call.id}/disposition",
        headers=headers,
        json={"outcome": "booked_demo", "notes": "Qualified prospect", "provider": "hubspot"},
    )
    assert upstream.status_code == 502
    assert upstream.json()["detail"]["code"] == "UPSTREAM_PROVIDER_ERROR"


@pytest.mark.asyncio
async def test_salesforce_routes_422_409_502_and_zero_501(
    client: AsyncClient,
    tenant_a,
    owner_a,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headers = await auth_headers(client, owner_a)

    # 1. Invalid payload -> 422
    bad = await client.post(
        "/api/integrations/salesforce/connect",
        headers=headers,
        json={},  # missing required fields
    )
    assert bad.status_code == 422

    # 2. Unconfigured Salesforce -> 409 NOT_CONFIGURED (never 501)
    unconf = await client.get("/api/integrations/salesforce/api-limits", headers=headers)
    assert unconf.status_code == 409
    assert unconf.json()["detail"]["code"] == "NOT_CONFIGURED"

    # 3. Upstream Salesforce API failure -> 502 UPSTREAM_PROVIDER_ERROR
    class _FailingSfProvider:
        async def writeback_sobject(self, **_kw):
            raise CrmTransientError("Salesforce upstream 503")

    monkeypatch.setattr(
        salesforce_routes,
        "_build_provider",
        AsyncMock(return_value=(object(), _FailingSfProvider())),
    )

    wb = await client.post(
        "/api/integrations/salesforce/writeback",
        headers=headers,
        json={
            "entity_type": "lead",
            "action": "create",
            "fields": {"LastName": "Ada", "Company": "Analytical Engine"},
        },
    )
    assert wb.status_code == 502
    assert wb.json()["detail"]["code"] == "UPSTREAM_PROVIDER_ERROR"
