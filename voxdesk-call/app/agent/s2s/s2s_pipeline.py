"""Pipecat processor that drives a speech-to-speech session.

The processor is the seam between the Pipecat pipeline and a realtime
provider:

* caller audio (``InputAudioRawFrame``) is forwarded to the provider;
* provider audio becomes ``OutputAudioRawFrame``;
* provider transcripts become ``TranscriptionFrame`` (caller) and
  ``TextFrame`` (assistant), which is what the existing
  :class:`~app.agent.latency.LatencyObserver` and the turn persistence
  already understand;
* barge-in (``StartInterruptionFrame`` / ``InterruptionFrame``) cancels the
  provider's current response, so the caller is not made to listen to an
  answer they already interrupted;
* a tool call from the model is executed by the tools bridge and the result
  is returned to the provider.

The frames it emits are exactly the frames the cascaded pipeline emits, so
the surrounding pipeline, the observers and the dashboards do not care
which runtime is underneath.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    StartFrame,
    StartInterruptionFrame,
    TextFrame,
    TTSStartedFrame,
    TranscriptionFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.agent.s2s.s2s_audio import (
    GEMINI_LIVE_SAMPLE_RATE,
    OPENAI_REALTIME_SAMPLE_RATE,
    from_pipeline_pcm,
    to_pipeline_pcm,
)
from app.agent.s2s.s2s_cost import S2SCostMeter, build_cost_meter, persist_token_usage
from app.agent.s2s.s2s_session import S2SEvent, S2SEventType, S2SSession
from app.agent.s2s.s2s_tools_bridge import S2SToolsBridge
from app.core.logging import log
from app.core.metrics import observe_voice_ttfb

#: Called with (speaker, text) for every transcript the provider produces.
TranscriptSink = Callable[[str, str], Awaitable[None]]


class S2SPipelineProcessor(FrameProcessor):
    """Drives one speech-to-speech session inside a Pipecat pipeline."""

    def __init__(
        self,
        *,
        session: S2SSession,
        tools=None,
        sample_rate: int = 8000,
        provider_sample_rate: int = OPENAI_REALTIME_SAMPLE_RATE,
        provider_codec: str = "pcm16",
        transcript_sink: TranscriptSink | None = None,
        on_error: Callable[[str], Awaitable[None]] | None = None,
        auto_commit: bool = True,
    ) -> None:
        super().__init__()
        self.session = session
        self.tools = tools
        self.sample_rate = sample_rate
        self.provider_sample_rate = provider_sample_rate
        self.provider_codec = provider_codec
        self.transcript_sink = transcript_sink
        self.on_error = on_error
        self.auto_commit = auto_commit
        self._pump: asyncio.Task | None = None
        self._bot_speaking = False
        self._tool_calls = 0
        self.first_audio_at: float | None = None
        self.turn_started_at: float | None = None
        #: The measured time to the first provider audio of the last turn.
        self.time_to_first_audio_ms: float | None = None
        #: The cascaded chain that took over after a provider failure.
        self._delegate: FrameProcessor | None = None
        self._chain: list[FrameProcessor] = []
        self._setup_context: Any = None
        self._start_frame: Frame | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def setup(self, setup) -> None:
        """Remember the pipeline setup, so a failover chain can join it."""
        self._setup_context = setup
        await super().setup(setup)

    async def start(self, frame: StartFrame | None = None) -> None:
        """Open the provider session and start pumping its events."""
        await self.session.start()
        self._pump = asyncio.create_task(self._pump_events())

    async def stop(self, frame: Frame | None = None) -> None:
        """Stop the pump and close the provider session."""
        if self._pump is not None:
            self._pump.cancel()
            try:
                await self._pump
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover - best effort stop
                log.debug("s2s.pipeline.pump_stopped", error=str(exc)[:120])
            self._pump = None
        await self.session.close()

    async def cancel(self, frame: Frame | None = None) -> None:
        """Cancel the provider session when the pipeline is torn down."""
        await self.stop(frame)

    # ------------------------------------------------------------------
    # Mid-call failover
    # ------------------------------------------------------------------
    @property
    def fallback_active(self) -> bool:
        """True when the cascaded chain has taken over the audio path."""
        return self._delegate is not None

    async def activate_chain(self, chain: list[FrameProcessor]) -> bool:
        """Hand the call over to the cascaded chain, mid-call.

        The chain is linked into the running pipeline, set up with the same
        context as the rest of the pipeline and started, so the caller keeps
        talking to a live assistant instead of a dead socket.
        """
        if not chain:
            return False
        tail = self._next
        self.link(chain[0])
        for first, second in zip(chain, chain[1:]):
            first.link(second)
        if tail is not None:
            chain[-1].link(tail)
        else:
            chain[-1]._next = None
        if self._setup_context is not None:
            for processor in chain:
                await processor.setup(self._setup_context)
        if self._start_frame is not None:
            for processor in chain:
                await processor.process_frame(self._start_frame, FrameDirection.DOWNSTREAM)
        self._chain = list(chain)
        self._delegate = chain[0]
        log.info("s2s.pipeline.fallback_activated", chain=len(chain))
        return True

    # ------------------------------------------------------------------
    # Inbound frames
    # ------------------------------------------------------------------
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._start_frame = frame
            await self.start(frame)
            return
        if isinstance(frame, EndFrame):
            await self.stop(frame)
            return
        if isinstance(frame, CancelFrame):
            await self.cancel(frame)
            return

        if self._delegate is not None:
            # The cascaded chain owns the call now.
            await self._delegate.process_frame(frame, direction)
            return

        if isinstance(frame, InputAudioRawFrame):
            await self._forward_audio(frame)
            return

        if isinstance(frame, UserStartedSpeakingFrame):
            # The caller started talking over the assistant: drop whatever the
            # provider still has queued.
            if self._bot_speaking:
                await self._interrupt_provider()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, UserStoppedSpeakingFrame):
            self.turn_started_at = time.monotonic()
            self.time_to_first_audio_ms = None
            if self.auto_commit:
                await self._commit()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, (StartInterruptionFrame,)):
            await self._interrupt_provider()
            await self.push_frame(frame, direction)
            return

        await self.push_frame(frame, direction)

    async def _forward_audio(self, frame: InputAudioRawFrame) -> None:
        rate = int(getattr(frame, "sample_rate", self.sample_rate) or self.sample_rate)
        pcm = frame.audio
        if rate != self.provider_sample_rate:
            pcm = resample_for_provider(
                pcm,
                source_rate=rate,
                target_rate=self.provider_sample_rate,
            )
        try:
            await self.session.send_audio(pcm, sample_rate=self.provider_sample_rate)
        except Exception as exc:
            log.warning("s2s.pipeline.send_audio_failed", error=str(exc)[:160])
            await self._report_error("send_audio_failed")

    async def _commit(self) -> None:
        commit = getattr(self.session, "commit", None)
        if commit is None:
            return
        try:
            await commit()
        except Exception as exc:
            log.warning("s2s.pipeline.commit_failed", error=str(exc)[:160])
            await self._report_error("commit_failed")

    async def _interrupt_provider(self) -> None:
        try:
            await self.session.interrupt()
        except Exception as exc:
            log.warning("s2s.pipeline.interrupt_failed", error=str(exc)[:160])

    async def _report_error(self, reason: str) -> None:
        if self.on_error is None:
            return
        try:
            await self.on_error(reason)
        except Exception as exc:  # pragma: no cover - the sink must not raise
            log.debug("s2s.pipeline.error_sink_failed", error=str(exc)[:120])

    # ------------------------------------------------------------------
    # Provider events
    # ------------------------------------------------------------------
    async def _pump_events(self) -> None:
        try:
            async for event in self.session.events():
                await self._handle_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("s2s.pipeline.pump_failed", error=str(exc)[:160])
            await self._report_error("event_stream_failed")

    async def _handle_event(self, event: S2SEvent) -> None:
        if event.type == S2SEventType.AUDIO_OUT:
            await self._push_audio(event)
            return
        if event.type == S2SEventType.TRANSCRIPT_IN:
            if event.text:
                transcript = TranscriptionFrame(
                    text=event.text,
                    user_id="caller",
                    timestamp=str(time.time()),
                )
                await self.push_frame(transcript, FrameDirection.DOWNSTREAM)
                await self._persist("user", event.text)
            return
        if event.type == S2SEventType.TRANSCRIPT_OUT:
            if event.text:
                text = TextFrame(text=event.text)
                await self.push_frame(text, FrameDirection.DOWNSTREAM)
                await self._persist("assistant", event.text)
            return
        if event.type == S2SEventType.TOOL_CALL:
            await self._run_tool(event)
            return
        if event.type == S2SEventType.INTERRUPTED:
            await self._push_frame(StartInterruptionFrame())
            return
        if event.type == S2SEventType.TURN_END:
            await self._end_turn()
            return
        if event.type == S2SEventType.ERROR:
            log.warning("s2s.provider.error", provider=event.provider, error=event.error)
            await self._report_error(event.error or "provider_error")

    async def _push_audio(self, event: S2SEvent) -> None:
        if not event.audio:
            return
        if not self._bot_speaking:
            self._bot_speaking = True
            if self.first_audio_at is None:
                self.first_audio_at = time.monotonic()
            await self._measure_time_to_first_audio()
            await self.push_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        pcm = to_pipeline_pcm(
            event.audio,
            codec=self.provider_codec,
            source_rate=event.sample_rate or self.provider_sample_rate,
            target_rate=self.sample_rate,
        )
        if not pcm:
            return
        await self.push_frame(
            OutputAudioRawFrame(
                audio=pcm, sample_rate=self.sample_rate, num_channels=1
            ),
            FrameDirection.DOWNSTREAM,
        )

    async def _measure_time_to_first_audio(self) -> None:
        """Measure the time from the caller's turn to the first audio.

        The ``TTSStartedFrame`` that goes with it is what the pipeline's
        latency observer needs: it closes the turn with the stage TTFB and
        the end-to-end latency of this turn.
        """
        now = time.monotonic()
        anchor = self.turn_started_at or self.first_audio_at or now
        delta_s = max(0.0, now - anchor)
        self.time_to_first_audio_ms = round(delta_s * 1000.0, 3)
        provider = getattr(self.session, "provider", "") or "unknown"
        observe_voice_ttfb("tts", delta_s, provider=str(provider))
        await self.push_frame(TTSStartedFrame(), FrameDirection.DOWNSTREAM)

    async def _end_turn(self) -> None:
        if self._bot_speaking:
            self._bot_speaking = False
            await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)

    async def _push_frame(self, frame: Frame) -> None:
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)

    async def _persist(self, speaker: str, text: str) -> None:
        if self.transcript_sink is None:
            return
        try:
            await self.transcript_sink(speaker, text)
        except Exception as exc:  # pragma: no cover - the sink must not raise
            log.debug("s2s.pipeline.transcript_sink_failed", error=str(exc)[:120])

    async def _run_tool(self, event: S2SEvent) -> None:
        if self.tools is None:
            return
        self._tool_calls += 1
        try:
            result = await self.tools.execute(event.tool_name, event.arguments)
        except Exception as exc:
            log.warning("s2s.pipeline.tool_failed", tool=event.tool_name, error=str(exc)[:120])
            result = {"ok": False, "error": "tool_execution_failed"}
        try:
            await self.session.send_tool_result(
                event.tool_call_id, event.tool_name, result
            )
        except Exception as exc:
            log.warning(
                "s2s.pipeline.tool_result_failed",
                tool=event.tool_name,
                error=str(exc)[:120],
            )

    # ------------------------------------------------------------------
    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    @property
    def bot_speaking(self) -> bool:
        return self._bot_speaking


def resample_for_provider(pcm: bytes, *, source_rate: int, target_rate: int) -> bytes:
    """Convert pipeline audio into the provider's wire format."""
    return from_pipeline_pcm(
        pcm,
        codec="pcm16",
        source_rate=source_rate,
        target_rate=target_rate,
    )


def provider_sample_rate_for(provider: str) -> int:
    """The wire sample rate of a provider."""
    if provider == "gemini_live":
        return GEMINI_LIVE_SAMPLE_RATE
    return OPENAI_REALTIME_SAMPLE_RATE


# ----------------------------------------------------------------------
# The live runtime
# ----------------------------------------------------------------------


@dataclass
class S2SRuntime:
    """One live speech-to-speech session, wired to a call."""

    provider: str
    model: str
    session: S2SSession
    processor: S2SPipelineProcessor
    meter: S2SCostMeter
    bridge: S2SToolsBridge
    record: Any = None
    history: list[dict[str, str]] = field(default_factory=list)
    error: str = ""
    failover_reason: str = ""
    fallback_activated: bool = False

    @property
    def failed(self) -> bool:
        """True when the provider session ended badly."""
        return bool(self.error)


class S2STurnSink:
    """Persists the provider's transcripts as turns and keeps the history.

    The cascaded pipeline writes its turns from the LLM context at the end
    of the call. The realtime provider has no such context, so the
    transcripts are written as they arrive -- with the same redaction the
    cascaded path applies.
    """

    def __init__(self, *, session, call, history: list[dict[str, str]]) -> None:
        self._session = session
        self._call = call
        self._history = history

    async def __call__(self, speaker: str, text: str) -> None:
        cleaned = (text or "").strip()
        if not cleaned:
            return
        role = "user" if speaker == "user" else "assistant"
        self._history.append({"role": role, "content": cleaned})

        from app.db.models import Speaker, Turn
        from app.telephony.transcription import redact_turn_text

        spoken = await redact_turn_text(self._session, self._call, cleaned)
        self._session.add(
            Turn(
                call_id=self._call.id,
                speaker=Speaker.USER if role == "user" else Speaker.ASSISTANT,
                text=spoken,
            )
        )


def s2s_runtime_requested(runtime_cfg) -> bool:
    """True when this call should try the speech-to-speech runtime.

    The operator's master switch (``ENABLE_S2S``) and the per-call agent
    configuration must both agree: the master switch keeps the feature off
    for a deployment that has not been prepared for it.
    """
    from app.agent.providers.s2s_providers import is_s2s_feature_enabled
    from app.carriers_v2.settings import get_carrier_v2_settings

    carrier = get_carrier_v2_settings()
    if not carrier.s2s_enabled:
        return False
    return bool(is_s2s_feature_enabled(runtime_cfg))


def resolve_s2s_provider(runtime_cfg) -> str:
    """The provider this call should use, or an empty string for none."""
    provider = str(getattr(runtime_cfg, "s2s_provider", None) or "").strip().lower()
    if provider:
        return provider

    from app.agent.providers.registry import get_provider_credential

    for candidate, env_name, attr in (
        ("openai_realtime", "OPENAI_API_KEY", "openai_api_key"),
        ("gemini_live", "GOOGLE_API_KEY", "google_api_key"),
    ):
        if get_provider_credential(env_name, attr):
            return candidate
    return ""


async def build_s2s_runtime(
    *,
    session,
    tenant,
    call,
    runtime_cfg,
    handlers=None,
    voice: str = "",
    system_prompt: str = "",
) -> S2SRuntime | None:
    """Build the speech-to-speech runtime for a call.

    Returns ``None`` -- and the call continues on the cascaded pipeline --
    when the feature is off, when no provider credential is present, when the
    tenant's speech-to-speech budget is exhausted, or when the runtime
    configuration is invalid. The caller never sees a half-built runtime.
    """
    from app.agent.providers.registry import (
        get_provider_credential,
        missing_provider_env,
        resolve_provider_spec,
    )
    from app.carriers_v2.settings import get_carrier_v2_settings

    carrier = get_carrier_v2_settings()
    if not s2s_runtime_requested(runtime_cfg):
        return None

    provider = resolve_s2s_provider(runtime_cfg)
    if not provider:
        log.info("s2s.runtime.no_provider", tenant_id=str(tenant.id))
        return None

    try:
        spec = resolve_provider_spec("s2s", provider)
    except Exception:
        log.warning("s2s.runtime.unknown_provider", provider=provider)
        return None

    missing = missing_provider_env("s2s", provider)
    if missing:
        log.info(
            "s2s.runtime.provider_not_configured",
            provider=provider,
            missing=list(missing),
        )
        return None

    api_key = get_provider_credential(
        spec.required_env[0], spec.settings_attrs[0] if spec.settings_attrs else None
    )
    if not api_key:
        return None

    model = str(getattr(runtime_cfg, "s2s_model", None) or "").strip()
    if not model:
        model = (
            carrier.gemini_live_model
            if provider == "gemini_live"
            else carrier.openai_realtime_model
        )
    if not model:
        model = spec.default_model

    # The tenant's ceiling is checked before the socket is opened: an
    # exhausted budget must not start a paid session.
    meter = build_cost_meter(provider=provider, model=model)
    from app.agent.s2s.s2s_cost import tenant_spend_micro_usd

    meter.tenant_spend_micro_usd = await tenant_spend_micro_usd(session, tenant.id)
    if meter.tenant_budget_exceeded():
        log.warning(
            "s2s.runtime.tenant_budget_exhausted",
            tenant_id=str(tenant.id),
            spent_micro_usd=meter.tenant_spend_micro_usd,
        )
        return None

    bridge = S2SToolsBridge(
        handlers=handlers,
        session=session,
        tenant_id=tenant.id,
        custom_tools=list(getattr(runtime_cfg, "custom_tools", []) or []),
        mcp_tools=list(getattr(runtime_cfg, "mcp_tools", []) or []),
        max_tool_calls=carrier.s2s_max_tool_calls,
    )

    instructions = str(system_prompt or getattr(runtime_cfg, "system_prompt", "") or "")
    provider_voice = voice or str(getattr(runtime_cfg, "voice_id", "") or "") or "alloy"
    tools = bridge.tools_for(provider)

    if provider == "gemini_live":
        from app.agent.s2s.gemini_live import GeminiLiveSession

        provider_session: S2SSession = GeminiLiveSession(
            api_key=api_key,
            model=model,
            system_prompt=instructions,
            voice=provider_voice,
            tools=tools,
            sample_rate=GEMINI_LIVE_SAMPLE_RATE,
            max_session_seconds=carrier.s2s_max_session_seconds,
        )
    else:
        from app.agent.s2s.openai_realtime import OpenAIRealtimeSession

        provider_session = OpenAIRealtimeSession(
            api_key=api_key,
            model=model,
            system_prompt=instructions,
            voice=provider_voice,
            tools=tools,
            sample_rate=OPENAI_REALTIME_SAMPLE_RATE,
            max_session_seconds=carrier.s2s_max_session_seconds,
        )

    history: list[dict[str, str]] = []
    sink = S2STurnSink(session=session, call=call, history=history)

    async def _on_error(reason: str) -> None:
        log.warning("s2s.runtime.provider_error", provider=provider, reason=reason)

    processor = S2SPipelineProcessor(
        session=provider_session,
        tools=bridge,
        sample_rate=8000,
        provider_sample_rate=provider_sample_rate_for(provider),
        provider_codec="pcm16",
        transcript_sink=sink,
        on_error=_on_error,
    )

    record = None
    try:
        from app.db.carrier_s2s_models import S2SSessionRecord

        record = S2SSessionRecord(
            tenant_id=tenant.id,
            call_id=getattr(call, "id", None),
            environment_id=getattr(call, "environment_id", None),
            provider=provider,
            model=model,
            status="active",
            started_at=datetime.now(timezone.utc),
        )
        session.add(record)
        await session.flush()
    except Exception as exc:
        log.warning("s2s.runtime.record_failed", error=type(exc).__name__)
        record = None

    log.info(
        "s2s.runtime.ready",
        provider=provider,
        model=model,
        tools=len(tools),
        tenant_id=str(tenant.id),
    )
    return S2SRuntime(
        provider=provider,
        model=model,
        session=provider_session,
        processor=processor,
        meter=meter,
        bridge=bridge,
        record=record,
        history=history,
    )


async def finalize_s2s_runtime(
    runtime: S2SRuntime,
    *,
    session,
    tenant,
    call,
    error: str = "",
    failover_reason: str = "",
    fallback_activated: bool = False,
) -> None:
    """Close the provider session and write what the call consumed.

    The session record is the durable trace of the session; the usage
    ledger is written with an idempotency key, so a finalisation that runs
    twice bills once.
    """
    provider_session = runtime.session
    try:
        await provider_session.close()
    except Exception as exc:  # pragma: no cover - best effort close
        log.debug("s2s.runtime.close_failed", error=str(exc)[:120])

    usage = runtime.session.usage if hasattr(runtime.session, "usage") else {}
    input_tokens = int(usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or 0)
    runtime.meter.record_tokens(input_tokens=input_tokens, output_tokens=output_tokens)

    provider_rate = provider_sample_rate_for(runtime.provider)
    input_bytes = int(getattr(runtime.session, "input_bytes", 0) or 0)
    output_bytes = int(getattr(runtime.session, "output_bytes", 0) or 0)
    runtime.meter.record_audio_bytes(
        raw=bytes(input_bytes), output=False, sample_rate=provider_rate
    )
    runtime.meter.record_audio_bytes(
        raw=bytes(output_bytes), output=True, sample_rate=provider_rate
    )
    snapshot = runtime.meter.snapshot()

    record = runtime.record
    if record is not None:
        try:
            record.ended_at = datetime.now(timezone.utc)
            record.duration_ms = int(runtime.meter.elapsed_seconds() * 1000)
            record.input_audio_s = snapshot.input_audio_seconds
            record.output_audio_s = snapshot.output_audio_seconds
            record.input_tokens = snapshot.input_tokens
            record.output_tokens = snapshot.output_tokens
            record.cost_micro_usd = snapshot.cost_micro_usd
            record.cost_known = snapshot.cost_known
            record.failover_reason = failover_reason or None
            record.error = error or None
            if error:
                record.status = "failed_over" if fallback_activated else "failed"
            else:
                record.status = "completed"
            await session.flush()
        except Exception as exc:
            log.warning("s2s.runtime.record_update_failed", error=type(exc).__name__)

    await persist_token_usage(
        session,
        tenant,
        call_id=call.id,
        input_tokens=snapshot.input_tokens,
        output_tokens=snapshot.output_tokens,
        provider=runtime.provider,
    )

    log.info(
        "s2s.runtime.finished",
        provider=runtime.provider,
        model=runtime.model,
        input_audio_s=round(snapshot.input_audio_seconds, 2),
        output_audio_s=round(snapshot.output_audio_seconds, 2),
        input_tokens=snapshot.input_tokens,
        output_tokens=snapshot.output_tokens,
        cost_micro_usd=snapshot.cost_micro_usd,
        error=error or "",
    )


__all__ = [
    "S2SPipelineProcessor",
    "S2SRuntime",
    "S2STurnSink",
    "build_s2s_runtime",
    "finalize_s2s_runtime",
    "provider_sample_rate_for",
    "resample_for_provider",
    "resolve_s2s_provider",
    "s2s_runtime_requested",
]
