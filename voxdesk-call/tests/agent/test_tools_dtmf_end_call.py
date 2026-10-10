"""Unit tests for built-in telephony tools: `end_call` and `send_dtmf` (Sub-Phase 2D)."""

from __future__ import annotations

import pytest
from pipecat.frames.frames import EndFrame, EndTaskFrame, Frame, OutputDTMFFrame, TTSSpeakFrame

from app.agent.tools.builtin_calls import (
    execute_end_call,
    execute_send_dtmf,
    validate_dtmf_digits,
)
from app.telephony.providers.base import SimulatedTelephonyAdapter


class _DummyCall:
    def __init__(self, call_sid: str = "CA_TEST_2D_001") -> None:
        self.call_sid = call_sid
        self.end_reason: str | None = None


@pytest.mark.asyncio
async def test_end_call_emits_farewell_and_end_frames_and_calls_provider():
    call = _DummyCall("CA_END_123")
    provider = SimulatedTelephonyAdapter()
    emitted: list[Frame] = []

    async def _push(frame: Frame) -> None:
        emitted.append(frame)

    res = await execute_end_call(
        reason="user_requested",
        farewell_message="Thank you for calling VoxDesk. Goodbye!",
        call=call,
        telephony_provider=provider,
        frame_pusher=_push,
    )

    assert res["ok"] is True
    assert call.end_reason == "user_requested"
    assert isinstance(emitted[0], TTSSpeakFrame)
    assert isinstance(emitted[1], EndTaskFrame)
    assert isinstance(emitted[2], EndFrame)
    assert res["provider_result"]["status"] == "completed"


@pytest.mark.asyncio
async def test_send_dtmf_emits_keypad_frames_and_rejects_invalid_digits():
    call = _DummyCall("CA_DTMF_456")
    provider = SimulatedTelephonyAdapter()
    emitted: list[Frame] = []

    async def _push(frame: Frame) -> None:
        emitted.append(frame)

    res = await execute_send_dtmf(
        digits="1w2*#",
        call=call,
        telephony_provider=provider,
        frame_pusher=_push,
        honor_pauses=False,
    )

    assert res["ok"] is True
    assert res["digits"] == "1w2*#"
    assert res["emitted_buttons"] == ["1", "2", "*", "#"]
    assert len(emitted) == 4
    assert all(isinstance(f, OutputDTMFFrame) for f in emitted)
    assert res["provider_result"]["status"] == "sent"

    with pytest.raises(ValueError):
        validate_dtmf_digits("123ABC!")


@pytest.mark.asyncio
async def test_send_dtmf_urgent_and_dual_tone_pcm_and_tool_ceiling():
    from pipecat.frames.frames import OutputAudioRawFrame, OutputDTMFUrgentFrame

    from app.agent.tools.builtin_calls import (
        MAX_DTMF_DIGITS,
        enforce_tool_call_ceiling,
        synthesize_dtmf_pcm,
    )

    emitted: list[Frame] = []

    async def _push(frame: Frame) -> None:
        emitted.append(frame)

    res = await execute_send_dtmf(
        digits="5#",
        frame_pusher=_push,
        urgent=True,
        synthesize_audio=True,
    )
    assert res["ok"] is True
    assert res["pcm_bytes_emitted"] > 0
    assert any(isinstance(f, OutputDTMFUrgentFrame) for f in emitted)
    assert any(isinstance(f, OutputAudioRawFrame) for f in emitted)
    assert len(synthesize_dtmf_pcm("1", duration_ms=100, sample_rate=8000)) == 1600

    with pytest.raises(ValueError, match="exceeds maximum"):
        validate_dtmf_digits("1" * (MAX_DTMF_DIGITS + 1))

    enforce_tool_call_ceiling(0, limit=8)
    with pytest.raises(RuntimeError, match="Tool call ceiling exceeded"):
        enforce_tool_call_ceiling(8, limit=8)

