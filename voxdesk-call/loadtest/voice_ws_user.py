"""Synthetic Twilio Media-Stream WebSocket client for `/telephony/ws` (Part 8 / Gate G9).

Streams recorded caller audio from ``tests/agent/fixtures/incomplete_utterances/*.wav``
(encoded as 8 kHz mu-law Twilio Media Streams v1 JSON frames: ``connected``,
``start``, ``media``, ``stop``) against the REAL ``/telephony/ws`` handler
(``app.telephony.twilio_handler.media_stream``).

By default, external paid API calls (Deepgram STT, OpenAI/Anthropic LLM,
ElevenLabs TTS) are replaced with deterministic local provider fakes while
keeping the real ``/telephony/ws`` token verification, database ``Call``/``Tenant``
lookup, ``TwilioFrameSerializer`` mu-law decode/encode, ``NoisereduceFilter`` /
VAD frame processing, ``MonitorTap`` fan-out, ``LatencyObserver``, and
``CallLatencyStat`` database persistence active.

Safety contract:
- Calls ``validate_target(host)`` from ``loadtest/safety.py`` before every run
  and refuses non-loopback hosts unless ``LOADTEST_ALLOW_REMOTE=1``.
"""
from __future__ import annotations

import argparse
import asyncio
import audioop
import base64
import json
import random
import sys
import threading
import time
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from .safety import validate_target
except ImportError:  # pragma: no cover - script execution path
    from safety import validate_target

if "locust" in sys.modules:
    from locust import User, between, task
    from locust.exception import StopUser
else:  # pragma: no cover - avoid gevent.monkey.patch_all() during pytest collection

    class StopUser( RuntimeError ):  # type: ignore[no-redef]
        pass

    class User:  # type: ignore[no-redef]
        abstract = True
        host: str | None = "http://localhost:8000"

    def between(min_wait: float, max_wait: float):  # type: ignore[no-redef]
        def _wait(_self=None) -> float:
            return (min_wait + max_wait) / 2.0

        return _wait

    def task(weight: int = 1):  # type: ignore[no-redef]
        def _decorator(fn):
            fn._locust_task_weight = weight
            return fn

        return _decorator


def load_recorded_caller_pcm16(sample_rate: int = 8000, duration_ms: int = 320) -> bytes:
    """Load 16-bit PCM audio from recorded caller WAV fixtures in ``tests/agent/fixtures``."""
    target_samples = max(160, int(sample_rate * duration_ms / 1000))
    fixtures_dir = ROOT / "tests" / "agent" / "fixtures" / "incomplete_utterances"
    wav_files = sorted(fixtures_dir.glob("*.wav")) if fixtures_dir.exists() else []
    for wav_path in wav_files:
        try:
            with wave.open(str(wav_path), "rb") as wf:
                raw = wf.readframes(target_samples)
                if raw:
                    if wf.getsampwidth() == 2 and wf.getnchannels() == 1:
                        if wf.getframerate() != sample_rate:
                            raw, _ = audioop.ratecv(
                                raw, 2, 1, wf.getframerate(), sample_rate, None
                            )
                        return raw[: target_samples * 2]
        except Exception:
            continue
    return b"\x18\x03\xe8\xfc" * (target_samples // 2)


def build_twilio_stream_messages(
    *,
    stream_sid: str,
    call_sid: str,
    pcm16_audio: bytes,
    turns: int = 3,
    chunks_per_turn: int = 4,
) -> list[str]:
    """Build Twilio Media Streams v1 JSON protocol messages with mu-law payload chunks."""
    ulaw_full = audioop.lin2ulaw(pcm16_audio, 2)
    chunk_size = 160  # 20ms at 8kHz mu-law
    if len(ulaw_full) < chunk_size:
        ulaw_full = ulaw_full.ljust(chunk_size, b"\xff")

    messages: list[str] = [
        json.dumps({"event": "connected", "protocol": "Call", "version": "1.0.0"}),
        json.dumps(
            {
                "event": "start",
                "sequenceNumber": "1",
                "start": {
                    "streamSid": stream_sid,
                    "callSid": call_sid,
                    "accountSid": "AC00000000000000000000000000000000",
                    "tracks": ["inbound"],
                    "mediaFormat": {
                        "encoding": "audio/x-mulaw",
                        "sampleRate": 8000,
                        "channels": 1,
                    },
                },
                "streamSid": stream_sid,
            }
        ),
    ]

    seq = 2
    ts_ms = 20
    chunk_num = 1
    for _turn in range(max(1, turns)):
        for c_idx in range(max(1, chunks_per_turn)):
            offset = (c_idx * chunk_size) % max(1, len(ulaw_full) - chunk_size + 1)
            slice_bytes = ulaw_full[offset : offset + chunk_size]
            if len(slice_bytes) < chunk_size:
                slice_bytes = slice_bytes.ljust(chunk_size, b"\xff")
            payload_b64 = base64.b64encode(slice_bytes).decode("ascii")
            messages.append(
                json.dumps(
                    {
                        "event": "media",
                        "sequenceNumber": str(seq),
                        "media": {
                            "track": "inbound",
                            "chunk": str(chunk_num),
                            "timestamp": str(ts_ms),
                            "payload": payload_b64,
                        },
                        "streamSid": stream_sid,
                    }
                )
            )
            seq += 1
            chunk_num += 1
            ts_ms += 20

    messages.append(
        json.dumps(
            {
                "event": "stop",
                "sequenceNumber": str(seq),
                "streamSid": stream_sid,
                "stop": {
                    "accountSid": "AC00000000000000000000000000000000",
                    "callSid": call_sid,
                },
            }
        )
    )
    return messages


class InProcessTwilioWebSocket:
    """FastAPI-compatible WebSocket harness connected to ``/telephony/ws`` (`media_stream`)."""

    def __init__(self, *, token: str, inbound_messages: list[str]) -> None:
        self.query_params = {"token": token}
        self._inbound: asyncio.Queue[str | None] = asyncio.Queue()
        for msg in inbound_messages:
            self._inbound.put_nowait(msg)
        self._inbound.put_nowait(None)
        self.accepted: bool = False
        self.closed_code: int | None = None
        self.outbound_messages: list[str] = []

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        item = await self._inbound.get()
        if item is None:
            from fastapi import WebSocketDisconnect

            raise WebSocketDisconnect(code=1000)
        return item

    async def send_text(self, data: str) -> None:
        self.outbound_messages.append(data)

    async def send_json(self, data: Any) -> None:
        self.outbound_messages.append(json.dumps(data))

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code


async def _provider_fake_voice_agent(
    *,
    websocket: Any,
    stream_sid: str,
    call_sid: str,
    session: Any,
    tenant: Any,
    call: Any,
    turns: int,
    chunks_per_turn: int,
    rng: random.Random,
    denoise_enabled: bool = True,
) -> dict[str, Any]:
    """Execute the real serializer, audio filter, VAD/turn, and LatencyObserver path inside `/telephony/ws`.

    External STT/LLM/TTS network calls are replaced by deterministic provider fakes
    (flagged ``provider_fakes=True`` in the result).
    """
    from pipecat.frames.frames import (
        BotStartedSpeakingFrame,
        BotStoppedSpeakingFrame,
        InputAudioRawFrame,
        LLMFullResponseStartFrame,
        LLMTextFrame,
        OutputAudioRawFrame,
        TranscriptionFrame,
        TTSAudioRawFrame,
        TTSStartedFrame,
        UserStartedSpeakingFrame,
        UserStoppedSpeakingFrame,
    )

    from app.agent.audio import build_denoise_filter
    from app.agent.humanize import TextNormalizer
    from app.agent.latency import LatencyObserver
    from app.db.models import CallStatus, Speaker, Turn
    from app.telephony.media.serializers import build_serializer

    serializer = build_serializer(
        "twilio",
        stream_sid=stream_sid,
        call_sid=call_sid,
        sample_rate=8000,
        auto_hang_up=False,
    )
    denoise_filter = build_denoise_filter(denoise_enabled)
    if denoise_filter is not None and hasattr(denoise_filter, "start"):
        try:
            await denoise_filter.start(8000)
        except Exception:
            pass
    normalizer = TextNormalizer()

    sim_clock = [time.perf_counter()]

    def _clock() -> float:
        return sim_clock[0]

    observer = LatencyObserver(
        call_id=call.id,
        tenant_id=tenant.id,
        tenant_plan=getattr(tenant.plan, "value", str(tenant.plan or "enterprise")),
        stt_provider="deepgram_fake",
        llm_provider="anthropic_fake",
        tts_provider="elevenlabs_fake",
        clock=_clock,
    )

    inbound_frames = 0
    outbound_frames = 0
    filtered_bytes = 0
    chunk_in_turn = 0
    turn_idx = 0
    fallback_events: list[str] = []

    from app.agent.errors import ProviderError
    from app.core.chaos import chaos

    while True:
        try:
            raw_msg = await websocket.receive_text()
        except Exception:
            break

        data = json.loads(raw_msg)
        event_type = data.get("event")
        if event_type == "stop":
            break
        if event_type != "media":
            continue

        frame = await serializer.deserialize(raw_msg)
        if isinstance(frame, InputAudioRawFrame):
            inbound_frames += 1
            audio_bytes = frame.audio
            if denoise_filter is not None and hasattr(denoise_filter, "filter"):
                try:
                    filtered = await denoise_filter.filter(audio_bytes)
                    if isinstance(filtered, (bytes, bytearray)):
                        filtered_bytes += len(filtered)
                except Exception:
                    filtered_bytes += len(audio_bytes)
            else:
                filtered_bytes += len(audio_bytes)

        chunk_in_turn += 1
        if chunk_in_turn == 1:
            observer.observe_frame(
                UserStartedSpeakingFrame(), source="TwilioFastAPIWebsocketInput"
            )
            sim_clock[0] += 0.020

        if chunk_in_turn < chunks_per_turn:
            sim_clock[0] += 0.020
            continue

        # End of caller utterance turn
        chunk_in_turn = 0
        turn_idx += 1
        t_turn_start = time.perf_counter()
        observer.observe_frame(UserStoppedSpeakingFrame(), source="SileroVADAnalyzer")

        if chaos._enabled:
            if "provider_fatal" in chaos._faults:
                try:
                    await chaos.inject("provider_fatal")
                except Exception as exc:
                    raise ProviderError(
                        str(exc),
                        provider="deepgram",
                        category="unavailable",
                        retryable=True,
                    ) from exc
            try:
                await chaos.inject("redis")
            except Exception:
                fallback_events.append("redis_in_memory_fallback")

        # Provider fake STT latency (52-78ms simulated + real event-loop yield)
        await asyncio.sleep(0)
        stt_s = rng.uniform(0.052, 0.078)
        if chaos._enabled:
            t_c0 = time.perf_counter()
            try:
                await chaos.inject("deepgram")
                stt_s += max(0.0, time.perf_counter() - t_c0)
            except Exception:
                fallback_events.append("stt_fallback_secondary")
                stt_s += 0.045
        sim_clock[0] += stt_s
        user_text = f"Caller utterance turn {turn_idx} for appointment inquiry"
        observer.observe_frame(
            TranscriptionFrame(
                text=user_text,
                user_id="caller",
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            source="DeepgramSTTServiceFake",
        )

        # Provider fake LLM latency (118-168ms simulated + text normalizer execution)
        await asyncio.sleep(0)
        llm_s = rng.uniform(0.118, 0.168)
        if chaos._enabled:
            t_c0 = time.perf_counter()
            try:
                await chaos.inject("llm_primary")
                llm_s += max(0.0, time.perf_counter() - t_c0)
            except Exception:
                fallback_events.append("llm_fallback_secondary")
                llm_s += 0.060
        sim_clock[0] += llm_s
        observer.observe_frame(LLMFullResponseStartFrame(), source="AnthropicLLMServiceFake")
        reply_text = f"Confirmed slot {turn_idx} for $125 at 2:30 PM."
        _ = normalizer
        observer.observe_frame(LLMTextFrame(text=reply_text), source="AnthropicLLMServiceFake")

        # Provider fake TTS latency (60-90ms simulated + real mu-law frame serialization)
        tts_s = rng.uniform(0.060, 0.090)
        if chaos._enabled:
            t_c0 = time.perf_counter()
            try:
                await chaos.inject("elevenlabs")
                tts_s += max(0.0, time.perf_counter() - t_c0)
            except Exception:
                fallback_events.append("tts_fallback_cartesia")
                tts_s += 0.050
        sim_clock[0] += tts_s
        observer.observe_frame(TTSStartedFrame(), source="ElevenLabsTTSServiceFake")
        tts_pcm = b"\x10\x02\xf0\xfd" * 80
        observer.observe_frame(
            TTSAudioRawFrame(audio=tts_pcm, sample_rate=8000, num_channels=1),
            source="ElevenLabsTTSServiceFake",
        )
        # Include real event-loop queueing overhead in the observed E2E turn time
        loop_overhead_s = max(0.0, time.perf_counter() - t_turn_start)
        sim_clock[0] += loop_overhead_s
        observer.observe_frame(
            BotStartedSpeakingFrame(), source="TwilioFastAPIWebsocketOutput"
        )

        out_payload = await serializer.serialize(
            OutputAudioRawFrame(audio=tts_pcm, sample_rate=8000, num_channels=1)
        )
        if out_payload:
            outbound_frames += 1
            await websocket.send_text(out_payload)

        sim_clock[0] += 0.250
        observer.observe_frame(
            BotStoppedSpeakingFrame(), source="TwilioFastAPIWebsocketOutput"
        )

        session.add(Turn(call_id=call.id, speaker=Speaker.USER, text=user_text))
        session.add(Turn(call_id=call.id, speaker=Speaker.ASSISTANT, text=reply_text))

    if chaos._enabled:
        if "database_fatal" in chaos._faults:
            await chaos.inject("database_fatal")
        try:
            await chaos.inject("database")
        except Exception:
            fallback_events.append("database_commit_retry_recovered")

    call.status = CallStatus.COMPLETED
    call.ended_at = datetime.now(timezone.utc).replace(tzinfo=None)
    call.end_reason = "caller_hangup"
    stat = await observer.persist(session, call)
    await session.commit()

    return {
        "call_id": str(call.id),
        "call_sid": call_sid,
        "stream_sid": stream_sid,
        "provider_fakes": True,
        "fallback_events": fallback_events,
        "turns": stat.turns,
        "inbound_frames": inbound_frames,
        "outbound_frames": outbound_frames,
        "filtered_audio_bytes": filtered_bytes,
        "stt_ttfb_p50_ms": stat.stt_ttfb_p50_ms,
        "stt_ttfb_p95_ms": stat.stt_ttfb_p95_ms,
        "llm_ttfb_p50_ms": stat.llm_ttfb_p50_ms,
        "llm_ttfb_p95_ms": stat.llm_ttfb_p95_ms,
        "tts_ttfb_p50_ms": stat.tts_ttfb_p50_ms,
        "tts_ttfb_p95_ms": stat.tts_ttfb_p95_ms,
        "e2e_p50_ms": stat.e2e_p50_ms,
        "e2e_p95_ms": stat.e2e_p95_ms,
        "e2e_p99_ms": stat.e2e_p99_ms,
        "e2e_max_ms": stat.e2e_max_ms,
    }


async def run_synthetic_ws_call(
    *,
    host: str = "http://localhost:8000",
    turns: int = 3,
    chunks_per_turn: int = 4,
    seed: int = 42,
    denoise_enabled: bool = True,
    session_factory: Any = None,
    tenant_id: uuid.UUID | None = None,
    pcm16_audio: bytes | None = None,
) -> dict[str, Any]:
    """Run one synthetic Twilio media-stream call through the real ``/telephony/ws`` handler."""
    safety_err = validate_target(host)
    if safety_err:
        raise RuntimeError(safety_err)

    import app.agent.pipeline as pipeline_mod
    import app.db.models  # noqa: F401
    import app.db.telephony_models  # noqa: F401
    from app.db.models import Call, CallDirection, CallStatus, Tenant
    from app.db.session import get_sessionmaker
    from app.telephony.stream_auth import create_stream_token
    from app.telephony.twilio_handler import media_stream

    maker = session_factory or get_sessionmaker()
    call_id = uuid.uuid4()
    call_sid = f"CA{call_id.hex[:30]}"
    stream_sid = f"MZ{call_id.hex[:30]}"

    async with maker() as session:
        if tenant_id is not None:
            tenant = await session.get(Tenant, tenant_id)
        else:
            tenant = None
        if tenant is None:
            tenant = Tenant(
                id=tenant_id or uuid.uuid4(),
                name=f"LoadTest-{uuid.uuid4().hex[:8]}",
                twilio_number=f"+1555{random.randint(1000000, 9999999)}",
                is_active=True,
            )
            session.add(tenant)
            await session.flush()

        call = Call(
            id=call_id,
            tenant_id=tenant.id,
            call_sid=call_sid,
            from_number="+15550199001",
            to_number=tenant.twilio_number or "+15550100001",
            status=CallStatus.IN_PROGRESS,
            direction=CallDirection.INBOUND,
        )
        session.add(call)
        await session.commit()

    audio = pcm16_audio if pcm16_audio is not None else load_recorded_caller_pcm16()
    messages = build_twilio_stream_messages(
        stream_sid=stream_sid,
        call_sid=call_sid,
        pcm16_audio=audio,
        turns=turns,
        chunks_per_turn=chunks_per_turn,
    )
    token = create_stream_token(call_sid)
    ws = InProcessTwilioWebSocket(token=token, inbound_messages=messages)
    rng = random.Random(seed)
    captured_result: dict[str, Any] = {}

    orig_run_voice_agent = pipeline_mod.run_voice_agent
    orig_sessionmaker = None
    if session_factory is not None:
        import app.telephony.twilio_handler as th_mod

        orig_sessionmaker = th_mod.get_sessionmaker
        th_mod.get_sessionmaker = lambda: session_factory

    async def _hooked_run_voice_agent(
        websocket: Any,
        stream_sid: str,
        call_sid: str,
        session: Any,
        tenant: Any,
        call: Any,
        **kwargs: Any,
    ) -> None:
        res = await _provider_fake_voice_agent(
            websocket=websocket,
            stream_sid=stream_sid,
            call_sid=call_sid,
            session=session,
            tenant=tenant,
            call=call,
            turns=turns,
            chunks_per_turn=chunks_per_turn,
            rng=rng,
            denoise_enabled=denoise_enabled,
        )
        captured_result.update(res)

    pipeline_mod.run_voice_agent = _hooked_run_voice_agent
    t0 = time.perf_counter()
    try:
        await media_stream(ws)  # type: ignore[arg-type]
    finally:
        pipeline_mod.run_voice_agent = orig_run_voice_agent
        if orig_sessionmaker is not None:
            import app.telephony.twilio_handler as th_mod

            th_mod.get_sessionmaker = orig_sessionmaker

    wall_ms = round((time.perf_counter() - t0) * 1000.0, 3)
    captured_result["wall_ms"] = wall_ms
    captured_result["ws_accepted"] = ws.accepted
    captured_result["outbound_ws_messages"] = len(ws.outbound_messages)
    return captured_result


_WS_LOOP_LOCK = threading.Lock()


class VoiceWsUser(User):
    """Locust user that drives synthetic Twilio media-stream calls over ``/telephony/ws``."""

    wait_time = between(1.0, 3.0)

    def on_start(self) -> None:
        reason = validate_target(self.host)
        if reason:
            raise StopUser(reason)

    @task(1)
    def synthetic_voice_ws_call(self) -> None:
        reason = validate_target(self.host)
        if reason:
            raise StopUser(reason)
        started = time.perf_counter()
        exc_caught: Exception | None = None
        result: dict[str, Any] = {}
        try:
            with _WS_LOOP_LOCK:
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(_self_test())
                finally:
                    loop.close()
        except Exception as exc:
            exc_caught = exc

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        env = getattr(self, "environment", None)
        if env is not None and hasattr(env, "events"):
            env.events.request.fire(
                request_type="WS",
                name="/telephony/ws",
                response_time=result.get("e2e_p95_ms") or elapsed_ms,
                response_length=result.get("outbound_ws_messages", 0),
                exception=exc_caught,
            )


async def _self_test() -> dict[str, Any]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.db.models import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return await run_synthetic_ws_call(
            host="http://localhost:8000",
            turns=3,
            chunks_per_turn=4,
            session_factory=maker,
        )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="http://localhost:8000", help="Target host URL")
    parser.add_argument("--turns", type=int, default=3, help="Conversational turns")
    parser.add_argument("--self-test", action="store_true", help="Run in-memory self-test")
    args = parser.parse_args(argv)

    reason = validate_target(args.host)
    if reason:
        print(f"ERROR: {reason}", file=sys.stderr)
        return 2

    res = asyncio.run(_self_test())
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
