"""Prompt 5 Security Tests: Public Key Hashing, Origin Policy, Lifecycle, and Capability Boundaries."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, AgentVersion, PublicWidgetKey, UserRole
from tests.conftest import auth_headers, make_tenant, make_user


async def _seed_published_agent(db: AsyncSession, tenant_id) -> Agent:
    agent = Agent(
        tenant_id=tenant_id,
        external_key="agent_pub_widget_sec",
        name="Acme Support Concierge",
        description="Published support agent for widget security tests",
        status="published",
        published_version_number=1,
    )
    db.add(agent)
    await db.flush()
    ver = AgentVersion(
        tenant_id=tenant_id,
        agent_id=agent.id,
        version_number=1,
        status="published",
        config_hash="sha256_acme_v1",
        config_snapshot={
            "identity": {"name": "Acme Support Concierge"},
            "model": {"system_prompt": "You are the Acme Support Concierge."},
        },
    )
    db.add(ver)
    await db.flush()
    agent.published_version_id = ver.id
    await db.commit()
    await db.refresh(agent)
    return agent


@pytest.mark.asyncio
async def test_public_key_lifecycle_hashed_storage_and_origin_enforcement(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant_a = await make_tenant(db, "Key Lifecycle Tenant A")
    owner_a = await make_user(db, tenant_a, UserRole.OWNER)
    headers_a = await auth_headers(client, owner_a)
    agent = await _seed_published_agent(db, tenant_a.id)

    # 1. Create public key
    create_resp = await client.post(
        "/api/v1/public-keys",
        headers=headers_a,
        json={
            "agent_id": str(agent.id),
            "agent_kind": "voice",
            "name": "Production Website Widget Key",
            "environment_name": "production",
            "allowed_origins": ["https://www.acme-dental.example.com"],
            "rate_limit_per_minute": 30,
            "session_ttl_seconds": 600,
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    created = create_resp.json()
    plaintext_key = created["public_key"]
    key_meta = created["key"]
    key_id = key_meta["id"]

    assert plaintext_key.startswith("vdpk_")
    assert key_meta["key_prefix"] == plaintext_key[:13]
    assert key_meta["status"] == "active"
    assert key_meta["allowed_origins"] == ["https://www.acme-dental.example.com"]
    assert "key_hash" not in key_meta

    # Verify DB stores only SHA-256 hash and never plaintext secret
    db_row = (
        await db.execute(
            select(PublicWidgetKey).where(PublicWidgetKey.id == uuid.UUID(key_meta["id"]))
        )
    ).scalar_one()
    secret_part = plaintext_key.split("_", 2)[2]
    assert db_row.key_hash != secret_part
    assert secret_part not in db_row.key_hash
    assert len(db_row.key_hash) == 64

    # Subsequent list/get never return plaintext_key
    list_resp = await client.get("/api/v1/public-keys", headers=headers_a)
    assert list_resp.status_code == 200
    assert plaintext_key not in list_resp.text

    # 2. Allowed origin succeeds on widget config
    ok_cfg = await client.get(
        "/api/v1/public/widget/config",
        params={"public_key": plaintext_key},
        headers={"Origin": "https://www.acme-dental.example.com"},
    )
    assert ok_cfg.status_code == 200, ok_cfg.text
    assert ok_cfg.json()["agent_name"] == "Acme Support Concierge"

    # 3. Disallowed origin is rejected with 403 FORBIDDEN_ORIGIN
    bad_origin = await client.get(
        "/api/v1/public/widget/config",
        params={"public_key": plaintext_key},
        headers={"Origin": "https://evil-attacker.example.org"},
    )
    assert bad_origin.status_code == 403
    assert bad_origin.json()["error"]["code"] == "FORBIDDEN_ORIGIN"

    # Missing origin is rejected with 403 FORBIDDEN_ORIGIN
    missing_origin = await client.get(
        "/api/v1/public/widget/config",
        params={"public_key": plaintext_key},
    )
    assert missing_origin.status_code == 403
    assert missing_origin.json()["error"]["code"] == "FORBIDDEN_ORIGIN"

    # 4. Rotate key -> old key becomes ROTATED and fails immediately; new key works
    rotate_resp = await client.post(
        f"/api/v1/public-keys/{key_id}/rotate",
        headers=headers_a,
        json={"reason": "scheduled_quarterly_rotation"},
    )
    assert rotate_resp.status_code == 201, rotate_resp.text
    rotated_data = rotate_resp.json()
    new_plaintext_key = rotated_data["public_key"]
    new_key_id = rotated_data["key"]["id"]
    assert new_plaintext_key != plaintext_key
    assert rotated_data["key"]["rotated_from_key_id"] == key_id

    # Old key now fails with 401 INVALID_PUBLIC_KEY
    old_key_try = await client.get(
        "/api/v1/public/widget/config",
        params={"public_key": plaintext_key},
        headers={"Origin": "https://www.acme-dental.example.com"},
    )
    assert old_key_try.status_code == 401
    assert old_key_try.json()["error"]["code"] == "INVALID_PUBLIC_KEY"

    # New key succeeds
    new_key_try = await client.get(
        "/api/v1/public/widget/config",
        params={"public_key": new_plaintext_key},
        headers={"Origin": "https://www.acme-dental.example.com"},
    )
    assert new_key_try.status_code == 200

    # 5. Revoke new key -> fails immediately with 401 INVALID_PUBLIC_KEY
    revoke_resp = await client.post(
        f"/api/v1/public-keys/{new_key_id}/revoke",
        headers=headers_a,
        json={"reason": "compromised_embed_snippet"},
    )
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["status"] == "revoked"

    revoked_try = await client.get(
        "/api/v1/public/widget/config",
        params={"public_key": new_plaintext_key},
        headers={"Origin": "https://www.acme-dental.example.com"},
    )
    assert revoked_try.status_code == 401
    assert revoked_try.json()["error"]["code"] == "INVALID_PUBLIC_KEY"


@pytest.mark.asyncio
async def test_expired_public_key_and_forbidden_capabilities_and_cross_tenant_isolation(
    client: AsyncClient,
    db: AsyncSession,
):
    tenant_a = await make_tenant(db, "Tenant Alpha")
    tenant_b = await make_tenant(db, "Tenant Beta")
    owner_a = await make_user(db, tenant_a, UserRole.OWNER)
    owner_b = await make_user(db, tenant_b, UserRole.OWNER)
    headers_a = await auth_headers(client, owner_a)
    headers_b = await auth_headers(client, owner_b)
    agent_a = await _seed_published_agent(db, tenant_a.id)

    # 1. Forbidden capabilities are rejected on key creation (422)
    for forbidden_cap in (
        "agent_edit",
        "agent_publish",
        "tenant_admin",
        "billing_admin",
        "raw_tool_execution",
        "connector_secret_access",
        "full_call_history_export",
        "*",
    ):
        bad_cap_resp = await client.post(
            "/api/v1/public-keys",
            headers=headers_a,
            json={
                "agent_id": str(agent_a.id),
                "name": "Bad Cap Key",
                "allowed_origins": ["https://app.example.com"],
                "allowed_capabilities": ["widget_config_read", forbidden_cap],
            },
        )
        assert bad_cap_resp.status_code == 422, f"Expected 422 for capability {forbidden_cap}"

    # 2. Wildcard and invalid origins are rejected on key creation (422)
    for bad_origin in ("*", "https://*.example.com", "null", "javascript:alert(1)", "https://example.com/path"):
        bad_orig_resp = await client.post(
            "/api/v1/public-keys",
            headers=headers_a,
            json={
                "agent_id": str(agent_a.id),
                "name": "Bad Origin Key",
                "allowed_origins": [bad_origin],
            },
        )
        assert bad_orig_resp.status_code == 422, f"Expected 422 for origin {bad_origin}"

    # 3. Create valid key in Tenant A, then expire it in DB and verify 401 INVALID_PUBLIC_KEY
    create_resp = await client.post(
        "/api/v1/public-keys",
        headers=headers_a,
        json={
            "agent_id": str(agent_a.id),
            "name": "Expiring Key",
            "allowed_origins": ["https://portal.example.com"],
            "expires_in_days": 1,
        },
    )
    assert create_resp.status_code == 201
    key_id = create_resp.json()["key"]["id"]
    raw_key = create_resp.json()["public_key"]

    # Cross-tenant check: Tenant B cannot view, rotate, or revoke Tenant A's key
    assert (await client.get(f"/api/v1/public-keys/{key_id}", headers=headers_b)).status_code == 404
    assert (await client.post(f"/api/v1/public-keys/{key_id}/rotate", headers=headers_b)).status_code == 404
    assert (await client.post(f"/api/v1/public-keys/{key_id}/revoke", headers=headers_b)).status_code == 404

    # Force expiration in DB
    key_row = (
        await db.execute(
            select(PublicWidgetKey).where(PublicWidgetKey.id == uuid.UUID(key_id))
        )
    ).scalar_one()
    key_row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    await db.commit()

    exp_try = await client.get(
        "/api/v1/public/widget/config",
        params={"public_key": raw_key},
        headers={"Origin": "https://portal.example.com"},
    )
    assert exp_try.status_code == 401
    assert exp_try.json()["error"]["code"] == "INVALID_PUBLIC_KEY"
