"""The gateway ramp harness, exercised against a real local WebSocket server.

The server below speaks the gateway's actual wire vocabulary (welcome -> hello ->
ready -> subscribe -> subscribed -> ping -> pong) over a real TCP socket, so this
file proves the harness's own HTTP upgrade, frame masking and latency bookkeeping.
It does not claim to be gateway-go: the Go service is measured by running the
harness against it, which is what ``loadtest/README_RUN.md`` documents.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
import uuid

import pytest

from loadtest.gateway_ramp import (
    AUDIO_PAYLOAD_BYTES,
    PING_INTERVAL_SECONDS,
    RtpSender,
    _mu_law_silence,
    _parse_args,
    _parse_udp_target,
    _percentile,
    _jitter,
    mint_token,
    run_session,
    service_stats,
)

pytestmark = pytest.mark.asyncio

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


async def _read_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    header = await reader.readexactly(2)
    opcode = header[0] & 0x0F
    masked = bool(header[1] & 0x80)
    length = header[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", await reader.readexactly(8))[0]
    mask = await reader.readexactly(4) if masked else b""
    payload = await reader.readexactly(length) if length else b""
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


def _server_frame(payload: dict) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    header = bytearray([0x81])
    if len(data) < 126:
        header.append(len(data))
    else:
        header.append(126)
        header.extend(struct.pack(">H", len(data)))
    return bytes(header) + data


async def _handshake(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    request = await reader.readuntil(b"\r\n\r\n")
    key = ""
    for line in request.decode("latin-1").splitlines():
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
    accept = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
    writer.write(
        (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode("ascii")
    )
    await writer.drain()


class GatewayStub:
    """A real socket server implementing the gateway's documented exchange."""

    def __init__(self, *, reject_hello: bool = False, deliveries: int = 0) -> None:
        self.reject_hello = reject_hello
        self.deliveries = deliveries
        self._server: asyncio.AbstractServer | None = None
        self.port = 0
        self.seen: list[dict] = []

    async def start(self) -> "GatewayStub":
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await _handshake(reader, writer)
            writer.write(
                _server_frame(
                    {
                        "type": "welcome",
                        "session_id": str(uuid.uuid4()),
                        "auth_required": True,
                        "auth_timeout_secs": 10,
                        "heartbeat_secs": 20,
                    }
                )
            )
            await writer.drain()
            while True:
                opcode, payload = await _read_frame(reader)
                if opcode == 0x8:
                    break
                if opcode != 0x1:
                    continue
                frame = json.loads(payload)
                self.seen.append(frame)
                kind = frame.get("type")
                if kind == "hello":
                    if self.reject_hello:
                        writer.write(
                            _server_frame({"type": "error", "code": "auth_failed", "message": "nope"})
                        )
                    else:
                        writer.write(
                            _server_frame(
                                {
                                    "type": "ready",
                                    "session_id": "s-1",
                                    "tenant_id": "t-1",
                                    "role": "owner",
                                }
                            )
                        )
                elif kind == "subscribe":
                    writer.write(_server_frame({"type": "subscribed", "room": "calls", "peers": 1}))
                    for index in range(self.deliveries):
                        writer.write(
                            _server_frame(
                                {
                                    "type": "delivery",
                                    "room": "calls",
                                    "kind": "call.updated",
                                    "payload": {"n": index},
                                    "event_id": str(uuid.uuid4()),
                                    "sent_at": "2026-10-24T09:00:00+00:00",
                                }
                            )
                        )
                elif kind == "ping":
                    writer.write(_server_frame({"type": "pong", "server_time": "now"}))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()


async def test_a_real_session_measures_every_handshake_stage():
    stub = await GatewayStub(deliveries=2).start()
    try:
        sample = await run_session(
            index=0,
            host="127.0.0.1",
            port=stub.port,
            path="/ws",
            token="dashboard-token",
            duration=PING_INTERVAL_SECONDS * 1.2,
            connect_timeout=5.0,
        )
    finally:
        await stub.stop()

    assert sample.connected is True
    assert sample.connect_ms is not None and sample.connect_ms >= 0
    assert sample.ready_ms is not None
    assert sample.subscribed_ms is not None
    assert sample.pings_sent >= 1
    assert sample.pongs_received == sample.pings_sent
    assert sample.pong_rtts_ms and all(value >= 0 for value in sample.pong_rtts_ms)
    assert sample.deliveries == 2
    assert sample.error == ""
    assert stub.seen[0]["type"] == "hello"


async def test_a_rejected_hello_is_reported_not_hidden():
    stub = await GatewayStub(reject_hello=True).start()
    try:
        sample = await run_session(
            index=1,
            host="127.0.0.1",
            port=stub.port,
            path="/ws",
            token="dashboard-token",
            duration=0.1,
            connect_timeout=5.0,
        )
    finally:
        await stub.stop()

    assert sample.error.startswith("session_failed") or sample.error == "no_ready"
    assert sample.ready_ms is None


async def test_a_refused_upgrade_is_reported_as_a_connect_failure():
    async def _refuse(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.readuntil(b"\r\n\r\n")
        writer.write(b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(_refuse, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        sample = await run_session(
            index=2,
            host="127.0.0.1",
            port=port,
            path="/ws",
            token="token",
            duration=0.1,
            connect_timeout=5.0,
        )
    finally:
        server.close()
        await server.wait_closed()

    assert sample.connected is False
    assert sample.error.startswith("connect_failed")


def test_rtp_packets_are_well_formed_and_real_time_paced():
    sender = RtpSender("127.0.0.1", 9)
    try:
        first = sender.packet(_mu_law_silence(1))
        second = sender.packet(_mu_law_silence(1))
    finally:
        sender.close()

    version_byte, payload_type = first[0], first[1]
    sequence_1, timestamp_1, ssrc_1 = struct.unpack(">HII", first[2:12])
    sequence_2, timestamp_2, ssrc_2 = struct.unpack(">HII", second[2:12])

    assert version_byte == 0x80
    assert payload_type == 0  # PCMU
    assert sequence_2 == sequence_1 + 1
    assert timestamp_2 - timestamp_1 == 160  # 20 ms at 8 kHz
    assert ssrc_1 == ssrc_2
    assert len(first[12:]) == AUDIO_PAYLOAD_BYTES
    assert first[12:] == b"\xff" * AUDIO_PAYLOAD_BYTES


def test_mu_law_silence_is_the_g711_silence_byte():
    payload = _mu_law_silence(3)

    assert payload == b"\xff" * (AUDIO_PAYLOAD_BYTES * 3)


def test_udp_candidate_is_parsed_from_an_sdp_answer():
    sdp = (
        "v=0\r\n"
        "o=- 1 1 IN IP4 10.0.0.5\r\n"
        "a=candidate:1 1 udp 2130706431 10.0.0.5 40000 typ host\r\n"
    )

    assert _parse_udp_target({"sdp": sdp}) == "10.0.0.5:40000"
    assert _parse_udp_target({"candidate": {"address": "10.0.0.6", "port": 41000}}) == "10.0.0.6:41000"
    assert _parse_udp_target({}) == ""


def test_percentile_and_jitter_are_computed_from_the_samples():
    assert _percentile([], 50) is None
    assert _percentile([10.0], 95) == 10.0
    assert _percentile([10.0, 20.0, 30.0], 50) == 20.0
    assert _jitter([10.0]) is None
    assert _jitter([10.0, 10.0, 10.0]) == 0.0
    assert _jitter([0.0, 20.0]) == pytest.approx(10.0)


def test_service_stats_never_invents_a_number_for_an_unknown_service():
    stats = service_stats("voxdesk-no-such-service-process-xyz")

    assert stats["available"] is False
    assert stats["reason"]


def test_service_stats_reads_a_real_process():
    stats = service_stats("pytest")

    assert stats["available"] is True
    assert stats["source"] == "/proc"
    assert stats["rss_mb"] > 0
    assert stats["cpu_seconds"] >= 0


def test_mint_token_produces_a_verifiable_dashboard_token():
    from app.auth.jwt import decode_access_token

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()

    token = mint_token(str(tenant_id), str(user_id), "owner")
    claims = decode_access_token(token)

    assert str(claims.tenant_id) == str(tenant_id)
    assert str(claims.user_id) == str(user_id)


def test_the_cli_requires_a_tenant_and_user():
    with pytest.raises(SystemExit):
        _parse_args(["--ramp", "1", "--step-seconds", "0.1"])


def test_the_cli_defaults_to_the_loopback_ramp_and_documents_its_targets():
    args = _parse_args(["--tenant-id", str(uuid.uuid4()), "--user-id", str(uuid.uuid4())])

    assert args.ws_target == "localhost:8790"
    assert args.ramp == "10,25,50,100,200,500"
    assert args.step_seconds == 10.0
