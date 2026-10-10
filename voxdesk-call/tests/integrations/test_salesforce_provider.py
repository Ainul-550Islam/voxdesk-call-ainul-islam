"""Unit and route tests for SalesforceProvider and CRM write-back consolidation (Part 1F / Gate G1)."""
from __future__ import annotations

import base64
import json
import os
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.api import crm_writeback_routes, salesforce_routes
from app.auth.dependencies import TenantContext
from app.core.config import settings
from app.db.enterprise_models import (
    CrmWritebackLog,
    SalesforceConnection,
)
from app.db.models import Call, CrmIntegration, CrmProviderType, Tenant, User, UserRole
from app.integrations.crm.base import ProviderContext
from app.integrations.crm.errors import (
    CrmConfigurationError,
    CrmRateLimited,
)
from app.integrations.crm.mapping import (
    MappingError,
    validate_field_mappings_against_describe,
)
from app.integrations.crm.models import NormalizedActivity, NormalizedContact
from app.integrations.crm.providers.salesforce import (
    SalesforceProvider,
    build_authorization_url,
    escape_soql_literal,
    generate_pkce_pair,
    validate_salesforce_instance_url,
)


def _make_provider(
    handler,
    monkeypatch,
    *,
    credentials: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> SalesforceProvider:
    transport = httpx.MockTransport(handler)
    original_init = httpx.AsyncClient.__init__

    def _patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        return original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _patched_init)
    creds = dict(
        credentials
        or {
            "access_token": "sf-access-token-initial",
            "refresh_token": "sf-refresh-token-secret",
            "client_id": "sf-client-id",
            "client_secret": "sf-client-secret",
        }
    )
    cfg = dict(config or {"instance_url": "https://acme.my.salesforce.com"})
    return SalesforceProvider(
        ProviderContext(
            tenant_id=str(uuid.uuid4()),
            credentials=creds,
            config=cfg,
            field_mappings={},
        )
    )


def test_soql_injection_prevention_escapes_quotes_and_backslashes():
    payload = "O'Reilly' OR Name != '' -- \\ \n\r\t"
    escaped = escape_soql_literal(payload)
    assert "\\'" in escaped
    assert "\\\\" in escaped
    assert "\n" not in escaped
    assert "\r" not in escaped
    # Any single quote in escaped must be preceded by a backslash
    for idx, ch in enumerate(escaped):
        if ch == "'":
            assert idx > 0 and escaped[idx - 1] == "\\"


def test_instance_url_ssrf_and_domain_validation():
    assert (
        validate_salesforce_instance_url("https://acme.my.salesforce.com/")
        == "https://acme.my.salesforce.com"
    )
    with pytest.raises(CrmConfigurationError):
        validate_salesforce_instance_url("http://acme.my.salesforce.com")
    with pytest.raises(CrmConfigurationError):
        validate_salesforce_instance_url("https://169.254.169.254")
    with pytest.raises(CrmConfigurationError):
        validate_salesforce_instance_url("https://evil.example.com")


@pytest.mark.asyncio
async def test_oauth_pkce_and_code_exchange(monkeypatch):
    verifier, challenge = generate_pkce_pair()
    assert len(verifier) >= 40
    assert len(challenge) >= 40
    url = build_authorization_url(
        client_id="cid-123",
        redirect_uri="https://app.voxdesk.ai/oauth/sf/callback",
        state="state-xyz",
        code_challenge=challenge,
    )
    assert "code_challenge_method=S256" in url
    assert f"code_challenge={challenge}" in url

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "00Dxx0000001gPL!fresh",
                "refresh_token": "5Aep861TSESvWeug_refresh",
                "instance_url": "https://acme.my.salesforce.com",
                "token_type": "Bearer",
                "scope": "api refresh_token openid",
            },
        )

    provider = _make_provider(handler, monkeypatch, credentials={"access_token": "unused"})
    token_data = await provider.exchange_authorization_code(
        code="aPrx_auth_code",
        client_id="cid-123",
        client_secret="csec-456",
        redirect_uri="https://app.voxdesk.ai/oauth/sf/callback",
        code_verifier=verifier,
    )
    assert token_data["access_token"] == "00Dxx0000001gPL!fresh"
    assert len(captured) == 1
    assert captured[0].url.params["code_verifier"] == verifier
    assert captured[0].url.params["grant_type"] == "authorization_code"


@pytest.mark.asyncio
async def test_automatic_token_refresh_on_401(monkeypatch):
    calls: list[tuple[str, str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        auth = request.headers.get("Authorization", "")
        calls.append((request.method, request.url.path, auth))
        if request.url.path.endswith("/services/oauth2/token"):
            return httpx.Response(
                200,
                json={
                    "access_token": "sf-access-token-refreshed",
                    "instance_url": "https://acme.my.salesforce.com",
                },
            )
        if auth == "Bearer sf-access-token-initial":
            return httpx.Response(
                401,
                json=[{"errorCode": "INVALID_SESSION_ID", "message": "Session expired"}],
            )
        return httpx.Response(201, json={"id": "003xx000004TmiQAAS", "success": True})

    provider = _make_provider(handler, monkeypatch)
    res = await provider.create_contact(
        NormalizedContact(first_name="Ada", last_name="Lovelace", phone="+15550109999")
    )
    assert res.external_id == "003xx000004TmiQAAS"
    assert provider.context.credentials["access_token"] == "sf-access-token-refreshed"
    assert [c[1] for c in calls] == [
        "/services/data/v59.0/sobjects/Contact",
        "/services/oauth2/token",
        "/services/data/v59.0/sobjects/Contact",
    ]


@pytest.mark.asyncio
async def test_contact_upsert_and_call_task_logging_and_case_and_opportunity(monkeypatch):
    requests_log: list[ tuple[str, str, dict[str, Any]] ] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode()) if request.content else {}
        requests_log.append((request.method, request.url.path, body))
        if request.url.path.endswith("/query"):
            q = request.url.params.get("q", "")
            if "ada@example.com" in q:
                return httpx.Response(
                    200,
                    json={
                        "totalSize": 1,
                        "done": True,
                        "records": [{"Id": "003EXISTING0001"}],
                    },
                )
            return httpx.Response(200, json={"totalSize": 0, "done": True, "records": []})
        if request.method == "PATCH":
            return httpx.Response(204)
        if request.url.path.endswith("/sobjects/Task"):
            return httpx.Response(201, json={"id": "00TTASK00000001", "success": True})
        if request.url.path.endswith("/sobjects/Case"):
            return httpx.Response(201, json={"id": "500CASE00000001", "success": True})
        if request.url.path.endswith("/sobjects/OpportunityContactRole"):
            return httpx.Response(201, json={"id": "00KOCR000000001", "success": True})
        return httpx.Response(201, json={"id": "003NEW000000001", "success": True})

    provider = _make_provider(handler, monkeypatch)

    # 1. Upsert existing contact
    upsert_res = await provider.upsert_contact(
        NormalizedContact(
            first_name="Ada",
            last_name="Lovelace",
            email="ada@example.com",
            phone="+15550101234",
        )
    )
    assert upsert_res.external_id == "003EXISTING0001"
    assert upsert_res.already_existed is True

    # 2. Log call as Salesforce Task with Subject, Description, CallDurationInSeconds, CallType, WhoId
    task_res = await provider.create_activity(
        "003EXISTING0001",
        NormalizedActivity(
            title="Inbound Support Call",
            body="Customer requested billing statement.",
            duration_seconds=142,
            attributes={"direction": "inbound", "disposition": "resolved"},
        ),
    )
    assert task_res.external_id == "00TTASK00000001"
    task_req = next(r for r in requests_log if r[1].endswith("/sobjects/Task"))
    assert task_req[2]["Subject"] == "Inbound Support Call"
    assert task_req[2]["Description"] == "Customer requested billing statement."
    assert task_req[2]["CallDurationInSeconds"] == 142
    assert task_req[2]["CallType"] == "Inbound"
    assert task_req[2]["WhoId"] == "003EXISTING0001"

    # 3. Create Case and link Opportunity
    case_res = await provider.create_case(
        subject="Billing inquiry",
        description="Needs invoice copy",
        contact_id="003EXISTING0001",
    )
    assert case_res.external_id == "500CASE00000001"

    opp_res = await provider.link_opportunity(
        opportunity_id="006OPP000000001",
        contact_id="003EXISTING0001",
    )
    assert opp_res.external_id == "00KOCR000000001"


@pytest.mark.asyncio
async def test_request_limit_exceeded_maps_to_rate_limited(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json=[
                {
                    "errorCode": "REQUEST_LIMIT_EXCEEDED",
                    "message": "TotalRequests Limit exceeded.",
                }
            ],
        )

    provider = _make_provider(handler, monkeypatch)
    with pytest.raises(CrmRateLimited) as exc_info:
        await provider.get_limits()
    assert exc_info.value.retryable is True


def test_validate_field_mappings_against_describe():
    describe_meta = {
        "fields": [
            {"name": "Phone", "updateable": True, "createable": True},
            {"name": "Description", "updateable": True, "createable": True},
            {"name": "CreatedDate", "updateable": False, "createable": False},
        ]
    }
    valid = validate_field_mappings_against_describe(
        {"Phone": "call_direction", "Description": "call_summary"},
        describe_metadata=describe_meta,
    )
    assert valid == {"Phone": "call_direction", "Description": "call_summary"}

    with pytest.raises(MappingError, match="does not exist"):
        validate_field_mappings_against_describe(
            {"NonExistent__c": "call_direction"},
            describe_metadata=describe_meta,
        )

    with pytest.raises(MappingError, match="read-only"):
        validate_field_mappings_against_describe(
            {"CreatedDate": "call_direction"},
            describe_metadata=describe_meta,
        )


@pytest.mark.asyncio
async def test_salesforce_and_crm_writeback_routes_persist_real_external_id(
    db, monkeypatch
):
    raw_key = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    monkeypatch.setattr(settings, "crm_encryption_keys", f"k1:{raw_key}")

    tenant = Tenant(
        id=uuid.uuid4(),
        name="SF Route Corp",
        twilio_number=f"+1555{uuid.uuid4().int % 10000000:07d}",
    )
    user = User(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        email=f"sf-{uuid.uuid4().hex[:6]}@example.com",
        password_hash="x",
        role=UserRole.ADMIN,
        is_active=True,
    )
    call = Call(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        call_sid=f"CA{uuid.uuid4().hex[:32]}",
        from_number="+15550108888",
        to_number="+15550199999",
        direction="inbound",
        status="completed",
        duration_seconds=95,
        summary="Caller asked for product demo.",
    )
    db.add_all([tenant, user, call])
    await db.commit()

    ctx = TenantContext(
        tenant=tenant,
        user=user,
    )

    # Connect Salesforce via route
    conn_out = await salesforce_routes.connect_salesforce(
        payload=salesforce_routes.SalesforceConnectRequest(
            instance_url="https://acme.my.salesforce.com",
            access_token="00Dxx0000001gPL!real_token_value",
            refresh_token="5Aep861TSESvWeug_refresh_value",
        ),
        ctx=ctx,
        session=db,
        x_idempotency_key="sf-conn-idem-001",
    )
    assert conn_out.instance_url == "https://acme.my.salesforce.com"

    # Verify both SalesforceConnection and CrmIntegration(SALESFORCE) were created and encrypted
    sf_conn = (
        await db.execute(
            select(SalesforceConnection).where(SalesforceConnection.tenant_id == tenant.id)
        )
    ).scalar_one()
    crm_int = (
        await db.execute(
            select(CrmIntegration).where(
                CrmIntegration.tenant_id == tenant.id,
                CrmIntegration.provider == CrmProviderType.SALESFORCE,
            )
        )
    ).scalar_one()
    assert sf_conn.crm_integration_id == crm_int.id
    assert "00Dxx0000001gPL" not in (sf_conn.access_token_encrypted or "")

    # Mock SalesforceProvider HTTP calls for crm_writeback_routes
    async def fake_request(self, method: str, url: str, **kwargs):
        if url.endswith("/query"):
            return 200, {"totalSize": 1, "done": True, "records": [{"Id": "003SFCONTACT01"}]}
        if method == "PATCH":
            return 204, {}
        if url.endswith("/sobjects/Task"):
            return 201, {"id": "00TSFTASK00099", "success": True}
        return 201, {"id": "003SFCONTACT01", "success": True}

    monkeypatch.setattr(SalesforceProvider, "request", fake_request)

    wb_out = await crm_writeback_routes.writeback_disposition(
        call_id=call.id,
        payload=crm_writeback_routes.DispositionWriteback(
            provider="salesforce",
            outcome="demo_booked",
            notes="Scheduled product walkthrough",
            idempotency_key="wb-disp-idem-001",
        ),
        ctx=ctx,
        session=db,
        x_idempotency_key=None,
    )
    assert wb_out.status == "completed"
    assert wb_out.entity_id == "00TSFTASK00099"

    log_row = await db.get(CrmWritebackLog, uuid.UUID(wb_out.id))
    assert log_row is not None
    assert log_row.entity_id == "00TSFTASK00099"
    assert log_row.status == "completed"
