"""The speech-to-speech runtime, wired into the live call path.

These tests cover the wiring itself -- not just the modules:

* the dispatcher in ``app.agent.pipeline`` takes the speech-to-speech path
  when the feature is on, and stays on the cascaded path when it is off;
* the provider really receives the caller's audio and its audio really
  comes back;
* the transcripts land in the turn table and in the conversation history;
* the session record and the usage ledger are written;
* a provider that cannot be reached fails over to the cascaded pipeline
  *before* the caller hears anything;
* a provider that dies mid-call hands the audio path over to the rebuilt
  cascaded chain.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, AsyncIterator

import pytest
from pipecat.frames.frames import (
    InputAudioRawFrame,
    OutputAudioRawFrame,
    StartFrame,
    TextFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)

from app.agent.s2s.s2s_pipeline import (
    build_s2s_runtime,
    finalize_s2s_runtime,
    s2s_runtime_requested,
)
from app.agent.s2s.s2s_session import S2SEvent, S2SEventType, S2SSession
from app.db.carrier_s2s_models import S2SSessionRecord
from app.db.models import Call, CallStatus, Speaker, Tenant, Turn
from tests.conftest import make_tenant


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------
class FakeProviderSession(S2SSession):
    """A provider session that never touches the network."""

    provider = "openai_realtime"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.audio: list[bytes] = []
        self.rates: list[int] = []
        self.started = False
        self.is_closed = False
        self.connect_calls = 0
        self.connect_error: BaseException | None = None
        self.tool_results: list[tuple[str, dict[str, Any]]] = []
        self.usage_payload = {"input_tokens": 42, "output_tokens": 17}
        self._output_bytes = 0

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.is_closed = True

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_error is not None:
            raise self.connect_error

    async def send_audio(self, pcm: bytes, *, sample_rate: int) -> None:
        self.audio.append(pcm)
        self.rates.append(sample_rate)

    async def interrupt(self) -> None:
        return None

    async def send_tool_result(
        self, call_id: str, name: str, result: dict[str, Any]
    ) -> None:
        self.tool_results.append((name, result))

    async def events(self) -> AsyncIterator[S2SEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    @property
    def usage(self) -> dict[str, int]:
        return dict(self.usage_payload)

    @property
    def input_bytes(self) -> int:
        return sum(len(chunk) for chunk in self.audio)

    @property
    def output_bytes(self) -> int:
        return self._output_bytes


async def _setup_processor(processor: FrameProcessor) -> None:
    """Give the processor the pipeline setup it needs to run."""
    from pipecat.clocks.system_clock import SystemClock
    from pipecat.utils.asyncio.task_manager import TaskManager, TaskManagerParams

    manager = TaskManager()
    manager.setup(TaskManagerParams(loop=asyncio.get_running_loop()))
    await processor.setup(
        FrameProcessorSetup(clock=SystemClock(), task_manager=manager)
    )


def _capture_pushes(processor) -> list:
    """Record every frame the processor pushes downstream."""
    pushed: list = []

    async def _push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    processor.push_frame = _push
    return pushed


class CaptureProcessor(FrameProcessor):
    """A downstream processor that records what reaches it."""

    def __init__(self) -> None:
        super().__init__()
        self.frames: list[Any] = []

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        self.frames.append(frame)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _runtime_config(*, s2s_enabled: bool = True, provider: str = "openai_realtime"):
    from app.runtime.agent_config_resolver import RuntimeConfig

    return RuntimeConfig(
        tenant_id=uuid.uuid4(),
        s2s_enabled=s2s_enabled,
        s2s_provider=provider if s2s_enabled else None,
        s2s_model="gpt-4o-realtime-preview" if s2s_enabled else None,
        system_prompt="You are the voice receptionist.",
    )


def _enable_s2s(monkeypatch, *, fallback_enabled: bool = True) -> None:
    """Turn the Batch 02 feature switches on for the test."""
    from app.carriers_v2 import settings as carrier_settings

    real_settings = carrier_settings.get_carrier_v2_settings()

    def _settings():
        return real_settings.model_copy(
            update={
                "enable_s2s": True,
                "s2s_fallback_enabled": fallback_enabled,
                "openai_realtime_model": "gpt-4o-realtime-preview",
            }
        )

    monkeypatch.setattr(carrier_settings, "get_carrier_v2_settings", _settings)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")


def _install_fake_provider(monkeypatch, factory) -> None:
    from app.agent.s2s import openai_realtime

    monkeypatch.setattr(openai_realtime, "OpenAIRealtimeSession", factory)


def _fake_cascaded_services(monkeypatch) -> None:
    """Fake the cascaded services, so the failover can be built offline."""
    from app.agent import stt as stt_module
    from app.agent import tts as tts_module
    from app.ai import runtime as ai_runtime

    class _FakeAggregator:
        def user(self):
            return self

        def assistant(self):
            return self

        def get_context_frame(self):
            return None

        async def process_frame(self, frame, direction):
            return None

    class _FakeLLM:
        def create_context_aggregator(self, context, **kwargs):
            return _FakeAggregator()

        def register_function(self, name, handler, **kwargs):
            return None

    class _FakeChoice:
        provider = "openai"
        model = "gpt-4o-mini"
        notes = ""

    class _FakePrepared:
        choice = _FakeChoice()
        system_prompt = "You are the voice receptionist."
        blocked_output = ()
        timeout_ms = 5000
        prompt_version = 1
        approval_state = "approved"
        budget_reserved = False

    async def _fake_voice_llm(*args, **kwargs):
        return _FakeLLM(), _FakePrepared()

    monkeypatch.setattr(stt_module, "build_stt_for_runtime", lambda cfg: object())
    monkeypatch.setattr(tts_module, "build_tts_for_runtime", lambda cfg: object())
    monkeypatch.setattr(ai_runtime, "voice_llm", _fake_voice_llm)


def _failover_count(reason: str) -> float:
    """The current value of the failover counter for one reason."""
    from prometheus_client import REGISTRY

    return REGISTRY.get_sample_value(
        "voxdesk_s2s_failover_total", {"reason": reason}
    ) or 0.0


async def _make_call(db, tenant: Tenant, *, call_sid: str = "CA_S2S_001") -> Call:
    call = Call(
        id=uuid.uuid4(),
        tenant_id=tenant.id,
        call_sid=call_sid,
        from_number="+14155550100",
        to_number="+14155550199",
        status=CallStatus.IN_PROGRESS,
    )
    db.add(call)
    await db.flush()
    return call


# ---------------------------------------------------------------------------
# The dispatcher
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_dispatcher_takes_the_speech_to_speech_path(monkeypatch, db) -> None:
    """A call with the feature on runs on the provider, not the cascade."""
    from app.agent import pipeline as agent_pipeline

    tenant = await make_tenant(db, name="S2S Dispatch Tenant")
    call = await _make_call(db, tenant)

    ran: list[str] = []

    async def _fake_s2s(runtime, **kwargs):
        ran.append("s2s")

    async def _fake_cascaded(*args, **kwargs):
        ran.append("cascaded")

    monkeypatch.setattr(agent_pipeline, "_run_s2s_agent", _fake_s2s)
    monkeypatch.setattr(agent_pipeline, "_run_cascaded_agent", _fake_cascaded)

    class _FakeRuntime:
        provider = "openai_realtime"
        model = "gpt-4o-realtime-preview"
        history: list = []

        class _Session:
            async def connect(self):
                return None

        session = _Session()

    async def _fake_build(**kwargs):
        return _FakeRuntime(), object()

    monkeypatch.setattr(agent_pipeline, "_build_s2s_runtime", _fake_build)

    await agent_pipeline._run_voice_agent(
        None,  # the websocket is unused when a transport is supplied
        "MZ1",
        "CA_S2S_001",
        db,
        tenant,
        call,
        runtime_config=_runtime_config(),
        transport=object(),
    )
    assert ran == ["s2s"]


@pytest.mark.asyncio
async def test_the_dispatcher_stays_cascaded_when_the_feature_is_off(
    monkeypatch, db
) -> None:
    from app.agent import pipeline as agent_pipeline

    tenant = await make_tenant(db, name="S2S Dispatch Off Tenant")
    call = await _make_call(db, tenant)

    ran: list[str] = []

    async def _fake_s2s(runtime, **kwargs):
        ran.append("s2s")

    async def _fake_cascaded(*args, **kwargs):
        ran.append("cascaded")

    monkeypatch.setattr(agent_pipeline, "_run_s2s_agent", _fake_s2s)
    monkeypatch.setattr(agent_pipeline, "_run_cascaded_agent", _fake_cascaded)

    async def _fake_build(**kwargs):
        return None, None

    monkeypatch.setattr(agent_pipeline, "_build_s2s_runtime", _fake_build)

    await agent_pipeline._run_voice_agent(
        None,
        "MZ1",
        "CA_S2S_002",
        db,
        tenant,
        call,
        runtime_config=_runtime_config(s2s_enabled=False),
        transport=object(),
    )
    assert ran == ["cascaded"]


# ---------------------------------------------------------------------------
# The runtime
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_runtime_is_built_and_records_the_session(monkeypatch, db) -> None:
    _enable_s2s(monkeypatch)
    created: list[FakeProviderSession] = []

    def _factory(**kwargs):
        session = FakeProviderSession(**kwargs)
        created.append(session)
        return session

    _install_fake_provider(monkeypatch, _factory)

    tenant = await make_tenant(db, name="S2S Runtime Tenant")
    call = await _make_call(db, tenant)

    runtime = await build_s2s_runtime(
        session=db,
        tenant=tenant,
        call=call,
        runtime_cfg=_runtime_config(),
    )
    assert runtime is not None
    assert runtime.provider == "openai_realtime"
    assert runtime.model == "gpt-4o-realtime-preview"
    assert len(created) == 1
    assert created[0].tools, "the provider must receive the tool schemas"

    # The session row exists before the first audio frame.
    await db.commit()
    from sqlalchemy import select

    rows = (
        await db.execute(
            select(S2SSessionRecord).where(S2SSessionRecord.tenant_id == tenant.id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].provider == "openai_realtime"
    assert rows[0].status == "active"


@pytest.mark.asyncio
async def test_the_provider_receives_the_audio_and_answers(monkeypatch, db) -> None:
    _enable_s2s(monkeypatch)
    created: list[FakeProviderSession] = []

    def _factory(**kwargs):
        session = FakeProviderSession(**kwargs)
        created.append(session)
        return session

    _install_fake_provider(monkeypatch, _factory)

    tenant = await make_tenant(db, name="S2S Audio Tenant")
    call = await _make_call(db, tenant)

    runtime = await build_s2s_runtime(
        session=db,
        tenant=tenant,
        call=call,
        runtime_cfg=_runtime_config(),
    )
    assert runtime is not None
    processor = runtime.processor
    pushed = _capture_pushes(processor)
    await _setup_processor(processor)
    await processor.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)
    assert created[0].started is True

    # The caller's audio goes to the provider.
    await processor.process_frame(
        InputAudioRawFrame(audio=b"\x01\x02" * 320, sample_rate=8000, num_channels=1),
        FrameDirection.DOWNSTREAM,
    )
    from app.agent.s2s.s2s_audio import OPENAI_REALTIME_SAMPLE_RATE

    assert created[0].audio, "the provider never received the caller audio"
    assert created[0].rates == [OPENAI_REALTIME_SAMPLE_RATE]

    # The provider's audio comes back as pipeline audio.
    created[0]._output_bytes = 640
    created[0].emit(
        S2SEvent(
            type=S2SEventType.AUDIO_OUT,
            provider="openai_realtime",
            audio=b"\x03\x04" * 320,
            sample_rate=16000,
        )
    )
    await asyncio_sleep()

    audio_frames = [f for f in pushed if isinstance(f, OutputAudioRawFrame)]
    assert audio_frames, "the provider audio never reached the pipeline"
    assert audio_frames[0].sample_rate == 8000
    started = [f for f in pushed if type(f).__name__ == "BotStartedSpeakingFrame"]
    assert started, "the pipeline was never told the assistant started speaking"

    await processor.stop()


@pytest.mark.asyncio
async def test_the_transcripts_land_in_the_turn_table(monkeypatch, db) -> None:
    _enable_s2s(monkeypatch)
    created: list[FakeProviderSession] = []

    def _factory(**kwargs):
        session = FakeProviderSession(**kwargs)
        created.append(session)
        return session

    _install_fake_provider(monkeypatch, _factory)

    tenant = await make_tenant(db, name="S2S Transcript Tenant")
    call = await _make_call(db, tenant)

    runtime = await build_s2s_runtime(
        session=db,
        tenant=tenant,
        call=call,
        runtime_cfg=_runtime_config(),
    )
    assert runtime is not None
    processor = runtime.processor
    pushed = _capture_pushes(processor)
    await _setup_processor(processor)
    await processor.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)

    created[0].emit(
        S2SEvent(
            type=S2SEventType.TRANSCRIPT_IN,
            provider="openai_realtime",
            text="I need an appointment",
        )
    )
    created[0].emit(
        S2SEvent(
            type=S2SEventType.TRANSCRIPT_OUT,
            provider="openai_realtime",
            text="Of course, when works for you?",
        )
    )
    await asyncio_sleep()

    # The pipeline saw both transcripts.
    assert any(isinstance(f, TranscriptionFrame) for f in pushed)
    assert any(isinstance(f, TextFrame) for f in pushed)

    # The conversation history carries both sides.
    assert runtime.history == [
        {"role": "user", "content": "I need an appointment"},
        {"role": "assistant", "content": "Of course, when works for you?"},
    ]

    # ... and the same text is persisted as turns.
    await db.commit()
    from sqlalchemy import select

    turns = (
        (await db.execute(select(Turn).where(Turn.call_id == call.id))).scalars().all()
    )
    assert len(turns) == 2
    speakers = {row.speaker for row in turns}
    assert speakers == {Speaker.USER, Speaker.ASSISTANT}
    texts = {row.text for row in turns}
    assert "I need an appointment" in texts

    await processor.stop()


@pytest.mark.asyncio
async def test_the_finalisation_writes_the_usage(monkeypatch, db) -> None:
    _enable_s2s(monkeypatch)
    created: list[FakeProviderSession] = []

    def _factory(**kwargs):
        session = FakeProviderSession(**kwargs)
        created.append(session)
        return session

    _install_fake_provider(monkeypatch, _factory)

    tenant = await make_tenant(db, name="S2S Finalise Tenant")
    call = await _make_call(db, tenant)

    runtime = await build_s2s_runtime(
        session=db,
        tenant=tenant,
        call=call,
        runtime_cfg=_runtime_config(),
    )
    assert runtime is not None
    await runtime.session.start()
    await runtime.session.send_audio(b"\x00" * 3200, sample_rate=16000)

    await finalize_s2s_runtime(
        runtime,
        session=db,
        tenant=tenant,
        call=call,
    )
    await db.commit()

    assert runtime.session.is_closed is True
    record = runtime.record
    assert record is not None
    assert record.status == "completed"
    assert record.input_tokens == 42
    assert record.output_tokens == 17
    from app.agent.s2s.s2s_audio import OPENAI_REALTIME_SAMPLE_RATE

    assert record.input_audio_s == pytest.approx(
        3200 / 2 / OPENAI_REALTIME_SAMPLE_RATE
    )
    assert record.ended_at is not None


# ---------------------------------------------------------------------------
# The failover
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_provider_that_cannot_be_reached_fails_over(monkeypatch, db) -> None:
    from app.agent import pipeline as agent_pipeline
    _enable_s2s(monkeypatch)
    created: list[FakeProviderSession] = []

    def _factory(**kwargs):
        session = FakeProviderSession(**kwargs)
        session.connect_error = ConnectionError("the provider is unreachable")
        created.append(session)
        return session

    _install_fake_provider(monkeypatch, _factory)

    _fake_cascaded_services(monkeypatch)

    tenant = await make_tenant(db, name="S2S Preflight Tenant")
    call = await _make_call(db, tenant)

    runtime = await build_s2s_runtime(
        session=db,
        tenant=tenant,
        call=call,
        runtime_cfg=_runtime_config(),
    )
    assert runtime is not None

    error = await agent_pipeline._s2s_preflight(runtime)
    assert isinstance(error, ConnectionError)

    before = _failover_count("connection_lost")
    fallback = await agent_pipeline._s2s_failover(
        error=error,
        session=db,
        tenant=tenant,
        call=call,
        runtime_cfg=_runtime_config(),
        history=runtime.history,
        provider=runtime.provider,
    )
    after = _failover_count("connection_lost")
    assert after == before + 1
    assert fallback is not None
    assert fallback.stt is not None and fallback.llm is not None


@pytest.mark.asyncio
async def test_a_disabled_fallback_ends_the_call(monkeypatch, db) -> None:
    from app.agent import pipeline as agent_pipeline

    _enable_s2s(monkeypatch, fallback_enabled=False)
    created: list[FakeProviderSession] = []

    def _factory(**kwargs):
        session = FakeProviderSession(**kwargs)
        session.connect_error = ConnectionError("the provider is unreachable")
        created.append(session)
        return session

    _install_fake_provider(monkeypatch, _factory)

    tenant = await make_tenant(db, name="S2S No Fallback Tenant")
    call = await _make_call(db, tenant)

    runtime = await build_s2s_runtime(
        session=db,
        tenant=tenant,
        call=call,
        runtime_cfg=_runtime_config(),
    )
    assert runtime is not None
    error = await agent_pipeline._s2s_preflight(runtime)
    fallback = await agent_pipeline._s2s_failover(
        error=error,
        session=db,
        tenant=tenant,
        call=call,
        runtime_cfg=_runtime_config(),
        history=runtime.history,
        provider=runtime.provider,
    )
    assert fallback is None


@pytest.mark.asyncio
async def test_a_provider_that_dies_mid_call_hands_over_the_audio_path(
    monkeypatch, db
) -> None:
    """The cascaded chain takes the audio path when the provider dies."""
    _enable_s2s(monkeypatch)
    created: list[FakeProviderSession] = []

    def _factory(**kwargs):
        session = FakeProviderSession(**kwargs)
        created.append(session)
        return session

    _install_fake_provider(monkeypatch, _factory)

    tenant = await make_tenant(db, name="S2S Handover Tenant")
    call = await _make_call(db, tenant)

    runtime = await build_s2s_runtime(
        session=db,
        tenant=tenant,
        call=call,
        runtime_cfg=_runtime_config(),
    )
    assert runtime is not None

    processor = runtime.processor
    await _setup_processor(processor)
    await processor.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)

    delegate = CaptureProcessor()
    chain = [delegate]
    assert await processor.activate_chain(chain) is True
    assert processor.fallback_active is True

    # The audio now belongs to the cascaded chain.
    await processor.process_frame(
        InputAudioRawFrame(audio=b"\x09" * 320, sample_rate=8000, num_channels=1),
        FrameDirection.DOWNSTREAM,
    )
    assert delegate.frames, "the cascaded chain never received the caller audio"

    await processor.stop()


# ---------------------------------------------------------------------------
# The feature switch
# ---------------------------------------------------------------------------
def test_the_runtime_is_only_requested_when_the_feature_is_on(monkeypatch) -> None:
    _enable_s2s(monkeypatch)
    assert s2s_runtime_requested(_runtime_config(s2s_enabled=True)) is True

    from app.carriers_v2 import settings as carrier_settings

    real_settings = carrier_settings.get_carrier_v2_settings()
    monkeypatch.setattr(
        carrier_settings,
        "get_carrier_v2_settings",
        lambda: real_settings.model_copy(update={"enable_s2s": False}),
    )
    assert s2s_runtime_requested(_runtime_config(s2s_enabled=True)) is False


async def asyncio_sleep() -> None:
    """Let the provider event pump drain."""
    import asyncio

    await asyncio.sleep(0.05)
