"""End-to-end frame contracts for the PART 12 live voice controls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pipecat.audio.dtmf.types import KeypadEntry
from pipecat.frames.frames import (
    EndFrame,
    EndTaskFrame,
    InputDTMFFrame,
    InputTransportMessageFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    OutputTransportMessageFrame,
    TextFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transcriptions.language import Language

from app.agent.adaptive_pace import AdaptivePaceProcessor
from app.agent.expressive import ExpressiveTextProcessor
from app.agent.language import build_language_policy
from app.agent.live_voice_controls import (
    CallAutomationProcessor,
    LanguagePolicyGate,
    StructuredOutputProcessor,
    UserDtmfCollectionProcessor,
)
from app.agent.pipeline import (
    build_language_runtime,
    build_live_voice_controls,
    filter_enabled_tools,
    requires_runtime_stt,
    s2s_voice_behavior_fallback_reason,
)
from app.agent.providers.llm_providers import StructuredOutputError
from app.agent.s2s.s2s_tools_bridge import S2SToolsBridge
from app.runtime.agent_config_resolver import RuntimeConfig


async def _capture(processor, frame):
    captured = []
    original = processor.push_frame

    async def push(pushed_frame, direction=FrameDirection.DOWNSTREAM):
        captured.append((pushed_frame, direction))

    processor.push_frame = push  # type: ignore[method-assign]
    try:
        await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
    finally:
        processor.push_frame = original  # type: ignore[method-assign]
    return captured


def _schema(name: str, say_type: str = "string") -> dict:
    return {
        "type": "object",
        "required": ["say"],
        "properties": {"say": {"type": say_type}},
    }


def test_live_tool_allowlist_is_applied_without_granting_write_tools() -> None:
    from app.agent.functions import TOOL_SCHEMAS

    by_name = {s["function"]["name"]: s for s in TOOL_SCHEMAS}
    check_only = filter_enabled_tools([by_name["check_availability"], by_name["book_appointment"]], ["check_availability"])
    assert [s["function"]["name"] for s in check_only] == ["check_availability"]

    booking = filter_enabled_tools([by_name["check_availability"], by_name["book_appointment"]], ["book_appointment"])
    assert {s["function"]["name"] for s in booking} == {"check_availability", "book_appointment"}

    ivr = by_name["navigate_ivr"]
    assert filter_enabled_tools([ivr], None) == [ivr]
    assert filter_enabled_tools([ivr], ["navigate_ivr"], ivr_navigation_enabled=False) == []
    # A missing tools allowlist must not override the explicit IVR feature switch.
    assert filter_enabled_tools([ivr], None, ivr_navigation_enabled=False) == []

    custom = {"type": "function", "function": {"name": "my_lookup"}}
    assert filter_enabled_tools([custom], [], custom_names={"my_lookup"}) == [custom]


def test_p12_controls_are_built_into_their_live_pipeline_positions() -> None:
    from app.agent.audio.denoise import BackgroundVoiceGate, NullSpeakerAdapter

    cfg = RuntimeConfig(
        tenant_id=uuid4(),
        denoise_mode="noise_and_speech",
        denoise_gate=BackgroundVoiceGate(adapter=NullSpeakerAdapter()),
        dynamic_pace=True,
        colloquial=True,
        expressive=True,
        expressive_auto=True,
        expressive_emotions=["neutral", "warm", "calm"],
        begin_message_delay_ms=250,
        normalize_for_speech=False,
        read_numbers_slowly=True,
        format_transcript=True,
        structured_output_schema=_schema("response"),
        call_tooling={
            "user_dtmf_enabled": True,
            "user_dtmf_mode": "end_key",
            "user_dtmf_end_key": "#",
            "call_screening_enabled": True,
        },
    )
    tenant = SimpleNamespace(humanize=False)
    call = SimpleNamespace(call_sid="CA-test")
    turn_config = SimpleNamespace(
        vad_params=SimpleNamespace(stop_secs=0.5),
        vad_analyzer=SimpleNamespace(params=SimpleNamespace(stop_secs=0.5)),
        turn_analyzer=None,
    )
    policy = build_language_policy(
        languages=["en", "es", "multi"], language="en", reply_policy="follow_caller"
    )

    groups = build_live_voice_controls(
        cfg,
        tenant=tenant,
        call=call,
        call_sid="CA-test",
        telephony_provider=None,
        tts=SimpleNamespace(),
        turn_config=turn_config,
        language_policy=policy,
    )

    assert len(groups["input"]) == 2
    assert type(groups["input"][0]).__name__ == "BackgroundVoiceGateProcessor"
    assert isinstance(groups["input"][1], UserDtmfCollectionProcessor)
    assert isinstance(groups["transcript"][0], CallAutomationProcessor)
    assert isinstance(groups["transcript"][1], LanguagePolicyGate)
    assert groups["transcript"][0].call_sid == "CA-test"
    assert groups["transcript"][0].telephony_provider is None
    assert len(groups["after_hearing"]) == 1
    assert isinstance(groups["after_hearing"][0], AdaptivePaceProcessor)
    assert [type(p).__name__ for p in groups["output"]] == [
        "StructuredOutputProcessor",
        "ColloquialRewriter",
        "TextNormalizer",
        "ExpressiveTextProcessor",
        "FirstMessageDelay",
        "FillerInjector",
    ]
    assert groups["output"][0].schema is cfg.structured_output_schema
    assert groups["output"][2].options.normalize_for_speech is False
    assert groups["output"][2].options.read_numbers_slowly is True


def test_s2s_routes_calls_to_cascaded_when_live_text_controls_are_enabled() -> None:
    cfg = RuntimeConfig(tenant_id=uuid4(), structured_output_schema=_schema("response"))
    assert s2s_voice_behavior_fallback_reason(cfg) == "structured output"
    cfg.structured_output_schema = None
    cfg.call_tooling = {"user_dtmf_enabled": True}
    assert s2s_voice_behavior_fallback_reason(cfg) == "caller DTMF collection"

    cfg.call_tooling = {}
    cfg.colloquial = True
    assert s2s_voice_behavior_fallback_reason(cfg) == "colloquial rewriting"

    cfg.colloquial = False
    cfg.allowed_languages = ["en"]
    cfg.raw_config = {"voice_behavior": {"languages": ["en"]}}
    assert s2s_voice_behavior_fallback_reason(cfg) == "language allowlist policy"
    cfg.raw_config = {}
    assert s2s_voice_behavior_fallback_reason(cfg) == ""

    cfg.raw_config = {
        "mode": "flow",
        "flow": {"nodes": [{"id": "start", "type": "start"}], "edges": []},
    }
    assert s2s_voice_behavior_fallback_reason(cfg) == "conversation flow"


def test_language_runtime_instruction_matches_the_runtime_gate() -> None:
    cfg = RuntimeConfig(
        tenant_id=uuid4(),
        language="en",
        allowed_languages=["en", "es", "multi"],
        language_reply_policy="follow_caller",
        call_tooling={"user_dtmf_enabled": True, "user_dtmf_prompt": "Enter your reference number."},
    )
    policy, instruction = build_language_runtime(cfg)
    assert policy.allowlist == ("en", "es")
    assert policy.detect_multilingual is True
    assert instruction.startswith("\n\nOnly speak these languages:")
    assert "switching only among the allowed languages" in instruction
    assert "Enter your reference number" in instruction


def test_runtime_stt_selection_covers_every_nonlegacy_override(monkeypatch) -> None:
    monkeypatch.setattr("app.agent.pipeline.settings.deepgram_model", "nova-3")
    tenant = SimpleNamespace(language="en")
    cfg = RuntimeConfig(
        tenant_id=uuid4(),
        language="en",
        stt_language="en-US",
        stt_model="nova-3",
    )
    assert requires_runtime_stt(cfg, tenant) is False

    cfg.stt_language = "multi"
    assert requires_runtime_stt(cfg, tenant) is True
    cfg.stt_language = "en-US"
    cfg.stt_endpointing_mode = "accuracy"
    assert requires_runtime_stt(cfg, tenant) is True
    cfg.stt_endpointing_mode = "default"
    cfg.stt_medical_vocabulary = True
    assert requires_runtime_stt(cfg, tenant) is True
    cfg.stt_medical_vocabulary = False
    cfg.stt_medical_terms = ["metformin"]
    assert requires_runtime_stt(cfg, tenant) is True
    cfg.stt_medical_terms = []
    cfg.stt_fallback_providers = ["soniox"]
    assert requires_runtime_stt(cfg, tenant) is True
    cfg.stt_fallback_providers = []
    cfg.boosted_keywords = [("Ainul", 3.0)]
    assert requires_runtime_stt(cfg, tenant) is True
    cfg.boosted_keywords = []
    cfg.stt_model = "nova-2"
    assert requires_runtime_stt(cfg, tenant) is True


@pytest.mark.asyncio
async def test_structured_output_call_bypasses_s2s_before_provider_setup(monkeypatch) -> None:
    import app.agent.s2s.s2s_pipeline as s2s_pipeline
    from app.agent.pipeline import _build_s2s_runtime

    def must_not_build(_cfg):
        raise AssertionError("structured-output calls must stay on cascaded STT/LLM/TTS")

    monkeypatch.setattr(s2s_pipeline, "s2s_runtime_requested", must_not_build)
    cfg = RuntimeConfig(
        tenant_id=uuid4(),
        s2s_enabled=True,
        structured_output_schema=_schema("response"),
    )
    runtime, prepared = await _build_s2s_runtime(
        session=None,
        tenant=SimpleNamespace(id=uuid4()),
        call=SimpleNamespace(),
        runtime_cfg=cfg,
        handlers=None,
    )
    assert (runtime, prepared) == (None, None)
    assert s2s_voice_behavior_fallback_reason(cfg) == "structured output"


@pytest.mark.asyncio
async def test_caller_dtmf_becomes_a_masked_transcript_only() -> None:
    processor = UserDtmfCollectionProcessor(
        {"user_dtmf_enabled": True, "user_dtmf_mode": "end_key", "user_dtmf_end_key": "#", "user_dtmf_keep_last": 2}
    )
    captured = []
    original = processor.push_frame

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    processor.push_frame = push  # type: ignore[method-assign]
    try:
        for digit in "5550142#":
            await processor.process_frame(
                InputDTMFFrame(button=KeypadEntry(digit)), FrameDirection.DOWNSTREAM
            )
    finally:
        processor.push_frame = original  # type: ignore[method-assign]

    assert len(captured) == 1
    assert isinstance(captured[0], TranscriptionFrame)
    assert captured[0].text == "Caller entered keypad input *****42 (7 digits)."
    assert processor.last_event["digits_masked"] == "*****42"
    assert "5550142" not in str(processor.last_event)


@pytest.mark.asyncio
async def test_browser_transport_dtmf_message_is_masked_before_context() -> None:
    processor = UserDtmfCollectionProcessor(
        {"user_dtmf_enabled": True, "user_dtmf_mode": "end_key", "user_dtmf_end_key": "#"}
    )
    serializer = ProtobufFrameSerializer()
    wire = await serializer.serialize(
        OutputTransportMessageFrame(message={"type": "dtmf", "digits": "5550142#"})
    )
    assert isinstance(wire, bytes)
    message = await serializer.deserialize(wire)
    assert isinstance(message, InputTransportMessageFrame)
    captured = await _capture(processor, message)

    assert len(captured) == 1
    assert isinstance(captured[0][0], TranscriptionFrame)
    assert captured[0][0].text == "Caller entered keypad input *****42 (7 digits)."
    assert "5550142" not in str(processor.last_event)


@pytest.mark.asyncio
async def test_language_gate_drops_unsupported_transcripts_and_speaks_fallback() -> None:
    policy = build_language_policy(languages=["en", "es", "multi"], language="en")
    processor = LanguagePolicyGate(policy)
    blocked = TranscriptionFrame(
        text="bonjour", user_id="caller", timestamp="now", language=Language.FR
    )
    captured = await _capture(processor, blocked)
    assert len(captured) == 1
    assert isinstance(captured[0][0], TTSSpeakFrame)
    assert captured[0][0].text == policy.fallback_phrase
    assert processor.blocked_count == 1


@pytest.mark.asyncio
async def test_structured_output_buffers_json_and_only_emits_speech_text() -> None:
    processor = StructuredOutputProcessor(_schema("response"))
    captured = []
    original = processor.push_frame

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    processor.push_frame = push  # type: ignore[method-assign]
    try:
        for frame in (
            LLMFullResponseStartFrame(),
            TextFrame(text='{"say":"Your appointment is at 3."}'),
            LLMFullResponseEndFrame(),
        ):
            await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
    finally:
        processor.push_frame = original  # type: ignore[method-assign]

    spoken = [frame.text for frame in captured if isinstance(frame, TextFrame)]
    assert spoken == ["Your appointment is at 3."]
    assert processor.validated_count == 1
    assert all("\"say\"" not in getattr(frame, "text", "") for frame in captured)


@pytest.mark.asyncio
async def test_structured_output_uses_safe_speech_fallback_on_invalid_json() -> None:
    processor = StructuredOutputProcessor(_schema("response"))
    captured = []
    processor.push_frame = lambda frame, direction=FrameDirection.DOWNSTREAM: _capture_push(captured, frame)

    await processor.process_frame(LLMFullResponseStartFrame(), FrameDirection.DOWNSTREAM)
    await processor.process_frame(TextFrame(text="not JSON"), FrameDirection.DOWNSTREAM)
    await processor.process_frame(LLMFullResponseEndFrame(), FrameDirection.DOWNSTREAM)

    assert [frame.text for frame in captured if isinstance(frame, TextFrame)] == [processor.FALLBACK_TEXT]
    assert "not JSON" in processor.last_error


async def _capture_push(captured, frame):
    captured.append(frame)


@pytest.mark.asyncio
async def test_expressive_processor_strips_tags_and_emits_provider_settings() -> None:
    processor = ExpressiveTextProcessor(
        provider="elevenlabs", model="eleven_flash_v2_5", enabled=True
    )
    frame = TextFrame(text="[sigh] I'm sorry about that.")
    captured = []
    original = processor.push_frame

    async def push(pushed, direction=FrameDirection.DOWNSTREAM):
        captured.append(pushed)

    processor.push_frame = push  # type: ignore[method-assign]
    try:
        await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
    finally:
        processor.push_frame = original  # type: ignore[method-assign]

    assert frame.text == "I'm sorry about that."
    assert any(type(item).__name__ == "TTSUpdateSettingsFrame" for item in captured)


@pytest.mark.asyncio
async def test_adaptive_pace_updates_elevenlabs_and_next_turn_endpointing() -> None:
    class _TTS:
        def __init__(self):
            self.speeds = []

        async def update_speed(self, speed):
            self.speeds.append(speed)
            return speed

    class _Clock:
        value = 0.0

        def __call__(self):
            return self.value

    clock = _Clock()
    tts = _TTS()
    turn_config = SimpleNamespace(
        vad_params=SimpleNamespace(stop_secs=0.5),
        vad_analyzer=SimpleNamespace(params=SimpleNamespace(stop_secs=0.5)),
        turn_analyzer=None,
    )
    processor = AdaptivePaceProcessor(tts=tts, turn_config=turn_config, clock=clock)
    pushed = []
    original = processor.push_frame

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    processor.push_frame = push  # type: ignore[method-assign]
    try:
        await processor.process_frame(UserStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        clock.value = 15.0
        await processor.process_frame(UserStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        await processor.process_frame(
            TranscriptionFrame(
                text="one two three four five six seven eight nine ten",
                user_id="caller",
                timestamp="now",
            ),
            FrameDirection.DOWNSTREAM,
        )
    finally:
        processor.push_frame = original  # type: ignore[method-assign]

    assert tts.speeds == [0.8]
    assert turn_config.vad_analyzer.params.stop_secs > 0.5
    assert len(processor.decisions) == 1
    assert len(pushed) == 3


@pytest.mark.asyncio
async def test_voicemail_control_speaks_safe_message_and_hangs_up() -> None:
    class _Provider:
        def __init__(self):
            self.hung_up = []

        async def hangup_call(self, call_sid):
            self.hung_up.append(call_sid)

    def clock() -> float:
        return 0.0

    call = SimpleNamespace(call_sid="CA-vm", end_reason="")
    provider = _Provider()
    processor = CallAutomationProcessor(
        config={
            "voicemail_detection_enabled": True,
            "voicemail_action": "static_message",
            "voicemail_message": "Please call us back when you have a moment.",
            "voicemail_window_s": 12,
        },
        call=call,
        call_sid="CA-vm",
        telephony_provider=provider,
        clock=clock,
    )
    captured = []
    original = processor.push_frame

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    processor.push_frame = push  # type: ignore[method-assign]
    try:
        await processor.process_frame(
            TranscriptionFrame(
                text="Please leave your message after the tone.",
                user_id="caller",
                timestamp="now",
            ),
            FrameDirection.DOWNSTREAM,
        )
    finally:
        processor.push_frame = original  # type: ignore[method-assign]

    assert any(isinstance(frame, TTSSpeakFrame) for frame in captured)
    assert any(isinstance(frame, EndTaskFrame) for frame in captured)
    assert any(isinstance(frame, EndFrame) for frame in captured)
    assert provider.hung_up == ["CA-vm"]
    assert call.end_reason == "voicemail_left"


@pytest.mark.asyncio
async def test_browser_call_automation_uses_web_sid_and_ends_without_carrier() -> None:
    call = SimpleNamespace(call_sid="web_0123456789abcdef01234567", end_reason="")
    processor = CallAutomationProcessor(
        config={
            "voicemail_detection_enabled": True,
            "voicemail_action": "static_message",
            "voicemail_message": "Please call us back.",
            "voicemail_window_s": 12,
        },
        call=call,
        call_sid=call.call_sid,
        telephony_provider=None,
        clock=lambda: 0.0,
    )
    captured = []
    original = processor.push_frame

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    processor.push_frame = push  # type: ignore[method-assign]
    try:
        await processor.process_frame(
            TranscriptionFrame(
                text="Please leave your message after the tone.",
                user_id="caller",
                timestamp="now",
            ),
            FrameDirection.DOWNSTREAM,
        )
    finally:
        processor.push_frame = original  # type: ignore[method-assign]

    assert processor.call_sid.startswith("web_")
    assert processor.telephony_provider is None
    assert [type(frame) for frame in captured[:3]] == [TTSSpeakFrame, EndTaskFrame, EndFrame]
    assert call.end_reason == "voicemail_left"


@pytest.mark.asyncio
async def test_browser_call_screening_speaks_configured_message_without_carrier() -> None:
    async def hold(_seconds):
        await asyncio.Event().wait()

    call = SimpleNamespace(call_sid="web_screening", end_reason="")
    processor = CallAutomationProcessor(
        config={
            "call_screening_enabled": True,
            "call_screening_message": "This is Acme calling about your delivery.",
            "call_screening_wait_s": 15,
        },
        call=call,
        call_sid=call.call_sid,
        telephony_provider=None,
        clock=lambda: 0.0,
        sleep=hold,
    )
    captured = []
    original = processor.push_frame

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    processor.push_frame = push  # type: ignore[method-assign]
    try:
        await processor.process_frame(
            TranscriptionFrame(
                text="Please say your name and why you are calling.",
                user_id="caller",
                timestamp="now",
            ),
            FrameDirection.DOWNSTREAM,
        )
    finally:
        processor.push_frame = original  # type: ignore[method-assign]
        await processor.cleanup()

    assert [frame.text for frame in captured if isinstance(frame, TTSSpeakFrame)] == [
        "This is Acme calling about your delivery."
    ]
    assert processor._screening_replies == 1
    assert call.end_reason == ""


@pytest.mark.asyncio
async def test_live_pickup_cancels_screening_timeout() -> None:
    async def hold(_seconds):
        await asyncio.Event().wait()

    call = SimpleNamespace(call_sid="web_live_pickup", end_reason="")
    processor = CallAutomationProcessor(
        config={"call_screening_enabled": True, "call_screening_wait_s": 30},
        call=call,
        call_sid=call.call_sid,
        telephony_provider=None,
        clock=lambda: 0.0,
        sleep=hold,
    )
    captured = []
    original = processor.push_frame

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    processor.push_frame = push  # type: ignore[method-assign]
    try:
        await processor.process_frame(
            TranscriptionFrame(
                text="Please say your name and why you are calling.",
                user_id="caller",
                timestamp="now",
            ),
            FrameDirection.DOWNSTREAM,
        )
        timer = processor._screening_timer_task
        assert timer is not None
        await processor.process_frame(
            TranscriptionFrame(text="Hello, who is this?", user_id="caller", timestamp="later"),
            FrameDirection.DOWNSTREAM,
        )
        await asyncio.sleep(0)
        assert processor._screening_resolved is True
        assert processor._screening_timer_task is None
        assert call.end_reason == ""
        assert not any(isinstance(frame, EndFrame) for frame in captured)
    finally:
        processor.push_frame = original  # type: ignore[method-assign]
        await processor.cleanup()


@pytest.mark.asyncio
async def test_screening_wait_timeout_hangs_up_cleanly_on_browser_transport() -> None:
    waited = []

    async def instant_sleep(seconds):
        waited.append(seconds)

    call = SimpleNamespace(call_sid="web_timeout", end_reason="")
    processor = CallAutomationProcessor(
        config={
            "call_screening_enabled": True,
            "call_screening_message": "This is Acme calling.",
            "call_screening_wait_s": 3,
        },
        call=call,
        call_sid=call.call_sid,
        telephony_provider=None,
        clock=lambda: 0.0,
        sleep=instant_sleep,
    )
    captured = []
    original = processor.push_frame

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        captured.append(frame)

    processor.push_frame = push  # type: ignore[method-assign]
    try:
        await processor.process_frame(
            TranscriptionFrame(
                text="Please say your name and why you are calling.",
                user_id="caller",
                timestamp="now",
            ),
            FrameDirection.DOWNSTREAM,
        )
        timer = processor._screening_timer_task
        assert timer is not None
        await timer
    finally:
        processor.push_frame = original  # type: ignore[method-assign]
        await processor.cleanup()

    assert waited == [3.0]
    assert call.end_reason == "screening_timeout"
    assert processor.handled_action == {"action": "screening_timeout", "wait_s": 3.0}
    assert any(isinstance(frame, EndTaskFrame) for frame in captured)
    assert any(isinstance(frame, EndFrame) for frame in captured)


@pytest.mark.asyncio
async def test_s2s_bridge_rejects_calls_not_in_the_advertised_allowlist() -> None:
    class _Handlers:
        def __init__(self):
            self.called = []

        async def dispatch(self, name, arguments):
            self.called.append(name)
            return {"ok": True}

    schema = {
        "type": "function",
        "function": {"name": "book_appointment", "parameters": {"type": "object", "properties": {}}},
    }
    handlers = _Handlers()
    bridge = S2SToolsBridge(handlers=handlers, tools=[schema])

    assert {tool["name"] for tool in bridge.tools_for("openai_realtime")} == {"book_appointment"}
    refused = await bridge.execute("take_message", {})
    assert refused["error"] == "tool_not_enabled"
    assert handlers.called == []


def test_boolean_is_not_accepted_as_a_json_number() -> None:
    from app.agent.providers.llm_providers import parse_structured_response

    schema = {"type": "object", "required": ["n"], "properties": {"n": {"type": "number"}}}
    with pytest.raises(StructuredOutputError, match="n should be number"):
        parse_structured_response('{"n": true}', schema)
