"""Offline protocol tests for OAuth2 + PKCE flow, state TTL/tenant binding, single-flight refresh, rotation, revoke, and encrypted connection store."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.db.integrations_v2_models import IntegrationConnection
from app.db.models import UserRole
from app.integrations_v2.core.connection_store import (
    load_runtime_context,
    mask_connection,
    open_secret_bundle,
    seal_secret_bundle,
    upsert_connection,
)
from app.integrations_v2.core.errors import AuthExpired
from app.integrations_v2.core.oauth2 import (
    exchange_oauth_callback,
    refresh_connection_token,
    revoke_connection_tokens,
    start_oauth_flow,
)
from app.integrations_v2.settings import IntegrationsV2Settings, reset_int2_settings_cache
from tests.conftest import make_tenant, make_user
from tests.integrations_v2.conftest import make_protocol_http_client


def test_settings_validator_refuses_empty_secret_when_provider_enabled(monkeypatch):
    monkeypatch.setenv("ZENDESK_ENABLED", "true")
    monkeypatch.setenv("ZENDESK_CLIENT_ID", "zd-cid")
    monkeypatch.setenv("ZENDESK_CLIENT_SECRET", "")
    with pytest.raises(ValueError, match="zendesk"):
        IntegrationsV2Settings()


@pytest.mark.asyncio
async def test_oauth2_pkce_state_exchange_refresh_and_revoke(db, monkeypatch):
    monkeypatch.setenv("PIPEDRIVE_CLIENT_ID", "pd-client-id")
    monkeypatch.setenv("PIPEDRIVE_CLIENT_SECRET", "pd-super-secret-value")
    monkeypatch.setenv("PIPEDRIVE_REDIRECT_URI", "https://app.voxdesk.ai/oauth/pipedrive")
    reset_int2_settings_cache()

    tenant_a = await make_tenant(db, "OAuth Tenant A")
    tenant_b = await make_tenant(db, "OAuth Tenant B")
    user_a = await make_user(db, tenant_a, UserRole.OWNER)

    auth_url, _ = await start_oauth_flow(
        db,
        tenant_id=tenant_a.id,
        user_id=user_a.id,
        provider="pipedrive",
    )
    await db.commit()

    parsed_qs = parse_qs(urlsplit(auth_url).query)
    state_param = parsed_qs["state"][0]
    sent_challenge = parsed_qs["code_challenge"][0]
    assert parsed_qs["code_challenge_method"] == ["S256"]

    revoked_calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        if str(req.url) == "https://oauth.pipedrive.com/oauth/token":
            form = parse_qs(req.content.decode("utf-8"))
            if form.get("grant_type") == ["authorization_code"]:
                verifier = form["code_verifier"][0]
                expected_challenge = (
                    base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest())
                    .decode("ascii")
                    .rstrip("=")
                )
                assert expected_challenge == sent_challenge
                return httpx.Response(
                    200,
                    json={
                        "access_token": "pd-access-1",
                        "refresh_token": "pd-refresh-1",
                        "expires_in": 3600,
                        "api_domain": "https://acme.pipedrive.com",
                    },
                )
        if str(req.url) == "https://oauth.pipedrive.com/oauth/revoke":
            revoked_calls.append(req.content.decode("utf-8"))
            return httpx.Response(200, json={"success": True})
        return httpx.Response(404)

    http_client = make_protocol_http_client(handler)

    with pytest.raises(AuthExpired):
        await exchange_oauth_callback(
            db,
            tenant_id=tenant_b.id,
            provider="pipedrive",
            state_token=state_param,
            code="auth-code-1",
            http_client=http_client,
        )

    token_payload, _ = await exchange_oauth_callback(
        db,
        tenant_id=tenant_a.id,
        provider="pipedrive",
        state_token=state_param,
        code="auth-code-1",
        http_client=http_client,
    )
    assert token_payload["access_token"] == "pd-access-1"

    with pytest.raises(AuthExpired):
        await exchange_oauth_callback(
            db,
            tenant_id=tenant_a.id,
            provider="pipedrive",
            state_token=state_param,
            code="auth-code-1",
            http_client=http_client,
        )

    conn = await upsert_connection(
        db,
        tenant_id=tenant_a.id,
        provider="pipedrive",
        category="crm",
        auth_kind="oauth2",
        credentials={
            "access_token": token_payload["access_token"],
            "refresh_token": token_payload["refresh_token"],
        },
        expires_in=3600,
    )
    await db.commit()

    revoked = await revoke_connection_tokens(conn, http_client=http_client)
    assert revoked is True
    assert len(revoked_calls) == 1
    assert "pd-access-1" in revoked_calls[0]


@pytest.mark.asyncio
async def test_expired_oauth_state_rejected(db, monkeypatch):
    monkeypatch.setenv("SLACK_CLIENT_ID", "sl-cid")
    monkeypatch.setenv("SLACK_CLIENT_SECRET", "sl-secret-123456")
    monkeypatch.setenv("SLACK_REDIRECT_URI", "https://app.voxdesk.ai/oauth/slack")
    reset_int2_settings_cache()

    tenant = await make_tenant(db, "Expired State Tenant")
    user = await make_user(db, tenant, UserRole.ADMIN)

    auth_url, state_row = await start_oauth_flow(
        db,
        tenant_id=tenant.id,
        user_id=user.id,
        provider="slack",
    )
    state_row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    await db.commit()

    state_param = parse_qs(urlsplit(auth_url).query)["state"][0]
    http_client = make_protocol_http_client(lambda _r: httpx.Response(200, json={"ok": True}))

    with pytest.raises(AuthExpired):
        await exchange_oauth_callback(
            db,
            tenant_id=tenant.id,
            provider="slack",
            state_token=state_param,
            code="some-code",
            http_client=http_client,
        )


@pytest.mark.asyncio
async def test_single_flight_concurrent_token_refresh_coalesces_requests(db, monkeypatch):
    monkeypatch.setenv("ZOHO_CLIENT_ID", "zoho-cid")
    monkeypatch.setenv("ZOHO_CLIENT_SECRET", "zoho-secret-abcdef")
    monkeypatch.setenv("ZOHO_REDIRECT_URI", "https://app.voxdesk.ai/oauth/zoho")
    reset_int2_settings_cache()

    tenant = await make_tenant(db, "SingleFlight Tenant")
    conn = await upsert_connection(
        db,
        tenant_id=tenant.id,
        provider="zoho_crm",
        category="crm",
        auth_kind="oauth2",
        credentials={"access_token": "old-access", "refresh_token": "old-refresh"},
        config={"region": "eu"},
        expires_in=30,
    )
    await db.commit()

    refresh_http_calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal refresh_http_calls
        assert str(req.url) == "https://accounts.zoho.eu/oauth/v2/token"
        refresh_http_calls += 1
        return httpx.Response(
            200,
            json={
                "access_token": f"rotated-access-{refresh_http_calls}",
                "refresh_token": "rotated-refresh-token",
                "expires_in": 3600,
            },
        )

    http_client = make_protocol_http_client(handler)

    results = await asyncio.gather(
        *[
            refresh_connection_token(db, conn, http_client=http_client, force=False)
            for _ in range(6)
        ]
    )
    assert refresh_http_calls == 1
    assert all(r["access_token"] == "rotated-access-1" for r in results)

    refresh_bundle = open_secret_bundle(
        conn.refresh_secret_ref,
        tenant_id=tenant.id,
        provider="zoho_crm:refresh",
    )
    assert refresh_bundle["refresh_token"] == "rotated-refresh-token"


@pytest.mark.asyncio
async def test_connection_store_encryption_key_rotation_and_zero_plaintext(db, monkeypatch):
    key1 = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    key2 = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    monkeypatch.setattr(settings, "crm_encryption_keys", f"k1:{key1}")

    tenant_1 = await make_tenant(db, "Crypto Tenant 1")
    tenant_2 = await make_tenant(db, "Crypto Tenant 2")

    raw_access = "xoxb-plaintext-secret-access-token-998877"
    raw_refresh = "xoxr-plaintext-secret-refresh-token-112233"
    raw_wh = "whsec_plaintext_signing_secret_445566"

    conn = await upsert_connection(
        db,
        tenant_id=tenant_1.id,
        provider="slack",
        category="collab",
        auth_kind="oauth2",
        credentials={"access_token": raw_access},
        refresh_token=raw_refresh,
        webhook_secret=raw_wh,
    )
    await db.commit()

    stored = (
        await db.execute(
            select(IntegrationConnection).where(IntegrationConnection.id == conn.id)
        )
    ).scalar_one()
    for raw_secret in (raw_access, raw_refresh, raw_wh):
        assert raw_secret not in stored.token_secret_ref
        assert raw_secret not in stored.refresh_secret_ref
        assert raw_secret not in stored.webhook_secret_ref
        assert raw_secret not in str(mask_connection(stored))

    monkeypatch.setattr(settings, "crm_encryption_keys", f"k2:{key2},k1:{key1}")
    runtime = load_runtime_context(stored)
    assert runtime.credentials["access_token"] == raw_access
    assert runtime.refresh_token == raw_refresh
    assert runtime.webhook_secret == raw_wh

    new_blob, active_kid = seal_secret_bundle(
        {"access_token": raw_access}, tenant_id=tenant_1.id, provider="slack"
    )
    assert active_kid == "k2"

    with pytest.raises(AuthExpired):
        open_secret_bundle(new_blob, tenant_id=tenant_2.id, provider="slack")
