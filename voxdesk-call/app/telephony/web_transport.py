"""Browser audio transport for VoxDesk web calls (PART 3 — Gate G4).

Provides:
1. MVP WebSocket transport at `/telephony/web/ws`:
   - `FastAPIWebsocketTransport` + `ProtobufFrameSerializer` (PCM16 mono 16 kHz)
   - Single-use JWT verification + Origin binding (`consume_web_call_token`)
   - Wires `LatencyObserver`, `TurnTrackingObserver`, `UsageTracker`, `GovernedHearing`,
     `GovernedSpeech`, `Turn` persistence, `CallLatencyStat` persistence, and billing usage hooks
2. Phase-2 WebRTC signaling endpoint at `/telephony/web/offer`:
   - Uses `pipecat.transports.smallwebrtc.transport.SmallWebRTCTransport` when `aiortc` is installed
   - Returns honest `501 UNSUPPORTED_CAPABILITY` when `aiortc` is not installed (Rule R3)
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InputAudioRawFrame,
    InputTransportMessageFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    TextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.observers.turn_tracking_observer import TurnTrackingObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.frame_processor import FrameDirection
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.audio import build_ambient_mixer, build_denoise_filter
from app.agent.functions import FunctionHandlers
from app.agent.humanize import Backchannel, FillerInjector, TextNormalizer, vary_greeting
from app.agent.idle_reminders import build_idle_reminder_processor
from app.agent.language import resolve_language_config
from app.agent.latency import LatencyObserver, LatencyTrackingProcessor
from app.agent.pipeline import GovernedHearing, GovernedSpeech, MAX_TOOL_CALLS_PER_CALL
from app.agent.prompts import build_system_prompt
from app.agent.stt import build_stt, build_stt_for_runtime
from app.agent.tts import build_tts, build_tts_for_runtime
from app.agent.turn_taking import build_turn_config
from app.agent.usage_tracker import UsageTracker
from app.billing.hooks import on_llm_tokens, on_tts_characters
from app.core.config import settings
from app.core.correlation import correlation_scope
from app.db.models import (
    Call,
    CallStatus,
    Speaker,
    Tenant,
    TestRun,
    TestRunModeEnum,
    TestRunStatusEnum,
    Turn,
)
from app.db.session import get_session
from app.runtime.agent_config_resolver import RuntimeConfig, resolve_runtime_config
from app.services.public_origin_service import extract_request_origin
from app.telephony.media_gateway import media_gateway_manager
from app.telephony.transcription import redact_turn_text
from app.telephony.web_call import WebCallSecurityError, consume_web_call_token

router = APIRouter(tags=["web-call-transport"])

WEB_AUDIO_SAMPLE_RATE = 16000
WEB_AUDIO_CHANNELS = 1


@dataclass
class WebCallProviderFake:
    """Recording provider fake for deterministic browser call contract tests (`is_mock_provider=True`).

    Used by `tests/telephony/test_web_call_flow.py` to exercise the full Protobuf
    WebSocket transport, `LatencyObserver`, `CallLatencyStat` persistence, `Turn`
    storage, and `UsageEvent` metering without external network calls.
    """

    is_mock_provider: bool = True
    stt_provider: str = "deepgram"
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"
    tts_provider: str = "elevenlabs"
    transcript_text: str | None = None
    reply_text: str | None = None
    transcript_for_audio: Callable[[bytes, int], str] = field(
        default=lambda audio_bytes, turn_idx: (
            f"Hello agent, I would like to book an appointment (turn {turn_idx}, {len(audio_bytes)} bytes)."
        )
    )
    reply_for_transcript: Callable[[str, RuntimeConfig, int], str] = field(
        default=lambda text, cfg, turn_idx: (
            f"I can help you with that right away. Confirmed for turn {turn_idx}."
        )
    )
    stt_delay_s: float = 0.045
    llm_delay_s: float = 0.110
    tts_delay_s: float = 0.060
    recorded_audio_frames: list[bytes] = field(default_factory=list)
    recorded_dtmf_digits: list[str] = field(default_factory=list)

    @property
    def received_audio_bytes(self) -> int:
        return sum(len(chunk) for chunk in self.recorded_audio_frames)

    @property
    def received_dtmf(self) -> list[str]:
        return self.recorded_dtmf_digits


_ACTIVE_PROVIDER_FAKE: WebCallProviderFake | None = None


@contextmanager
def use_web_call_provider_fake(fake: WebCallProviderFake | None = None) -> Iterator[WebCallProviderFake]:
    """Context manager to inject a `WebCallProviderFake` (`is_mock_provider=True`) into `/telephony/web/ws`."""
    global _ACTIVE_PROVIDER_FAKE
    instance = fake or WebCallProviderFake()
    prev = _ACTIVE_PROVIDER_FAKE
    _ACTIVE_PROVIDER_FAKE = instance
    try:
        yield instance
    finally:
        _ACTIVE_PROVIDER_FAKE = prev


class WebRTCOfferRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(..., min_length=16, max_length=4096)
    sdp: str = Field(..., min_length=1, max_length=65536)
    type: str = Field(default="offer", pattern="^(offer)$")


async def _send_protobuf_frame(
    websocket: WebSocket,
    serializer: ProtobufFrameSerializer,
    frame: Any,
) -> None:
    payload = await serializer.serialize(frame)
    if isinstance(payload, bytes):
        await websocket.send_bytes(payload)
    elif isinstance(payload, str):
        await websocket.send_bytes(payload.encode("utf-8"))


async def _send_json_message_frame(
    websocket: WebSocket,
    serializer: ProtobufFrameSerializer,
    data: dict[str, Any],
) -> None:
    await _send_protobuf_frame(
        websocket,
        serializer,
        OutputTransportMessageFrame(message=data),
    )


async def _run_provider_fake_session(
    *,
    websocket: WebSocket,
    session: AsyncSession,
    tenant: Tenant,
    call: Call,
    runtime_cfg: RuntimeConfig,
    fake: WebCallProviderFake,
) -> None:
    """Execute the browser WebSocket Protobuf session against `WebCallProviderFake` (`is_mock_provider=True`)."""
    from app.ai.runtime import guard_heard_text, guard_spoken_text

    assert fake.is_mock_provider is True
    serializer = ProtobufFrameSerializer()

    sim_clock = [time.perf_counter()]

    def _clock() -> float:
        return sim_clock[0]

    latency_observer = LatencyObserver(
        call_id=call.id,
        tenant_id=tenant.id,
        tenant_plan=getattr(tenant.plan, "value", str(tenant.plan)),
        stt_provider=fake.stt_provider,
        llm_provider=fake.llm_provider,
        tts_provider=fake.tts_provider,
        clock=_clock,
    )

    stt_usage = UsageTracker(track_stt=True)
    voice_usage = UsageTracker(track_voice=True, provider=fake.llm_provider)

    greeting = vary_greeting(
        runtime_cfg.greeting or tenant.greeting,
        tenant.name,
        tenant.agent_name,
    )
    greeting = guard_spoken_text(greeting)

    persisted_messages: list[tuple[Speaker, str]] = [
        (Speaker.ASSISTANT, greeting),
    ]

    # Emit call_started + initial greeting over Pipecat Protobuf frames
    await _send_json_message_frame(
        websocket,
        serializer,
        {
            "type": "call_started",
            "call_id": str(call.id),
            "agent_id": str(runtime_cfg.agent_id) if runtime_cfg.agent_id else None,
            "agent_version": runtime_cfg.agent_version_number,
            "sample_rate": WEB_AUDIO_SAMPLE_RATE,
            "is_mock_provider": True,
        },
    )
    await _send_protobuf_frame(
        websocket,
        serializer,
        TranscriptionFrame(
            text=greeting,
            user_id="assistant",
            timestamp=datetime.now(timezone.utc).isoformat(),
        ),
    )
    await _send_json_message_frame(
        websocket,
        serializer,
        {
            "type": "transcript_update",
            "role": "assistant",
            "text": greeting,
            "turn_index": 0,
            "is_final": True,
        },
    )

    turn_index = 0
    total_llm_tokens = 0
    total_tts_chars = len(greeting)
    total_stt_chars = 0

    try:
        while True:
            ws_msg = await websocket.receive()
            msg_type = ws_msg.get("type")
            if msg_type == "websocket.disconnect":
                break

            raw_bytes = ws_msg.get("bytes")
            raw_text = ws_msg.get("text")

            if raw_text and not raw_bytes:
                try:
                    parsed_ctrl = json.loads(raw_text)
                except ValueError:
                    parsed_ctrl = {}
                ctrl_type = str(parsed_ctrl.get("type") or "").lower()
                if ctrl_type in {"stop", "end_call", "hangup"}:
                    break
                if ctrl_type == "dtmf":
                    digits = str(parsed_ctrl.get("digits") or parsed_ctrl.get("digit") or "")
                    fake.recorded_dtmf_digits.append(digits)
                    await _send_json_message_frame(
                        websocket,
                        serializer,
                        {"type": "dtmf_ack", "digits": digits, "digit": digits},
                    )
                    continue
                raw_bytes = raw_text.encode("utf-8")

            if not raw_bytes:
                continue

            frame = await serializer.deserialize(raw_bytes)
            if frame is None:
                continue

            if isinstance(frame, InputTransportMessageFrame):
                msg_payload = frame.message if isinstance(frame.message, dict) else {}
                mtype = str(msg_payload.get("type") or "").lower()
                if mtype in {"stop", "end_call", "hangup"}:
                    break
                if mtype == "dtmf":
                    digits = str(msg_payload.get("digits") or msg_payload.get("digit") or "")
                    fake.recorded_dtmf_digits.append(digits)
                    await _send_json_message_frame(
                        websocket,
                        serializer,
                        {"type": "dtmf_ack", "digits": digits, "digit": digits},
                    )
                continue

            if isinstance(frame, (InputAudioRawFrame, TextFrame)):
                turn_index += 1
                if isinstance(frame, InputAudioRawFrame):
                    audio_bytes = bytes(frame.audio or b"\x00\x00" * 160)
                    fake.recorded_audio_frames.append(audio_bytes)
                    media_gateway_manager.ingest_inbound_frame(
                        call.id,
                        payload=audio_bytes,
                        encoding="pcm16",
                        sample_rate=int(frame.sample_rate or WEB_AUDIO_SAMPLE_RATE),
                        timestamp_ms=turn_index * 20,
                        speech_detected=True,
                    )
                    heard_text = (
                        fake.transcript_text
                        if fake.transcript_text is not None
                        else fake.transcript_for_audio(audio_bytes, turn_index)
                    )
                else:
                    heard_text = str(frame.text or "").strip()

                if not heard_text:
                    continue

                # 1. User speaking -> stopped speaking
                latency_observer.observe_frame(UserStartedSpeakingFrame(), source="WebProtobufInput")
                sim_clock[0] += 0.250
                latency_observer.observe_frame(UserStoppedSpeakingFrame(), source="SileroVADAnalyzer")

                # 2. STT TTFB
                sim_clock[0] += fake.stt_delay_s
                now_iso = datetime.now(timezone.utc).isoformat()
                stt_frame = TranscriptionFrame(
                    text=heard_text,
                    user_id="user",
                    timestamp=now_iso,
                )
                latency_observer.observe_frame(stt_frame, source=fake.stt_provider)
                total_stt_chars += len(heard_text)
                await stt_usage.process_frame(stt_frame, FrameDirection.DOWNSTREAM)

                guard_verdict = guard_heard_text(heard_text)
                if not guard_verdict.allowed:
                    await _send_json_message_frame(
                        websocket,
                        serializer,
                        {
                            "type": "guardrail_blocked",
                            "reason": guard_verdict.reason,
                        },
                    )
                    continue

                persisted_messages.append((Speaker.USER, heard_text))
                await _send_protobuf_frame(websocket, serializer, stt_frame)
                await _send_json_message_frame(
                    websocket,
                    serializer,
                    {
                        "type": "transcript",
                        "role": "user",
                        "text": heard_text,
                        "turn_index": turn_index,
                        "final": True,
                        "is_final": True,
                    },
                )
                await _send_json_message_frame(
                    websocket,
                    serializer,
                    {
                        "type": "transcript_update",
                        "role": "user",
                        "text": heard_text,
                        "turn_index": turn_index,
                        "is_final": True,
                    },
                )

                # 3. LLM TTFB
                sim_clock[0] += fake.llm_delay_s
                latency_observer.observe_frame(LLMFullResponseStartFrame(), source=fake.llm_provider)
                reply_text = (
                    fake.reply_text
                    if fake.reply_text is not None
                    else fake.reply_for_transcript(heard_text, runtime_cfg, turn_index)
                )
                reply_text = guard_spoken_text(reply_text)
                llm_text_frame = LLMTextFrame(text=reply_text)
                latency_observer.observe_frame(llm_text_frame, source=fake.llm_provider)

                turn_tokens = max(8, len(reply_text.split()) * 2)
                total_llm_tokens += turn_tokens

                # 4. TTS TTFB & Outbound Audio
                sim_clock[0] += fake.tts_delay_s
                latency_observer.observe_frame(TTSStartedFrame(), source=fake.tts_provider)
                pcm_out = b"\x10\x00" * 320  # 20ms mono 16kHz PCM16 frame
                tts_audio_frame = TTSAudioRawFrame(
                    audio=pcm_out,
                    sample_rate=WEB_AUDIO_SAMPLE_RATE,
                    num_channels=WEB_AUDIO_CHANNELS,
                )
                latency_observer.observe_frame(tts_audio_frame, source=fake.tts_provider)
                latency_observer.observe_frame(BotStartedSpeakingFrame(), source="WebProtobufOutput")
                await voice_usage.process_frame(TextFrame(text=reply_text), FrameDirection.DOWNSTREAM)
                total_tts_chars += len(reply_text)

                persisted_messages.append((Speaker.ASSISTANT, reply_text))
                media_gateway_manager.enqueue_outbound_frame(
                    call.id,
                    payload=pcm_out,
                    timestamp_ms=turn_index * 40,
                )

                await _send_json_message_frame(
                    websocket,
                    serializer,
                    {
                        "type": "agent_start_talking",
                        "call_id": str(call.id),
                        "turn_index": turn_index,
                    },
                )
                await _send_protobuf_frame(
                    websocket,
                    serializer,
                    TranscriptionFrame(
                        text=reply_text,
                        user_id="assistant",
                        timestamp=datetime.now(timezone.utc).isoformat(),
                    ),
                )
                await _send_json_message_frame(
                    websocket,
                    serializer,
                    {
                        "type": "transcript",
                        "role": "assistant",
                        "text": reply_text,
                        "turn_index": turn_index,
                        "final": True,
                        "is_final": True,
                    },
                )
                await _send_json_message_frame(
                    websocket,
                    serializer,
                    {
                        "type": "transcript_update",
                        "role": "assistant",
                        "text": reply_text,
                        "turn_index": turn_index,
                        "is_final": True,
                    },
                )
                await _send_protobuf_frame(
                    websocket,
                    serializer,
                    OutputAudioRawFrame(
                        audio=pcm_out,
                        sample_rate=WEB_AUDIO_SAMPLE_RATE,
                        num_channels=WEB_AUDIO_CHANNELS,
                    ),
                )

                sim_clock[0] += 0.300
                latency_observer.observe_frame(BotStoppedSpeakingFrame(), source="WebProtobufOutput")
                await _send_json_message_frame(
                    websocket,
                    serializer,
                    {
                        "type": "agent_stop_talking",
                        "call_id": str(call.id),
                        "turn_index": turn_index,
                    },
                )
                total_ms = int(round((fake.stt_delay_s + fake.llm_delay_s + fake.tts_delay_s) * 1000))
                await _send_json_message_frame(
                    websocket,
                    serializer,
                    {
                        "type": "latency",
                        "total_ms": total_ms,
                        "stt_ms": int(round(fake.stt_delay_s * 1000)),
                        "llm_ms": int(round(fake.llm_delay_s * 1000)),
                        "tts_ms": int(round(fake.tts_delay_s * 1000)),
                    },
                )
    except WebSocketDisconnect:
        pass
    finally:
        now = datetime.now(timezone.utc)
        for speaker, text in persisted_messages:
            redacted = await redact_turn_text(session, call, text)
            session.add(
                Turn(
                    call_id=call.id,
                    speaker=speaker,
                    text=redacted,
                )
            )
        call.llm_used = f"{fake.llm_provider}/{fake.llm_model}"
        call.status = CallStatus.COMPLETED
        call.ended_at = now
        if call.started_at:
            started_aware = (
                call.started_at
                if call.started_at.tzinfo is not None
                else call.started_at.replace(tzinfo=timezone.utc)
            )
            call.duration_seconds = max(0.001, round((now - started_aware).total_seconds(), 3))

        await latency_observer.persist(session, call)

        if total_llm_tokens > 0:
            await on_llm_tokens(
                session,
                tenant,
                call_id=call.id,
                tokens=total_llm_tokens,
                turn=turn_index,
                provider=fake.llm_provider,
            )
        if total_tts_chars > 0:
            await on_tts_characters(
                session,
                tenant,
                call_id=call.id,
                characters=total_tts_chars,
                turn=turn_index,
                provider=fake.tts_provider,
            )

        test_run = TestRun(
            tenant_id=tenant.id,
            environment_id=call.environment_id,
            agent_id=str(runtime_cfg.agent_id) if runtime_cfg.agent_id else "",
            agent_kind="voice",
            agent_version_id=runtime_cfg.agent_version_id,
            agent_version_number=int(runtime_cfg.agent_version_number or 1),
            mode=TestRunModeEnum.WEB_CALL.value,
            status=TestRunStatusEnum.PASSED.value,
            is_mock_provider=True,
            provider=fake.llm_provider,
            transcript_snapshot=[
                {"role": spk.value.lower(), "content": txt}
                for spk, txt in persisted_messages
            ],
            usage_metadata={
                "call_id": str(call.id),
                "stt_chars": total_stt_chars,
                "llm_tokens": total_llm_tokens,
                "tts_chars": total_tts_chars,
                "turns": turn_index,
                "is_mock_provider": True,
            },
            started_at=call.started_at or now,
            completed_at=now,
            created_at=now,
        )
        session.add(test_run)
        await session.commit()


async def _run_live_browser_pipecat_session(
    *,
    websocket: WebSocket,
    session: AsyncSession,
    tenant: Tenant,
    call: Call,
    runtime_cfg: RuntimeConfig,
) -> None:
    """Run the live Pipecat 0.0.94 voice pipeline over `FastAPIWebsocketTransport` + `ProtobufFrameSerializer`."""
    from app.ai.runtime import guard_spoken_text, voice_llm

    llm, prepared = await voice_llm(
        session,
        tenant,
        call,
        temperature=runtime_cfg.temperature,
        max_tokens=runtime_cfg.max_tokens or 110,
    )
    choice = prepared.choice

    turn_cfg = build_turn_config(runtime_cfg)
    serializer = ProtobufFrameSerializer()
    denoise_filter = build_denoise_filter(runtime_cfg.denoise_enabled)
    ambient_mixer = build_ambient_mixer(
        runtime_cfg.ambient_sound,
        ambient_volume=runtime_cfg.ambient_volume,
    )

    transport_kwargs: dict[str, Any] = {
        "audio_in_enabled": True,
        "audio_out_enabled": True,
        "add_wav_header": False,
        "serializer": serializer,
        "vad_analyzer": turn_cfg.vad_analyzer,
    }
    if turn_cfg.turn_analyzer is not None:
        transport_kwargs["turn_analyzer"] = turn_cfg.turn_analyzer
    if denoise_filter is not None:
        transport_kwargs["audio_in_filter"] = denoise_filter
    if ambient_mixer is not None:
        transport_kwargs["audio_out_mixer"] = ambient_mixer

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(**transport_kwargs),
    )

    if (
        runtime_cfg.stt_provider != "deepgram"
        or runtime_cfg.stt_fallback_providers
        or runtime_cfg.boosted_keywords
    ):
        stt = build_stt_for_runtime(runtime_cfg)
    else:
        stt = build_stt(tenant)

    if (
        runtime_cfg.tts_provider != "elevenlabs"
        or runtime_cfg.tts_fallback_providers
        or runtime_cfg.voice_settings
    ):
        tts = build_tts_for_runtime(runtime_cfg)
    else:
        tts = build_tts(tenant)

    task: PipelineTask | None = None

    async def _push_frame_to_task(frame: Any) -> None:
        if task is not None:
            await task.queue_frames([frame])

    handlers = FunctionHandlers(
        session=session,
        tenant=tenant,
        call=call,
        runtime_config=runtime_cfg,
        frame_pusher=_push_frame_to_task,
    )
    active_tools = handlers.available_tools()
    tool_calls_this_call = 0

    async def _tool_bridge(params: Any) -> None:
        nonlocal tool_calls_this_call
        tool_calls_this_call += 1
        if tool_calls_this_call > MAX_TOOL_CALLS_PER_CALL:
            await params.result_callback(
                {"ok": False, "message": "I can't do that right now. Offer to take a message."}
            )
            return
        from app.ai.guardrails.tool_policy import agent_runtime_decision

        custom_names = {t.get("name") for t in runtime_cfg.custom_tools} | {
            t.get("qualified_name") or t.get("name") for t in runtime_cfg.mcp_tools
        }
        if params.function_name not in custom_names:
            verdict = agent_runtime_decision(params.function_name)
            if not verdict.allowed:
                await params.result_callback({"ok": False, "message": "That action is not allowed."})
                return
        result = await handlers.dispatch(params.function_name, params.arguments or {})
        await params.result_callback(result)

    for schema in active_tools:
        llm.register_function(schema["function"]["name"], _tool_bridge)

    greeting = vary_greeting(
        runtime_cfg.greeting or tenant.greeting,
        tenant.name,
        tenant.agent_name,
    )
    lang_profile = resolve_language_config(runtime_cfg.language or tenant.language)

    context = OpenAILLMContext(
        messages=[
            {
                "role": "system",
                "content": (
                    (
                        runtime_cfg.system_prompt
                        or prepared.system_prompt
                        or build_system_prompt(tenant, choice.provider)
                    )
                    + lang_profile.llm_instruction
                ),
            },
            {"role": "assistant", "content": greeting},
        ],
        tools=active_tools,
    )
    context_aggregator = llm.create_context_aggregator(context)

    humanizers = []
    if tenant.humanize or runtime_cfg.backchannel_enabled:
        humanizers.append(
            Backchannel(
                after_seconds=3.0,
                cooldown=15.0,
                enabled=runtime_cfg.backchannel_enabled,
                frequency=runtime_cfg.backchannel_frequency,
                words=list(runtime_cfg.backchannel_words) if runtime_cfg.backchannel_words else None,
            )
        )
    idle_proc = build_idle_reminder_processor(runtime_cfg)
    idle_frame_proc = idle_proc[0] if isinstance(idle_proc, tuple) else idle_proc
    if idle_frame_proc is not None:
        humanizers.append(idle_frame_proc)

    latency_observer = LatencyObserver(
        call_id=call.id,
        tenant_id=tenant.id,
        tenant_plan=getattr(tenant.plan, "value", str(tenant.plan)),
        stt_provider=runtime_cfg.stt_provider,
        llm_provider=choice.provider,
        tts_provider=runtime_cfg.tts_provider,
    )
    latency_tracker = LatencyTrackingProcessor(latency_observer)
    turn_tracking_observer = TurnTrackingObserver()

    stt_usage = UsageTracker(track_stt=True)
    voice_usage = UsageTracker(track_voice=True, provider=choice.provider)

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            stt_usage,
            GovernedHearing(),
            *humanizers,
            context_aggregator.user(),
            llm,
            FillerInjector(),
            TextNormalizer(),
            GovernedSpeech(blocked_substrings=prepared.blocked_output),
            tts,
            voice_usage,
            latency_tracker,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=turn_cfg.allow_interruptions,
            enable_metrics=True,
            enable_usage_metrics=True,
            audio_in_sample_rate=WEB_AUDIO_SAMPLE_RATE,
            audio_out_sample_rate=WEB_AUDIO_SAMPLE_RATE,
        ),
        observers=[latency_observer, turn_tracking_observer],
    )

    @transport.event_handler("on_client_connected")
    async def _on_connected(_transport: Any, _client: Any) -> None:
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(_transport: Any, _client: Any) -> None:
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    try:
        await runner.run(task)
    finally:
        now = datetime.now(timezone.utc)
        for msg in context.get_messages():
            role, content = msg.get("role"), msg.get("content")
            if not content or role == "system":
                continue
            spoken = content if isinstance(content, str) else str(content)
            if role != "user":
                spoken = guard_spoken_text(spoken, blocked_substrings=prepared.blocked_output)
            spoken = await redact_turn_text(session, call, spoken)
            session.add(
                Turn(
                    call_id=call.id,
                    speaker=Speaker.USER if role == "user" else Speaker.ASSISTANT,
                    text=spoken,
                )
            )
        call.llm_used = f"{choice.provider}/{choice.model}"
        call.status = CallStatus.COMPLETED
        call.ended_at = now
        if call.started_at:
            started_aware = (
                call.started_at
                if call.started_at.tzinfo is not None
                else call.started_at.replace(tzinfo=timezone.utc)
            )
            call.duration_seconds = max(0.0, round((now - started_aware).total_seconds(), 3))

        await latency_observer.persist(session, call)
        usage = voice_usage.snapshot()
        measured_llm_tokens = usage["llm_tokens"]
        if isinstance(measured_llm_tokens, int) and not isinstance(measured_llm_tokens, bool):
            await on_llm_tokens(
                session,
                tenant,
                call_id=call.id,
                tokens=measured_llm_tokens,
                turn=0,
                provider=choice.provider,
            )
        await on_tts_characters(
            session,
            tenant,
            call_id=call.id,
            characters=int(usage["tts_chars"] or 0),
            turn=0,
            provider=runtime_cfg.tts_provider or settings.tts_provider,
        )
        await session.commit()


@router.websocket("/telephony/web/ws")
async def web_call_websocket_endpoint(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Browser WebSocket endpoint speaking Pipecat Protobuf frames (`ProtobufFrameSerializer`)."""
    raw_token = token
    if not raw_token:
        auth_header = websocket.headers.get("authorization") or ""
        if auth_header.lower().startswith("bearer "):
            raw_token = auth_header[7:].strip()

    origin = extract_request_origin(
        origin_header=websocket.headers.get("origin"),
        referer_header=websocket.headers.get("referer"),
    )

    try:
        call, tenant, _ = await consume_web_call_token(
            session,
            raw_token,
            request_origin=origin,
        )
    except WebCallSecurityError as exc:
        close_code = 4401
        if exc.status_code == 403:
            close_code = 4403
        elif exc.status_code == 404:
            close_code = 4404
        await websocket.close(code=close_code, reason=exc.code)
        return

    await websocket.accept()

    now = datetime.now(timezone.utc)
    call.status = CallStatus.IN_PROGRESS
    call.started_at = now
    await session.commit()

    media_gateway_manager.open_session(
        call_id=call.id,
        tenant_id=tenant.id,
        provider_call_id=call.call_sid,
        encoding="pcm16",
        sample_rate=WEB_AUDIO_SAMPLE_RATE,
    )

    ctx = dict(call.transfer_context or {})
    dyn_vars = dict(ctx.get("dynamic_variables") or {})
    meta = dict(ctx.get("metadata") or {})

    runtime_cfg = await resolve_runtime_config(
        session,
        call,
        tenant=tenant,
        agent_id=call.agent_id,
        environment=call.environment_id,
    )

    with correlation_scope(
        call_sid=call.call_sid,
        call_id=str(call.id),
        tenant_id=str(tenant.id),
    ):
        try:
            active_fake = _ACTIVE_PROVIDER_FAKE
            if active_fake is None and bool(meta.get("is_mock_provider") or dyn_vars.get("is_mock_provider")):
                active_fake = WebCallProviderFake(is_mock_provider=True)

            if active_fake is not None:
                await _run_provider_fake_session(
                    websocket=websocket,
                    session=session,
                    tenant=tenant,
                    call=call,
                    runtime_cfg=runtime_cfg,
                    fake=active_fake,
                )
            else:
                await _run_live_browser_pipecat_session(
                    websocket=websocket,
                    session=session,
                    tenant=tenant,
                    call=call,
                    runtime_cfg=runtime_cfg,
                )
        finally:
            media_gateway_manager.close_session(call.id)


@router.post("/telephony/web/offer")
async def web_call_webrtc_offer_endpoint(
    payload: WebRTCOfferRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Phase-2 SmallWebRTC SDP signaling endpoint (`/telephony/web/offer`).

    Validates and consumes the single-use web-call `access_token`, then negotiates
    a WebRTC peer connection via `pipecat.transports.smallwebrtc.transport.SmallWebRTCTransport`
    when `aiortc` is installed. Fails closed with `501 UNSUPPORTED_CAPABILITY` if `aiortc`
    is not installed in the runtime environment.
    """
    origin = extract_request_origin(
        origin_header=request.headers.get("origin"),
        referer_header=request.headers.get("referer"),
    )
    call, tenant, _ = await consume_web_call_token(
        session,
        payload.access_token,
        request_origin=origin,
    )

    try:
        from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport  # noqa: F401
    except Exception as exc:
        raise HTTPException(
            status_code=501,
            detail={
                "code": "UNSUPPORTED_CAPABILITY",
                "state": "UNSUPPORTED_CAPABILITY",
                "fallback_transport": "ws-protobuf",
                "message": (
                    "SmallWebRTCTransport requires pipecat-ai[webrtc] (aiortc) to be installed. "
                    "Use the WebSocket Protobuf transport at /telephony/web/ws."
                ),
                "call_id": str(call.id),
            },
        ) from exc

    conn = SmallWebRTCConnection()
    await conn.initialize(sdp=payload.sdp, type=payload.type)
    answer = conn.get_answer()
    if not answer:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "WEBRTC_NEGOTIATION_FAILED",
                "message": "SmallWebRTCConnection did not produce an SDP answer.",
            },
        )

    return {
        "call_id": str(call.id),
        "tenant_id": str(tenant.id),
        "transport": "webrtc",
        "sdp": answer.get("sdp") if isinstance(answer, dict) else getattr(answer, "sdp", ""),
        "type": answer.get("type", "answer") if isinstance(answer, dict) else getattr(answer, "type", "answer"),
    }
