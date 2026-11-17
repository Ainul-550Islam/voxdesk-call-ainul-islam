"""End-to-end integration tests for PART 3 Web Calls, WebSocket Protobuf Transport, Widget, and Python SDK."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import hmac
import json
import math
import struct
import time
from typing import AsyncIterator
from urllib.parse import urlsplit
import uuid

from pipecat.frames.protobufs import frames_pb2
import pytest
from sqlalchemy import select
from starlette.websockets import WebSocketDisconnect

import app.db.models as db_models
from app.db.models import Agent, AgentVersion, Call, Turn, UsageEvent, UserRole
from app.db.telephony_models import CallLatencyStat
from app.telephony.web_transport import (
    WebCallProviderFake,
    use_web_call_provider_fake,
)
from sdk.client import VoxDeskClient, verify_webhook_signature
from tests.conftest import auth_headers, make_tenant, make_user


class _AsgiWebSocketSession:
    """In-loop ASGI WebSocket test client running on the active pytest-asyncio event loop."""

    def __init__(
        self,
        receive_queue: asyncio.Queue[dict],
        send_queue: asyncio.Queue[dict],
    ) -> None:
        self._receive_queue = receive_queue
        self._send_queue = send_queue

    async def send_bytes(self, data: bytes) -> None:
        await self._receive_queue.put({"type": "websocket.receive", "bytes": data})

    async def receive_bytes(self) -> bytes:
        msg = await asyncio.wait_for(self._send_queue.get(), timeout=5.0)
        mtype = msg.get("type")
        if mtype == "websocket.close":
            raise WebSocketDisconnect(
                code=int(msg.get("code", 1000)),
                reason=str(msg.get("reason") or ""),
            )
        if mtype == "websocket.send":
            if msg.get("bytes") is not None:
                return bytes(msg["bytes"])
            if msg.get("text") is not None:
                return str(msg["text"]).encode("utf-8")
        raise AssertionError(f"Unexpected ASGI websocket message: {msg}")


@asynccontextmanager
async def asgi_websocket_connect(
    asgi_app,
    url: str,
    *,
    headers: dict[str, str] | None = None,
) -> AsyncIterator[_AsgiWebSocketSession]:
    parsed = urlsplit(url)
    path = parsed.path or "/"
    query_bytes = (parsed.query or "").encode("ascii")
    raw_headers = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "scheme": "ws",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": query_bytes,
        "root_path": "",
        "headers": raw_headers,
        "client": ("127.0.0.1", 50000),
        "server": ("test", 80),
        "subprotocols": [],
        "state": {},
    }

    receive_queue: asyncio.Queue[dict] = asyncio.Queue()
    send_queue: asyncio.Queue[dict] = asyncio.Queue()
    await receive_queue.put({"type": "websocket.connect"})

    app_task = asyncio.create_task(
        asgi_app(scope, receive_queue.get, send_queue.put)
    )
    try:
        first = await asyncio.wait_for(send_queue.get(), timeout=5.0)
        if first.get("type") == "websocket.close":
            raise WebSocketDisconnect(
                code=int(first.get("code", 1000)),
                reason=str(first.get("reason") or ""),
            )
        assert first.get("type") == "websocket.accept", f"Expected accept, got {first}"
        yield _AsgiWebSocketSession(receive_queue, send_queue)
    finally:
        await receive_queue.put({"type": "websocket.disconnect", "code": 1000})
        try:
            await asyncio.wait_for(app_task, timeout=5.0)
        except Exception:
            app_task.cancel()


def _make_sine_pcm16(sample_rate: int = 16000, duration_ms: int = 100) -> bytes:
    num_samples = int(sample_rate * duration_ms / 1000)
    samples = [
        int(12000 * math.sin(2.0 * math.pi * 440.0 * i / sample_rate))
        for i in range(num_samples)
    ]
    return struct.pack(f"<{num_samples}h", *samples)


def _encode_audio_frame(pcm_bytes: bytes, sample_rate: int = 16000) -> bytes:
    proto = frames_pb2.Frame()
    proto.audio.id = 1
    proto.audio.name = "mic_audio"
    proto.audio.audio = pcm_bytes
    proto.audio.sample_rate = sample_rate
    proto.audio.num_channels = 1
    return proto.SerializeToString()


def _encode_message_frame(payload: dict) -> bytes:
    proto = frames_pb2.Frame()
    proto.message.data = json.dumps(payload)
    return proto.SerializeToString()


def _decode_proto_frame(raw: bytes) -> dict:
    proto = frames_pb2.Frame()
    proto.ParseFromString(raw)
    which = proto.WhichOneof("frame")
    if which == "audio":
        return {
            "kind": "audio",
            "audio": bytes(proto.audio.audio),
            "sample_rate": int(proto.audio.sample_rate),
            "num_channels": int(proto.audio.num_channels),
        }
    if which == "message":
        return {
            "kind": "message",
            "data": json.loads(proto.message.data),
        }
    if which == "transcription":
        return {
            "kind": "transcription",
            "text": proto.transcription.text,
            "user_id": proto.transcription.user_id,
        }
    if which == "text":
        return {
            "kind": "text",
            "text": proto.text.text,
        }
    return {"kind": "unknown"}


async def _seed_published_agent(db, tenant_id: uuid.UUID) -> Agent:
    agent = Agent(
        tenant_id=tenant_id,
        external_key="web-voice-agent",
        name="Web Voice Support Agent",
        status="published",
        published_version_number=1,
    )
    db.add(agent)
    await db.flush()

    version = AgentVersion(
        tenant_id=tenant_id,
        agent_id=agent.id,
        version_number=1,
        status="published",
        config_hash="sha256_web_voice_v1",
        is_active=True,
        config_snapshot={
            "system_prompt": "You are a concise web voice support agent.",
            "stt_provider": "deepgram",
            "tts_provider": "elevenlabs",
            "voice_id": "voice_rachel",
            "llm_model": "gpt-4o-mini",
            "greeting": "Welcome to VoxDesk web voice!",
            "dynamic_variables": {"customer_name": "Friend"},
        },
    )
    db.add(version)
    await db.flush()
    agent.published_version_id = version.id
    await db.commit()
    return agent


@pytest.mark.asyncio
async def test_web_call_create_ws_protobuf_roundtrip_and_usage_persistence(
    app, client, db
) -> None:
    tenant = await make_tenant(db, name="WebCall Flow Tenant")
    user = await make_user(db, tenant=tenant, role=UserRole.ADMIN)
    agent = await _seed_published_agent(db, tenant.id)
    headers = await auth_headers(client, user)

    # 1. Create web call via POST /api/web-calls
    create_resp = await client.post(
        "/api/web-calls",
        headers={**headers, "Origin": "https://app.voxdesk.test"},
        json={
            "agent_id": str(agent.id),
            "dynamic_vars": {"customer_name": "Ada"},
            "metadata": {"channel": "browser_test"},
        },
    )
    assert create_resp.status_code == 201, create_resp.text
    bootstrap = create_resp.json()
    call_id = bootstrap["call_id"]
    access_token = bootstrap["access_token"]

    assert uuid.UUID(call_id)
    assert bootstrap["direction"] == "web"
    assert bootstrap["transport"] in {"websocket", "ws-protobuf"}
    assert bootstrap["url"].endswith("/telephony/web/ws")
    assert not access_token.startswith("tok_")
    assert access_token.count(".") == 2  # Real HS256 JWT

    # 2. Verify Call(direction="web") row exists via GET /api/web-calls/{call_id}
    get_resp = await client.get(f"/api/web-calls/{call_id}", headers=headers)
    assert get_resp.status_code == 200, get_resp.text
    call_info = get_resp.json()
    assert call_info["call_id"] == call_id
    assert call_info["direction"] == "web"
    assert call_info["agent_id"] == str(agent.id)

    # 3. Connect to /telephony/web/ws using explicit WebCallProviderFake (is_mock_provider=True)
    fake_provider = WebCallProviderFake(
        transcript_text="Hello, I need help with my invoice",
        reply_text="I would be glad to help with your invoice, Ada.",
    )
    assert fake_provider.is_mock_provider is True

    received_frames: list[dict] = []
    with use_web_call_provider_fake(fake_provider):
        async with asgi_websocket_connect(
            app,
            f"/telephony/web/ws?token={access_token}",
            headers={"Origin": "https://app.voxdesk.test"},
        ) as ws:
            # Send 100ms of 16kHz PCM16 audio + DTMF digit + end_call signal
            await ws.send_bytes(_encode_audio_frame(_make_sine_pcm16(16000, 100), 16000))
            await ws.send_bytes(_encode_message_frame({"type": "dtmf", "digit": "5"}))

            # Collect frames until we receive assistant audio + transcript + latency
            for _ in range(16):
                raw_msg = await ws.receive_bytes()
                decoded = _decode_proto_frame(raw_msg)
                received_frames.append(decoded)
                if (
                    decoded["kind"] == "message"
                    and decoded["data"].get("type") == "latency"
                ):
                    break

            await ws.send_bytes(_encode_message_frame({"type": "end_call"}))

    assert fake_provider.received_audio_bytes > 0
    assert fake_provider.received_dtmf == ["5"]

    message_types = [
        f["data"].get("type") for f in received_frames if f["kind"] == "message"
    ]
    assert "call_started" in message_types
    assert "transcript" in message_types
    assert "latency" in message_types

    audio_frames = [f for f in received_frames if f["kind"] == "audio"]
    assert len(audio_frames) >= 1
    assert audio_frames[0]["sample_rate"] == 16000
    assert len(audio_frames[0]["audio"]) > 0

    transcript_messages = [
        f["data"]
        for f in received_frames
        if f["kind"] == "message" and f["data"].get("type") == "transcript"
    ]
    roles = {m["role"] for m in transcript_messages}
    assert "user" in roles
    assert "assistant" in roles

    # 4. Verify UsageEvent, Turn, and CallLatencyStat persisted in DB
    call_uuid = uuid.UUID(call_id)
    tenant_uuid = tenant.id
    db.expire_all()
    usage_rows = (
        await db.execute(select(UsageEvent).where(UsageEvent.tenant_id == tenant_uuid))
    ).scalars().all()
    assert len(usage_rows) >= 1

    turn_rows = (
        await db.execute(select(Turn).where(Turn.call_id == call_uuid))
    ).scalars().all()
    assert len(turn_rows) >= 2

    latency_row = (
        await db.execute(
            select(CallLatencyStat).where(CallLatencyStat.call_id == call_uuid)
        )
    ).scalar_one_or_none()
    assert latency_row is not None
    assert latency_row.e2e_p50_ms is not None and latency_row.e2e_p50_ms >= 0

    # 5. Verify Call row completed with direction="web"
    call_row = await db.get(Call, call_uuid)
    assert call_row is not None
    assert str(call_row.direction) == "web"
    assert call_row.status == db_models.CallStatus.COMPLETED


@pytest.mark.asyncio
async def test_small_webrtc_offer_returns_honest_501_when_aiortc_unavailable(
    client, db, monkeypatch
) -> None:
    # `aiortc` is pinned in requirements-test.txt (PART 11 / P11-10), so the absence this test
    # is about has to be simulated explicitly rather than assumed from the environment.
    import builtins

    real_import = builtins.__import__

    def _block_smallwebrtc(name, *args, **kwargs):
        if name.startswith("pipecat.transports.smallwebrtc"):
            raise ImportError("aiortc not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_smallwebrtc)

    tenant = await make_tenant(db, name="WebRTC Offer Tenant")
    user = await make_user(db, tenant=tenant, role=UserRole.ADMIN)
    agent = await _seed_published_agent(db, tenant.id)
    headers = await auth_headers(client, user)

    create_resp = await client.post(
        "/api/web-calls",
        headers=headers,
        json={"agent_id": str(agent.id)},
    )
    assert create_resp.status_code == 201
    token = create_resp.json()["access_token"]

    offer_resp = await client.post(
        "/telephony/web/offer",
        json={
            "access_token": token,
            "sdp": "v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\n",
            "type": "offer",
        },
    )
    # corrected: /telephony/web/offer now returns 503 (not 501) advertising fallback_transport="ws-protobuf" when aiortc is absent
    assert offer_resp.status_code == 503
    detail = offer_resp.json()["detail"]
    assert detail["code"] == "UNSUPPORTED_CAPABILITY"
    assert detail["fallback_transport"] == "ws-protobuf"


@pytest.mark.asyncio
async def test_widget_embed_js_and_python_sdk_helpers(client, db) -> None:
    # 1. Serve /widget/embed.js and /api/v1/public/widget/embed.js with CSP-safe headers
    for path in ("/widget/embed.js", "/api/v1/public/widget/embed.js"):
        resp = await client.get(path)
        assert resp.status_code == 200
        assert "application/javascript" in resp.headers.get("content-type", "")
        assert "VoxDeskWidget" in resp.text
        assert "data-public-key" in resp.text

    # 2. Verify Python SDK webhook signature helper
    secret = "whsec_test_secret_key_12345"
    body = json.dumps({"event": "call.ended", "call_id": "call_web_123"}).encode(
        "utf-8"
    )
    ts = str(int(time.time()))
    valid_sig = hmac.new(
        secret.encode("utf-8"), f"{ts}.".encode("utf-8") + body, hashlib.sha256
    ).hexdigest()
    assert (
        verify_webhook_signature(
            body, f"sha256={valid_sig}", secret, timestamp_header=ts
        )
        is True
    )
    assert (
        verify_webhook_signature(
            body, "sha256=0000000000000000", secret, timestamp_header=ts
        )
        is False
    )

    # 3. Verify Python SDK imports cleanly from both sdk.client and sdk.python.voxdesk.client
    from sdk.python.voxdesk.client import VoxDeskClient as DirectVoxDeskClient

    assert VoxDeskClient is DirectVoxDeskClient


@pytest.mark.asyncio
async def test_live_browser_pipeline_masks_protobuf_dtmf_before_llm_context(
    app, client, db, monkeypatch
) -> None:
    """Exercise the non-mock browser route, its Protobuf input, and the real voice-control chain."""
    from types import SimpleNamespace

    import app.ai.runtime as ai_runtime
    import app.telephony.web_transport as web_transport
    from pipecat.frames.frames import InterimTranscriptionFrame, TranscriptionFrame
    from pipecat.processors.aggregators.openai_llm_context import (
        OpenAILLMContextFrame,
    )
    from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

    class _PassThrough(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            await self.push_frame(frame, direction)

    class _ContextPassThrough(_PassThrough):
        def __init__(self, context, role: str) -> None:
            super().__init__()
            self.context = context
            self.role = role

        def get_context_frame(self):
            return OpenAILLMContextFrame(context=self.context)

        async def process_frame(self, frame, direction):
            if (
                self.role == "user"
                and direction == FrameDirection.DOWNSTREAM
                and isinstance(frame, TranscriptionFrame)
                and not isinstance(frame, InterimTranscriptionFrame)
            ):
                self.context.add_message({"role": "user", "content": frame.text})
            await super().process_frame(frame, direction)

    class _ContextPair:
        def __init__(self, context) -> None:
            self.context = context

        def user(self):
            return _ContextPassThrough(self.context, "user")

        def assistant(self):
            return _ContextPassThrough(self.context, "assistant")

    class _RecordingLLM(_PassThrough):
        def __init__(self) -> None:
            super().__init__()
            self.context = None
            self.transcriptions: list[str] = []
            self.transcription_seen = asyncio.Event()

        def register_function(self, *_args, **_kwargs) -> None:
            return None

        def create_context_aggregator(self, context):
            self.context = context
            return _ContextPair(context)

        async def process_frame(self, frame, direction):
            if (
                direction == FrameDirection.DOWNSTREAM
                and isinstance(frame, TranscriptionFrame)
                and not isinstance(frame, InterimTranscriptionFrame)
            ):
                self.transcriptions.append(str(frame.text or ""))
                self.transcription_seen.set()
            await super().process_frame(frame, direction)

    tenant = await make_tenant(db, name="Live Browser DTMF Tenant")
    tenant.humanize = False
    user = await make_user(db, tenant=tenant, role=UserRole.ADMIN)
    agent = await _seed_published_agent(db, tenant.id)
    version = (
        await db.execute(select(AgentVersion).where(AgentVersion.agent_id == agent.id))
    ).scalar_one()
    version.config_snapshot = {
        **version.config_snapshot,
        "call_tooling": {
            "user_dtmf_enabled": True,
            "user_dtmf_mode": "end_key",
            "user_dtmf_end_key": "#",
            "user_dtmf_keep_last": 2,
            "user_dtmf_prompt": "Enter your four-digit reference number.",
            "voicemail_detection_enabled": True,
            "voicemail_action": "hangup",
        },
    }
    await db.commit()

    headers = await auth_headers(client, user)
    created = await client.post(
        "/api/web-calls",
        headers={**headers, "Origin": "https://app.voxdesk.test"},
        json={"agent_id": str(agent.id)},
    )
    assert created.status_code == 201, created.text
    access_token = created.json()["access_token"]
    call_id = uuid.UUID(created.json()["call_id"])

    llm = _RecordingLLM()
    prepared = SimpleNamespace(
        choice=SimpleNamespace(provider="test", model="test-model"),
        system_prompt="Test live browser voice pipeline.",
        blocked_output=(),
    )

    async def _fake_voice_llm(*_args, **_kwargs):
        return llm, prepared

    def _fake_component(*_args, **_kwargs):
        return _PassThrough()

    monkeypatch.setattr(ai_runtime, "voice_llm", _fake_voice_llm)
    monkeypatch.setattr(web_transport, "build_stt", _fake_component)
    monkeypatch.setattr(web_transport, "build_stt_for_runtime", _fake_component)
    monkeypatch.setattr(web_transport, "build_tts", _fake_component)
    monkeypatch.setattr(web_transport, "build_tts_for_runtime", _fake_component)
    monkeypatch.setattr(web_transport, "_ACTIVE_PROVIDER_FAKE", None)

    async with asgi_websocket_connect(
        app,
        f"/telephony/web/ws?token={access_token}",
        headers={"Origin": "https://app.voxdesk.test"},
    ) as ws:
        await ws.send_bytes(_encode_message_frame({"type": "dtmf", "digits": "1234#"}))
        await asyncio.wait_for(llm.transcription_seen.wait(), timeout=5.0)

    assert len(llm.transcriptions) == 1
    assert "Caller entered keypad input" in llm.transcriptions[0]
    assert "1234" not in llm.transcriptions[0]
    assert "**34" in llm.transcriptions[0]
    assert llm.context is not None
    user_messages = [
        message["content"]
        for message in llm.context.get_messages()
        if message.get("role") == "user"
    ]
    assert user_messages == [llm.transcriptions[0]]

    db.expire_all()
    saved_turns = (
        await db.execute(select(Turn).where(Turn.call_id == call_id, Turn.speaker == db_models.Speaker.USER))
    ).scalars().all()
    assert [turn.text for turn in saved_turns] == [llm.transcriptions[0]]
    assert all("1234" not in turn.text for turn in saved_turns)
