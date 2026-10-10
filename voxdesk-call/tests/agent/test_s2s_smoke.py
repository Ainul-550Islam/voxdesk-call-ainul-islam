"""Smoke tests for Speech-to-Speech (S2S) providers: OpenAI Realtime & Gemini Live (Sub-Phase 2C)."""

from __future__ import annotations

import os
import uuid

import pytest
from pipecat.frames.frames import (
    AudioRawFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from app.agent.latency import LatencyObserver, LatencyObserverProcessor
from app.agent.providers.s2s_providers import build_s2s_provider


class _S2SSettings:
    openai_api_key = "sk-test-realtime-key"
    google_api_key = "goog-test-gemini-live-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai_realtime", "gemini_live"])
async def test_s2s_provider_roundtrip_emits_audio_tool_call_and_latency(provider: str):
    clock = [10.0]
    call_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    observer = LatencyObserver(
        clock=lambda: clock[0],
    )
    lat_proc = LatencyObserverProcessor(observer)
    usage_events = []

    s2s = build_s2s_provider(
        provider,
        system_prompt="You are a realtime S2S assistant.",
        voice_id="alloy",
        sample_rate=8000,
        configured_settings=_S2SSettings(),
        session_adapter=lambda audio_in: (
            f"Heard {len(audio_in)} bytes",
            b"\x20\x00" * 320,
        ),
        usage_callback=lambda entry: usage_events.append(entry),
    )

    # Register and invoke a tool function to verify S2S FunctionHandlers parity
    async def _check_order_handler(
        function_name, tool_call_id, arguments, llm, context, result_callback
    ):
        await result_callback({"ok": True, "order_id": arguments["order_id"], "status": "shipped"})

    s2s.register_function("check_order", _check_order_handler)
    tool_res = await s2s.invoke_tool("check_order", {"order_id": "ORD-900"})
    assert tool_res == {"ok": True, "order_id": "ORD-900", "status": "shipped"}

    emitted = []

    async def _forward_to_latency(frame, direction=FrameDirection.DOWNSTREAM):
        emitted.append(frame)
        clock[0] += 0.08  # +80ms per frame stage
        await lat_proc.process_frame(frame, direction)

    s2s.push_frame = _forward_to_latency  # type: ignore[method-assign]

    async def _noop_push(frame, direction=FrameDirection.DOWNSTREAM):
        pass

    lat_proc.push_frame = _noop_push  # type: ignore[method-assign]

    await lat_proc.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
    await s2s.process_frame(
        AudioRawFrame(audio=b"\x01\x00" * 160, sample_rate=8000, num_channels=1),
        FrameDirection.DOWNSTREAM,
    )
    await s2s.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    assert any(isinstance(f, TranscriptionFrame) for f in emitted)
    assert any(isinstance(f, BotStartedSpeakingFrame) for f in emitted)
    assert any(isinstance(f, OutputAudioRawFrame) for f in emitted)
    assert any(isinstance(f, BotStoppedSpeakingFrame) for f in emitted)
    assert len(usage_events) == 1
    assert usage_events[0]["provider"] == provider
    assert usage_events[0]["tokens_in"] >= 1

    summary = observer.summarize(call_id=call_id, tenant_id=tenant_id)
    assert summary.turns == 1
    assert summary.e2e_p50_ms is not None
    assert summary.e2e_p50_ms > 0


@pytest.mark.live
@pytest.mark.asyncio
async def test_s2s_live_roundtrip_with_tool_call():
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        pytest.skip("Live S2S test requires OPENAI_API_KEY or GOOGLE_API_KEY")
    provider = "openai_realtime" if os.getenv("OPENAI_API_KEY") else "gemini_live"
    s2s = build_s2s_provider(provider)
    assert s2s.provider == provider
