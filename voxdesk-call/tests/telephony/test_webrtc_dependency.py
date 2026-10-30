"""Behavioural tests for WebRTC signaling fallback (Capability 9)."""
from __future__ import annotations

import builtins
from types import ModuleType

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, AgentVersion
from tests.conftest import auth_headers


async def _mint_web_call_token(
    client: AsyncClient,
    db: AsyncSession,
    tenant,
    owner,
) -> str:
    import uuid
    agent = Agent(
        tenant_id=tenant.id,
        external_key=f"wrtc_{uuid.uuid4().hex[:10]}",
        name="WebRTC Test Agent",
        agent_type="voice",
        status="published",
        current_draft_config={"greeting": "Hello!", "system_prompt": "Help user."},
    )
    db.add(agent)
    await db.flush()
    av = AgentVersion(
        tenant_id=tenant.id,
        agent_id=agent.id,
        version_number=1,
        status="published",
        config_snapshot={"greeting": "Hello!", "system_prompt": "Help user."},
        config_hash="webrtc1",
    )
    db.add(av)
    await db.flush()
    agent.published_version_id = av.id
    agent.published_version_number = 1
    agent_id = str(agent.id)
    await db.commit()

    headers = await auth_headers(client, owner)
    created = await client.post(
        "/api/web-calls",
        headers=headers,
        json={"agent_id": agent_id},
    )
    assert created.status_code in (200, 201), created.text
    return created.json()["access_token"]


@pytest.mark.asyncio
async def test_webrtc_offer_returns_503_with_ws_protobuf_fallback_when_aiortc_absent(
    client: AsyncClient,
    db: AsyncSession,
    tenant_a,
    owner_a,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    access_token = await _mint_web_call_token(client, db, tenant_a, owner_a)

    real_import = builtins.__import__

    def _block_smallwebrtc(name, *args, **kwargs):
        if name.startswith("pipecat.transports.smallwebrtc"):
            raise ImportError("aiortc not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_smallwebrtc)

    resp = await client.post(
        "/telephony/web/offer",
        json={"access_token": access_token, "sdp": "v=0\r\n", "type": "offer"},
    )
    assert resp.status_code == 503, resp.text
    assert resp.status_code != 501
    detail = resp.json()["detail"]
    assert detail["code"] == "UNSUPPORTED_CAPABILITY"
    assert detail["fallback_transport"] == "ws-protobuf"


@pytest.mark.asyncio
async def test_webrtc_offer_negotiates_sdp_when_smallwebrtc_available(
    client: AsyncClient,
    db: AsyncSession,
    tenant_a,
    owner_a,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys

    access_token = await _mint_web_call_token(client, db, tenant_a, owner_a)

    conn_mod = ModuleType("pipecat.transports.smallwebrtc.connection")
    trans_mod = ModuleType("pipecat.transports.smallwebrtc.transport")

    class FakeSmallWebRTCConnection:
        async def initialize(self, *, sdp: str, type: str) -> None:
            self._sdp = sdp
            self._type = type

        def get_answer(self) -> dict[str, str]:
            return {"sdp": "v=0\r\na=answer\r\n", "type": "answer"}

    conn_mod.SmallWebRTCConnection = FakeSmallWebRTCConnection  # type: ignore[attr-defined]
    trans_mod.SmallWebRTCTransport = object  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pipecat.transports.smallwebrtc.connection", conn_mod)
    monkeypatch.setitem(sys.modules, "pipecat.transports.smallwebrtc.transport", trans_mod)

    resp = await client.post(
        "/telephony/web/offer",
        json={"access_token": access_token, "sdp": "v=0\r\n", "type": "offer"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["transport"] == "webrtc"
    assert body["type"] == "answer"
    assert "a=answer" in body["sdp"]
