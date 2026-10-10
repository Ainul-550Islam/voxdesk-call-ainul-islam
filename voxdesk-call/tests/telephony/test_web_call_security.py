"""Security tests for PART 3 Web Calls: single-use JWT replay, Origin allowlist, expiry, rate limit, and fail-closed production secret check."""

from __future__ import annotations

import pytest
from starlette.websockets import WebSocketDisconnect

from app.db.models import Agent, AgentVersion, UserRole
from app.domain.public_widget_models import PublicWidgetKeyCreate
from app.services.public_key_service import create_public_key
from app.telephony.web_call import issue_web_call_jwt, reset_web_call_rate_limits
from app.telephony.web_transport import (
    WebCallProviderFake,
    use_web_call_provider_fake,
)
from tests.conftest import auth_headers, make_tenant, make_user
from tests.telephony.test_web_call_flow import asgi_websocket_connect


async def _seed_published_agent(db, tenant_id) -> Agent:
    agent = Agent(
        tenant_id=tenant_id,
        external_key="sec-web-agent",
        name="Security Web Agent",
        status="published",
        published_version_number=1,
    )
    db.add(agent)
    await db.flush()

    version = AgentVersion(
        tenant_id=tenant_id,
        agent_id=agent.id,
        version_number=1,
        status="published",
        config_hash="sha256_sec_web_v1",
        is_active=True,
        config_snapshot={
            "system_prompt": "You are a secure web voice agent.",
            "stt_provider": "deepgram",
            "tts_provider": "elevenlabs",
            "voice_id": "voice_rachel",
            "llm_model": "gpt-4o-mini",
        },
    )
    db.add(version)
    await db.flush()
    agent.published_version_id = version.id
    await db.commit()
    return agent


@pytest.mark.asyncio
async def test_single_use_jwt_cannot_be_replayed(app, client, db) -> None:
    reset_web_call_rate_limits()
    tenant = await make_tenant(db, name="Replay Security Tenant")
    user = await make_user(db, tenant=tenant, role=UserRole.ADMIN)
    agent = await _seed_published_agent(db, tenant.id)
    headers = await auth_headers(client, user)

    resp = await client.post(
        "/api/web-calls",
        headers={**headers, "Origin": "https://allowed.voxdesk.test"},
        json={"agent_id": str(agent.id)},
    )
    assert resp.status_code == 201
    token = resp.json()["access_token"]

    fake = WebCallProviderFake()
    with use_web_call_provider_fake(fake):
        # First connection succeeds and consumes the single-use jti
        async with asgi_websocket_connect(
            app,
            f"/telephony/web/ws?token={token}",
            headers={"Origin": "https://allowed.voxdesk.test"},
        ) as ws:
            first_frame = await ws.receive_bytes()
            assert len(first_frame) > 0

        # Second connection with the same token MUST be rejected (4401)
        with pytest.raises(WebSocketDisconnect) as exc_info:
            async with asgi_websocket_connect(
                app,
                f"/telephony/web/ws?token={token}",
                headers={"Origin": "https://allowed.voxdesk.test"},
            ) as ws2:
                await ws2.receive_bytes()
        assert exc_info.value.code == 4401


@pytest.mark.asyncio
async def test_origin_mismatch_rejected_on_public_web_call_and_websocket(
    app, client, db
) -> None:
    reset_web_call_rate_limits()
    tenant = await make_tenant(db, name="Origin Security Tenant")
    user = await make_user(db, tenant=tenant, role=UserRole.ADMIN)
    agent = await _seed_published_agent(db, tenant.id)

    issued = await create_public_key(
        db,
        tenant_id=tenant.id,
        actor_user_id=user.id,
        actor_email=user.email,
        payload=PublicWidgetKeyCreate(
            name="Widget Key",
            agent_id=str(agent.id),
            agent_kind="voice",
            allowed_origins=["https://shop.example.com"],
            rate_limit_per_minute=10,
        ),
    )
    await db.commit()

    # 1. Disallowed origin on POST /api/public/web-calls -> 403
    bad_origin_resp = await client.post(
        "/api/public/web-calls",
        headers={
            "X-VoxDesk-Public-Key": issued.public_key,
            "Origin": "https://evil.example.org",
        },
        json={"agent_id": str(agent.id)},
    )
    assert bad_origin_resp.status_code == 403
    assert "ORIGIN" in str(bad_origin_resp.json()["detail"]["code"]).upper()

    # 2. Allowed origin on POST /api/public/web-calls -> 201 Created
    good_origin_resp = await client.post(
        "/api/public/web-calls",
        headers={
            "X-VoxDesk-Public-Key": issued.public_key,
            "Origin": "https://shop.example.com",
        },
        json={"agent_id": str(agent.id)},
    )
    assert good_origin_resp.status_code == 201, good_origin_resp.text
    token = good_origin_resp.json()["access_token"]

    # 3. Connecting to /telephony/web/ws from a different Origin than bound in JWT -> 4403
    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with asgi_websocket_connect(
            app,
            f"/telephony/web/ws?token={token}",
            headers={"Origin": "https://evil.example.org"},
        ) as ws:
            await ws.receive_bytes()
    assert exc_info.value.code == 4403


@pytest.mark.asyncio
async def test_expired_jwt_rejected_on_websocket(app, client, db) -> None:
    tenant = await make_tenant(db, name="Expiry Security Tenant")
    agent = await _seed_published_agent(db, tenant.id)

    expired_token, _exp = issue_web_call_jwt(
        call_id="call_web_expired_test",
        tenant_id=tenant.id,
        agent_id=agent.id,
        agent_version=1,
        origin="https://allowed.voxdesk.test",
        ttl_seconds=-10,
    )

    with pytest.raises(WebSocketDisconnect) as exc_info:
        async with asgi_websocket_connect(
            app,
            f"/telephony/web/ws?token={expired_token}",
            headers={"Origin": "https://allowed.voxdesk.test"},
        ) as ws:
            await ws.receive_bytes()
    assert exc_info.value.code == 4401


@pytest.mark.asyncio
async def test_public_key_rate_limit_enforced_on_public_web_calls(
    client, db
) -> None:
    reset_web_call_rate_limits()
    tenant = await make_tenant(db, name="RateLimit Security Tenant")
    user = await make_user(db, tenant=tenant, role=UserRole.ADMIN)
    agent = await _seed_published_agent(db, tenant.id)

    issued = await create_public_key(
        db,
        tenant_id=tenant.id,
        actor_user_id=user.id,
        actor_email=user.email,
        payload=PublicWidgetKeyCreate(
            name="Low Rate Key",
            agent_id=str(agent.id),
            agent_kind="voice",
            allowed_origins=["https://rate.example.com"],
            rate_limit_per_minute=2,
        ),
    )
    await db.commit()

    req_headers = {
        "X-VoxDesk-Public-Key": issued.public_key,
        "Origin": "https://rate.example.com",
    }
    r1 = await client.post(
        "/api/public/web-calls", headers=req_headers, json={"agent_id": str(agent.id)}
    )
    r2 = await client.post(
        "/api/public/web-calls", headers=req_headers, json={"agent_id": str(agent.id)}
    )
    r3 = await client.post(
        "/api/public/web-calls", headers=req_headers, json={"agent_id": str(agent.id)}
    )

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r3.status_code == 429
    assert r3.json()["detail"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_production_env_fails_closed_when_signing_key_missing(
    client, db, monkeypatch
) -> None:
    reset_web_call_rate_limits()
    tenant = await make_tenant(db, name="Prod Signing Key Tenant")
    user = await make_user(db, tenant=tenant, role=UserRole.ADMIN)
    agent = await _seed_published_agent(db, tenant.id)
    headers = await auth_headers(client, user)

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("REALTIME_MEDIA_SIGNING_KEY", raising=False)
    monkeypatch.delenv("WEB_CALL_JWT_SECRET", raising=False)

    resp = await client.post(
        "/api/web-calls",
        headers=headers,
        json={"agent_id": str(agent.id)},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "WEB_CALL_SIGNING_KEY_MISSING"
