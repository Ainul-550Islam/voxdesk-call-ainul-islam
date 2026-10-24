"""The SIP media bridge: the gateway token, the per-call session token and the
socket that carries the G.711 media.

The gateway authenticates twice: once with the shared bearer token and once
with an HMAC over ``"{call_id}:{timestamp}:{nonce}"`` that binds the socket
to a single call. Both checks happen before a single frame is read.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

import pytest
from starlette.websockets import WebSocketDisconnect

from app.telephony.sip import sip_handler
from app.telephony.sip.sip_handler import session_token, verify_session

_GATEWAY_TOKEN = "sip-gateway-token-for-tests"


class _Settings:
    """The carrier settings the bridge reads."""

    def __init__(self, *, token: str = _GATEWAY_TOKEN, ready: bool = True) -> None:
        self.sip_gateway_token = token
        self._ready = ready

    def sip_gateway_ready(self) -> bool:
        return self._ready


# ---------------------------------------------------------------------------
# ASGI websocket client
# ---------------------------------------------------------------------------
class _Socket:
    """A minimal in-loop ASGI websocket client."""

    def __init__(self, receive_queue: asyncio.Queue, send_queue: asyncio.Queue) -> None:
        self._receive_queue = receive_queue
        self._send_queue = send_queue

    async def send_text(self, data: str) -> None:
        await self._receive_queue.put({"type": "websocket.receive", "text": data})

    async def expect_close(self) -> int:
        message = await asyncio.wait_for(self._send_queue.get(), timeout=5.0)
        assert message.get("type") == "websocket.close", message
        return int(message.get("code") or 1000)


@asynccontextmanager
async def _connect(asgi_app, url: str, *, headers: dict[str, str] | None = None):
    parsed = urlsplit(url)
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "ws",
        "path": parsed.path or "/",
        "raw_path": (parsed.path or "/").encode("ascii"),
        "query_string": (parsed.query or "").encode("ascii"),
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
        "subprotocols": [],
        "state": {},
    }
    receive_queue: asyncio.Queue = asyncio.Queue()
    send_queue: asyncio.Queue = asyncio.Queue()
    await receive_queue.put({"type": "websocket.connect"})
    app_task = asyncio.create_task(asgi_app(scope, receive_queue.get, send_queue.put))
    try:
        first = await asyncio.wait_for(send_queue.get(), timeout=5.0)
        if first.get("type") == "websocket.close":
            raise WebSocketDisconnect(code=int(first.get("code") or 1000))
        assert first.get("type") == "websocket.accept", first
        yield _Socket(receive_queue, send_queue)
    finally:
        await receive_queue.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(app_task, timeout=5.0)
        except Exception:
            app_task.cancel()


def _headers(
    call_id: str,
    *,
    token: str = _GATEWAY_TOKEN,
    secret: str = _GATEWAY_TOKEN,
    timestamp: int | None = None,
    nonce: str = "nonce-0001",
    presented: str | None = None,
) -> dict[str, str]:
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    signature = presented if presented is not None else session_token(
        secret, call_id, stamp, nonce
    )
    return {
        "authorization": f"Bearer {token}",
        "x-voxdesk-sip-call-id": call_id,
        "x-voxdesk-sip-timestamp": stamp,
        "x-voxdesk-sip-nonce": nonce,
        "x-voxdesk-sip-session": signature,
    }


def _start(call_id: str, *, to: str = "+15550001111", codec: str = "PCMU") -> str:
    return json.dumps(
        {
            "type": "start",
            "event": "start",
            "call_id": call_id,
            "from": "+15559998888",
            "to": to,
            "codec": codec,
        }
    )


@pytest.fixture(autouse=True)
def _gateway_settings(monkeypatch):
    """Every test in this module talks to a configured gateway."""
    monkeypatch.setattr(
        "app.carriers_v2.settings.get_carrier_v2_settings",
        lambda: _Settings(),
    )


# ---------------------------------------------------------------------------
# The session token
# ---------------------------------------------------------------------------
def test_session_token_is_deterministic() -> None:
    first = session_token("secret", "call-1", "1700000000", "nonce")
    second = session_token("secret", "call-1", "1700000000", "nonce")
    assert first == second
    assert len(first) == 64


def test_verify_session_accepts_a_fresh_token() -> None:
    now = int(time.time())
    token = session_token("secret", "call-1", str(now), "nonce")
    assert verify_session(
        "secret", call_id="call-1", timestamp=str(now), nonce="nonce", presented=token
    )


def test_verify_session_rejects_a_stale_token() -> None:
    issued = int(time.time()) - 600
    token = session_token("secret", "call-1", str(issued), "nonce")
    assert not verify_session(
        "secret", call_id="call-1", timestamp=str(issued), nonce="nonce", presented=token
    )


def test_verify_session_rejects_a_wrong_secret() -> None:
    now = int(time.time())
    token = session_token("other-secret", "call-1", str(now), "nonce")
    assert not verify_session(
        "secret", call_id="call-1", timestamp=str(now), nonce="nonce", presented=token
    )


def test_verify_session_rejects_an_empty_presentation() -> None:
    now = int(time.time())
    assert not verify_session(
        "secret", call_id="call-1", timestamp=str(now), nonce="nonce", presented=""
    )


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------
async def test_the_bridge_refuses_a_socket_without_a_gateway(app, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.carriers_v2.settings.get_carrier_v2_settings",
        lambda: _Settings(ready=False),
    )
    async with _connect(app, "/telephony/sip/ws") as socket:
        assert await socket.expect_close() == 1012


async def test_the_bridge_refuses_a_socket_without_the_bearer_token(app) -> None:
    headers = _headers("call-1")
    headers.pop("authorization")
    async with _connect(app, "/telephony/sip/ws", headers=headers) as socket:
        assert await socket.expect_close() == 1008


async def test_the_bridge_refuses_a_wrong_bearer_token(app) -> None:
    headers = _headers("call-1", token="not-the-token")
    async with _connect(app, "/telephony/sip/ws", headers=headers) as socket:
        assert await socket.expect_close() == 1008


async def test_the_bridge_refuses_a_socket_without_a_session_token(app) -> None:
    headers = _headers("call-1", presented="")
    async with _connect(app, "/telephony/sip/ws", headers=headers) as socket:
        assert await socket.expect_close() == 1008


async def test_the_bridge_refuses_a_stale_session_token(app) -> None:
    issued = int(time.time()) - 600
    headers = _headers("call-1", timestamp=issued)
    async with _connect(app, "/telephony/sip/ws", headers=headers) as socket:
        assert await socket.expect_close() == 1008


async def test_the_bridge_refuses_a_mismatched_call_id(app) -> None:
    headers = _headers("call-1")
    async with _connect(app, "/telephony/sip/ws", headers=headers) as socket:
        await socket.send_text(_start("call-2"))
        assert await socket.expect_close() == 1008


async def test_the_bridge_refuses_a_start_that_is_not_json(app) -> None:
    headers = _headers("call-1")
    async with _connect(app, "/telephony/sip/ws", headers=headers) as socket:
        await socket.send_text("this is not json")
        assert await socket.expect_close() == 1008


async def test_the_bridge_refuses_an_unsupported_codec(app) -> None:
    headers = _headers("call-1")
    async with _connect(app, "/telephony/sip/ws", headers=headers) as socket:
        await socket.send_text(_start("call-1", codec="OPUS"))
        assert await socket.expect_close() == 1008


async def test_the_bridge_refuses_an_unknown_destination(app, sessionmaker_, monkeypatch) -> None:
    monkeypatch.setattr(sip_handler, "get_sessionmaker", lambda: sessionmaker_)
    headers = _headers("call-1")
    async with _connect(app, "/telephony/sip/ws", headers=headers) as socket:
        await socket.send_text(_start("call-1", to="+19999999999"))
        assert await socket.expect_close() == 1008


# ---------------------------------------------------------------------------
# The bound call: the number decides the tenant and the pipeline starts
# ---------------------------------------------------------------------------
async def test_the_bridge_binds_the_number_to_its_tenant_and_starts_the_pipeline(
    app, sessionmaker_, db, monkeypatch
) -> None:
    """A valid start event runs the agent for the tenant that owns the number."""
    from app.telephony.number_provisioning import PhoneNumber, PhoneNumberStatus
    from tests.conftest import make_tenant

    tenant = await make_tenant(db, name="SIP Tenant")
    number = PhoneNumber(
        tenant_id=tenant.id,
        provider="sip",
        e164="+15550001111",
        status=PhoneNumberStatus.ACTIVE.value,
    )
    db.add(number)
    await db.commit()

    # The bridge opens its own session; point it at the test database.
    monkeypatch.setattr(sip_handler, "get_sessionmaker", lambda: sessionmaker_)

    started: dict = {}

    async def _fake_run_voice_agent(**kwargs):
        started.update(kwargs)
        return None

    monkeypatch.setattr("app.agent.pipeline.run_voice_agent", _fake_run_voice_agent)

    headers = _headers("call-1")
    async with _connect(app, "/telephony/sip/ws", headers=headers) as socket:
        await socket.send_text(_start("call-1"))
        # The agent returns immediately, so the socket closes on its own.
        for _ in range(50):
            if started:
                break
            await asyncio.sleep(0.02)

    assert started, "the voice agent was never started"
    assert started["tenant"].id == tenant.id
    assert started["call"].to_number == "+15550001111"
    assert started["call"].tenant_id == tenant.id
    assert started["carrier_provider"] == "sip"
    assert started["call_sid"] == "call-1"
    assert isinstance(started["transport"], sip_handler.SipWebsocketTransport)


async def test_the_bridge_binds_the_call_to_the_tenant_of_the_number(
    app, sessionmaker_, db, monkeypatch
) -> None:
    """The call row is written for the tenant that owns the called number."""
    from sqlalchemy import select

    from app.db.models import Call
    from app.telephony.number_provisioning import PhoneNumber, PhoneNumberStatus
    from tests.conftest import make_tenant

    tenant = await make_tenant(db, name="SIP Tenant Two")
    number = PhoneNumber(
        tenant_id=tenant.id,
        provider="sip",
        e164="+15550002222",
        status=PhoneNumberStatus.ACTIVE.value,
    )
    db.add(number)
    await db.commit()

    monkeypatch.setattr(sip_handler, "get_sessionmaker", lambda: sessionmaker_)

    async def _fake_run_voice_agent(**_kwargs):
        return None

    monkeypatch.setattr("app.agent.pipeline.run_voice_agent", _fake_run_voice_agent)

    headers = _headers("call-bound")
    async with _connect(app, "/telephony/sip/ws", headers=headers) as socket:
        await socket.send_text(_start("call-bound", to="+15550002222"))
        await asyncio.sleep(0.4)

    async with sessionmaker_() as session:
        call = (
            await session.execute(select(Call).where(Call.call_sid == "call-bound"))
        ).scalar_one_or_none()
        assert call is not None
        assert call.tenant_id == tenant.id
        assert call.from_number == "+15559998888"
        assert call.to_number == "+15550002222"
