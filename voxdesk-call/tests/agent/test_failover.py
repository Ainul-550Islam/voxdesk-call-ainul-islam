"""Unit tests for circuit-breaker provider failover (Sub-Phase 2C)."""

from __future__ import annotations

import pytest
from pipecat.frames.frames import ErrorFrame, Frame, TextFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.agent.providers.failover import CircuitState, FailoverServiceWrapper
from app.core.metrics import PROVIDER_FAILOVER_TOTAL


class _FlakyProcessor(FrameProcessor):
    def __init__(self, provider: str, fail_mode: str = "exception") -> None:
        super().__init__()
        self.provider = provider
        self.fail_mode = fail_mode
        self.calls = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self.calls += 1
        if self.fail_mode == "exception":
            raise RuntimeError(f"{self.provider} HTTP 503 Service Unavailable")
        if self.fail_mode == "error_frame":
            await self.push_frame(ErrorFrame(error=f"{self.provider} stream failed"), direction)
            return
        await self.push_frame(
            TextFrame(text=f"[{self.provider}] {getattr(frame, 'text', '')}"),
            direction,
        )


@pytest.mark.asyncio
async def test_failover_switches_after_two_failures_and_increments_metric():
    clock_now = [100.0]
    primary = _FlakyProcessor("openai", fail_mode="exception")
    fallback = _FlakyProcessor("anthropic", fail_mode="ok")

    wrapper = FailoverServiceWrapper(
        stage="llm",
        primary=("openai", primary),
        fallbacks=[("anthropic", fallback)],
        failure_threshold=2,
        window_seconds=30.0,
        cooldown_seconds=60.0,
        clock=lambda: clock_now[0],
    )

    outputs: list[Frame] = []

    async def _capture(frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM):
        outputs.append(frame)

    wrapper.push_frame = _capture  # type: ignore[method-assign]

    before_metric = PROVIDER_FAILOVER_TOTAL.labels(
        stage="llm", from_provider="openai", to_provider="anthropic"
    )._value.get()

    # 1st failure: threshold is 2, so circuit stays CLOSED and first call raises
    with pytest.raises(RuntimeError):
        await wrapper.process_frame(TextFrame(text="hello 1"), FrameDirection.DOWNSTREAM)
    assert wrapper.slots[0].state == CircuitState.CLOSED
    assert wrapper.active_provider == "openai"

    # 2nd failure within 30s: trips circuit to OPEN, switches to anthropic, and succeeds on retry!
    clock_now[0] = 105.0
    await wrapper.process_frame(TextFrame(text="hello 2"), FrameDirection.DOWNSTREAM)

    assert wrapper.slots[0].state == CircuitState.OPEN
    assert wrapper.active_provider == "anthropic"
    assert len(outputs) == 1
    assert isinstance(outputs[0], TextFrame)
    assert outputs[0].text == "[anthropic] hello 2"

    after_metric = PROVIDER_FAILOVER_TOTAL.labels(
        stage="llm", from_provider="openai", to_provider="anthropic"
    )._value.get()
    assert after_metric == before_metric + 1.0

    # After cooldown (60s), primary transitions to HALF_OPEN
    clock_now[0] = 170.0
    wrapper._refresh_circuits()
    assert wrapper.slots[0].state == CircuitState.HALF_OPEN


@pytest.mark.asyncio
async def test_failover_handles_error_frame_from_primary_tts():
    primary_tts = _FlakyProcessor("elevenlabs", fail_mode="error_frame")
    fallback_tts = _FlakyProcessor("cartesia", fail_mode="ok")

    wrapper = FailoverServiceWrapper(
        stage="tts",
        primary=("elevenlabs", primary_tts),
        fallbacks=[("cartesia", fallback_tts)],
        failure_threshold=2,
    )
    outputs: list[Frame] = []

    async def _capture(frame: Frame, direction: FrameDirection = FrameDirection.DOWNSTREAM):
        outputs.append(frame)

    wrapper.push_frame = _capture  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        await wrapper.process_frame(TextFrame(text="turn 1"), FrameDirection.DOWNSTREAM)

    await wrapper.process_frame(TextFrame(text="turn 2"), FrameDirection.DOWNSTREAM)
    assert wrapper.active_provider == "cartesia"
    assert len(outputs) == 1
    assert getattr(outputs[0], "text", "") == "[cartesia] turn 2"
