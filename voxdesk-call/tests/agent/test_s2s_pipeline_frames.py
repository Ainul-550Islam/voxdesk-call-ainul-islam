"""The speech-to-speech audio path: the exact converters, the frame chunking
and the Pipecat processor that carries the audio in both directions.

No provider is contacted: the session is a local double that speaks the
:class:`S2SSession` contract.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

import pytest

from app.agent.s2s.s2s_audio import (
    ALAW,
    GEMINI_LIVE_SAMPLE_RATE,
    OPENAI_REALTIME_SAMPLE_RATE,
    PCM16,
    PIPELINE_SAMPLE_RATE,
    ULAW,
    chunk_payload,
    concat,
    decode,
    encode,
    frame_duration_ms,
    from_pipeline_pcm,
    resample_pcm16,
    to_pipeline_pcm,
)
from app.agent.s2s.s2s_session import (
    S2SEvent,
    S2SEventType,
    S2SSession,
)


# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
def test_2x_resampling_is_exact() -> None:
    pcm = b"\x00\x10" * 160
    up = resample_pcm16(pcm, source_rate=8000, target_rate=16000)
    assert len(up) == len(pcm) * 2
    down = resample_pcm16(up, source_rate=16000, target_rate=8000)
    assert len(down) == len(pcm)


def test_rational_resampling_produces_the_expected_length() -> None:
    pcm = b"\x00\x10" * 240
    out = resample_pcm16(pcm, source_rate=24000, target_rate=16000)
    # 240 samples at 24 kHz are 160 samples at 16 kHz.
    assert len(out) == 160 * 2


def test_resampling_rejects_an_invalid_rate() -> None:
    with pytest.raises(ValueError):
        resample_pcm16(b"", source_rate=0, target_rate=8000)


def test_ulaw_and_alaw_round_trip() -> None:
    pcm = b"\x00\x10" * 160
    assert len(decode(encode(pcm, ULAW), ULAW)) == len(pcm)
    assert len(decode(encode(pcm, ALAW), ALAW)) == len(pcm)


def test_an_unknown_codec_is_rejected() -> None:
    with pytest.raises(ValueError):
        encode(b"\x00\x00", "opus")
    with pytest.raises(ValueError):
        decode(b"\x00", "opus")


def test_pcm16_is_the_identity_codec() -> None:
    pcm = b"\x01\x02\x03\x04"
    assert encode(pcm, PCM16) == pcm
    assert decode(pcm, PCM16) == pcm


def test_provider_audio_reaches_the_pipeline() -> None:
    pcm = b"\x00\x10" * 320
    pipeline_pcm = to_pipeline_pcm(
        pcm, codec=PCM16, source_rate=GEMINI_LIVE_SAMPLE_RATE, target_rate=PIPELINE_SAMPLE_RATE
    )
    assert len(pipeline_pcm) == len(pcm) // 2


def test_pipeline_audio_reaches_the_provider() -> None:
    pcm = b"\x00\x10" * 160
    wire = from_pipeline_pcm(
        pcm, codec=PCM16, source_rate=PIPELINE_SAMPLE_RATE, target_rate=OPENAI_REALTIME_SAMPLE_RATE
    )
    assert len(wire) == len(pcm) * 3


def test_frame_duration_is_measured_in_milliseconds() -> None:
    # 20 ms of 16-bit PCM at 8 kHz is 320 bytes.
    assert frame_duration_ms(b"\x00" * 320, sample_rate=8000) == pytest.approx(20.0)
    assert frame_duration_ms(b"\x00" * 160, sample_rate=8000, codec=ULAW) == pytest.approx(20.0)


def test_chunking_keeps_a_short_tail() -> None:
    chunks = chunk_payload(b"\x00" * 250, frame_bytes=100)
    assert [len(chunk) for chunk in chunks] == [100, 100, 50]
    assert concat(chunks) == b"\x00" * 250


# ---------------------------------------------------------------------------
# The processor
# ---------------------------------------------------------------------------
class _FakeSession(S2SSession):
    provider = "fake"

    def __init__(self, **kwargs) -> None:
        super().__init__(api_key="test", **kwargs)
        self.sent: list[bytes] = []
        self.interrupts = 0
        self.closed_called = False
        self.tool_results: list[tuple[str, str, dict]] = []

    async def start(self) -> None:
        self.started_at = 0.0

    async def send_audio(self, pcm: bytes, *, sample_rate: int) -> None:
        self.sent.append(pcm)

    async def interrupt(self) -> None:
        self.interrupts += 1

    async def close(self) -> None:
        self.closed_called = True
        self.finish()

    async def events(self) -> AsyncIterator[S2SEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    async def send_tool_result(self, call_id: str, name: str, result: dict) -> None:
        self.tool_results.append((call_id, name, result))


async def _noop_push(frame, direction):  # noqa: ANN001
    return None


@pytest.mark.asyncio
async def test_the_processor_forwards_audio_and_emits_frames() -> None:
    from pipecat.frames.frames import InputAudioRawFrame

    from app.agent.s2s.s2s_pipeline import S2SPipelineProcessor

    session = _FakeSession(sample_rate=16000)
    processor = S2SPipelineProcessor(
        session=session,
        provider_sample_rate=16000,
        provider_codec=PCM16,
    )
    pushed: list = []

    class _Sink:
        async def push_frame(self, frame, direction):  # noqa: ANN001
            pushed.append(frame)

    processor.push_frame = _Sink().push_frame  # type: ignore[method-assign]
    await processor.start()
    try:
        await processor.process_frame(
            InputAudioRawFrame(audio=b"\x00\x10" * 160, sample_rate=8000, num_channels=1),
            __import__(
                "pipecat.processors.frame_processor", fromlist=["FrameDirection"]
            ).FrameDirection.DOWNSTREAM,
        )
        assert session.sent
        session.emit(
            S2SEvent(
                type=S2SEventType.AUDIO_OUT,
                audio=b"\x00\x10" * 160,
                sample_rate=16000,
            )
        )
        session.emit(
            S2SEvent(type=S2SEventType.TRANSCRIPT_IN, text="hello", provider="fake")
        )
        await asyncio.sleep(0.05)
    finally:
        await processor.stop()

    kinds = [type(frame).__name__ for frame in pushed]
    assert "BotStartedSpeakingFrame" in kinds
    assert "OutputAudioRawFrame" in kinds
    assert "TranscriptionFrame" in kinds


@pytest.mark.asyncio
async def test_the_processor_interrupts_the_provider_on_barge_in() -> None:
    from pipecat.processors.frame_processor import FrameDirection

    from app.agent.s2s.s2s_pipeline import S2SPipelineProcessor

    session = _FakeSession()
    processor = S2SPipelineProcessor(session=session)
    processor.push_frame = _noop_push  # type: ignore[method-assign]
    await processor.start()
    try:
        from pipecat.frames.frames import StartInterruptionFrame

        await processor.process_frame(StartInterruptionFrame(), FrameDirection.DOWNSTREAM)
        assert session.interrupts == 1
    finally:
        await processor.stop()


# ---------------------------------------------------------------------------
# The latency hook
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_processor_measures_the_time_to_first_audio() -> None:
    """The provider's first audio closes the measured turn."""
    from pipecat.processors.frame_processor import FrameDirection

    from app.agent.s2s.s2s_pipeline import S2SPipelineProcessor

    session = _FakeSession(sample_rate=16000)
    processor = S2SPipelineProcessor(
        session=session,
        provider_sample_rate=16000,
        provider_codec=PCM16,
    )
    pushed: list = []

    class _Sink:
        async def push_frame(self, frame, direction):  # noqa: ANN001
            pushed.append(frame)

    processor.push_frame = _Sink().push_frame  # type: ignore[method-assign]
    await processor.start()
    try:
        from pipecat.frames.frames import UserStoppedSpeakingFrame

        await processor.process_frame(
            UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        session.emit(
            S2SEvent(
                type=S2SEventType.AUDIO_OUT,
                audio=b"\x00\x10" * 160,
                sample_rate=16000,
            )
        )
        await asyncio.sleep(0.05)
    finally:
        await processor.stop()

    kinds = [type(frame).__name__ for frame in pushed]
    # The latency observer needs the TTS frame to close the turn.
    assert "TTSStartedFrame" in kinds
    assert processor.time_to_first_audio_ms is not None
    assert processor.time_to_first_audio_ms >= 0.0


@pytest.mark.asyncio
async def test_the_latency_observer_sees_the_turn_of_a_speech_to_speech_call() -> None:
    """The pipeline's observer closes the turn with the provider's audio."""
    from pipecat.frames.frames import UserStoppedSpeakingFrame
    from pipecat.processors.frame_processor import FrameDirection

    from app.agent.latency import LatencyObserver
    from app.agent.s2s.s2s_pipeline import S2SPipelineProcessor

    observer = LatencyObserver(clock=lambda: 100.0)
    session = _FakeSession(sample_rate=16000)
    processor = S2SPipelineProcessor(
        session=session,
        provider_sample_rate=16000,
        provider_codec=PCM16,
    )
    pushed: list = []

    class _Sink:
        async def push_frame(self, frame, direction):  # noqa: ANN001
            pushed.append(frame)
            observer.observe_frame(frame)

    processor.push_frame = _Sink().push_frame  # type: ignore[method-assign]
    await processor.start()
    try:
        await processor.process_frame(
            UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM
        )
        session.emit(S2SEvent(type=S2SEventType.TRANSCRIPT_IN, text="hi"))
        session.emit(S2SEvent(type=S2SEventType.TRANSCRIPT_OUT, text="hello"))
        session.emit(
            S2SEvent(
                type=S2SEventType.AUDIO_OUT,
                audio=b"\x00\x10" * 160,
                sample_rate=16000,
            )
        )
        await asyncio.sleep(0.05)
    finally:
        await processor.stop()

    kinds = [type(frame).__name__ for frame in pushed]
    assert "TranscriptionFrame" in kinds
    assert "TextFrame" in kinds
    assert "TTSStartedFrame" in kinds
    assert observer.turn_samples, "the observer did not record the turn"
    sample = observer.turn_samples[-1]
    assert sample.stt_first_at is not None
    assert sample.llm_first_at is not None
    assert sample.tts_first_at is not None
    assert sample.e2e_ms is not None
