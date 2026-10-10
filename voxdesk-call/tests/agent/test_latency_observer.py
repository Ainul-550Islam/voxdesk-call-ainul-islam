"""Unit and DB persistence tests for LatencyObserver (Sub-Phase 2A)."""

from __future__ import annotations

import uuid

import pytest
from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    InterruptionFrame,
    MetricsFrame,
    TextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.observers.base_observer import FramePushed
from pipecat.processors.frame_processor import FrameDirection

from app.agent.latency import LatencyObserver
from app.core.metrics import VOICE_E2E_LATENCY, VOICE_INTERRUPTIONS, VOICE_TTFB
from app.db.models import Call, CallStatus, Tenant


def test_latency_observer_computes_percentiles_and_metrics():
    now = 100.0

    def _clock() -> float:
        return now

    observer = LatencyObserver(
        tenant_plan="growth",
        stt_provider="deepgram",
        llm_provider="openai",
        tts_provider="elevenlabs",
        clock=_clock,
    )

    e2e_before = VOICE_E2E_LATENCY.labels(
        tenant_plan="growth",
        llm_provider="openai",
        tts_provider="elevenlabs",
    )._sum.get()
    stt_before = VOICE_TTFB.labels(stage="stt", provider="deepgram")._sum.get()
    intr_before = VOICE_INTERRUPTIONS.labels(tenant_plan="growth")._value.get()

    # Turn 1: 80ms STT + 150ms LLM + 70ms TTS = 300ms E2E
    observer.observe_frame(UserStartedSpeakingFrame(), timestamp_s=now)
    now += 1.0
    observer.observe_frame(UserStoppedSpeakingFrame(), timestamp_s=now)
    now += 0.080
    observer.observe_frame(
        MetricsFrame(
            data=[TTFBMetricsData(processor="DeepgramSTTService", model="nova-3", value=0.080)]
        ),
        timestamp_s=now,
    )
    observer.observe_frame(
        TranscriptionFrame(text="Hello", user_id="u1", timestamp=str(now)),
        timestamp_s=now,
    )
    now += 0.150
    observer.observe_frame(
        MetricsFrame(
            data=[TTFBMetricsData(processor="OpenAILLMService", model="gpt-4o-mini", value=0.150)]
        ),
        timestamp_s=now,
    )
    observer.observe_frame(TextFrame(text="Hi there!"), timestamp_s=now)
    now += 0.070
    observer.observe_frame(
        MetricsFrame(
            data=[
                TTFBMetricsData(
                    processor="ElevenLabsTTSService",
                    model="eleven_flash_v2_5",
                    value=0.070,
                )
            ]
        ),
        timestamp_s=now,
    )
    observer.observe_frame(TTSStartedFrame(), timestamp_s=now)
    observer.observe_frame(BotStartedSpeakingFrame(), timestamp_s=now)

    # Caller barges in while bot is still speaking
    now += 0.200
    observer.observe_frame(UserStartedSpeakingFrame(), timestamp_s=now)
    observer.observe_frame(InterruptionFrame(), timestamp_s=now)
    observer.observe_frame(BotStoppedSpeakingFrame(), timestamp_s=now)

    # Turn 2: 100ms STT + 200ms LLM + 100ms TTS = 400ms E2E
    now += 0.800
    observer.observe_frame(UserStoppedSpeakingFrame(), timestamp_s=now)
    now += 0.100
    observer.observe_frame(
        TranscriptionFrame(text="Book tomorrow", user_id="u1", timestamp=str(now)),
        timestamp_s=now,
    )
    now += 0.200
    observer.observe_frame(TextFrame(text="Sure, 10 AM works."), timestamp_s=now)
    now += 0.100
    observer.observe_frame(
        TTSAudioRawFrame(audio=b"\x00" * 160, sample_rate=8000, num_channels=1),
        timestamp_s=now,
    )
    observer.observe_frame(BotStoppedSpeakingFrame(), timestamp_s=now)

    stat = observer.summarize()
    assert stat.turns == 2
    assert stat.interruptions == 1
    assert stat.e2e_p50_ms == pytest.approx(350.0, rel=1e-2)
    assert stat.e2e_p95_ms == pytest.approx(395.0, rel=1e-2)
    assert stat.e2e_p99_ms == pytest.approx(399.0, rel=1e-2)
    assert stat.stt_ttfb_p50_ms == pytest.approx(90.0, rel=1e-2)
    assert stat.llm_ttfb_p50_ms == pytest.approx(175.0, rel=1e-2)
    assert stat.tts_ttfb_p50_ms == pytest.approx(85.0, rel=1e-2)

    e2e_after = VOICE_E2E_LATENCY.labels(
        tenant_plan="growth",
        llm_provider="openai",
        tts_provider="elevenlabs",
    )._sum.get()
    stt_after = VOICE_TTFB.labels(stage="stt", provider="deepgram")._sum.get()
    intr_after = VOICE_INTERRUPTIONS.labels(tenant_plan="growth")._value.get()

    assert e2e_after > e2e_before
    assert stt_after > stt_before
    assert intr_after == intr_before + 1


@pytest.mark.asyncio
async def test_latency_observer_on_push_frame_and_persist(db):
    tenant = Tenant(
        id=uuid.uuid4(),
        name="Latency Tenant",
        twilio_number="+14155550199",
    )
    db.add(tenant)
    await db.flush()

    call = Call(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        call_sid="CA_LATENCY_001",
        from_number="+14155550100",
        to_number="+14155550199",
        status=CallStatus.IN_PROGRESS,
    )
    db.add(call)
    await db.flush()

    observer = LatencyObserver(tenant_plan="pro")
    await observer.on_push_frame(
        FramePushed(
            source=None,
            destination=None,
            frame=UserStoppedSpeakingFrame(),
            direction=FrameDirection.DOWNSTREAM,
            timestamp=1_000_000_000,  # 1.0s in ns
        )
    )
    await observer.on_push_frame(
        FramePushed(
            source=None,
            destination=None,
            frame=BotStartedSpeakingFrame(),
            direction=FrameDirection.DOWNSTREAM,
            timestamp=1_420_000_000,  # 1.42s in ns -> 420ms E2E
        )
    )

    persisted = await observer.persist(db, call)
    await db.commit()

    assert persisted.call_id == call.id
    assert persisted.tenant_id == tenant.id
    assert persisted.turns == 1
    assert persisted.e2e_p50_ms == pytest.approx(420.0, rel=1e-2)
    assert call.avg_response_ms == pytest.approx(420.0, rel=1e-2)
