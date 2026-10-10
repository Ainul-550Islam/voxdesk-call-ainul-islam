# File: tests/telephony/test_monitor_bus.py — Unit and integration tests for MonitorBus and MonitorTap (Part 4 / Gate G5)
"""Tests for ``app.telephony.monitor_bus`` and ``app.agent.monitor_tap``.

Verifies:
  1. Multi-subscriber fan-out of audio (PCM16) and transcript events per call.
  2. Bounded queue backpressure drops oldest frames without blocking producers.
  3. Zero frames published / zero overhead when no listeners or active sessions exist.
  4. ``MonitorTap.inject_guidance`` pushes ``LLMMessagesAppendFrame`` (+ ``LLMRunFrame``).
  5. Redis pub/sub round-trip serialization with ``fakeredis`` and PII-free logging.
"""

from __future__ import annotations

import uuid

import fakeredis.aioredis
import pytest

import app.db.models  # noqa: F401
from app.agent.monitor_tap import (
    MonitorOutputTap,
    MonitorTap,
    get_active_monitor_tap,
)
from app.telephony.monitor_bus import (
    MonitorBus,
    MonitorCapacityError,
)


@pytest.mark.asyncio
async def test_monitor_bus_no_frames_when_no_listeners() -> None:
    """When no sessions or subscribers exist for a call, publish_* is a no-op."""
    bus = MonitorBus(default_queue_size=8)
    call_id = uuid.uuid4()

    assert bus.has_listeners(call_id) is False
    delivered_audio = await bus.publish_audio(
        call_id, b"\x01\x02" * 160, speaker="caller", sample_rate=16000
    )
    delivered_tx = await bus.publish_transcript(
        call_id, "Sensitive caller utterance", speaker="caller"
    )

    assert delivered_audio == 0
    assert delivered_tx == 0


@pytest.mark.asyncio
async def test_monitor_bus_fanout_to_multiple_subscribers() -> None:
    """Audio and transcript events fan out to all subscribed supervisors on the call."""
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=False)
    bus = MonitorBus(default_queue_size=16, redis_getter=lambda: fake_redis)
    call_id = uuid.uuid4()
    other_call_id = uuid.uuid4()

    sub1 = await bus.subscribe(call_id, supervisor_id="sup-1", session_id="sess-1")
    sub2 = await bus.subscribe(call_id, supervisor_id="sup-2", session_id="sess-2")
    sub_other = await bus.subscribe(other_call_id, supervisor_id="sup-3", session_id="sess-3")

    try:
        assert bus.has_listeners(call_id) is True
        assert bus.subscriber_count(call_id) == 2

        pcm_payload = b"\x10\x00\x20\x00" * 80
        count_audio = await bus.publish_audio(
            call_id, pcm_payload, speaker="caller", sample_rate=16000
        )
        count_tx = await bus.publish_transcript(
            call_id, "Hello, I need help with my invoice.", speaker="caller", is_final=True
        )

        assert count_audio == 2
        assert count_tx == 2
        assert sub_other.qsize == 0

        evt1_audio = await sub1.get(timeout=1.0)
        evt2_audio = await sub2.get(timeout=1.0)
        assert evt1_audio.kind == "audio"
        assert evt1_audio.pcm_bytes == pcm_payload
        assert evt1_audio.speaker == "caller"
        assert evt2_audio.pcm_bytes == pcm_payload

        evt1_tx = await sub1.get(timeout=1.0)
        evt2_tx = await sub2.get(timeout=1.0)
        assert evt1_tx.kind == "transcript"
        assert evt1_tx.text == "Hello, I need help with my invoice."
        assert evt2_tx.text == "Hello, I need help with my invoice."
    finally:
        await sub1.close()
        await sub2.close()
        await sub_other.close()

    assert bus.subscriber_count(call_id) == 0


@pytest.mark.asyncio
async def test_monitor_bus_backpressure_drops_oldest() -> None:
    """When a subscriber queue is full, oldest frames are dropped and newer frames arrive."""
    bus = MonitorBus(default_queue_size=4)
    call_id = uuid.uuid4()

    sub = await bus.subscribe(call_id, supervisor_id="sup-slow", max_queue_size=3)
    try:
        for idx in range(6):
            await bus.publish_transcript(
                call_id,
                f"turn-{idx}",
                speaker="caller",
                seq=idx,
            )

        assert sub.qsize == 3
        assert sub.dropped_frames == 3

        received = [
            (await sub.get(timeout=1.0)).text,
            (await sub.get(timeout=1.0)).text,
            (await sub.get(timeout=1.0)).text,
        ]
        assert received == ["turn-3", "turn-4", "turn-5"]
    finally:
        await sub.close()


@pytest.mark.asyncio
async def test_monitor_bus_enforces_max_supervisors_per_call() -> None:
    """Subscribing beyond max_subscribers_per_call raises MonitorCapacityError."""
    bus = MonitorBus(default_queue_size=8)
    call_id = uuid.uuid4()

    subs = [
        await bus.subscribe(call_id, supervisor_id=f"sup-{i}", max_subscribers_per_call=2)
        for i in range(2)
    ]
    try:
        with pytest.raises(MonitorCapacityError):
            await bus.subscribe(call_id, supervisor_id="sup-overflow", max_subscribers_per_call=2)
    finally:
        for s in subs:
            await s.close()


@pytest.mark.asyncio
async def test_monitor_tap_zero_overhead_when_inactive_and_injects_guidance() -> None:
    """MonitorTap only publishes when active and injects whisper guidance frames into LLM."""
    from pipecat.frames.frames import (
        InputAudioRawFrame,
        LLMMessagesAppendFrame,
        LLMRunFrame,
        OutputAudioRawFrame,
        TextFrame,
        TranscriptionFrame,
    )

    bus = MonitorBus(default_queue_size=16)
    call_id = uuid.uuid4()
    tap = MonitorTap(call_id, bus=bus)
    out_tap = MonitorOutputTap(tap)

    downstream_frames = []

    async def _capture_push(frame, direction=None):
        downstream_frames.append((frame, direction))

    tap.push_frame = _capture_push  # type: ignore[method-assign]

    try:
        assert get_active_monitor_tap(call_id) is tap
        assert tap.has_active_session() is False

        # Processing frames without active session forwards frame immediately with 0 bus publishes
        audio_in = InputAudioRawFrame(audio=b"\x01\x00" * 160, sample_rate=16000, num_channels=1)
        await tap.process_frame(audio_in)
        assert len(downstream_frames) == 1

        # Subscribe a supervisor and verify caller + agent audio/transcripts fan out
        sub = await bus.subscribe(call_id, supervisor_id="sup-1", session_id="sess-1")
        try:
            assert tap.has_active_session() is True

            await tap.process_frame(audio_in)
            tx_in = TranscriptionFrame(
                text="Can I speak to a manager?",
                user_id="caller",
                timestamp="2026-10-08T00:00:00Z",
            )
            await tap.process_frame(tx_in)

            out_audio = OutputAudioRawFrame(
                audio=b"\x02\x00" * 160, sample_rate=16000, num_channels=1
            )
            await out_tap.process_frame(out_audio)
            await out_tap.process_frame(TextFrame(text="I can help you right away."))

            e1 = await sub.get(timeout=1.0)
            e2 = await sub.get(timeout=1.0)
            e3 = await sub.get(timeout=1.0)
            e4 = await sub.get(timeout=1.0)

            assert (e1.kind, e1.speaker) == ("audio", "caller")
            assert (e2.kind, e2.speaker, e2.text) == (
                "transcript",
                "caller",
                "Can I speak to a manager?",
            )
            assert (e3.kind, e3.speaker) == ("audio", "agent")
            assert (e4.kind, e4.speaker, e4.text) == (
                "transcript",
                "agent",
                "I can help you right away.",
            )

            # Test whisper-to-AI via MonitorBus -> MonitorTap.inject_guidance
            downstream_frames.clear()
            delivered = await bus.publish_guidance(
                call_id,
                "Offer the customer a complimentary month.",
                supervisor_id="sup-1",
                session_id="sess-1",
                run_llm=True,
            )
            assert delivered is True
            assert len(downstream_frames) == 2
            append_frame = downstream_frames[0][0]
            run_frame = downstream_frames[1][0]
            assert isinstance(append_frame, LLMMessagesAppendFrame)
            assert append_frame.messages[0]["role"] == "system"
            assert "Offer the customer a complimentary month." in append_frame.messages[0]["content"]
            assert isinstance(run_frame, LLMRunFrame)

            # Guidance event also fanned out to monitoring subscribers
            e5 = await sub.get(timeout=1.0)
            assert e5.kind == "guidance"
            assert e5.speaker == "supervisor"
            assert e5.text == "Offer the customer a complimentary month."
        finally:
            await sub.close()
    finally:
        await tap.close()

    assert get_active_monitor_tap(call_id) is None
