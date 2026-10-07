"""Prompt 5 Integration Tests: Public Widget Sessions, Permission Boundaries, Honest NOT_CONFIGURED Voice, and Rate Limiting."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, AgentVersion, TestRun as DbTestRun, UserRole
from app.services.public_widget_service import reset_widget_rate_limits
from tests.conftest import auth_headers, make_tenant, make_user


@pytest.mark.asyncio
async def test_public_key_and_widget_session_token_cannot_access_private_or_admin_apis(
    client: AsyncClient,
    db: AsyncSession,
):
    reset_widget_rate_limits()
    tenant = await make_tenant(db, "Boundary Guard Tenant")
    owner = await make_user(db, tenant, UserRole.OWNER)
    headers = await auth_headers(client, owner)

    agent = Agent(
        tenant_id=tenant.id,
        external_key="agent_boundary_guard",
        name="Boundary Guard Agent",
        description="Published agent",
        status="published",
        published_version_number=1,
    )
    db.add(agent)
    await db.flush()
    ver = AgentVersion(
        tenant_id=tenant.id,
        agent_id=agent.id,
        version_number=1,
        status="published",
        config_hash="sha256_boundary_v1",
        config_snapshot={
            "identity": {"name": "Boundary Guard Agent"},
            "model": {"system_prompt": "CONFIDENTIAL_INTERNAL_PROMPT_DO_NOT_LEAK"},
        },
    )
    db.add(ver)
    await db.flush()
    agent.published_version_id = ver.id
    await db.commit()

    key_resp = await client.post(
        "/api/v1/public-keys",
        headers=headers,
        json={
            "agent_id": str(agent.id),
            "name": "Widget Embed Key",
            "allowed_origins": ["https://embed.customer.example.com"],
        },
    )
    assert key_resp.status_code == 201
    public_key = key_resp.json()["public_key"]

    # Create a widget chat session
    sess_resp = await client.post(
        "/api/v1/public/widget/sessions",
        json={"public_key": public_key, "mode": "chat", "visitor_id": "vis_101"},
        headers={"Origin": "https://embed.customer.example.com"},
    )
    assert sess_resp.status_code == 201, sess_resp.text
    session_token = sess_resp.json()["session_token"]
    assert session_token.startswith("vdws_")

    # Attempt to use `public_key` (`vdpk_...`) and `session_token` (`vdws_...`) against private/admin routes
    for cred in (public_key, session_token):
        bad_headers = {"Authorization": f"Bearer {cred}"}

        # Cannot list or edit agents
        r1 = await client.get("/api/agents", headers=bad_headers)
        assert r1.status_code == 403
        assert r1.json()["error"]["code"] == "public_credential_forbidden_on_private_api"

        r2 = await client.patch(
            f"/api/agents/{agent.id}",
            headers=bad_headers,
            json={"name": "Hacked Agent Name"},
        )
        assert r2.status_code == 403

        # Cannot publish agents
        r3 = await client.post(f"/api/agents/{agent.id}/publish", headers=bad_headers, json={})
        assert r3.status_code == 403

        # Cannot manage public keys
        r4 = await client.get("/api/v1/public-keys", headers=bad_headers)
        assert r4.status_code == 403

        # Cannot access user profile or conductor control plane
        r5 = await client.get("/auth/me", headers=bad_headers)
        assert r5.status_code == 403

        r6 = await client.get("/api/v1/conductor/sessions", headers=bad_headers)
        assert r6.status_code == 403


@pytest.mark.asyncio
async def test_widget_chat_session_multi_turn_and_honest_voice_not_configured_and_rate_limit(
    client: AsyncClient,
    db: AsyncSession,
):
    reset_widget_rate_limits()
    tenant = await make_tenant(db, "Widget Runtime Tenant")
    owner = await make_user(db, tenant, UserRole.OWNER)
    headers = await auth_headers(client, owner)

    # 1. Draft (unpublished) agent is blocked when require_published_agent=True
    draft_agent = Agent(
        tenant_id=tenant.id,
        external_key="agent_draft_only",
        name="Draft Only Agent",
        status="draft",
        published_version_number=None,
    )
    db.add(draft_agent)
    await db.commit()

    draft_key_resp = await client.post(
        "/api/v1/public-keys",
        headers=headers,
        json={
            "agent_id": str(draft_agent.id),
            "name": "Draft Key",
            "allowed_origins": ["https://shop.example.com"],
            "require_published_agent": True,
        },
    )
    assert draft_key_resp.status_code == 201
    draft_pub_key = draft_key_resp.json()["public_key"]

    draft_cfg_resp = await client.get(
        "/api/v1/public/widget/config",
        params={"public_key": draft_pub_key},
        headers={"Origin": "https://shop.example.com"},
    )
    assert draft_cfg_resp.status_code == 403
    assert draft_cfg_resp.json()["error"]["code"] == "AGENT_NOT_PUBLISHED"

    # 2. Published agent works for bootstrap, chat turns, and honest voice NOT_CONFIGURED
    pub_agent = Agent(
        tenant_id=tenant.id,
        external_key="agent_published_live",
        name="Shop Concierge AI",
        status="published",
        published_version_number=2,
    )
    db.add(pub_agent)
    await db.flush()
    ver2 = AgentVersion(
        tenant_id=tenant.id,
        agent_id=pub_agent.id,
        version_number=2,
        status="published",
        config_hash="sha256_shop_v2",
        config_snapshot={
            "identity": {"name": "Shop Concierge AI"},
            "model": {"system_prompt": "Help visitors book product demos. SECRET_INTERNAL_TOKEN=xyz987"},
        },
    )
    db.add(ver2)
    await db.flush()
    pub_agent.published_version_id = ver2.id
    await db.commit()

    pub_key_resp = await client.post(
        "/api/v1/public-keys",
        headers=headers,
        json={
            "agent_id": str(pub_agent.id),
            "name": "Shop Live Widget Key",
            "allowed_origins": ["https://shop.example.com"],
            "rate_limit_per_minute": 6,
            "widget_config": {
                "title": "Talk with Shop Concierge",
                "subtitle": "Instant answers & demo scheduling",
                "greeting": "Welcome to Shop Concierge! How can I help you?",
                "placeholder": "Ask about pricing or book a demo...",
                "primary_color": "#10b981",
                "position": "bottom-right",
                "enable_chat": True,
                "enable_voice": True,
                "show_branding": True,
            },
        },
    )
    assert pub_key_resp.status_code == 201
    pub_key = pub_key_resp.json()["public_key"]

    # Bootstrap config never leaks internal prompt secrets
    cfg_resp = await client.get(
        "/api/v1/public/widget/config",
        params={"public_key": pub_key},
        headers={"Origin": "https://shop.example.com"},
    )
    assert cfg_resp.status_code == 200
    assert "SECRET_INTERNAL_TOKEN" not in cfg_resp.text
    cfg_json = cfg_resp.json()
    assert cfg_json["published_version_number"] == 2
    assert cfg_json["voice_transport_configured"] is False
    assert cfg_json["voice_transport_status"] == "NOT_CONFIGURED"

    # Voice session returns honest NOT_CONFIGURED when WebRTC/SIP transport is not configured
    voice_sess_resp = await client.post(
        "/api/v1/public/widget/sessions",
        json={"public_key": pub_key, "mode": "voice", "visitor_id": "vis_voice_1"},
        headers={"Origin": "https://shop.example.com"},
    )
    assert voice_sess_resp.status_code == 201, voice_sess_resp.text
    voice_sess = voice_sess_resp.json()
    assert voice_sess["status"] == "not_configured"
    assert voice_sess["error_code"] == "NOT_CONFIGURED"
    assert voice_sess["transport"] == "not_configured"

    # Chat session succeeds, pins version 2, executes turns, and updates linked TestRun
    chat_sess_resp = await client.post(
        "/api/v1/public/widget/sessions",
        json={"public_key": pub_key, "mode": "chat", "visitor_id": "vis_chat_1"},
        headers={"Origin": "https://shop.example.com"},
    )
    assert chat_sess_resp.status_code == 201
    chat_sess = chat_sess_resp.json()
    assert chat_sess["status"] == "connected"
    assert chat_sess["agent_version_number"] == 2
    assert len(chat_sess["transcript"]) == 1
    session_id = chat_sess["session_id"]
    session_token = chat_sess["session_token"]

    msg_resp = await client.post(
        f"/api/v1/public/widget/sessions/{session_id}/messages",
        headers={
            "X-VoxDesk-Widget-Session": session_token,
            "Origin": "https://shop.example.com",
        },
        json={"content": "Can I book a demo for tomorrow afternoon?"},
    )
    assert msg_resp.status_code == 200, msg_resp.text
    msg_data = msg_resp.json()
    assert msg_data["turns_count"] == 1
    assert msg_data["user_turn"]["content"] == "Can I book a demo for tomorrow afternoon?"
    assert "Shop Concierge AI" in msg_data["assistant_turn"]["content"]
    assert "SECRET_INTERNAL_TOKEN" not in msg_resp.text

    # End session cleanly
    end_resp = await client.post(
        f"/api/v1/public/widget/sessions/{session_id}/end",
        headers={
            "X-VoxDesk-Widget-Session": session_token,
            "Origin": "https://shop.example.com",
        },
        json={"reason": "completed_by_user"},
    )
    assert end_resp.status_code == 200
    assert end_resp.json()["status"] == "completed"

    # Verify linked TestRun was persisted in PostgreSQL/SQLite pinned to version 2
    runs = (
        await db.execute(
            select(DbTestRun).where(
                DbTestRun.tenant_id == tenant.id,
                DbTestRun.agent_id == str(pub_agent.id),
                DbTestRun.provider == "public_web_widget",
            )
        )
    ).scalars().all()
    assert len(runs) == 2
    assert any(r.error_code == "NOT_CONFIGURED" and r.agent_version_number == 2 for r in runs)
    assert any(r.status == "passed" and r.agent_version_number == 2 and len(r.transcript_snapshot) == 3 for r in runs)

    # 3. Exceeding per-key rate limit (6 requests/minute) returns 429 RATE_LIMITED
    rate_limited_hit = False
    for _ in range(5):
        r = await client.get(
            "/api/v1/public/widget/config",
            params={"public_key": pub_key},
            headers={"Origin": "https://shop.example.com"},
        )
        if r.status_code == 429:
            rate_limited_hit = True
            assert r.json()["error"]["code"] == "RATE_LIMITED"
            break
    assert rate_limited_hit is True
