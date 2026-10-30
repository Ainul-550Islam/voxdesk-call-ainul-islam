"""Behavioural tests for durable ``VoiceDemoSession`` public endpoints & reaper (Capability 10)."""
from __future__ import annotations

import inspect
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import public_home_routes
from app.core.config import settings
from app.db.enterprise_models import VoiceDemoSession
from app.db.models import Agent
from app.jobs import scheduler
from app.services import voice_demo_service


def test_no_in_memory_sessions_dict_in_voice_demo() -> None:
    for mod in (public_home_routes, voice_demo_service):
        src = inspect.getsource(mod)
        assert "_SESSIONS" not in src


@pytest.mark.asyncio
async def test_voice_demo_fails_closed_503_when_no_demo_agent_configured(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "voice_demo_agent_id", "")
    resp = await client.post("/api/v1/public/voice-demo/session", json={})
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "voice_demo_not_configured"


@pytest.mark.asyncio
async def test_voice_demo_lifecycle_hmac_401_rate_limit_429_expiry_410_and_reaper(
    client: AsyncClient,
    db: AsyncSession,
    tenant_a,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    demo_agent = Agent(
        tenant_id=tenant_a.id,
        external_key=f"demo_{uuid.uuid4().hex[:10]}",
        name="Public Concierge Demo",
        agent_type="voice",
        status="published",
        current_draft_config={
            "greeting": "Hi! Welcome to VoxDesk live demo.",
            "system_prompt": "Answer product questions concisely.",
        },
        meta={"public_voice_demo": True},
    )
    db.add(demo_agent)
    await db.commit()
    await db.refresh(demo_agent)
    monkeypatch.setattr(settings, "voice_demo_agent_id", str(demo_agent.id))
    monkeypatch.setattr(settings, "voice_demo_rate_limit_per_minute", 3)

    # 1. Create durable session
    created = await client.post("/api/v1/public/voice-demo/session", json={"metadata": {"source": "hero"}})
    assert created.status_code == 201, created.text
    body = created.json()
    sid = body["session_id"]
    token = body["session_token"]
    assert token.startswith("vds1.")

    row = await db.get(VoiceDemoSession, uuid.UUID(sid))
    assert row is not None
    assert row.agent_id == demo_agent.id

    # 2. Missing or forged HMAC token -> 401
    no_tok = await client.get(f"/api/v1/public/voice-demo/session/{sid}")
    assert no_tok.status_code == 401

    bad_tok = await client.get(
        f"/api/v1/public/voice-demo/session/{sid}",
        headers={"X-Demo-Session-Token": token[:-4] + "0000"},
    )
    assert bad_tok.status_code == 401

    # 3. Valid token -> start, turn, get, end
    started = await client.post(
        f"/api/v1/public/voice-demo/session/{sid}/event",
        headers={"X-Demo-Session-Token": token},
        json={"event": "start"},
    )
    assert started.status_code == 200
    assert started.json()["new_state"] == "LISTENING"

    turn = await client.post(
        f"/api/v1/public/voice-demo/session/{sid}/turn",
        headers={"X-Demo-Session-Token": token},
        json={"text": "Do you support SIP trunking?"},
    )
    assert turn.status_code == 200
    assert turn.json()["turn_count"] == 1
    assert "Public Concierge Demo" in (turn.json()["reply"] or "")

    state = await client.get(
        f"/api/v1/public/voice-demo/session/{sid}",
        headers={"X-Demo-Session-Token": token},
    )
    assert state.status_code == 200
    assert state.json()["turn_count"] == 1
    assert len(state.json()["transcript"]) == 3

    # 4. Per-IP rate limit (budget=3 -> 2nd & 3rd succeed, 4th returns 429)
    r2 = await client.post("/api/v1/public/voice-demo/session", json={})
    r3 = await client.post("/api/v1/public/voice-demo/session", json={})
    assert r2.status_code == 201
    assert r3.status_code == 201
    r4 = await client.post("/api/v1/public/voice-demo/session", json={})
    assert r4.status_code == 429

    # 5. Expired session returns 410 AND background reaper cleans expired rows
    row = await db.get(VoiceDemoSession, uuid.UUID(sid))
    assert row is not None
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    await db.commit()

    expired_resp = await client.get(
        f"/api/v1/public/voice-demo/session/{sid}",
        headers={"X-Demo-Session-Token": token},
    )
    assert expired_resp.status_code == 410
    assert expired_resp.json()["detail"]["code"] == "DEMO_SESSION_EXPIRED"

    tick_out = await scheduler.tick(db)
    await db.commit()
    assert tick_out["reaped_voice_demos"] >= 1
    db.expire_all()
    assert await db.get(VoiceDemoSession, uuid.UUID(sid)) is None
