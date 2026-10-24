"""API-level tests for /api/v1/integrations/*, /api/v1/helpdesk/*, /api/v1/collab/*, /api/v1/agent-tools/* RBAC & tenant isolation."""

from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from app.api.integrations_v2_routes import get_int2_http_client
from app.db.integrations_v2_models import IntegrationConnection
from app.db.models import UserRole
from app.integrations_v2.core.connection_store import open_secret_bundle
from app.integrations_v2.core.errors import AuthExpired
from app.integrations_v2.settings import reset_int2_settings_cache
from tests.conftest import auth_headers, make_user
from tests.integrations_v2.conftest import make_protocol_http_client


@pytest.mark.asyncio
async def test_routes_rbac_tenant_isolation_idempotency_and_no_secret_leak(
    db, app, client, tenant_a, tenant_b, owner_a, viewer_a, monkeypatch
):
    for var in (
        "ZENDESK_CLIENT_ID",
        "ZENDESK_CLIENT_SECRET",
        "SERVICENOW_CLIENT_ID",
        "ZOHO_CLIENT_ID",
        "FRESHSALES_CLIENT_ID",
        "DYNAMICS_CLIENT_ID",
        "PIPEDRIVE_CLIENT_ID",
        "TEAMS_CLIENT_ID",
        "ZOOM_CLIENT_ID",
        "SLACK_CLIENT_ID",
        "FRESHDESK_DOMAIN",
    ):
        monkeypatch.delenv(var, raising=False)
    reset_int2_settings_cache()

    owner_b = await make_user(db, tenant_b, UserRole.OWNER)

    def fd_mock(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/v2/agents/me":
            return httpx.Response(200, json={"id": 99})
        return httpx.Response(200, json={})

    app.dependency_overrides[get_int2_http_client] = lambda: make_protocol_http_client(fd_mock)
    try:
        owner_a_hdrs = await auth_headers(client, owner_a)
        viewer_a_hdrs = await auth_headers(client, viewer_a)
        owner_b_hdrs = await auth_headers(client, owner_b)

        cat_resp = await client.get("/api/v1/integrations/catalog", headers=owner_a_hdrs)
        assert cat_resp.status_code == 200
        assert all(p["ui_status"] == "NOT_CONFIGURED" for p in cat_resp.json()["providers"])

        viewer_post = await client.post(
            "/api/v1/integrations/freshdesk/connect",
            headers=viewer_a_hdrs,
            json={
                "credentials": {"api_key": "secret-fd-key-999999"},
                "config": {"domain": "acme"},
            },
        )
        assert viewer_post.status_code == 403

        raw_api_key = "secret_fd_api_key_never_leak_7777"
        owner_post = await client.post(
            "/api/v1/integrations/freshdesk/connect",
            headers={**owner_a_hdrs, "Idempotency-Key": "idem-fd-connect-1"},
            json={
                "credentials": {"api_key": raw_api_key},
                "config": {"domain": "acme"},
                "verify_now": True,
            },
        )
        assert owner_post.status_code == 200, owner_post.text
        raw_resp_text = json.dumps(owner_post.json())
        assert raw_api_key not in raw_resp_text
        conn_id = owner_post.json()["connection"]["id"]

        replay_post = await client.post(
            "/api/v1/integrations/freshdesk/connect",
            headers={**owner_a_hdrs, "Idempotency-Key": "idem-fd-connect-1"},
            json={
                "credentials": {"api_key": raw_api_key},
                "config": {"domain": "acme"},
                "verify_now": True,
            },
        )
        assert replay_post.status_code == 200
        assert replay_post.json()["replayed"] is True
        assert replay_post.json()["connection"]["id"] == conn_id

        # Tenant B cannot see, test, or delete Tenant A's connection
        b_list = await client.get("/api/v1/integrations/connections", headers=owner_b_hdrs)
        assert b_list.status_code == 200
        assert b_list.json()["connections"] == []

        b_test = await client.post(
            f"/api/v1/integrations/connections/{conn_id}/test",
            headers=owner_b_hdrs,
        )
        assert b_test.status_code == 404

        b_delete = await client.delete(
            f"/api/v1/integrations/connections/{conn_id}",
            headers=owner_b_hdrs,
        )
        assert b_delete.status_code == 404

        # Dry-run endpoint executes zero vendor calls
        dry_resp = await client.post(
            "/api/v1/agent-tools/tools/helpdesk_create_ticket/dry-run",
            headers=owner_a_hdrs,
            json={"arguments": {"subject": "Test", "description": "Body"}},
        )
        assert dry_resp.status_code == 200
        assert dry_resp.json()["vendor_called"] is False
    finally:
        app.dependency_overrides.pop(get_int2_http_client, None)

    row_a = (
        await db.execute(
            select(IntegrationConnection).where(
                IntegrationConnection.tenant_id == tenant_a.id
            )
        )
    ).scalar_one()
    with pytest.raises(AuthExpired):
        open_secret_bundle(
            row_a.token_secret_ref,
            tenant_id=tenant_b.id,
            provider="freshdesk",
        )
