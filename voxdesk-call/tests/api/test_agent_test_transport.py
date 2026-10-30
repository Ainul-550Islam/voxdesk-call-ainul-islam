"""Behavioural tests for durable DB-backed agent-test transport (Capability 6)."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent
from app.db.retell_models import TestRun
from tests.conftest import auth_headers


async def _create_agent(db: AsyncSession, tenant_id: uuid.UUID) -> uuid.UUID:
    agent = Agent(
        tenant_id=tenant_id,
        external_key=f"at_{uuid.uuid4().hex[:10]}",
        name="Transport Support Bot",
        agent_type="voice",
        status="published",
        current_draft_config={
            "greeting": "Hello from Transport Support Bot!",
            "system_prompt": "Assist callers with order tracking.",
            "tools": [{"name": "lookup_order"}],
        },
    )
    db.add(agent)
    await db.commit()
    return agent.id


@pytest.mark.asyncio
async def test_agent_test_session_persists_across_restart_and_completes_exchange(
    client: AsyncClient,
    db: AsyncSession,
    tenant_a,
    owner_a,
    tenant_b,
    owner_b,
) -> None:
    tenant_a_id = tenant_a.id
    headers_a = await auth_headers(client, owner_a)
    headers_b = await auth_headers(client, owner_b)
    agent_id = await _create_agent(db, tenant_a_id)

    # 1. Create persisted session -> 201
    created = await client.post(
        f"/api/v1/agents/{agent_id}/test",
        headers=headers_a,
        json={"mode": "simulation", "timeout_sec": 120},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    session_id = body["session_id"]
    session_uuid = uuid.UUID(session_id)
    token = body["token"]
    assert token.startswith("ats1.")
    assert body["ws_url"].endswith(f"/api/v1/agent-tests/{session_id}/ws?token={token}")

    # Verify persisted TestRun row in DB (survives process restart)
    db.expire_all()
    persisted = await db.get(TestRun, session_uuid)
    assert persisted is not None
    assert persisted.tenant_id == tenant_a_id

    # 2. Cross-tenant isolation -> 404
    cross_get = await client.get(f"/api/v1/agent-tests/{session_id}", headers=headers_b)
    assert cross_get.status_code == 404

    # 3. Health check -> 200
    health = await client.get(f"/api/v1/agent-tests/{session_id}/health", headers=headers_a)
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    # 4. Exchange events (start -> turn -> stop) persisted to DB
    ev1 = await client.post(
        f"/api/v1/agent-tests/{session_id}/events",
        headers=headers_a,
        json={"event": "turn", "data": {"text": "Where is order #98765?"}},
    )
    assert ev1.status_code == 200
    ev1_json = ev1.json()
    assert ev1_json["reply"]
    assert len(ev1_json["transcript"]) >= 3  # greeting + user + assistant

    ev_stop = await client.post(
        f"/api/v1/agent-tests/{session_id}/events",
        headers=headers_a,
        json={"event": "stop"},
    )
    assert ev_stop.status_code == 200
    assert ev_stop.json()["new_state"] == "COMPLETED"

    # 5. Re-read after clearing SQLAlchemy session identity map ("process restart")
    db.expunge_all()
    final_get = await client.get(f"/api/v1/agent-tests/{session_id}", headers=headers_a)
    assert final_get.status_code == 200
    final_body = final_get.json()
    assert final_body["state"] == "COMPLETED"
    assert any("98765" in t["text"] for t in final_body["transcript"] if t["role"] == "user")


@pytest.mark.asyncio
async def test_agent_test_expired_session_returns_410(
    client: AsyncClient,
    db: AsyncSession,
    tenant_a,
    owner_a,
) -> None:
    tenant_a_id = tenant_a.id
    headers_a = await auth_headers(client, owner_a)
    agent_id = await _create_agent(db, tenant_a_id)

    created = await client.post(
        f"/api/v1/agents/{agent_id}/test",
        headers=headers_a,
        json={"mode": "simulation", "timeout_sec": 30},
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    session_uuid = uuid.UUID(session_id)

    db.expire_all()
    row = await db.get(TestRun, session_uuid)
    assert row is not None
    meta = dict(row.pinned_config_snapshot or {})
    meta["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    row.pinned_config_snapshot = meta
    await db.commit()

    expired = await client.get(f"/api/v1/agent-tests/{session_id}", headers=headers_a)
    assert expired.status_code == 410
    assert expired.json()["detail"]["code"] == "TEST_SESSION_EXPIRED"
