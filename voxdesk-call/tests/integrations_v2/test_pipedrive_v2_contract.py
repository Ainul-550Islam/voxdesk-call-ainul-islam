"""Contract and protocol tests for Pipedrive API v2 (/api/v2/persons, deals, activities, next_cursor) + opt-in sandbox check."""

from __future__ import annotations

import json
import os

import httpx
import pytest

from app.integrations_v2.core.provider_base import (
    Capability,
    ConnectionRuntimeContext,
    ProviderV2,
)
from app.integrations_v2.crm.pipedrive_v2 import PipedriveV2Provider
from tests.integrations_v2.conftest import make_protocol_http_client


@pytest.mark.asyncio
async def test_pipedrive_v2_provider_contract_and_cursor_sync():
    assert issubclass(PipedriveV2Provider, ProviderV2)
    assert Capability.CONTACTS_READ in PipedriveV2Provider.capabilities
    assert Capability.CALL_LOG_WRITE in PipedriveV2Provider.capabilities
    assert Capability.INCREMENTAL_SYNC in PipedriveV2Provider.capabilities

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "acme.pipedrive.com"
        assert req.headers.get("Authorization") == "Bearer pd-tok"
        path = req.url.path
        if path == "/v1/users/me":
            return httpx.Response(200, json={"data": {"id": 12, "company_id": 99, "company_name": "Acme PD"}})
        if path == "/api/v2/persons/search":
            return httpx.Response(200, json={"data": {"items": []}})
        if path == "/api/v2/persons" and req.method == "POST":
            body = json.loads(req.content)
            return httpx.Response(
                201,
                json={"data": {"id": 321, "first_name": body["first_name"], "last_name": body["last_name"]}},
            )
        if path == "/api/v2/deals" and req.method == "POST":
            body = json.loads(req.content)
            assert body["person_id"] == 321
            return httpx.Response(
                201,
                json={"data": {"id": 654, "title": body["title"], "value": body["value"], "currency": "USD"}},
            )
        if path == "/api/v2/activities" and req.method == "POST":
            body = json.loads(req.content)
            assert body["type"] == "call" and body["done"] is True
            return httpx.Response(201, json={"data": {"id": 777}})
        if path == "/api/v2/persons" and req.method == "GET":
            cur = req.url.params.get("cursor")
            if not cur:
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": 1, "first_name": "P1", "update_time": "2026-10-24T01:00:00Z"}],
                        "additional_data": {"next_cursor": "c2"},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": [{"id": 2, "first_name": "P2", "update_time": "2026-10-24T02:00:00Z"}],
                    "additional_data": {"next_cursor": None},
                },
            )
        return httpx.Response(404)

    ctx = ConnectionRuntimeContext(
        tenant_id="t1",
        connection_id="c-pd",
        provider="pipedrive",
        credentials={"access_token": "pd-tok", "api_domain": "https://acme.pipedrive.com"},
    )
    provider = PipedriveV2Provider(ctx, http_client=make_protocol_http_client(handler))

    assert (await provider.health()).account_id == "99"
    p = await provider.upsert_contact(first_name="Linus", last_name="Torvalds", email="linus@kernel.org")
    assert p.external_id == "321"

    d = await provider.create_or_update_deal(title="Enterprise Support", amount=12000, contact_id="321")
    assert d.external_id == "654"

    act = await provider.log_call_activity(
        {"external_contact_id": "321", "duration_seconds": 180, "summary": "Demo call completed"}
    )
    assert act == "777"

    pages = [b async for b in provider.iter_updated_contacts()]
    assert len(pages) == 2
    assert [i.external_id for b in pages for i in b.items] == ["1", "2"]


@pytest.mark.live
@pytest.mark.asyncio
async def test_live_pipedrive_v2_sandbox():
    token = os.getenv("PIPEDRIVE_LIVE_API_TOKEN", "").strip()
    if not token:
        pytest.skip("Set PIPEDRIVE_LIVE_API_TOKEN to run")

    ctx = ConnectionRuntimeContext(
        tenant_id="live-test",
        connection_id="live-pd",
        provider="pipedrive",
        auth_kind="api_key",
        credentials={"api_token": token},
    )
    provider = PipedriveV2Provider(ctx)
    health = await provider.health()
    assert health.ok is True
