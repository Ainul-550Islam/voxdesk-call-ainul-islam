"""Small test-only SIP media WebSocket peer for the Compose stack.

It validates the same bearer/HMAC headers as the application endpoint, records
start events, and accepts the mixed G.711 binary / JSON control protocol. It is
not a production app and performs no call routing or media processing.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from typing import Any

from websockets.legacy.server import WebSocketServerProtocol, serve

TOKEN = os.environ.get("SIP_GATEWAY_TOKEN", "")
HTTP_PORT = int(os.environ.get("MEDIA_TEST_HTTP_PORT", "8766"))
WS_PORT = int(os.environ.get("MEDIA_TEST_WS_PORT", "8765"))
MAX_SKEW_SECONDS = 120
starts: list[dict[str, Any]] = []
starts_lock = asyncio.Lock()


def _valid_session(headers: Any) -> tuple[bool, str]:
    bearer = str(headers.get("Authorization", ""))
    presented = bearer[7:].strip() if bearer.lower().startswith("bearer ") else ""
    if not TOKEN or not hmac.compare_digest(presented, TOKEN):
        return False, "bad bearer token"

    call_id = str(headers.get("X-VoxDesk-SIP-Call-Id", "")).strip()
    timestamp = str(headers.get("X-VoxDesk-SIP-Timestamp", "")).strip()
    nonce = str(headers.get("X-VoxDesk-SIP-Nonce", "")).strip()
    signature = str(headers.get("X-VoxDesk-SIP-Session", "")).strip().lower()
    try:
        issued = int(timestamp)
    except ValueError:
        return False, "bad timestamp"
    if not call_id or not nonce or abs(int(time.time()) - issued) > MAX_SKEW_SECONDS:
        return False, "stale or incomplete session"
    material = f"{call_id}:{timestamp}:{nonce}".encode()
    expected = hmac.new(TOKEN.encode(), material, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return False, "bad session signature"
    return True, call_id


async def websocket_handler(websocket: WebSocketServerProtocol) -> None:
    valid, detail = _valid_session(websocket.request_headers)
    if not valid:
        await websocket.close(code=1008, reason=detail[:100])
        return
    call_id = detail
    try:
        raw = await asyncio.wait_for(websocket.recv(), timeout=5)
        if not isinstance(raw, str):
            await websocket.close(code=1008, reason="start frame must be JSON text")
            return
        start = json.loads(raw)
        if not isinstance(start, dict) or str(start.get("type", "")).lower() != "start":
            await websocket.close(code=1008, reason="start event required")
            return
        if str(start.get("call_id", "")) != call_id:
            await websocket.close(code=1008, reason="call id mismatch")
            return
        direction = str(start.get("direction", "inbound")).lower()
        if direction not in {"inbound", "outbound"}:
            await websocket.close(code=1008, reason="invalid call direction")
            return
        async with starts_lock:
            starts.append(
                {
                    "call_id": call_id,
                    "from": str(start.get("from", "")),
                    "to": str(start.get("to", "")),
                    "codec": str(start.get("codec", "")),
                    "direction": direction,
                }
            )
        async for _message in websocket:
            # Binary G.711 and control events are accepted and discarded.
            pass
    except (asyncio.TimeoutError, json.JSONDecodeError, ConnectionError):
        await websocket.close(code=1008, reason="invalid media handshake")
    except Exception as exc:
        # A client close is normal in this test process; other exceptions are
        # intentionally reduced to a close without exposing details.
        if not websocket.closed:
            await websocket.close(code=1011, reason=type(exc).__name__[:100])


async def _http_response(writer: asyncio.StreamWriter, status: int, body: bytes) -> None:
    label = "OK" if status == 200 else "Not Found"
    headers = (
        f"HTTP/1.1 {status} {label}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    writer.write(headers + body)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


async def http_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), timeout=3)
        fields = line.decode("latin-1").strip().split()
        if len(fields) < 2 or fields[0] != "GET":
            await _http_response(writer, 404, b'{"error":"not_found"}')
            return
        while True:
            header = await asyncio.wait_for(reader.readline(), timeout=3)
            if header in {b"\r\n", b"\n", b""}:
                break
        path = fields[1].split("?", 1)[0]
        if path == "/healthz":
            body = b'{"ok":true}'
        elif path == "/starts":
            async with starts_lock:
                body = json.dumps(starts, separators=(",", ":")).encode()
        else:
            await _http_response(writer, 404, b'{"error":"not_found"}')
            return
        await _http_response(writer, 200, body)
    except (asyncio.TimeoutError, ConnectionError, UnicodeDecodeError):
        writer.close()
        await writer.wait_closed()


async def main() -> None:
    if not TOKEN:
        raise SystemExit("SIP_GATEWAY_TOKEN is required")
    ws_server = await serve(
        websocket_handler,
        "0.0.0.0",
        WS_PORT,
        ping_interval=20,
        max_size=1_048_576,
    )
    http_server = await asyncio.start_server(http_handler, "0.0.0.0", HTTP_PORT)
    async with ws_server, http_server:
        await asyncio.gather(ws_server.wait_closed(), http_server.serve_forever())


if __name__ == "__main__":
    asyncio.run(main())
