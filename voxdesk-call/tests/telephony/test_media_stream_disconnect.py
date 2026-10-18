"""Twilio media stream handshake: a client that disconnects must not provoke a second close.

Regression: a client that dropped during the handshake raised WebSocketDisconnect, and the
handler then called websocket.close() on the already-closed socket. The ASGI server logged
"Unexpected ASGI message 'websocket.close' ... response already completed". A timeout still
closes the socket with 1008.
"""

from __future__ import annotations

import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from app.telephony import twilio_handler as th


class _FakeWS:
    def __init__(self, receive_exc: BaseException | None = None, *, hang: bool = False):
        self.query_params: dict[str, str] = {}
        self._receive_exc = receive_exc
        self._hang = hang
        self.closed: list[int] = []

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        if self._hang:
            await asyncio.sleep(3600)
        raise self._receive_exc

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed.append(code)


@pytest.mark.asyncio
async def test_client_disconnect_during_handshake_does_not_close_again():
    ws = _FakeWS(WebSocketDisconnect(code=1000))
    await th.media_stream(ws)  # must return cleanly, without raising
    assert ws.closed == []


@pytest.mark.asyncio
async def test_handshake_timeout_still_closes_with_policy_violation(monkeypatch):
    monkeypatch.setattr(th.settings, "stream_handshake_timeout_seconds", 0.05)
    ws = _FakeWS(hang=True)
    await th.media_stream(ws)
    assert ws.closed == [1008]


class _ScriptedWS(_FakeWS):
    """Delivers a scripted sequence of frames; an exception item is raised."""

    def __init__(self, items):
        super().__init__()
        self._items = list(items)

    async def receive_text(self) -> str:
        item = self._items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.mark.asyncio
async def test_malformed_start_frame_closes_once_with_policy_violation():
    ws = _ScriptedWS(['{"event":"connected"}', "not json"])
    await th.media_stream(ws)  # must not raise
    assert ws.closed == [1008]


@pytest.mark.asyncio
async def test_binary_frame_during_handshake_closes_once_with_policy_violation():
    # Starlette's receive_text() on a binary frame raises KeyError("text").
    ws = _ScriptedWS([KeyError("text")])
    await th.media_stream(ws)
    assert ws.closed == [1008]


@pytest.mark.asyncio
async def test_safe_close_swallows_completed_response_errors():
    class _Done(_FakeWS):
        async def close(self, code: int = 1000, reason: str = "") -> None:
            raise RuntimeError('Unexpected ASGI message \'websocket.close\', after sending \'websocket.close\'')

    await th._safe_close(_Done(), 1008)  # must not raise


@pytest.mark.asyncio
async def test_safe_close_swallows_disconnect_on_close():
    class _Gone(_FakeWS):
        async def close(self, code: int = 1000, reason: str = "") -> None:
            raise WebSocketDisconnect(code=1001)

    await th._safe_close(_Gone(), 1008)
