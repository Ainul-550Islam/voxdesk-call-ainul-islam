"""Source-level checks for the disposable media peer used by docker-compose.sip.yml."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib.util
import time
from pathlib import Path

import pytest


_MEDIA_SERVER = (
    Path(__file__).resolve().parents[2]
    / "services"
    / "sip-gateway"
    / "test-media"
    / "server.py"
)
_SPEC = importlib.util.spec_from_file_location("sip_test_media_server", _MEDIA_SERVER)
assert _SPEC is not None and _SPEC.loader is not None
media = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(media)


@pytest.fixture(autouse=True)
def _test_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media, "TOKEN", "local-e2e-secret")


def _headers(*, call_id: str = "call-123", timestamp: int | None = None) -> dict[str, str]:
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    nonce = "nonce-456"
    material = f"{call_id}:{stamp}:{nonce}".encode()
    signature = hmac.new(b"local-e2e-secret", material, hashlib.sha256).hexdigest()
    return {
        "Authorization": "Bearer local-e2e-secret",
        "X-VoxDesk-SIP-Call-Id": call_id,
        "X-VoxDesk-SIP-Timestamp": stamp,
        "X-VoxDesk-SIP-Nonce": nonce,
        "X-VoxDesk-SIP-Session": signature,
    }


def test_media_peer_accepts_only_a_fresh_bearer_and_hmac_session() -> None:
    valid, call_id = media._valid_session(_headers())
    assert valid is True
    assert call_id == "call-123"

    invalid_bearer = _headers()
    invalid_bearer["Authorization"] = "Bearer wrong"
    assert media._valid_session(invalid_bearer) == (False, "bad bearer token")

    invalid_signature = _headers()
    invalid_signature["X-VoxDesk-SIP-Session"] = "0" * 64
    assert media._valid_session(invalid_signature) == (False, "bad session signature")

    stale = _headers(timestamp=int(time.time()) - media.MAX_SKEW_SECONDS - 1)
    assert media._valid_session(stale) == (False, "stale or incomplete session")


@pytest.mark.asyncio
async def test_health_endpoint_returns_a_valid_http_response() -> None:
    server = await asyncio.start_server(media.http_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=2)
        assert response.startswith(b"HTTP/1.1 200 OK\r\n")
        assert b"Content-Length: 11\r\n" in response
        assert response.endswith(b'{"ok":true}')
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_unknown_media_peer_endpoint_is_not_found() -> None:
    server = await asyncio.start_server(media.http_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(b"GET /admin HTTP/1.1\r\nHost: localhost\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), timeout=2)
        assert response.startswith(b"HTTP/1.1 404 Not Found\r\n")
        assert response.endswith(b'{"error":"not_found"}')
        writer.close()
        await writer.wait_closed()
