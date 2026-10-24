"""The Vonage media handler: the answer URL, the signed event webhook and the
media socket.

The answer URL is what Vonage fetches when a call arrives; it must answer
with an NCCO that opens the bidirectional media socket, and it must refuse
(501) when the deployment has no Vonage credential set. The event webhook
must refuse anything that is not signed with the shared secret.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from starlette.websockets import WebSocketDisconnect

from app.core.config import settings
from app.telephony.providers.vonage import VonageAdapter
from app.telephony.vonage_handler import (
    create_vonage_stream_token,
    verify_vonage_stream_token,
)
from tests.conftest import make_tenant

_TOKEN_SECRET = "vonage-signing-secret-for-tests"


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

    async def send_bytes(self, data: bytes) -> None:
        await self._receive_queue.put({"type": "websocket.receive", "bytes": data})

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


# ---------------------------------------------------------------------------
# The answer URL
# ---------------------------------------------------------------------------
async def test_the_answer_url_is_501_without_the_credential_set(client, monkeypatch) -> None:
    monkeypatch.setattr(
        VonageAdapter, "supports_ai_media_stream", property(lambda self: False)
    )
    response = await client.get("/telephony/vonage/answer?to=%2B15550001111")
    assert response.status_code == 501
    assert response.json()["detail"]["code"] == "unsupported_capability"


async def test_the_answer_url_returns_a_media_streaming_ncco(
    client, db, monkeypatch
) -> None:
    monkeypatch.setattr(
        VonageAdapter, "supports_ai_media_stream", property(lambda self: True)
    )
    tenant = await make_tenant(db, name="Vonage Media Tenant")
    response = await client.get(
        "/telephony/vonage/answer",
        params={
            "to": tenant.twilio_number,
            "from": "+15559998888",
            "uuid": "vonage-call-0001",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    ncco = body["ncco"]
    assert isinstance(ncco, list) and len(ncco) == 1
    action = ncco[0]
    assert action["action"] == "connect"
    assert action["type"] == "websocket"
    assert action["contentType"] == "audio/l16;rate=16000"
    assert "/telephony/vonage/ws" in action["uri"]
    assert "call_id=vonage-call-0001" in action["uri"]
    assert action["eventUrl"][0].endswith("/telephony/vonage/events")
    token = action["headers"]["X-VoxDesk-Vonage-Token"]
    assert verify_vonage_stream_token("vonage-call-0001", token)
    assert body["uuid"] == "vonage-call-0001"


async def test_the_answer_url_rejects_an_unknown_number(client, monkeypatch) -> None:
    monkeypatch.setattr(
        VonageAdapter, "supports_ai_media_stream", property(lambda self: True)
    )
    response = await client.get("/telephony/vonage/answer?to=%2B19999999999")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "not_found"


async def test_the_answer_url_requires_a_destination(client, monkeypatch) -> None:
    monkeypatch.setattr(
        VonageAdapter, "supports_ai_media_stream", property(lambda self: True)
    )
    response = await client.get("/telephony/vonage/answer")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "validation"


# ---------------------------------------------------------------------------
# The event webhook
# ---------------------------------------------------------------------------
def _signature(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def test_the_event_webhook_rejects_an_unsigned_event(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "vonage_signature_secret", _TOKEN_SECRET, raising=False)
    response = await client.post(
        "/telephony/vonage/events", json={"uuid": "vonage-call-0001", "status": "completed"}
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "signature_rejected"


async def test_the_event_webhook_rejects_a_wrong_signature(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "vonage_signature_secret", _TOKEN_SECRET, raising=False)
    body = json.dumps({"uuid": "vonage-call-0001", "status": "completed"}).encode()
    response = await client.post(
        "/telephony/vonage/events",
        content=body,
        headers={"content-type": "application/json", "x-vonage-signature": "0" * 64},
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "signature_rejected"


async def test_the_event_webhook_accepts_a_signed_event(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "vonage_signature_secret", _TOKEN_SECRET, raising=False)
    body = json.dumps({"uuid": "vonage-unknown-call", "status": "completed"}).encode()
    response = await client.post(
        "/telephony/vonage/events",
        content=body,
        headers={
            "content-type": "application/json",
            "x-vonage-signature": _signature(_TOKEN_SECRET, body),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "applied": False}


# ---------------------------------------------------------------------------
# The media socket
# ---------------------------------------------------------------------------
def test_the_stream_token_is_bound_to_its_call() -> None:
    token = create_vonage_stream_token("vonage-call-0001")
    assert verify_vonage_stream_token("vonage-call-0001", token)
    assert not verify_vonage_stream_token("vonage-call-0002", token)
    assert not verify_vonage_stream_token("vonage-call-0001", "not-a-token")
    assert not verify_vonage_stream_token("vonage-call-0001", None)


def test_an_expired_stream_token_is_refused() -> None:
    issued = int(time.time()) - 3600
    token = create_vonage_stream_token("vonage-call-0001", issued_at=issued)
    assert not verify_vonage_stream_token("vonage-call-0001", token)


async def test_the_media_socket_refuses_a_missing_token(app) -> None:
    async with _connect(app, "/telephony/vonage/ws?call_id=vonage-call-0001") as socket:
        assert await socket.expect_close() == 1008


async def test_the_media_socket_refuses_a_token_of_another_call(app) -> None:
    token = create_vonage_stream_token("vonage-call-0001")
    async with _connect(
        app, f"/telephony/vonage/ws?call_id=vonage-call-0002&token={token}"
    ) as socket:
        assert await socket.expect_close() == 1008
