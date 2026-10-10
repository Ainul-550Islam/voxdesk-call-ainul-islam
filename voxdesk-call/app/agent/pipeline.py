"""THE CORE FILE — এখানেই সব হয়।

অডিও চেইন (measured latency documented in `docs/LATENCY_BENCHMARK.md`):
  Twilio / Telnyx / Plivo / SIP (mu-law / PCM 8kHz)
    -> NoisereduceFilter   caller background denoise (2G)
    -> Silero VAD + SmartTurn V3 (2B)
    -> Multi-Provider STT + Failover (2C)
    -> Backchannel & Idle Reminder (2B)
    -> LLM (OpenAI / Anthropic / Gemini / Groq / Bedrock + Failover)
    -> FillerInjector      tool চলার সময় "let me check"
    -> TextNormalizer      "$150" -> "one hundred fifty dollars"
    -> Multi-Provider TTS + Failover (2C)
    -> SoundfileMixer      ambient background bed (2G)
    -> Carrier Serializer out (2F)
    -> LatencyObserver + TurnTrackingObserver  per-turn STT/LLM/TTS TTFB & E2E p50/p95/p99 (2A/2B)
"""
from __future__ import annotations

from fastapi import WebSocket
from pipecat.observers.turn_tracking_observer import TurnTrackingObserver
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.frames.frames import InterimTranscriptionFrame, TextFrame, TranscriptionFrame
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.audio import build_ambient_mixer, build_denoise_filter
from app.agent.flow_processor import build_flow_processor_for_runtime
from app.agent.functions import TOOL_SCHEMAS, FunctionHandlers
from app.agent.humanize import (
    Backchannel,
    FillerInjector,
    TextNormalizer,
    vary_greeting,
)
from app.agent.idle_reminders import build_idle_reminder_processor
from app.agent.language import resolve_language_config
from app.agent.latency import LatencyObserver, LatencyTrackingProcessor
from app.agent.llm_factory import governed_entry
from app.agent.monitor_tap import MonitorTap
from app.agent.prompts import build_system_prompt
from app.agent.stt import build_stt, build_stt_for_runtime
from app.agent.tts import build_tts, build_tts_for_runtime
from app.agent.turn_taking import build_turn_config
from app.agent.usage_tracker import UsageTracker
from app.core.config import settings
from app.core.logging import log
from app.db.models import Call, CallStatus, Speaker, Tenant, Turn
from app.runtime.agent_config_resolver import RuntimeConfig, resolve_runtime_config
from app.telephony.media.serializers import build_serializer
from app.telephony.takeover import is_takeover_pending


#: STEP 9 (item N): ceiling on tool invocations per live call. The text path
#: caps tool *rounds* at 4; the voice path caps individual tool calls at 12 so
#: a prompt-injected or looped model cannot run unlimited paid operations.
MAX_TOOL_CALLS_PER_CALL = 12


class GovernedHearing(FrameProcessor):
    """Drop a transcript the input guardrail refuses. A signal is not a block."""

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and not isinstance(frame, InterimTranscriptionFrame):
            text = getattr(frame, "text", "") or ""
            if text.strip():
                from app.ai.runtime import guard_heard_text

                decision = guard_heard_text(text)
                if not decision.allowed:
                    log.info("voice.input_rejected", reason=decision.reason)
                    return
        await self.push_frame(frame, direction)


class GovernedSpeech(FrameProcessor):
    """Replace blocked model text before TTS. The raw text is not spoken."""

    def __init__(self, blocked_substrings: tuple[str, ...] = ()):
        super().__init__()
        self._blocked = tuple(blocked_substrings)

    async def process_frame(self, frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame) and getattr(frame, "text", None):
            from app.ai.runtime import guard_spoken_text

            frame.text = guard_spoken_text(frame.text, blocked_substrings=self._blocked)
        await self.push_frame(frame, direction)


async def handle_pipeline_disconnect(
    call: Call,
    task: PipelineTask | None = None,
    session: AsyncSession | None = None,
) -> bool:
    """Handle client WebSocket disconnect, checking ``call.takeover_pending`` first.

    Returns ``False`` when ``call.takeover_pending`` is True (so a takeover is
    NOT finalized as a hang-up), and ``True`` when the call is finalized as a
    normal caller disconnect.
    """
    from datetime import datetime, timezone

    if session is not None and not is_takeover_pending(call):
        try:
            await session.refresh(call)
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            pass

    if is_takeover_pending(call):
        log.info(
            "call.disconnected_for_takeover",
            call_sid=getattr(call, "call_sid", ""),
            call_id=str(getattr(call, "id", "")),
        )
        if getattr(call, "status", None) in (CallStatus.COMPLETED, CallStatus.FAILED):
            call.status = CallStatus.IN_PROGRESS
        call.ended_at = None
        if task is not None:
            if hasattr(task, "stop_when_done"):
                await task.stop_when_done()
            elif hasattr(task, "cancel"):
                await task.cancel()
        return False

    log.info("call.disconnected", call_sid=getattr(call, "call_sid", ""))
    if getattr(call, "status", None) in (CallStatus.RINGING, CallStatus.IN_PROGRESS):
        call.status = CallStatus.COMPLETED
        call.ended_at = getattr(call, "ended_at", None) or datetime.now(timezone.utc)
        if not getattr(call, "end_reason", None):
            call.end_reason = "caller_hangup"
        if session is not None:
            await session.flush()
    if task is not None and hasattr(task, "cancel"):
        await task.cancel()
    return True


async def run_voice_agent(
    websocket: WebSocket,
    stream_sid: str,
    call_sid: str,
    session: AsyncSession,
    tenant: Tenant,
    call: Call,
    *,
    runtime_config: RuntimeConfig | None = None,
    carrier_provider: str = "twilio",
) -> None:
    """Run the voice pipeline inside a call-scoped correlation context.

    Every log line the call produces — provider selection, fallback, TTS
    normalisation, tool calls, usage, crashes — then carries the same
    ``call_sid``/``call_id``/``tenant_id``, which is what turns a single
    call's scattered logs into one trace (Step 7 correlation).
    """
    from app.core.correlation import correlation_scope

    with correlation_scope(
        call_sid=call_sid, call_id=str(call.id), tenant_id=str(tenant.id)
    ):
        await _run_voice_agent(
            websocket,
            stream_sid,
            call_sid,
            session,
            tenant,
            call,
            runtime_config=runtime_config,
            carrier_provider=carrier_provider,
        )


async def _run_voice_agent(
    websocket: WebSocket,
    stream_sid: str,
    call_sid: str,
    session: AsyncSession,
    tenant: Tenant,
    call: Call,
    *,
    runtime_config: RuntimeConfig | None = None,
    carrier_provider: str = "twilio",
) -> None:
    """একটা ফোন কল শুরু থেকে শেষ পর্যন্ত চালায়।"""

    # ---------------------------------------------------- 2E RuntimeConfig --
    runtime_cfg = runtime_config or await resolve_runtime_config(session, call, tenant=tenant)

    # ---------------------------------------------------- LLM নির্বাচন ----
    # Policy, budget, circuit, prompt and timeout run before the media path.
    # ``governed_entry`` is the documented live factory path; the call itself
    # is ``voice_llm``, which refuses a disabled provider before build_llm.
    from app.ai.runtime import guard_spoken_text, voice_llm

    llm, prepared = await voice_llm(
        session,
        tenant,
        call,
        temperature=runtime_cfg.temperature,
        max_tokens=runtime_cfg.max_tokens or 110,
    )
    choice = prepared.choice
    log.info(
        "llm.selected",
        provider=choice.provider,
        model=choice.model,
        tenant=tenant.name,
        note=choice.notes,
        governed=True,
        entry=governed_entry(),
        timeout_ms=prepared.timeout_ms,
        prompt_version=prepared.prompt_version,
        approval_state=prepared.approval_state,
        agent_id=str(runtime_cfg.agent_id) if runtime_cfg.agent_id else None,
        agent_version_id=str(runtime_cfg.agent_version_id) if runtime_cfg.agent_version_id else None,
    )

    # ---------------------------------------------------------- 2B/2F/2G ---
    turn_cfg = build_turn_config(runtime_cfg)
    serializer = build_serializer(
        carrier_provider,
        stream_sid=stream_sid,
        call_sid=call_sid,
        auto_hang_up=True,
    )
    denoise_filter = build_denoise_filter(runtime_cfg.denoise_enabled)
    ambient_mixer = build_ambient_mixer(
        runtime_cfg.ambient_sound,
        ambient_volume=runtime_cfg.ambient_volume,
    )

    transport_kwargs = {
        "audio_in_enabled": True,
        "audio_out_enabled": True,
        "add_wav_header": False,
        "serializer": serializer,
        "vad_analyzer": turn_cfg.vad_analyzer,
    }
    if turn_cfg.turn_analyzer is not None:
        transport_kwargs["turn_analyzer"] = turn_cfg.turn_analyzer
    if denoise_filter is not None:
        transport_kwargs["audio_in_filter"] = denoise_filter
    if ambient_mixer is not None:
        transport_kwargs["audio_out_mixer"] = ambient_mixer

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(**transport_kwargs),
    )

    # ---------------------------------------------------------- services ---
    # Each builder validates its own configuration and raises a typed
    # ProviderError (configuration_error / unsupported_feature) *before* any
    # network activity, so a misconfigured deployment fails fast instead of
    # starting a call that can never work.
    if (
        runtime_cfg.stt_provider != "deepgram"
        or runtime_cfg.stt_fallback_providers
        or runtime_cfg.boosted_keywords
    ):
        stt = build_stt_for_runtime(runtime_cfg)
    else:
        stt = build_stt(tenant)

    if (
        runtime_cfg.tts_provider != "elevenlabs"
        or runtime_cfg.tts_fallback_providers
        or runtime_cfg.voice_settings
    ):
        tts = build_tts_for_runtime(runtime_cfg)
    else:
        tts = build_tts(tenant)

    # ------------------------------------------------------ tool wiring ---
    task: PipelineTask | None = None

    async def _push_frame_to_task(frame):
        if task is not None:
            await task.queue_frames([frame])

    handlers = FunctionHandlers(
        session=session,
        tenant=tenant,
        call=call,
        runtime_config=runtime_cfg,
        frame_pusher=_push_frame_to_task,
    )

    # escalate_to_human is withheld when there is no usable destination or the
    # call cannot be transferred -- see FunctionHandlers.available_tools().
    active_tools = handlers.available_tools()
    if len(active_tools) != len(TOOL_SCHEMAS):
        log.info("tools.escalation_unavailable", call_sid=call_sid,
                 tenant=tenant.name)

    tool_calls_this_call = 0

    async def _tool_bridge(params):
        # STEP 9 (item N): a per-call ceiling on tool invocations, mirroring the
        # text path's MAX_TOOL_ROUNDS. The LLM decides what tools to call and is
        # never trusted to stop on its own -- a prompt-injected or looped model
        # cannot run unlimited tools (each of which costs money and may touch
        # the calendar/CRM).
        nonlocal tool_calls_this_call
        tool_calls_this_call += 1
        if tool_calls_this_call > MAX_TOOL_CALLS_PER_CALL:
            log.warning("tools.per_call_limit", call_sid=call_sid,
                        calls=tool_calls_this_call)
            await params.result_callback(
                {"ok": False, "message": "I can't do that right now. Offer to take a message."}
            )
            return
        from app.ai.guardrails.tool_policy import agent_runtime_decision

        custom_names = {t.get("name") for t in runtime_cfg.custom_tools} | {
            t.get("qualified_name") or t.get("name") for t in runtime_cfg.mcp_tools
        }
        if params.function_name not in custom_names:
            verdict = agent_runtime_decision(params.function_name)
            if not verdict.allowed:
                await params.result_callback(
                    {"ok": False, "message": "That action is not allowed."}
                )
                return
        result = await handlers.dispatch(params.function_name, params.arguments or {})
        log.info("tool.called", name=params.function_name, ok=result.get("ok"),
                 outcome=result.get("outcome"))
        await params.result_callback(result)

        # A successful escalation has already told Twilio to redirect this
        # call, so our media stream is about to be torn down. Ending the task
        # ourselves makes that orderly instead of surfacing as a stream crash.
        if (
            params.function_name == "escalate_to_human"
            and result.get("outcome") == "TRANSFER_STARTED"
        ):
            log.info("pipeline.stopping_for_transfer", call_sid=call_sid)
            await task.stop_when_done()

    for schema in active_tools:
        llm.register_function(schema["function"]["name"], _tool_bridge)

    # --------------------------------------------------------- context ----
    greeting = vary_greeting(
        runtime_cfg.greeting or tenant.greeting,
        tenant.name,
        tenant.agent_name,
    )
    lang_profile = resolve_language_config(runtime_cfg.language or tenant.language)

    context = OpenAILLMContext(
        messages=[
            {
                "role": "system",
                "content": (
                    (
                        runtime_cfg.system_prompt
                        or prepared.system_prompt
                        or build_system_prompt(tenant, choice.provider)
                    )
                    + lang_profile.llm_instruction
                ),
            },
            {"role": "assistant", "content": greeting},
        ],
        tools=active_tools,
    )
    context_aggregator = llm.create_context_aggregator(context)

    # ------------------------------------------------- মানুষের মতো লেয়ার --
    humanizers = []
    if tenant.humanize or runtime_cfg.backchannel_enabled:
        humanizers.append(
            Backchannel(
                after_seconds=3.0,
                cooldown=15.0,
                enabled=runtime_cfg.backchannel_enabled,
                frequency=runtime_cfg.backchannel_frequency,
                words=list(runtime_cfg.backchannel_words) if runtime_cfg.backchannel_words else None,
            )
        )
    idle_proc = build_idle_reminder_processor(runtime_cfg)
    idle_frame_proc = idle_proc[0] if isinstance(idle_proc, tuple) else idle_proc
    if idle_frame_proc is not None:
        humanizers.append(idle_frame_proc)

    # -------------------------------------------------------- 2A latency ---
    latency_observer = LatencyObserver(
        tenant_plan=getattr(tenant.plan, "value", str(tenant.plan)),
        stt_provider=runtime_cfg.stt_provider,
        llm_provider=choice.provider,
        tts_provider=runtime_cfg.tts_provider,
    )
    latency_tracker = LatencyTrackingProcessor(latency_observer)
    turn_tracking_observer = TurnTrackingObserver()

    # -------------------------------------------------------- pipeline ----
    # Step 7 usage trackers. Pass-through processors that tally the measured
    # AI usage (STT characters, LLM tokens, TTS characters) and feed the
    # Prometheus counters + cost model. They never raise and never gate a
    # frame — see app/agent/usage_tracker.py.
    stt_usage = UsageTracker(track_stt=True)
    voice_usage = UsageTracker(track_voice=True, provider=choice.provider)
    monitor_tap = MonitorTap(
        call_id=call.id,
        tenant_id=tenant.id,
        context=context,
        frame_pusher=_push_frame_to_task,
    )
    flow_proc = build_flow_processor_for_runtime(
        runtime_config=runtime_cfg,
        handlers=handlers,
        call=call,
        context=context,
        available_tools=active_tools,
    )
    flow_processors = [flow_proc] if flow_proc is not None else []

    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            stt_usage,                       # measured transcription characters
            GovernedHearing(),               # input guardrail: drop a refused transcript
            monitor_tap,                     # 4: live monitor caller tap + whisper-to-AI
            *flow_processors,                # 5: conversation flow node/edge runner
            *humanizers,                     # "mm-hmm" মাঝপথে + idle reminders
            context_aggregator.user(),
            llm,
            FillerInjector(),                # tool চলাকালীন "let me check"
            TextNormalizer(),                # সংখ্যা/markdown ঠিক করা
            GovernedSpeech(blocked_substrings=prepared.blocked_output),
            tts,
            monitor_tap.output_tap(),        # 4: live monitor agent PCM + transcript tap
            voice_usage,                     # measured LLM tokens + TTS characters
            latency_tracker,                 # 2A stage TTFB + E2E turn latency
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=turn_cfg.allow_interruptions,  # <-- barge-in ON
            enable_metrics=True,
            enable_usage_metrics=True,
            audio_in_sample_rate=8000,
            audio_out_sample_rate=8000,
        ),
        observers=[latency_observer, turn_tracking_observer],
    )

    # ------------------------------------------------------- lifecycle ----
    @transport.event_handler("on_client_connected")
    async def _on_connected(_transport, _client):
        log.info("call.connected", call_sid=call_sid, tenant=tenant.name,
                 llm=f"{choice.provider}/{choice.model}")
        await task.queue_frames([context_aggregator.user().get_context_frame()])

    @transport.event_handler("on_client_disconnected")
    async def _on_disconnected(_transport, _client):
        await handle_pipeline_disconnect(call, task=task, session=session)

    async def _persist_turns() -> None:
        """
        Flush the conversation to `turns` and persist `CallLatencyStat` (2A).

        SYSTEM turns written by the transfer service are never touched here --
        this only appends user/assistant utterances.
        """
        from app.telephony.transcription import redact_turn_text

        for msg in context.get_messages():
            role, content = msg.get("role"), msg.get("content")
            if not content or role == "system":
                continue
            spoken = content if isinstance(content, str) else str(content)
            if role != "user":
                spoken = guard_spoken_text(spoken, blocked_substrings=prepared.blocked_output)
            spoken = await redact_turn_text(session, call, spoken)
            session.add(
                Turn(
                    call_id=call.id,
                    speaker=Speaker.USER if role == "user" else Speaker.ASSISTANT,
                    text=spoken,
                )
            )
        call.llm_used = f"{choice.provider}/{choice.model}"
        try:
            await latency_observer.persist(session, call)
        except Exception as exc:
            log.warning("voice.latency.persist_failed", error=str(exc)[:160])

        # Step 7: the single-call AI-usage trace. Model string and counts are
        # log fields (correlation), never Prometheus labels — that is what
        # makes a per-call trace possible without unbounded cardinality.
        log.info(
            "call.usage",
            call_sid=call_sid,
            tenant=tenant.name,
            llm=f"{choice.provider}/{choice.model}",
            stt_chars=stt_usage.snapshot()["stt_chars"],
            tts_chars=voice_usage.snapshot()["tts_chars"],
            llm_tokens=voice_usage.snapshot()["llm_tokens"],
        )
        # Persist measured LLM/TTS usage in the same existing immutable
        # UsageEvent ledger consumed by ROI. The hooks are idempotent and
        # preserve UNKNOWN cost when operator pricing is not configured.
        from app.billing.hooks import on_llm_tokens, on_tts_characters
        usage = voice_usage.snapshot()
        try:
            async with session.begin_nested():
                measured_llm_tokens = usage["llm_tokens"]
                if isinstance(measured_llm_tokens, int) and not isinstance(measured_llm_tokens, bool):
                    await on_llm_tokens(session, tenant, call_id=call.id, tokens=measured_llm_tokens, turn=0, provider=choice.provider)
                await on_tts_characters(session, tenant, call_id=call.id, characters=int(usage["tts_chars"] or 0), turn=0, provider=runtime_cfg.tts_provider or settings.tts_provider)
        except Exception as exc:
            log.warning("billing.measured_usage_persist_failed", error_type=type(exc).__name__)
        measured_tokens = usage["llm_tokens"]
        if prepared.budget_reserved and isinstance(measured_tokens, int) and not isinstance(measured_tokens, bool):
            # Reconcile only when the provider supplied measured usage. An
            # absent Pipecat metrics frame is unknown, not a zero-token call.
            from app.ai.budget import reconcile

            await reconcile(
                session,
                tenant.id,
                reserved=1,
                actual=measured_tokens,
            )
        await session.commit()

    runner = PipelineRunner(handle_sigint=False)
    try:
        await runner.run(task)
    except Exception:
        # A pipeline-level failure must propagate to the caller (the media
        # stream handler) so the call can be finalised accurately; it must not
        # be masked by the persistence step below.
        log.exception("pipeline.run_failed", call_sid=call_sid,
                      tenant=tenant.name)
        raise
    finally:
        # Persisting turns is best-effort cleanup. It must never mask the
        # primary outcome of the call (a provider crash, a hangup) and never
        # turn a finished call into a crash for the caller.
        try:
            await monitor_tap.close()
            if not is_takeover_pending(call):
                await monitor_tap.bus.publish_call_ended(call.id)
        except Exception:
            log.warning("pipeline.monitor_tap_cleanup_failed", call_sid=call_sid)
        try:
            await _persist_turns()
        except Exception:
            log.exception("pipeline.persist_turns_failed", call_sid=call_sid,
                          tenant=tenant.name)
