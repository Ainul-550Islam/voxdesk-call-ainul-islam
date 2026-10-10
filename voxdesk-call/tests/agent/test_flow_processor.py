# File: tests/agent/test_flow_processor.py — Unit tests for FlowProcessor prompt/tool swapping via LLMMessagesUpdateFrame and LLMSetToolsFrame in a fake Pipecat pipeline (Part 5 / Gate G6)
"""Tests for `app.agent.flow_processor`."""

from __future__ import annotations

import pytest
from pipecat.frames.frames import (
    LLMMessagesUpdateFrame,
    LLMSetToolsFrame,
    LLMUpdateSettingsFrame,
    TTSTextFrame,
    TranscriptionFrame,
)

from app.agent.flow_processor import FlowProcessor
from app.builder.flow_runner import FlowRunner


class _FakeLLMContext:
    def __init__(self, initial_messages: list[dict] | None = None) -> None:
        self.messages: list[dict] = list(initial_messages or [])
        self.tools: list[dict] = []

    def get_messages(self) -> list[dict]:
        return list(self.messages)

    def set_messages(self, messages: list[dict]) -> None:
        self.messages = list(messages)

    def set_tools(self, tools: list[dict]) -> None:
        self.tools = list(tools)


class _FakeCall:
    def __init__(self) -> None:
        self.id = "call_fake_001"
        self.call_sid = "CA_FAKE_001"
        self.from_number = "+14155550101"
        self.to_number = "+14155550199"
        self.transfer_context: dict = {}


def _build_two_stage_flow() -> dict:
    return {
        "version": 1,
        "nodes": [
            {
                "id": "n_start",
                "type": "start",
                "label": "Start",
                "params": {"greeting": "Welcome!"},
            },
            {
                "id": "n_triage",
                "type": "conversation",
                "label": "Triage",
                "params": {
                    "prompt": "STAGE 1 PROMPT: Ask whether caller wants booking or billing.",
                    "tools": ["check_calendar"],
                },
            },
            {
                "id": "n_booking",
                "type": "conversation",
                "label": "Booking",
                "params": {
                    "prompt": "STAGE 2 PROMPT: Book the appointment now for {{caller_number}}.",
                    "tools": ["book_appointment", "send_sms_confirmation"],
                },
                "model_override": {
                    "provider": "openai",
                    "model": "gpt-4o-mini",
                    "temperature": 0.1,
                },
            },
            {
                "id": "n_end",
                "type": "end",
                "label": "Done",
                "params": {
                    "reason": "completed",
                    "speak_text": "Your booking is all set. Goodbye!",
                },
            },
        ],
        "edges": [
            {
                "id": "e_start_triage",
                "source": "n_start",
                "target": "n_triage",
                "condition": {"kind": "always"},
            },
            {
                "id": "e_triage_booking",
                "source": "n_triage",
                "target": "n_booking",
                "condition": {
                    "kind": "prompt",
                    "prompt": "Caller wants to book an appointment",
                },
            },
            {
                "id": "e_booking_end",
                "source": "n_booking",
                "target": "n_end",
                "condition": {
                    "kind": "prompt",
                    "prompt": "Caller says thank you or done",
                },
            },
        ],
    }


@pytest.mark.asyncio
async def test_flow_processor_swaps_prompt_and_tools_through_fake_pipeline() -> None:
    tools_catalog = [
        {"type": "function", "function": {"name": "check_calendar"}},
        {"type": "function", "function": {"name": "book_appointment"}},
        {"type": "function", "function": {"name": "send_sms_confirmation"}},
    ]

    async def fake_judge(prompt: str, utterance: str, _vars: dict) -> bool:
        if "book an appointment" in prompt.lower():
            return "book" in utterance.lower()
        if "thank you or done" in prompt.lower():
            return "thank" in utterance.lower()
        return False

    call = _FakeCall()
    ctx = _FakeLLMContext(
        initial_messages=[
            {"role": "system", "content": "OLD SYSTEM PROMPT"},
            {"role": "user", "content": "Hi there"},
        ]
    )

    runner = FlowRunner(
        _build_two_stage_flow(),
        base_system_prompt="BASE SYSTEM:",
        available_tools=tools_catalog,
        initial_variables={"caller_number": call.from_number},
        judge=fake_judge,
    )

    emitted_frames: list = []
    forwarded_frames: list = []

    async def capture_emit(frame: object) -> None:
        emitted_frames.append(frame)

    processor = FlowProcessor(
        runner,
        call=call,
        context=ctx,
        frame_pusher=capture_emit,
    )

    async def capture_push(frame: object, direction: object = None) -> None:
        forwarded_frames.append(frame)

    processor.push_frame = capture_push  # type: ignore[method-assign]

    # 1. Initialize -> transitions from `n_start` to `n_triage`
    await processor.initialize()
    assert runner.current_node_id == "n_triage"

    msg_frames_1 = [f for f in emitted_frames if isinstance(f, LLMMessagesUpdateFrame)]
    tool_frames_1 = [f for f in emitted_frames if isinstance(f, LLMSetToolsFrame)]
    assert len(msg_frames_1) == 1
    assert len(tool_frames_1) == 1
    assert "STAGE 1 PROMPT" in msg_frames_1[0].messages[0]["content"]
    # Existing non-system user message is preserved
    assert msg_frames_1[0].messages[1] == {"role": "user", "content": "Hi there"}
    assert [t["function"]["name"] for t in tool_frames_1[0].tools] == ["check_calendar"]

    emitted_frames.clear()

    # 2. User turn transitions `n_triage` -> `n_booking`
    turn1 = TranscriptionFrame(
        text="I would like to book an appointment for tomorrow",
        user_id="caller",
        timestamp="2026-10-08T12:01:00Z",
    )
    await processor.process_frame(turn1)

    assert runner.current_node_id == "n_booking"
    msg_frames_2 = [f for f in emitted_frames if isinstance(f, LLMMessagesUpdateFrame)]
    tool_frames_2 = [f for f in emitted_frames if isinstance(f, LLMSetToolsFrame)]
    settings_frames_2 = [f for f in emitted_frames if isinstance(f, LLMUpdateSettingsFrame)]

    assert len(msg_frames_2) == 1
    assert "STAGE 2 PROMPT: Book the appointment now for +14155550101." in (
        msg_frames_2[0].messages[0]["content"]
    )
    assert len(tool_frames_2) == 1
    assert [t["function"]["name"] for t in tool_frames_2[0].tools] == [
        "book_appointment",
        "send_sms_confirmation",
    ]
    assert len(settings_frames_2) == 1
    assert settings_frames_2[0].settings["model"] == "gpt-4o-mini"
    assert turn1 in forwarded_frames

    emitted_frames.clear()

    # 3. Second user turn transitions `n_booking` -> `n_end` and emits TTSTextFrame
    turn2 = TranscriptionFrame(
        text="Thank you so much, that is all!",
        user_id="caller",
        timestamp="2026-10-08T12:02:00Z",
    )
    await processor.process_frame(turn2)

    assert runner.current_node_id == "n_end"
    assert runner.ended is True
    tts_frames = [f for f in emitted_frames if isinstance(f, TTSTextFrame)]
    assert len(tts_frames) == 1
    assert tts_frames[0].text == "Your booking is all set. Goodbye!"

    # Call timeline has all 3 node transitions recorded
    assert len(call.transfer_context["flow_transitions"]) == 3
    assert [t["to_node_id"] for t in call.transfer_context["flow_transitions"]] == [
        "n_triage",
        "n_booking",
        "n_end",
    ]
