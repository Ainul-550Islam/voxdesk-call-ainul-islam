"""Unit tests for Voicemail Detection & Action Handler (`hangup`, `leave_message`, `ignore`) (Sub-Phase 2D)."""

from __future__ import annotations

import pytest
from pipecat.frames.frames import EndFrame, EndTaskFrame, Frame, TTSSpeakFrame

from app.agent.tools.voicemail import VoicemailFlowHandler
from app.telephony.providers.base import SimulatedTelephonyAdapter


class _DummyCall:
    def __init__(self, call_sid: str = "CA_VM_001") -> None:
        self.call_sid = call_sid
        self.end_reason: str | None = None


@pytest.mark.asyncio
async def test_voicemail_hangup_action():
    call = _DummyCall("CA_VM_HANGUP")
    emitted: list[Frame] = []

    async def _push(frame: Frame) -> None:
        emitted.append(frame)

    handler = VoicemailFlowHandler(
        action="hangup",
        call=call,
        telephony_provider=SimulatedTelephonyAdapter(),
        frame_pusher=_push,
    )
    res = await handler.inspect_transcript(
        "Hi, you have reached the voicemail of Dr. Miller. Please leave a message after the beep."
    )
    assert res is not None
    assert res["action"] == "hangup"
    assert call.end_reason == "voicemail_hangup"
    assert any(isinstance(f, EndTaskFrame) for f in emitted)
    assert any(isinstance(f, EndFrame) for f in emitted)
    assert not any(isinstance(f, TTSSpeakFrame) for f in emitted)


@pytest.mark.asyncio
async def test_voicemail_leave_message_action():
    call = _DummyCall("CA_VM_MSG")
    emitted: list[Frame] = []

    async def _push(frame: Frame) -> None:
        emitted.append(frame)

    handler = VoicemailFlowHandler(
        action="leave_message",
        voicemail_message="Hi, this is VoxDesk calling to confirm your dental cleaning tomorrow at 10 AM.",
        call=call,
        telephony_provider=SimulatedTelephonyAdapter(),
        frame_pusher=_push,
    )
    res = await handler.inspect_transcript(
        "The person you are trying to reach is not available. Please leave your message after the tone."
    )
    assert res is not None
    assert res["action"] == "leave_message"
    assert call.end_reason == "voicemail_left"
    assert isinstance(emitted[0], TTSSpeakFrame)
    assert "dental cleaning" in emitted[0].text
    assert isinstance(emitted[1], EndTaskFrame)
    assert isinstance(emitted[2], EndFrame)


@pytest.mark.asyncio
async def test_voicemail_ignore_action():
    call = _DummyCall("CA_VM_IGNORE")
    emitted: list[Frame] = []

    async def _push(frame: Frame) -> None:
        emitted.append(frame)

    handler = VoicemailFlowHandler(
        action="ignore",
        call=call,
        frame_pusher=_push,
    )
    res = await handler.inspect_transcript("Please leave a message after the beep.")
    assert res is not None
    assert res["action"] == "ignore"
    assert call.end_reason is None
    assert emitted == []
