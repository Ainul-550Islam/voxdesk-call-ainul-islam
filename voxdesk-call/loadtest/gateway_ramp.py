#!/usr/bin/env python3
"""Gateway ramp: concurrent real WebSocket sessions against gateway-go.

What it actually does, per session, with nothing simulated:

1. mints a **real dashboard access token** with ``app.auth.jwt.create_access_token``
   signed by ``JWT_SECRET`` -- the same secret and the same claims the API issues,
   because the gateway verifies that token itself;
2. opens a real WebSocket to ``GET /ws``, times the TCP+HTTP upgrade, reads the
   ``welcome`` frame, sends ``hello`` and times ``ready``, subscribes to ``calls``
   and times ``subscribed``;
3. for the duration of the step, sends ``ping`` on a fixed cadence and records the
   ``pong`` round-trip time, so *jitter* is the deviation of consecutive RTTs and
   *drop* is a ping whose pong never arrived before the next deadline;
4. when ``--publish-hz`` is set it drives ``POST /ingest/v1/publish`` at that rate
   with the gateway's Bearer secret and measures delivery latency end to end --
   that is the only way to measure the notice plane honestly, since a gateway with
   no publisher delivers nothing;
5. when ``--engine-url`` is given it also drives the Rust media engine: a real
   ``POST /v1/signal`` join + publish, then real 8 kHz G.711 mu-law RTP packets
   (20 ms, 160-byte payloads, correct RTP header + sequence + timestamp) sent over
   UDP at true real-time cadence, plus ``GET /metrics`` scrapes before and after so
   the engine's own receive counters say whether the audio was accepted.

CPU and RSS are *not* guessed from a formula: they are read from ``/proc`` for any
process whose command line matches the service name, or from ``docker stats`` when
the service runs in a container. Each step also scrapes the services' own
``/metrics`` exposition (``--gateway-metrics-url`` with ``--metrics-token``,
``--engine-metrics-url``) and reports the per-series growth across the step, so
"the gateway never dropped a delivery" is the gateway's own counter, not this
harness's opinion. If a source is unavailable the report says ``available: false``
with the reason and no number is invented.

Every target except loopback needs ``LOADTEST_ALLOW_REMOTE=1`` (``loadtest/safety.py``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import socket
import statistics
import struct
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from .safety import allow_remote, validate_target
except ImportError:  # pragma: no cover - direct script execution
    from safety import allow_remote, validate_target  # type: ignore[no-redef]

DEFAULT_RAMP = "10,25,50,100,200,500"
PING_INTERVAL_SECONDS = 1.0
PONG_TIMEOUT_SECONDS = 5.0
AUDIO_FRAME_MS = 20
AUDIO_PAYLOAD_BYTES = 160  # 8 kHz G.711: 20 ms == 160 bytes
RTP_CLOCK_HZ = 8000
RTCP_NONE = 0


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass
class SessionSample:
    """One session's measurements. Missing data stays ``None``, never zero."""

    index: int
    connected: bool = False
    connect_ms: float | None = None
    ready_ms: float | None = None
    subscribed_ms: float | None = None
    pings_sent: int = 0
    pongs_received: int = 0
    pong_rtts_ms: list[float] = field(default_factory=list)
    deliveries: int = 0
    delivery_latencies_ms: list[float] = field(default_factory=list)
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "connected": self.connected,
            "connect_ms": self.connect_ms,
            "ready_ms": self.ready_ms,
            "subscribed_ms": self.subscribed_ms,
            "pings_sent": self.pings_sent,
            "pongs_received": self.pongs_received,
            "deliveries": self.deliveries,
            "error": self.error,
        }


@dataclass
class AudioSample:
    """The media-engine audio leg for one session."""

    index: int
    joined: bool = False
    published: bool = False
    udp_target: str = ""
    frames_sent: int = 0
    frames_late: int = 0
    send_errors: int = 0
    send_gaps_ms: list[float] = field(default_factory=list)
    error: str = ""

    @property
    def gap_jitter_ms(self) -> float | None:
        if len(self.send_gaps_ms) < 2:
            return None
        return round(statistics.pstdev(self.send_gaps_ms), 3)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "joined": self.joined,
            "published": self.published,
            "udp_target": self.udp_target,
            "frames_sent": self.frames_sent,
            "frames_late": self.frames_late,
            "send_errors": self.send_errors,
            "gap_jitter_ms": self.gap_jitter_ms,
            "error": self.error,
        }


def _percentile(values: list[float], q: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (max(0.0, min(100.0, q)) / 100.0) * (len(ordered) - 1)
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def _jitter(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(statistics.pstdev(values), 3)


# --------------------------------------------------------------------------- #
# Service stats: /proc for bare processes, docker for containers
# --------------------------------------------------------------------------- #


def _proc_stats(pattern: str) -> dict[str, Any] | None:
    """CPU jiffies and RSS for the first process whose cmdline matches."""
    matches: list[Path] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if pattern and pattern in cmdline:
            matches.append(entry)
    if not matches:
        return None
    totals = {"utime_ticks": 0, "stime_ticks": 0, "rss_kb": 0, "threads": 0, "processes": 0}
    for entry in matches:
        try:
            stat_fields = (entry / "stat").read_text().split()
            status = (entry / "status").read_text()
        except OSError:
            continue
        totals["utime_ticks"] += int(stat_fields[13])
        totals["stime_ticks"] += int(stat_fields[14])
        totals["threads"] += int(stat_fields[19])
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                totals["rss_kb"] += int(line.split()[1])
                break
        totals["processes"] += 1
    return totals


def _docker_stats(container: str) -> dict[str, Any] | None:
    if not shutil.which("docker"):
        return None
    try:
        completed = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", container],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0 or not completed.stdout.strip():
        return None
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[0])
    except ValueError:
        return None
    return {"docker": payload}


def scrape_metrics(url: str, token: str = "") -> dict[str, Any]:
    """Read a service's own ``/metrics`` exposition.

    The gateway and the engine both publish Prometheus text. Only named counters
    are extracted; the rest of the exposition is left where it is. A refused or
    unreachable scrape is reported as such -- an empty dict would read as "zero
    traffic", which is a different, wrong answer.
    """
    import urllib.error
    import urllib.request

    if not (url or "").strip():
        return {"available": False, "reason": "no metrics URL configured"}
    request = urllib.request.Request(url, headers={"Accept": "text/plain"})
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - operator-supplied URL
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return {"available": False, "status": exc.code, "reason": "metrics endpoint refused the request"}
    except (urllib.error.URLError, OSError) as exc:
        return {"available": False, "reason": f"metrics unreachable: {type(exc).__name__}"}

    counters: dict[str, float] = {}
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, value = line.partition(" ")
        value = value.split()[0] if value else ""
        if not name or not value:
            continue
        try:
            counters[name] = float(value)
        except ValueError:
            continue
    return {"available": True, "series": len(counters), "counters": counters}


def _counter_delta(before: dict, after: dict) -> dict[str, float]:
    """Per-series growth across a ramp step; missing series are omitted."""
    if not before.get("available") or not after.get("available"):
        return {}
    earlier = before.get("counters", {})
    later = after.get("counters", {})
    delta: dict[str, float] = {}
    for name, value in later.items():
        if name in earlier:
            change = value - earlier[name]
            if change:
                delta[name] = round(change, 3)
    return dict(sorted(delta.items())[:40])


def service_stats(service: str) -> dict[str, Any]:
    """CPU/RSS for a service, or an explicit statement that it is unavailable."""
    override = os.environ.get(f"VOXDESK_LOADTEST_{service.upper().replace('-', '_')}_MATCH", "")
    pattern = override or service
    container = os.environ.get(f"VOXDESK_LOADTEST_{service.upper().replace('-', '_')}_CONTAINER", "")
    if container:
        docker = _docker_stats(container)
        if docker is not None:
            return {"available": True, "source": "docker", **docker}
    proc = _proc_stats(pattern)
    if proc is not None:
        hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        return {
            "available": True,
            "source": "/proc",
            "pattern": pattern,
            "cpu_seconds": round((proc["utime_ticks"] + proc["stime_ticks"]) / hz, 3),
            "rss_mb": round(proc["rss_kb"] / 1024.0, 2),
            "threads": proc["threads"],
            "processes": proc["processes"],
        }
    return {
        "available": False,
        "source": "none",
        "pattern": pattern,
        "reason": (
            "no local process matched the service name and no container was named; "
            "set VOXDESK_LOADTEST_<SERVICE>_CONTAINER or run the service on this host"
        ),
    }


# --------------------------------------------------------------------------- #
# WebSocket plumbing (minimal, stdlib-only: no client library needed)
# --------------------------------------------------------------------------- #


class WebSocketClosed(RuntimeError):
    pass


class MiniWebSocket:
    """Tiny RFC 6455 client: enough for a text-frame protocol, no dependencies."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer

    @classmethod
    async def connect(cls, host: str, port: int, path: str, timeout: float) -> "MiniWebSocket":
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
        key = uuid.uuid4().bytes
        import base64

        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {base64.b64encode(key).decode('ascii')}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        writer.write(request.encode("ascii"))
        await writer.drain()
        status_line = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if b"101" not in status_line:
            writer.close()
            raise WebSocketClosed(f"upgrade refused: {status_line.decode('latin-1').strip()}")
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=timeout)
            if line in (b"\r\n", b"\n", b""):
                break
        return cls(reader, writer)

    async def send_text(self, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        header = bytearray([0x81])
        length = len(data)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack(">Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self._writer.write(bytes(header) + masked)
        await self._writer.drain()

    async def recv_text(self, timeout: float) -> dict | None:
        """One text frame, or ``None`` on a close/ping-only frame."""
        try:
            header = await asyncio.wait_for(self._reader.readexactly(2), timeout=timeout)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError) as exc:
            raise WebSocketClosed(str(exc) or "closed") from exc
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", await self._reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", await self._reader.readexactly(8))[0]
        payload = await self._reader.readexactly(length) if length else b""
        if opcode == 0x8:
            raise WebSocketClosed("peer closed")
        if opcode == 0x9:
            self._writer.write(bytes([0x8A, len(payload)]) + payload)
            await self._writer.drain()
            return None
        if opcode != 0x1:
            return None
        try:
            parsed = json.loads(payload)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def close(self) -> None:
        try:
            self._writer.write(bytes([0x88, 0x00]))
            self._writer.close()
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass


def _record_delivery(sample: SessionSample, frame: dict) -> None:
    """Count a notice-plane delivery and measure its age from ``sent_at``."""
    sample.deliveries += 1
    sent_at = frame.get("sent_at") or ""
    try:
        when = datetime.fromisoformat(str(sent_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return
    latency = (datetime.now(timezone.utc) - when).total_seconds() * 1000.0
    if 0 <= latency < 60_000:
        sample.delivery_latencies_ms.append(round(latency, 3))


async def _await_frame(
    socket_: MiniWebSocket,
    wanted: str,
    timeout: float,
    *,
    sample: SessionSample | None = None,
) -> dict | None:
    """Wait for ``wanted``, recording any deliveries that arrive first.

    Deliveries must be counted where they arrive: dropping them while waiting
    for a pong would understate fan-out and make the latency numbers a lie.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = await socket_.recv_text(timeout=max(0.05, deadline - time.monotonic()))
        if frame is None:
            continue
        kind = frame.get("type")
        if kind == wanted:
            return frame
        if kind == "delivery" and sample is not None:
            _record_delivery(sample, frame)
            continue
        if kind == "error":
            raise WebSocketClosed(f"gateway error: {frame.get('code')}")
    return None


# --------------------------------------------------------------------------- #
# Token minting
# --------------------------------------------------------------------------- #


def mint_token(tenant_id: str, user_id: str, role: str) -> str:
    """A real access token, signed with the deployment's own JWT secret."""
    from app.auth.jwt import create_access_token

    token, _ttl = create_access_token(
        user_id=uuid.UUID(user_id),
        tenant_id=uuid.UUID(tenant_id),
        role=role,
        token_version=1,
        expires_minutes=30,
    )
    return token


# --------------------------------------------------------------------------- #
# One session
# --------------------------------------------------------------------------- #


async def run_session(
    *,
    index: int,
    host: str,
    port: int,
    path: str,
    token: str,
    duration: float,
    connect_timeout: float,
) -> SessionSample:
    sample = SessionSample(index=index)
    started = time.monotonic()
    try:
        socket_ = await MiniWebSocket.connect(host, port, path, timeout=connect_timeout)
    except (OSError, asyncio.TimeoutError, WebSocketClosed) as exc:
        sample.error = f"connect_failed: {type(exc).__name__}"
        return sample
    sample.connect_ms = round((time.monotonic() - started) * 1000.0, 3)
    sample.connected = True
    try:
        welcome = await _await_frame(socket_, "welcome", connect_timeout)
        if welcome is None:
            sample.error = "no_welcome"
            return sample
        sent = time.monotonic()
        await socket_.send_text({"type": "hello", "token": token})
        ready = await _await_frame(socket_, "ready", connect_timeout, sample=sample)
        if ready is None:
            sample.error = "no_ready"
            return sample
        sample.ready_ms = round((time.monotonic() - sent) * 1000.0, 3)

        sent = time.monotonic()
        await socket_.send_text({"type": "subscribe", "room": "calls"})
        subscribed = await _await_frame(socket_, "subscribed", connect_timeout, sample=sample)
        if subscribed is None:
            sample.error = "no_subscribed"
            return sample
        sample.subscribed_ms = round((time.monotonic() - sent) * 1000.0, 3)

        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            ping_sent = time.monotonic()
            await socket_.send_text({"type": "ping"})
            sample.pings_sent += 1
            pong = await _await_frame(socket_, "pong", PONG_TIMEOUT_SECONDS, sample=sample)
            if pong is not None:
                sample.pongs_received += 1
                sample.pong_rtts_ms.append(round((time.monotonic() - ping_sent) * 1000.0, 3))
            await asyncio.sleep(max(0.0, PING_INTERVAL_SECONDS - (time.monotonic() - ping_sent)))
    except (WebSocketClosed, OSError, asyncio.TimeoutError) as exc:
        sample.error = f"session_failed: {type(exc).__name__}"
    finally:
        socket_.close()
    return sample


# --------------------------------------------------------------------------- #
# Media-engine audio leg
# --------------------------------------------------------------------------- #


def _parse_udp_target(answer: dict) -> str:
    """Pull the engine's UDP candidate out of a real signalling answer."""
    for key in ("sdp", "answer", "candidate"):
        value = answer.get(key)
        if isinstance(value, str):
            for line in value.splitlines():
                if line.startswith("a=candidate:") and " udp " in line:
                    parts = line.split()
                    if len(parts) >= 6:
                        return f"{parts[4]}:{parts[5]}"
        if isinstance(value, dict):
            address = value.get("address") or value.get("ip")
            port = value.get("port")
            if address and port:
                return f"{address}:{port}"
    return ""


def _mu_law_silence(frames: int) -> bytes:
    """Real G.711 µ-law: 0xFF is the µ-law encoding of silence."""
    return b"\xff" * (AUDIO_PAYLOAD_BYTES * frames)


class RtpSender:
    """Send well-formed G.711 µ-law RTP over UDP at real-time cadence."""

    def __init__(self, host: str, port: int, payload_type: int = 0) -> None:
        self._target = (host, port)
        self._payload_type = payload_type
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setblocking(False)
        self._sequence = 0
        self._timestamp = 0
        self._ssrc = int.from_bytes(os.urandom(4), "big")

    def packet(self, payload: bytes) -> bytes:
        header = struct.pack(
            ">BBHII",
            0x80,  # version 2, no padding/extension/CSRC
            self._payload_type & 0x7F,
            self._sequence & 0xFFFF,
            self._timestamp & 0xFFFFFFFF,
            self._ssrc,
        )
        self._sequence += 1
        self._timestamp += int(RTP_CLOCK_HZ * AUDIO_FRAME_MS / 1000)
        return header + payload

    def send(self, payload: bytes) -> None:
        self._socket.sendto(self.packet(payload), self._target)

    def close(self) -> None:
        self._socket.close()


async def run_audio_session(
    *,
    index: int,
    engine_url: str,
    room: str,
    seconds: float,
) -> AudioSample:
    """Join the engine, publish a track, then send real µ-law RTP for ``seconds``."""
    sample = AudioSample(index=index)
    import urllib.error
    import urllib.request

    def _post_signal(frame: dict) -> dict | None:
        request = urllib.request.Request(
            f"{engine_url.rstrip('/')}/v1/signal",
            data=json.dumps(frame).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - operator-supplied URL
                body = response.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            sample.error = f"signal_failed: {type(exc).__name__}"
            return None
        try:
            parsed = json.loads(body)
        except ValueError:
            return None
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {}
        return parsed if isinstance(parsed, dict) else None

    join = await asyncio.to_thread(
        _post_signal,
        {"type": "join", "room": room, "participant": f"ramp-{index}"},
    )
    if not join:
        return sample
    session_id = str(join.get("session") or "")
    if not session_id:
        sample.error = sample.error or "join_returned_no_session"
        return sample
    sample.joined = True
    sample.udp_target = _parse_udp_target(join)

    published = await asyncio.to_thread(
        _post_signal,
        {"type": "publish", "session": session_id, "track": "mic", "kind": "audio"},
    )
    sample.published = bool(published)
    if not sample.udp_target and published:
        sample.udp_target = _parse_udp_target(published)
    if not sample.udp_target:
        sample.error = "no_udp_candidate"
        return sample

    host, _, port = sample.udp_target.partition(":")
    if not port.isdigit():
        sample.error = "udp_candidate_unparsable"
        return sample
    sender = RtpSender(host, int(port))
    silence = _mu_law_silence(1)
    deadline = time.monotonic() + seconds
    previous = time.monotonic()
    try:
        while time.monotonic() < deadline:
            tick = time.monotonic()
            try:
                sender.send(silence)
                sample.frames_sent += 1
            except OSError:
                sample.send_errors += 1
            gap_ms = (tick - previous) * 1000.0
            if sample.frames_sent > 1:
                sample.send_gaps_ms.append(round(gap_ms, 3))
                if gap_ms > AUDIO_FRAME_MS * 2:
                    sample.frames_late += 1
            previous = tick
            await asyncio.sleep(max(0.0, AUDIO_FRAME_MS / 1000.0 - (time.monotonic() - tick)))
    finally:
        sender.close()
    return sample


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


async def _publisher(gateway: str, secret: str, tenant_id: str, hz: float, room: str, stop: asyncio.Event) -> int:
    """Publish real ingest events so delivery latency is measurable."""
    import urllib.request

    published = 0
    interval = 1.0 / hz if hz > 0 else 0.0
    while not stop.is_set():
        event = {
            "tenant_id": tenant_id,
            "room": room,
            "kind": "call.updated",
            "payload": {"seq": published, "at": datetime.now(timezone.utc).isoformat()},
            "event_id": str(uuid.uuid4()),
        }
        request = urllib.request.Request(
            f"{gateway}/ingest/v1/publish",
            data=json.dumps(event).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {secret}"},
            method="POST",
        )
        try:
            await asyncio.to_thread(urllib.request.urlopen, request, None, 10)
            published += 1
        except Exception:  # noqa: BLE001 - a refused publish is visible in the count
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
    return published


async def ramp(args: argparse.Namespace) -> dict[str, Any]:
    token = mint_token(args.tenant_id, args.user_id, args.role)
    ws_host, _, ws_port = args.ws_target.partition(":")
    steps = [int(part) for part in args.ramp.split(",") if part.strip()]
    results: list[dict[str, Any]] = []

    for step in steps:
        gateway_stats_before = service_stats(args.gateway_service)
        engine_stats_before = service_stats(args.engine_service) if args.engine_url else None
        gateway_metrics_before = scrape_metrics(args.gateway_metrics_url, args.metrics_token)
        engine_metrics_before = (
            scrape_metrics(args.engine_metrics_url) if args.engine_url else {"available": False, "reason": "no --engine-url given"}
        )

        stop = asyncio.Event()
        publisher_task = None
        if args.publish_hz > 0 and args.ingest_secret:
            publisher_task = asyncio.create_task(
                _publisher(args.gateway_url, args.ingest_secret, args.tenant_id, args.publish_hz, "calls", stop)
            )

        started = time.monotonic()
        sessions = await asyncio.gather(
            *(
                run_session(
                    index=index,
                    host=ws_host,
                    port=int(ws_port),
                    path=args.ws_path,
                    token=token,
                    duration=args.step_seconds,
                    connect_timeout=args.connect_timeout,
                )
                for index in range(step)
            )
        )
        wall = time.monotonic() - started

        audio: list[AudioSample] = []
        if args.engine_url:
            audio = list(
                await asyncio.gather(
                    *(
                        run_audio_session(
                            index=index,
                            engine_url=args.engine_url,
                            room=f"ramp-{step}",
                            seconds=min(args.step_seconds, args.audio_seconds),
                        )
                        for index in range(min(step, args.audio_sessions))
                    )
                )
            )
            for sample in audio:
                sample.as_dict()

        stop.set()
        published = await publisher_task if publisher_task else 0

        gateway_stats_after = service_stats(args.gateway_service)
        engine_stats_after = service_stats(args.engine_service) if args.engine_url else None
        gateway_metrics_after = scrape_metrics(args.gateway_metrics_url, args.metrics_token)
        engine_metrics_after = (
            scrape_metrics(args.engine_metrics_url) if args.engine_url else {"available": False, "reason": "no --engine-url given"}
        )

        connected = [sample for sample in sessions if sample.connected]
        rtts = [value for sample in sessions for value in sample.pong_rtts_ms]
        pings = sum(sample.pings_sent for sample in sessions)
        pongs = sum(sample.pongs_received for sample in sessions)
        deliveries = [value for sample in sessions for value in sample.delivery_latencies_ms]
        audio_frames = sum(sample.frames_sent for sample in audio)

        step_report = {
            "concurrent_sessions": step,
            "wall_seconds": round(wall, 3),
            "connected": len(connected),
            "failed": len(sessions) - len(connected),
            "connect_ms": {
                "p50": _percentile([s.connect_ms for s in connected if s.connect_ms is not None], 50),
                "p95": _percentile([s.connect_ms for s in connected if s.connect_ms is not None], 95),
                "p99": _percentile([s.connect_ms for s in connected if s.connect_ms is not None], 99),
            },
            "ready_ms": {
                "p50": _percentile([s.ready_ms for s in connected if s.ready_ms is not None], 50),
                "p95": _percentile([s.ready_ms for s in connected if s.ready_ms is not None], 95),
            },
            "subscribed_ms": {
                "p50": _percentile([s.subscribed_ms for s in connected if s.subscribed_ms is not None], 50),
                "p95": _percentile([s.subscribed_ms for s in connected if s.subscribed_ms is not None], 95),
            },
            "pong_rtt_ms": {
                "samples": len(rtts),
                "p50": _percentile(rtts, 50),
                "p95": _percentile(rtts, 95),
                "p99": _percentile(rtts, 99),
                "jitter_ms": _jitter(rtts),
            },
            "ping_drop_pct": round(100.0 * (pings - pongs) / pings, 3) if pings else None,
            "delivery_latency_ms": {
                "samples": len(deliveries),
                "p50": _percentile(deliveries, 50),
                "p95": _percentile(deliveries, 95),
                "p99": _percentile(deliveries, 99),
                "jitter_ms": _jitter(deliveries),
            },
            "delivery_drop_pct": (
                round(100.0 * max(0, published * len(connected) - len(deliveries)) / (published * len(connected)), 3)
                if published and connected
                else None
            ),
            "ingest_published": published,
            "audio": {
                "sessions": len(audio),
                "frames_sent": audio_frames,
                "payload": "G.711 PCMU (0xFF silence), 160 B, 20 ms, RTP/PT=0",
                "late_frames": sum(sample.frames_late for sample in audio),
                "send_errors": sum(sample.send_errors for sample in audio),
                "gap_jitter_ms": [
                    value for value in (sample.gap_jitter_ms for sample in audio) if value is not None
                ][:5],
                "errors": sorted({sample.error for sample in audio if sample.error}),
            }
            if args.engine_url
            else {
                "sessions": 0,
                "frames_sent": 0,
                "reason": "no --engine-url given; the audio leg was not exercised",
            },
            "gateway_stats": {
                "before": gateway_stats_before,
                "after": gateway_stats_after,
                "cpu_seconds_delta": (
                    round(gateway_stats_after["cpu_seconds"] - gateway_stats_before["cpu_seconds"], 3)
                    if gateway_stats_before.get("available") and gateway_stats_after.get("available")
                    else None
                ),
                "rss_mb": gateway_stats_after.get("rss_mb") if gateway_stats_after.get("available") else None,
            },
            "gateway_metrics": {
                "before": {"available": gateway_metrics_before.get("available"), "series": gateway_metrics_before.get("series")},
                "after": {"available": gateway_metrics_after.get("available"), "series": gateway_metrics_after.get("series")},
                "reason": gateway_metrics_after.get("reason") or gateway_metrics_before.get("reason") or "",
                "delta": _counter_delta(gateway_metrics_before, gateway_metrics_after),
            },
            "engine_metrics": {
                "before": {"available": engine_metrics_before.get("available"), "series": engine_metrics_before.get("series")},
                "after": {"available": engine_metrics_after.get("available"), "series": engine_metrics_after.get("series")},
                "reason": engine_metrics_after.get("reason") or engine_metrics_before.get("reason") or "",
                "delta": _counter_delta(engine_metrics_before, engine_metrics_after),
            },
            "engine_stats": (
                {
                    "before": engine_stats_before,
                    "after": engine_stats_after,
                }
                if args.engine_url
                else {"available": False, "reason": "no --engine-url given"}
            ),
            "errors": sorted({sample.error for sample in sessions if sample.error})[:10],
            "session_samples": [sample.as_dict() for sample in sessions[:5]],
            "audio_samples": [sample.as_dict() for sample in audio[:5]],
        }
        results.append(step_report)
        print(json.dumps(step_report, indent=2), flush=True)
        if args.stop_on_failure and step_report["failed"]:
            print(
                json.dumps({"stopped_early": True, "reason": "connections failed at this step", "step": step}),
                flush=True,
            )
            break

    return {
        "schema": "voxdesk.loadtest.gateway_ramp.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": {
            "gateway_ws": args.ws_target,
            "gateway_http": args.gateway_url,
            "engine": args.engine_url or None,
            "ws_path": args.ws_path,
            "ramp": steps,
            "step_seconds": args.step_seconds,
            "remote_allowed": allow_remote(),
        },
        "provider_fakes": False,
        "note": (
            "This harness exercises the real gateway and, when configured, the real media "
            "engine. It does not stub any provider. Audio is the harness's own G.711 "
            "µ-law RTP, not the STT/LLM/TTS pipeline: engine receive counters are scraped "
            "so acceptance is visible rather than assumed."
        ),
        "steps": results,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--ws-target", default=os.environ.get("VOXDESK_GATEWAY_WS", "localhost:8790"))
    parser.add_argument(
        "--gateway-url", default=os.environ.get("VOXDESK_GATEWAY_HTTP", "http://localhost:8790")
    )
    parser.add_argument("--ws-path", default="/ws")
    parser.add_argument("--engine-url", default=os.environ.get("VOXDESK_MEDIA_ENGINE_URL", ""))
    parser.add_argument(
        "--gateway-metrics-url",
        default=os.environ.get("VOXDESK_GATEWAY_METRICS_URL", "http://127.0.0.1:8790/metrics"),
    )
    parser.add_argument(
        "--engine-metrics-url",
        default=os.environ.get("VOXDESK_ENGINE_METRICS_URL", "http://127.0.0.1:8791/metrics"),
    )
    parser.add_argument("--metrics-token", default=os.environ.get("VOXDESK_GATEWAY_METRICS_TOKEN", ""))
    parser.add_argument("--ramp", default=DEFAULT_RAMP)
    parser.add_argument("--step-seconds", type=float, default=10.0)
    parser.add_argument("--connect-timeout", type=float, default=10.0)
    parser.add_argument("--publish-hz", type=float, default=2.0)
    parser.add_argument("--ingest-secret", default=os.environ.get("VOXDESK_GATEWAY_INGEST_SECRET", ""))
    parser.add_argument("--audio-sessions", type=int, default=10)
    parser.add_argument("--audio-seconds", type=float, default=5.0)
    parser.add_argument("--tenant-id", default=os.environ.get("VOXDESK_LOADTEST_TENANT_ID", ""))
    parser.add_argument("--user-id", default=os.environ.get("VOXDESK_LOADTEST_USER_ID", ""))
    parser.add_argument("--role", default="owner")
    parser.add_argument("--gateway-service", default="gateway-go")
    parser.add_argument("--engine-service", default="media-engine-rs")
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument("--output", default="")
    args = parser.parse_args(argv)

    ws_check = validate_target(f"http://{args.ws_target}")
    http_check = validate_target(args.gateway_url)
    engine_check = validate_target(args.engine_url) if args.engine_url else None
    for problem in (ws_check, http_check, engine_check):
        if problem:
            parser.error(problem)
    if not args.tenant_id or not args.user_id:
        parser.error(
            "--tenant-id and --user-id are required: the ramp authenticates with a real "
            "dashboard token, so it needs a real tenant and user"
        )
    return args


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    report = asyncio.run(ramp(args))
    encoded = json.dumps(report, indent=2)
    if args.output:
        Path(args.output).write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    worst_drop = max(
        (step["ping_drop_pct"] or 0.0 for step in report["steps"]),
        default=0.0,
    )
    return 0 if worst_drop < 5.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
