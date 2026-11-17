"""P12-04 — begin-message delay and the endpointing presets it ships with."""

from __future__ import annotations

import pytest
from pipecat.frames.frames import TextFrame
from pipecat.processors.frame_processor import FrameDirection

from app.agent.turn_taking import (
    DEFAULT_ENDPOINTING_MODE,
    ENDPOINTING_MODES,
    STOP_SECS_BOUNDS,
    FirstMessageDelay,
    build_turn_config,
)


class _Cfg:
    responsiveness = 0.7
    interruption_sensitivity = 0.7
    enable_smart_turn = False
    barge_in_enabled = True


async def _drain(processor, frame: TextFrame) -> list[str]:
    pushed: list[str] = []

    async def capture(pushed_frame, direction=FrameDirection.DOWNSTREAM):
        if isinstance(pushed_frame, TextFrame):
            pushed.append(pushed_frame.text)

    processor.push_frame = capture  # type: ignore[assignment]
    await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
    return pushed


@pytest.mark.asyncio
async def test_first_message_is_delayed_by_the_configured_amount() -> None:
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    delay = FirstMessageDelay(delay_ms=800, sleep=fake_sleep)
    pushed = await _drain(delay, TextFrame(text="Hi, thanks for calling."))

    assert pushed == ["Hi, thanks for calling."]
    assert waited == [0.8]
    assert delay.applied is True
    assert delay.delayed_ms == 800


@pytest.mark.asyncio
async def test_later_frames_are_not_delayed() -> None:
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    delay = FirstMessageDelay(delay_ms=500, sleep=fake_sleep)
    await _drain(delay, TextFrame(text="Hello."))
    await _drain(delay, TextFrame(text="And here is the second sentence."))
    await _drain(delay, TextFrame(text="Third."))

    assert waited == [0.5], "only the first utterance may be held back"


@pytest.mark.asyncio
async def test_zero_delay_never_sleeps() -> None:
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    delay = FirstMessageDelay(delay_ms=0, sleep=fake_sleep)
    await _drain(delay, TextFrame(text="Hello."))
    assert waited == []
    assert delay.applied is False


def test_delay_is_clamped_to_a_sane_range() -> None:
    assert FirstMessageDelay(delay_ms=-500).delay_ms == 0
    assert FirstMessageDelay(delay_ms=99_000).delay_ms == 3000


def test_an_unset_endpointing_mode_keeps_the_legacy_mapping() -> None:
    class Cfg(_Cfg):
        begin_message_delay_ms = 1200
        stt_endpointing_mode = "accuracy"

    config = build_turn_config(Cfg())
    assert config.begin_message_delay_ms == 1200
    assert config.stt_endpointing_mode == "accuracy"

    default = build_turn_config(_Cfg())
    assert default.begin_message_delay_ms == 0
    assert default.stt_endpointing_mode == DEFAULT_ENDPOINTING_MODE == "default"


def test_an_unknown_endpointing_mode_falls_back_to_the_default() -> None:
    class Cfg(_Cfg):
        stt_endpointing_mode = "telepathy"

    assert build_turn_config(Cfg()).stt_endpointing_mode == DEFAULT_ENDPOINTING_MODE


def test_accuracy_waits_longer_than_default_and_default_longer_than_latency() -> None:
    class Accurate(_Cfg):
        stt_endpointing_mode = "accuracy"

    class Fast(_Cfg):
        stt_endpointing_mode = "latency"

    accurate = build_turn_config(Accurate()).vad_params.stop_secs
    fast = build_turn_config(Fast()).vad_params.stop_secs
    default = build_turn_config(_Cfg()).vad_params.stop_secs
    assert accurate > default > fast
    assert ENDPOINTING_MODES["accuracy"] > 1.0 > ENDPOINTING_MODES["latency"]


def test_eagerness_shortens_the_wait_and_the_bounds_still_hold() -> None:
    class Neutral(_Cfg):
        eagerness = 0.0

    class Eager(_Cfg):
        eagerness = 1.0

    class VeryEager(_Cfg):
        responsiveness = 1.0
        eagerness = 1.0
        stt_endpointing_mode = "latency"

    neutral = build_turn_config(Neutral()).vad_params.stop_secs
    eager = build_turn_config(Eager()).vad_params.stop_secs
    assert eager < neutral
    assert STOP_SECS_BOUNDS[0] <= build_turn_config(VeryEager()).vad_params.stop_secs <= STOP_SECS_BOUNDS[1]
