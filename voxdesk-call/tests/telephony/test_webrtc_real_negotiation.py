"""P11-10: a real aiortc browser offer must be negotiated by `/telephony/web/offer`.

The neighbouring `test_webrtc_dependency.py` proves the *fallback* contract with module
fakes. That is not evidence that the WebRTC path works: a fake connection will happily
return any SDP. This module drives a genuine `aiortc` peer connection as the browser
would — real offer, real ICE credentials, real DTLS fingerprint — and requires the
server's answer to be accepted by aiortc's own SDP parser.

Skipped (with reason) when `aiortc` is not installed, which is the runtime situation the
503 fallback exists for. Install it with `pip install 'pipecat-ai[webrtc]'`.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Agent, AgentVersion
from tests.conftest import auth_headers

aiortc = pytest.importorskip("aiortc", reason="WebRTC negotiation needs aiortc (pipecat-ai[webrtc])")

pytestmark = pytest.mark.asyncio


async def _mint_web_call_token(client: AsyncClient, db: AsyncSession, tenant, owner) -> str:
    agent = Agent(
        tenant_id=tenant.id,
        external_key=f"wrtc_real_{uuid.uuid4().hex[:10]}",
        name="WebRTC Real Negotiation Agent",
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
        config_hash=f"wrtr{uuid.uuid4().hex[:6]}",
    )
    db.add(av)
    await db.flush()
    agent.published_version_id = av.id
    agent.published_version_number = 1
    agent_id = str(agent.id)
    await db.commit()

    headers = await auth_headers(client, owner)
    created = await client.post("/api/web-calls", headers=headers, json={"agent_id": agent_id})
    assert created.status_code in (200, 201), created.text
    return created.json()["access_token"]


def _sdp_field(sdp: str, name: str) -> str:
    for line in sdp.splitlines():
        if line.startswith(f"a={name}:"):
            return line.split(":", 1)[1]
    return ""


async def test_real_aiortc_offer_is_answered_with_a_parsable_webrtc_answer(
    client: AsyncClient, db: AsyncSession, tenant_a, owner_a, monkeypatch
) -> None:
    access_token = await _mint_web_call_token(client, db, tenant_a, owner_a)

    # `/offer` currently proves signalling only and does not keep a transport registry.
    # Capture the server connection here so the test can close it and its Pipecat timeout
    # task rather than leaving aiortc tasks behind when pytest tears down the event loop.
    import pipecat.transports.smallwebrtc.connection as smallwebrtc_connection

    real_connection = smallwebrtc_connection.SmallWebRTCConnection
    server_connections = []

    class _TrackedConnection(real_connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            server_connections.append(self)

    monkeypatch.setattr(smallwebrtc_connection, "SmallWebRTCConnection", _TrackedConnection)

    tasks_before = asyncio.all_tasks()
    pc = aiortc.RTCPeerConnection()
    try:
        pc.addTransceiver("audio", direction="sendrecv")
        offer = await pc.createOffer()
        # aiortc >= 1.16 follows the spec: createOffer() does not apply the description,
        # setLocalDescription() does. Applying it also finishes host-candidate gathering,
        # so the SDP we post is non-trickle exactly like a browser's first POST.
        await pc.setLocalDescription(offer)
        local = pc.localDescription
        assert local is not None
        assert "a=ice-ufrag:" in local.sdp
        assert "a=fingerprint:" in local.sdp

        resp = await client.post(
            "/telephony/web/offer",
            json={"access_token": access_token, "sdp": local.sdp, "type": "offer"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["transport"] == "webrtc"
        assert body["type"] == "answer"

        answer_sdp = body["sdp"]
        assert answer_sdp.startswith("v=0"), answer_sdp[:80]
        assert "m=audio" in answer_sdp, "the server answer must negotiate an audio m-line"
        assert _sdp_field(answer_sdp, "ice-ufrag"), "answer carries no ICE username fragment"
        assert _sdp_field(answer_sdp, "ice-pwd"), "answer carries no ICE password"
        assert _sdp_field(answer_sdp, "fingerprint"), "answer carries no DTLS fingerprint"
        assert _sdp_field(answer_sdp, "setup").lower() in {"active", "passive", "actpass"}

        # The decisive check: aiortc's own parser accepts the server answer. A fabricated
        # or malformed SDP raises here instead of passing the string assertions above.
        await pc.setRemoteDescription(aiortc.RTCSessionDescription(sdp=answer_sdp, type="answer"))
        remote = pc.remoteDescription
        assert remote is not None
        assert remote.type == "answer"
        assert remote.sdp == answer_sdp
    finally:
        await pc.close()
        for server_connection in server_connections:
            await server_connection.disconnect()
            await server_connection.cleanup()

        # aiortc schedules __connect() with ensure_future but does not retain/cancel
        # that task when a still-negotiating peer is closed. Both peers are closed above;
        # cancel only the fire-and-forget tasks created by this test's negotiation.
        orphaned_connect_tasks = [
            task
            for task in asyncio.all_tasks()
            if task not in tasks_before
            and task is not asyncio.current_task()
            and "__connect" in getattr(task.get_coro(), "__qualname__", "")
        ]
        for task in orphaned_connect_tasks:
            task.cancel()
        if orphaned_connect_tasks:
            await asyncio.gather(*orphaned_connect_tasks, return_exceptions=True)


async def test_the_websocket_fallback_route_is_still_mounted(app) -> None:
    """The documented fallback transport must stay reachable next to WebRTC."""
    paths = {getattr(route, "path", "") for route in app.routes}
    assert "/telephony/web/ws" in paths, "the WebSocket protobuf transport route disappeared"
    assert "/telephony/web/offer" in paths
