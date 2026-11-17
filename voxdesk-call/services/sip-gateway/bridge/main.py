"""One-call RTP <-> VoxDesk WebSocket bridge.

The wire protocol matches ``app/telephony/sip/sip_handler.py``: the gateway
opens an authenticated WebSocket, sends one JSON ``start`` control message,
then sends raw negotiated G.711 payloads as binary frames. DTMF is JSON text;
application audio returns as raw G.711 binary frames and is wrapped in RTP.

Environment:

    BRIDGE_CALL_ID       call id used in the HMAC-bound WebSocket handshake
    BRIDGE_RTP_PORT      local UDP port rtpengine sends media to
    BRIDGE_REMOTE_HOST   rtpengine address to send app audio back to
    BRIDGE_REMOTE_PORT   rtpengine RTP port
    BRIDGE_APP_WS        app WebSocket URL, e.g. ws://app:8000/telephony/sip/ws
    BRIDGE_TOKEN         shared SIP gateway token (never placed in the URL)
    BRIDGE_FROM_NUMBER   calling number (E.164)
    BRIDGE_TO_NUMBER     called number (E.164)
    BRIDGE_DIRECTION     inbound (default) or outbound
    BRIDGE_CODEC         PCMU (default) or PCMA

Exit codes: 0 for a normal close, 2 for invalid configuration, and 3 when the
app refuses or loses the media socket.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
from typing import Any

from . import codecs
from .session import FRAME_AUDIO, FRAME_DTMF, BridgeSession

HEARTBEAT_SECONDS = 20.0
RECONNECT_DELAYS = (0.5, 1.0, 2.0, 5.0)


def _session_headers(call_id: str, token: str) -> dict[str, str]:
    """Create the same freshness-bounded HMAC headers as the Go gateway."""
    timestamp = str(int(time.time()))
    nonce = secrets.token_hex(12)
    material = f"{call_id}:{timestamp}:{nonce}".encode("utf-8")
    signature = hmac.new(token.encode("utf-8"), material, hashlib.sha256).hexdigest()
    return {
        "Authorization": f"Bearer {token}",
        "X-VoxDesk-SIP-Call-Id": call_id,
        "X-VoxDesk-SIP-Timestamp": timestamp,
        "X-VoxDesk-SIP-Nonce": nonce,
        "X-VoxDesk-SIP-Session": signature,
    }


class RtpEndpoint(asyncio.DatagramProtocol):
    """Receives RTP from rtpengine and hands bounded packets to the session."""

    def __init__(self, queue: asyncio.Queue[bytes]) -> None:
        self.queue = queue
        self.transport: asyncio.DatagramTransport | None = None
        self.dropped_packets = 0

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: Any) -> None:
        del addr
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            # Favor fresh audio over a growing delay: discard the oldest queued
            # datagram, then keep this newest packet.
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:  # a concurrent consumer just made room
                pass
            try:
                self.queue.put_nowait(data)
            except asyncio.QueueFull:  # defensive against another producer
                self.dropped_packets += 1
            else:
                self.dropped_packets += 1


async def pump_rtp_to_app(
    session: BridgeSession,
    queue: asyncio.Queue[bytes],
    websocket: Any,
) -> None:
    while True:
        packet = await queue.get()
        try:
            frames = session.ingest_g711(packet)
        except ValueError:
            # Malformed packets from the network are dropped, not fatal to the call.
            continue
        for frame in frames:
            if frame.kind == FRAME_AUDIO:
                if frame.payload:
                    await websocket.send(frame.payload)
            elif frame.kind == FRAME_DTMF and frame.meta.get("phase") == "start":
                # The app models a keypress as one InputDTMFFrame. Do not send
                # a second digit for the RFC 2833 end-of-event repeat packets.
                await websocket.send(
                    json.dumps(
                        {
                            "type": "dtmf",
                            "event": "dtmf",
                            "digit": frame.meta.get("digit", ""),
                        },
                        separators=(",", ":"),
                    )
                )


async def pump_app_to_rtp(
    session: BridgeSession,
    websocket: Any,
    endpoint: RtpEndpoint,
    remote: tuple[str, int],
) -> None:
    sequence = 0
    timestamp = 0
    async for message in websocket:
        if isinstance(message, (bytes, bytearray)):
            payload = bytes(message)
            packet = session.egress_g711(payload, sequence=sequence, timestamp=timestamp)
            if packet and endpoint.transport is not None:
                endpoint.transport.sendto(packet, remote)
            sequence = (sequence + 1) & 0xFFFF
            # G.711 uses one RTP clock tick per sample at 8 kHz.
            timestamp = (timestamp + len(payload)) & 0xFFFFFFFF
            continue

        try:
            control = json.loads(message)
        except (TypeError, ValueError):
            continue
        if not isinstance(control, dict):
            continue
        if str(control.get("type") or control.get("event") or "").lower() in {
            "end",
            "stop",
        }:
            return


async def _run_connected_call(
    *,
    websocket: Any,
    session: BridgeSession,
    queue: asyncio.Queue[bytes],
    endpoint: RtpEndpoint,
    remote: tuple[str, int],
    call_id: str,
    from_number: str,
    to_number: str,
    direction: str,
) -> None:
    await websocket.send(
        json.dumps(
            {
                "type": "start",
                "event": "start",
                "call_id": call_id,
                "from": from_number,
                "to": to_number,
                "codec": session.codec,
                "direction": direction,
            },
            separators=(",", ":"),
        )
    )
    tasks = {
        asyncio.create_task(pump_rtp_to_app(session, queue, websocket)),
        asyncio.create_task(pump_app_to_rtp(session, websocket, endpoint, remote)),
    }
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exception = task.exception()
            if exception is not None:
                raise exception
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def run(
    *,
    call_id: str,
    rtp_port: int,
    remote_host: str,
    remote_port: int,
    app_ws: str,
    token: str,
    codec: str,
    from_number: str,
    to_number: str,
    direction: str = "inbound",
) -> int:
    try:
        import websockets
    except ModuleNotFoundError:  # pragma: no cover - deployment image dependency
        print("bridge: websockets is not installed", file=sys.stderr)
        return 2

    try:
        codecs.codec_for(codec)
    except ValueError as exc:
        print(f"bridge: {exc}", file=sys.stderr)
        return 2
    if not call_id or not token or not from_number or not to_number:
        print("bridge: call id, token, from number and to number are required", file=sys.stderr)
        return 2
    if direction not in {"inbound", "outbound"}:
        print("bridge: direction must be inbound or outbound", file=sys.stderr)
        return 2
    if not (1 <= rtp_port <= 65535 and 1 <= remote_port <= 65535):
        print("bridge: RTP ports must be between 1 and 65535", file=sys.stderr)
        return 2
    if not remote_host.strip():
        print("bridge: BRIDGE_REMOTE_HOST is required", file=sys.stderr)
        return 2
    if not app_ws.startswith(("ws://", "wss://")):
        print("bridge: BRIDGE_APP_WS must use ws:// or wss://", file=sys.stderr)
        return 2

    session = BridgeSession(call_id=call_id, codec=codec)
    queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)
    loop = asyncio.get_running_loop()
    transport, endpoint = await loop.create_datagram_endpoint(
        lambda: RtpEndpoint(queue), local_addr=("0.0.0.0", rtp_port)
    )
    remote = (remote_host, remote_port)
    headers = _session_headers(call_id, token)
    attempt = 0
    try:
        while True:
            connected = False
            try:
                async with websockets.connect(
                    app_ws,
                    extra_headers=headers,
                    ping_interval=HEARTBEAT_SECONDS,
                    max_size=1_048_576,
                    max_queue=32,
                ) as websocket:
                    connected = True
                    await _run_connected_call(
                        websocket=websocket,
                        session=session,
                        queue=queue,
                        endpoint=endpoint,
                        remote=remote,
                        call_id=call_id,
                        from_number=from_number,
                        to_number=to_number,
                        direction=direction,
                    )
                    return 0
            except asyncio.CancelledError:
                raise
            except websockets.WebSocketException as exc:
                if connected:
                    print(f"bridge: media WebSocket failed: {exc}", file=sys.stderr)
                    return 3
                if attempt >= len(RECONNECT_DELAYS):
                    print(f"bridge: app refused the media socket: {exc}", file=sys.stderr)
                    return 3
                await asyncio.sleep(RECONNECT_DELAYS[attempt])
                attempt += 1
            except (OSError, asyncio.TimeoutError) as exc:
                if connected or attempt >= len(RECONNECT_DELAYS):
                    print(f"bridge: cannot connect to the app media socket: {exc}", file=sys.stderr)
                    return 3
                await asyncio.sleep(RECONNECT_DELAYS[attempt])
                attempt += 1
    finally:
        transport.close()


def _required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def main(argv: list[str] | None = None) -> int:
    del argv  # configuration is environment-based, including in the container
    try:
        call_id = _required_env("BRIDGE_CALL_ID")
        rtp_port = int(_required_env("BRIDGE_RTP_PORT"))
        app_ws = _required_env("BRIDGE_APP_WS")
        token = _required_env("BRIDGE_TOKEN")
        from_number = _required_env("BRIDGE_FROM_NUMBER")
        to_number = _required_env("BRIDGE_TO_NUMBER")
        remote_port = int(os.environ.get("BRIDGE_REMOTE_PORT", "5004"))
    except (ValueError, TypeError) as exc:
        print(f"bridge: invalid configuration: {exc}", file=sys.stderr)
        return 2

    return asyncio.run(
        run(
            call_id=call_id,
            rtp_port=rtp_port,
            remote_host=os.environ.get("BRIDGE_REMOTE_HOST", "127.0.0.1").strip(),
            remote_port=remote_port,
            app_ws=app_ws,
            token=token,
            codec=os.environ.get("BRIDGE_CODEC", "PCMU").strip().upper(),
            from_number=from_number,
            to_number=to_number,
            direction=os.environ.get("BRIDGE_DIRECTION", "inbound").strip().lower(),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
