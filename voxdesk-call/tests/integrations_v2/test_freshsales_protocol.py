"""Protocol-faithful offline tests for Freshsales Suite CRM (/crm/sales/api/*)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations_v2.core.provider_base import ConnectionRuntimeContext
from app.integrations_v2.crm.freshsales import FreshsalesProvider
from tests.integrations_v2.conftest import make_protocol_http_client


@pytest.mark.asyncio
async def test_freshsales_token_header_lookup_upsert_deals_and_phone_calls():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "acme.myfreshworks.com"
        assert req.headers.get("Authorization") == "Token token=fs-api-key-123"
        path = req.url.path
        if path == "/crm/sales/api/selector/owners":
            return httpx.Response(200, json={"users": [{"id": 7, "display_name": "Sales Owner"}]})
        if path == "/crm/sales/api/lookup":
            return httpx.Response(
                200,
                json={"contacts": {"contacts": [{"id": 404, "first_name": "Maya", "last_name": "Lin", "email": "maya@acme.com"}]}},
            )
        if path == "/crm/sales/api/contacts/upsert":
            body = json.loads(req.content)
            assert body["unique_identifier"] == {"emails": "maya@acme.com"}
            return httpx.Response(200, json={"contact": {"id": 404, "first_name": "Maya", "last_name": "Lin"}})
        if path == "/crm/sales/api/deals":
            return httpx.Response(201, json={"deal": {"id": 808, "name": "Expansion", "amount": 9500}})
        if path == "/crm/sales/api/phone_calls":
            return httpx.Response(201, json={"phone_call": {"id": 999}})
        return httpx.Response(404)

    ctx = ConnectionRuntimeContext(
        tenant_id="t1",
        connection_id="c-fs",
        provider="freshsales",
        auth_kind="api_key",
        credentials={"api_key": "fs-api-key-123"},
        config={"domain": "acme"},
    )
    provider = FreshsalesProvider(ctx, http_client=make_protocol_http_client(handler))

    assert (await provider.health()).account_id == "7"
    c = await provider.lookup_contact(email="maya@acme.com")
    assert c is not None and c.external_id == "404"

    u = await provider.upsert_contact(first_name="Maya", last_name="Lin", email="maya@acme.com")
    assert u.external_id == "404"

    d = await provider.create_or_update_deal(title="Expansion", amount=9500, contact_id="404")
    assert d.external_id == "808"

    call_log_id = await provider.log_call_activity(
        {"external_contact_id": "404", "duration_seconds": 75, "summary": "Pricing review"}
    )
    assert call_log_id == "999"
