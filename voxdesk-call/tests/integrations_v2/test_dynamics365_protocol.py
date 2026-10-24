"""Protocol-faithful offline tests for Microsoft Dynamics 365 Dataverse Web API v9.2 (OData v4 + alternate-key upsert + @odata.nextLink)."""

from __future__ import annotations

import json

import httpx
import pytest

from app.integrations_v2.core.provider_base import ConnectionRuntimeContext
from app.integrations_v2.crm.dynamics365 import Dynamics365Provider
from tests.integrations_v2.conftest import make_protocol_http_client


@pytest.mark.asyncio
async def test_dynamics365_whoami_odata_alternate_key_upsert_opportunities_phonecalls_and_nextlink():
    contact_guid = "11111111-2222-3333-4444-555555555555"
    opp_guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    call_guid = "99999999-8888-7777-6666-555555555555"

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "contoso.crm.dynamics.com"
        assert req.headers.get("OData-Version") == "4.0"
        path = req.url.path
        if path == "/api/data/v9.2/WhoAmI()":
            return httpx.Response(200, json={"OrganizationId": "org-guid-1", "UserId": "user-guid-1"})
        if path == "/api/data/v9.2/contacts" and req.method == "GET":
            if req.url.params.get("$top") == "1":
                return httpx.Response(200, json={"value": []})
            if req.url.params.get("page") == "2":
                return httpx.Response(
                    200,
                    json={"value": [{"contactid": "c-2", "firstname": "Two", "modifiedon": "2026-10-24T02:00:00Z"}]},
                )
            return httpx.Response(
                200,
                json={
                    "value": [{"contactid": "c-1", "firstname": "One", "modifiedon": "2026-10-24T01:00:00Z"}],
                    "@odata.nextLink": "https://contoso.crm.dynamics.com/api/data/v9.2/contacts?page=2",
                },
            )
        if path.startswith("/api/data/v9.2/contacts(emailaddress1=") and req.method == "PATCH":
            return httpx.Response(
                200,
                json={"contactid": contact_guid, "firstname": "AltKey", "emailaddress1": "alt@contoso.com"},
            )
        if path == "/api/data/v9.2/contacts" and req.method == "POST":
            return httpx.Response(
                204,
                headers={"OData-EntityId": f"https://contoso.crm.dynamics.com/api/data/v9.2/contacts({contact_guid})"},
            )
        if path == "/api/data/v9.2/opportunities" and req.method == "POST":
            body = json.loads(req.content)
            assert body["parentcontactid@odata.bind"] == f"/contacts({contact_guid})"
            return httpx.Response(201, json={"opportunityid": opp_guid, "name": body["name"]})
        if path == "/api/data/v9.2/phonecalls" and req.method == "POST":
            body = json.loads(req.content)
            assert body["regardingobjectid_contact@odata.bind"] == f"/contacts({contact_guid})"
            return httpx.Response(201, json={"activityid": call_guid})
        return httpx.Response(404)

    ctx = ConnectionRuntimeContext(
        tenant_id="t1",
        connection_id="c-dyn",
        provider="dynamics365",
        instance_url="https://contoso.crm.dynamics.com",
        credentials={"access_token": "entra-jwt"},
    )
    provider = Dynamics365Provider(ctx, http_client=make_protocol_http_client(handler))

    assert (await provider.health()).account_id == "org-guid-1"
    created_c = await provider.upsert_contact(
        first_name="Satya", last_name="Test", email="satya@contoso.com"
    )
    assert created_c.external_id == contact_guid

    alt_c = await provider.upsert_contact_by_alternate_key(
        key_name="emailaddress1",
        key_value="alt@contoso.com",
        first_name="AltKey",
        last_name="Contact",
    )
    assert alt_c.external_id == contact_guid

    opp = await provider.create_or_update_deal(
        title="Contoso Global Rollout", amount=150000, contact_id=contact_guid
    )
    assert opp.external_id == opp_guid

    act = await provider.log_call_activity(
        {"external_contact_id": contact_guid, "duration_seconds": 300, "summary": "Executive sponsor call"}
    )
    assert act == call_guid

    pages = [p async for p in provider.iter_updated_contacts()]
    assert len(pages) == 2
    assert [i.external_id for p in pages for i in p.items] == ["c-1", "c-2"]
