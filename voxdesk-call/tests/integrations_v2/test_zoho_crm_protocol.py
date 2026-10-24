"""Protocol-faithful offline tests for Zoho CRM v6 (multi-DC, batch upsert <=100, COQL, Deals, Calls, page_token sync)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations_v2.core.errors import ValidationError
from app.integrations_v2.core.provider_base import ConnectionRuntimeContext
from app.integrations_v2.crm.zoho_crm import ZohoCrmProvider
from tests.integrations_v2.conftest import make_protocol_http_client


@pytest.mark.asyncio
async def test_zoho_crm_v6_contacts_batch_upsert_coql_deals_calls_and_page_token_sync():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "www.zohoapis.in"
        assert req.headers.get("Authorization") == "Zoho-oauthtoken zcrm-in-tok"
        path = req.url.path
        if path == "/crm/v6/users":
            return httpx.Response(200, json={"users": [{"id": "u-1", "full_name": "Ops IN"}]})
        if path == "/crm/v6/Contacts/search":
            return httpx.Response(
                200,
                json={"data": [{"id": "zc-10", "First_Name": "Ravi", "Last_Name": "Patel", "Email": "ravi@acme.in"}]},
            )
        if path == "/crm/v6/Contacts/upsert":
            body = json.loads(req.content)
            assert len(body["data"]) <= 100
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"status": "success", "details": {"id": f"zc-{idx + 10}"}}
                        for idx in range(len(body["data"]))
                    ]
                },
            )
        if path == "/crm/v6/coql" and req.method == "POST":
            body = json.loads(req.content)
            assert body["select_query"].lower().startswith("select ")
            return httpx.Response(
                200,
                json={"data": [{"id": "zc-10", "Email": "ravi@acme.in"}], "info": {"more_records": False}},
            )
        if path == "/crm/v6/Deals" and req.method == "POST":
            return httpx.Response(
                201,
                json={"data": [{"status": "success", "details": {"id": "zdeal-55"}}]},
            )
        if path == "/crm/v6/Calls" and req.method == "POST":
            body = json.loads(req.content)["data"][0]
            assert body["Call_Duration"] == "02:05"
            return httpx.Response(
                201,
                json={"data": [{"status": "success", "details": {"id": "zact-99"}}]},
            )
        if path == "/crm/v6/Contacts" and req.method == "GET":
            pt = req.url.params.get("page_token")
            if not pt:
                return httpx.Response(
                    200,
                    json={
                        "data": [{"id": "1", "First_Name": "A", "Modified_Time": "2026-10-24T01:00:00+00:00"}],
                        "info": {"more_records": True, "next_page_token": "tok-2"},
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": [{"id": "2", "First_Name": "B", "Modified_Time": "2026-10-24T02:00:00+00:00"}],
                    "info": {"more_records": False},
                },
            )
        return httpx.Response(404)

    ctx = ConnectionRuntimeContext(
        tenant_id="t1",
        connection_id="c-zcrm",
        provider="zoho_crm",
        region="in",
        credentials={"access_token": "zcrm-in-tok"},
        config={"region": "in"},
    )
    provider = ZohoCrmProvider(ctx, http_client=make_protocol_http_client(handler))

    assert (await provider.health()).ok is True
    found = await provider.lookup_contact(email="ravi@acme.in")
    assert found is not None and found.external_id == "zc-10"

    upserted = await provider.upsert_contact(
        first_name="Ravi", last_name="Patel", email="ravi@acme.in"
    )
    assert upserted.external_id == "zc-10"

    batch_ids = await provider.upsert_contacts_batch(
        [
            {"First_Name": "A", "Last_Name": "One", "Email": "a@acme.in"},
            {"First_Name": "B", "Last_Name": "Two", "Email": "b@acme.in"},
        ]
    )
    assert batch_ids == ["zc-10", "zc-11"]

    with pytest.raises(ValidationError):
        await provider.upsert_contacts_batch([{"Last_Name": f"U{i}"} for i in range(101)])

    coql_res = await provider.coql_query("select id, Email from Contacts where Email is not null")
    assert len(coql_res["data"]) == 1 and coql_res["data"][0]["id"] == "zc-10"

    deal = await provider.create_or_update_deal(
        title="Enterprise Voice Seat", amount=24000, contact_id="zc-10"
    )
    assert deal.external_id == "zdeal-55"

    act_id = await provider.log_call_activity(
        {
            "subject": "Discovery Call",
            "duration_seconds": 125,
            "summary": "Qualified 50 seats",
            "external_contact_id": "zc-10",
        }
    )
    assert act_id == "zact-99"

    pages = [p async for p in provider.iter_updated_contacts()]
    assert len(pages) == 2
    assert [i.external_id for p in pages for i in p.items] == ["1", "2"]
