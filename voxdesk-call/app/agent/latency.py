"""Per-turn and per-call voice latency measurement (Sub-Phase 2A).

Provides:
- :class:`LatencyObserver`: Pipecat ``BaseObserver`` subclass that watches
  ``UserStartedSpeakingFrame``, ``UserStoppedSpeakingFrame``,
  ``InterimTranscriptionFrame``, ``TranscriptionFrame``,
  ``LLMFullResponseStartFrame``, ``TextFrame``, ``TTSStartedFrame``,
  ``TTSAudioRawFrame``, ``BotStartedSpeakingFrame``, ``BotStoppedSpeakingFrame``,
  ``InterruptionFrame``, and ``MetricsFrame`` (``TTFBMetricsData``).
- :class:`LatencyTrackingProcessor`: pass-through ``FrameProcessor`` that
  forwards pipeline frames to a :class:`LatencyObserver` when an inline
  processor is preferred or used alongside task observers.
- Percentile calculation (p50, p95, p99) and durable persistence to
  ``CallLatencyStat`` + ``Call.avg_response_ms``.
"""

from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    InterimTranscriptionFrame,
    InterruptionFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    MetricsFrame,
    StartInterruptionFrame,
    TextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSStartedFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.metrics.metrics import TTFBMetricsData
from pipecat.observers.base_observer import BaseObserver, FramePushed
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.core.metrics import (
    observe_voice_e2e_latency,
    observe_voice_ttfb,
    record_voice_interruption,
)
from app.db.models import Call
from app.db.telephony_models import CallLatencyStat


def percentile(values: list[float], q: float) -> float | None:
    """Compute the ``q``-th percentile (0..100) using linear interpolation."""
    if not values:
        return None
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    clamped = max(0.0, min(100.0, float(q)))
    pos = (clamped / 100.0) * (len(ordered) - 1)
    lower = int(math.floor(pos))
    upper = int(math.ceil(pos))
    if lower == upper:
        return round(ordered[lower], 3)
    weight = pos - lower
    interpolated = ordered[lower] * (1.0 - weight) + ordered[upper] * weight
    return round(interpolated, 3)


def _classify_processor_stage(processor_name: str, model_name: str = "") -> tuple[str | None, str]:
    """Infer ``(stage, provider)`` from a Pipecat processor label and model name."""
    lower = f"{processor_name} {model_name}".lower()
    provider = "unknown"
    for candidate in (
        "deepgram",
        "assemblyai",
        "whisper",
        "elevenlabs",
        "cartesia",
        "playht",
        "openai_realtime",
        "gemini_live",
        "azure_openai",
        "anthropic",
        "openai",
        "google",
        "groq",
        "bedrock",
        "azure",
    ):
        if candidate in lower:
            provider = candidate
            break
    if "awsbedrock" in lower or "nova" in lower:
        provider = "bedrock"

    if any(tok in lower for tok in ("ttsservice", "elevenlabs", "cartesia", "playht", "synthes")):
        return "tts", provider
    if any(tok in lower for tok in ("sttservice", "whisper", "deepgramstt", "assemblyai", "transcri")):
        return "stt", provider
    if "tts" in lower.split():
        return "tts", provider
    if "stt" in lower.split():
        return "stt", provider
    if any(tok in lower for tok in ("llm", "openai", "anthropic", "google", "gemini", "groq", "bedrock", "claude", "gpt")):
        return "llm", provider
    return None, provider


@dataclass
class TurnLatencySample:
    """Timing measurements for a single user->assistant conversational turn."""

    turn_index: int
    user_stopped_at: float
    stt_first_at: float | None = None
    llm_first_at: float | None = None
    tts_first_at: float | None = None
    bot_started_at: float | None = None
    stt_ttfb_ms: float | None = None
    llm_ttfb_ms: float | None = None
    tts_ttfb_ms: float | None = None
    e2e_ms: float | None = None
    interrupted: bool = False


class LatencyObserver(BaseObserver):
    """Pipecat pipeline observer that measures per-turn E2E and per-stage TTFB latency."""

    def __init__(
        self,
        *,
        call_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
        tenant_plan: str = "unknown",
        stt_provider: str = "deepgram",
        llm_provider: str = "openai",
        tts_provider: str = "elevenlabs",
        clock: Callable[[], float] | None = None,
    ) -> None:
        super().__init__()
        self.call_id = call_id
        self.tenant_id = tenant_id
        self.tenant_plan = (tenant_plan or "unknown").strip().lower()
        self.stt_provider = (stt_provider or "deepgram").strip().lower()
        self.llm_provider = (llm_provider or "openai").strip().lower()
        self.tts_provider = (tts_provider or "elevenlabs").strip().lower()
        self._clock = clock or time.perf_counter

        self.stt_ttfb_samples_ms: list[float] = []
        self.llm_ttfb_samples_ms: list[float] = []
        self.tts_ttfb_samples_ms: list[float] = []
        self.e2e_samples_ms: list[float] = []
        self.turn_samples: list[TurnLatencySample] = []
        self.interruptions: int = 0

        self._current_turn: TurnLatencySample | None = None
        self._bot_speaking: bool = False
        self._interruption_recorded_for_utterance: bool = False
        self._seen_frame_ids: set[int] = set()
        self._seen_metric_keys: set[tuple[str, float]] = set()

    async def on_push_frame(self, data: FramePushed) -> None:
        """Pipecat 0.0.94 observer hook invoked whenever a processor pushes a frame."""
        frame = data.frame
        frame_id = getattr(frame, "id", None)
        #MetricsFrame and control frames can traverse multiple links in the pipeline;
        # deduplicate by frame object/id so a single frame is only counted once.
        dedup_key = frame_id if isinstance(frame_id, int) else id(frame)
        if dedup_key in self._seen_frame_ids:
            return
        self._seen_frame_ids.add(dedup_key)

        timestamp_s: float | None = None
        raw_ts = getattr(data, "timestamp", None)
        if isinstance(raw_ts, int) and not isinstance(raw_ts, bool) and raw_ts > 0:
            timestamp_s = float(raw_ts) / 1_000_000_000.0
        elif isinstance(raw_ts, float) and raw_ts > 0:
            timestamp_s = raw_ts / 1_000_000_000.0 if raw_ts >= 1e7 else raw_ts

        source_name = type(data.source).__name__ if getattr(data, "source", None) is not None else ""
        self.observe_frame(frame, source=source_name, timestamp_s=timestamp_s)

    def observe_frame(
        self,
        frame: Frame | Any,
        *,
        source: str = "",
        timestamp_s: float | None = None,
    ) -> None:
        """Process a single frame for latency and interruption tracking."""
        now = float(timestamp_s) if timestamp_s is not None else float(self._clock())

        if isinstance(frame, MetricsFrame):
            self._handle_metrics_frame(frame)
            return

        if isinstance(frame, UserStartedSpeakingFrame):
            if self._bot_speaking and not self._interruption_recorded_for_utterance:
                self.interruptions += 1
                self._interruption_recorded_for_utterance = True
                record_voice_interruption(tenant_plan=self.tenant_plan)
            return

        if isinstance(frame, (InterruptionFrame, StartInterruptionFrame)):
            if not self._interruption_recorded_for_utterance:
                self.interruptions += 1
                self._interruption_recorded_for_utterance = True
                self._bot_speaking = False
                record_voice_interruption(tenant_plan=self.tenant_plan)
            return

        if isinstance(frame, UserStoppedSpeakingFrame):
            self._current_turn = TurnLatencySample(
                turn_index=len(self.turn_samples) + 1,
                user_stopped_at=now,
            )
            self._interruption_recorded_for_utterance = False
            return

        if isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            if self._current_turn is not None and self._current_turn.stt_first_at is None:
                self._current_turn.stt_first_at = now
                delta_s = max(0.0, now - self._current_turn.user_stopped_at)
                if self._current_turn.stt_ttfb_ms is None:
                    self._current_turn.stt_ttfb_ms = round(delta_s * 1000.0, 3)
                    self.stt_ttfb_samples_ms.append(self._current_turn.stt_ttfb_ms)
                    observe_voice_ttfb("stt", delta_s, provider=self.stt_provider)
            return

        if isinstance(frame, (LLMFullResponseStartFrame, LLMTextFrame, TextFrame)):
            if self._current_turn is not None and self._current_turn.llm_first_at is None:
                self._current_turn.llm_first_at = now
                anchor = self._current_turn.stt_first_at or self._current_turn.user_stopped_at
                delta_s = max(0.0, now - anchor)
                if self._current_turn.llm_ttfb_ms is None:
                    self._current_turn.llm_ttfb_ms = round(delta_s * 1000.0, 3)
                    self.llm_ttfb_samples_ms.append(self._current_turn.llm_ttfb_ms)
                    observe_voice_ttfb("llm", delta_s, provider=self.llm_provider)
            return

        if isinstance(frame, TTSStartedFrame):
            if self._current_turn is not None and self._current_turn.tts_first_at is None:
                self._current_turn.tts_first_at = now
                anchor = (
                    self._current_turn.llm_first_at
                    or self._current_turn.stt_first_at
                    or self._current_turn.user_stopped_at
                )
                delta_s = max(0.0, now - anchor)
                if self._current_turn.tts_ttfb_ms is None:
                    self._current_turn.tts_ttfb_ms = round(delta_s * 1000.0, 3)
                    self.tts_ttfb_samples_ms.append(self._current_turn.tts_ttfb_ms)
                    observe_voice_ttfb("tts", delta_s, provider=self.tts_provider)
            return

        if isinstance(frame, (BotStartedSpeakingFrame, TTSAudioRawFrame)):
            self._bot_speaking = True
            self._interruption_recorded_for_utterance = False
            if self._current_turn is not None and self._current_turn.bot_started_at is None:
                if self._current_turn.tts_first_at is None:
                    self._current_turn.tts_first_at = now
                    anchor = (
                        self._current_turn.llm_first_at
                        or self._current_turn.stt_first_at
                        or self._current_turn.user_stopped_at
                    )
                    tts_delta_s = max(0.0, now - anchor)
                    if self._current_turn.tts_ttfb_ms is None:
                        self._current_turn.tts_ttfb_ms = round(tts_delta_s * 1000.0, 3)
                        self.tts_ttfb_samples_ms.append(self._current_turn.tts_ttfb_ms)
                        observe_voice_ttfb("tts", tts_delta_s, provider=self.tts_provider)

                self._current_turn.bot_started_at = now
                e2e_s = max(0.0, now - self._current_turn.user_stopped_at)
                e2e_ms = round(e2e_s * 1000.0, 3)
                self._current_turn.e2e_ms = e2e_ms
                self.e2e_samples_ms.append(e2e_ms)
                self.turn_samples.append(self._current_turn)
                observe_voice_e2e_latency(
                    e2e_s,
                    tenant_plan=self.tenant_plan,
                    llm_provider=self.llm_provider,
                    tts_provider=self.tts_provider,
                )
                self._current_turn = None
            return

        if isinstance(frame, BotStoppedSpeakingFrame):
            self._bot_speaking = False
            self._interruption_recorded_for_utterance = False

    def _handle_metrics_frame(self, frame: MetricsFrame) -> None:
        data_items = getattr(frame, "data", None) or []
        for item in data_items:
            if not isinstance(item, TTFBMetricsData):
                continue
            val_s = float(getattr(item, "value", 0.0) or 0.0)
            if val_s <= 0.0:
                continue
            proc_name = str(getattr(item, "processor", "") or "")
            model_name = str(getattr(item, "model", "") or "")
            dedup = (proc_name, round(val_s, 6))
            if dedup in self._seen_metric_keys:
                continue
            self._seen_metric_keys.add(dedup)

            stage, inferred_provider = _classify_processor_stage(proc_name, model_name)
            if stage is None:
                continue
            ms = round(val_s * 1000.0, 3)
            if stage == "stt":
                provider = inferred_provider if inferred_provider != "unknown" else self.stt_provider
                self.stt_ttfb_samples_ms.append(ms)
                if self._current_turn is not None:
                    self._current_turn.stt_ttfb_ms = ms
                observe_voice_ttfb("stt", val_s, provider=provider)
            elif stage == "llm":
                provider = inferred_provider if inferred_provider != "unknown" else self.llm_provider
                self.llm_ttfb_samples_ms.append(ms)
                if self._current_turn is not None:
                    self._current_turn.llm_ttfb_ms = ms
                observe_voice_ttfb("llm", val_s, provider=provider)
            elif stage == "tts":
                provider = inferred_provider if inferred_provider != "unknown" else self.tts_provider
                self.tts_ttfb_samples_ms.append(ms)
                if self._current_turn is not None:
                    self._current_turn.tts_ttfb_ms = ms
                observe_voice_ttfb("tts", val_s, provider=provider)

    def summarize(
        self,
        call: Call | None = None,
        *,
        call_id: uuid.UUID | None = None,
        tenant_id: uuid.UUID | None = None,
    ) -> CallLatencyStat:
        """Build a :class:`CallLatencyStat` summary from all observed turns."""
        resolved_call_id = call_id or (call.id if call is not None else None) or self.call_id or uuid.uuid4()
        resolved_tenant_id = tenant_id or (call.tenant_id if call is not None else None) or self.tenant_id or uuid.uuid4()

        e2e_p50 = percentile(self.e2e_samples_ms, 50)
        e2e_p95 = percentile(self.e2e_samples_ms, 95)
        e2e_p99 = percentile(self.e2e_samples_ms, 99)
        e2e_max = round(max(self.e2e_samples_ms), 3) if self.e2e_samples_ms else None
        stt_p50 = percentile(self.stt_ttfb_samples_ms, 50)
        llm_p50 = percentile(self.llm_ttfb_samples_ms, 50)
        tts_p50 = percentile(self.tts_ttfb_samples_ms, 50)

        stat = CallLatencyStat(
            id=uuid.uuid4(),
            call_id=resolved_call_id,
            tenant_id=resolved_tenant_id,
            turn_idx=len(self.e2e_samples_ms),
            turns=len(self.e2e_samples_ms),
            stt_ms=stt_p50,
            llm_ttfb_ms=llm_p50,
            tts_ttfb_ms=tts_p50,
            e2e_ms=e2e_p50,
            stt_ttfb_p50_ms=stt_p50,
            stt_ttfb_p95_ms=percentile(self.stt_ttfb_samples_ms, 95),
            llm_ttfb_p50_ms=llm_p50,
            llm_ttfb_p95_ms=percentile(self.llm_ttfb_samples_ms, 95),
            tts_ttfb_p50_ms=tts_p50,
            tts_ttfb_p95_ms=percentile(self.tts_ttfb_samples_ms, 95),
            e2e_p50_ms=e2e_p50,
            e2e_p95_ms=e2e_p95,
            e2e_p99_ms=e2e_p99,
            e2e_max_ms=e2e_max,
            interrupted=self.interruptions > 0,
            interruptions=self.interruptions,
            created_at=datetime.now(timezone.utc),
        )
        if call is not None and e2e_p50 is not None:
            call.avg_response_ms = e2e_p50
        return stat

    async def persist(
        self,
        session: AsyncSession,
        call: Call,
    ) -> CallLatencyStat:
        """Upsert the call's ``CallLatencyStat`` row and update ``call.avg_response_ms``."""
        stat = self.summarize(call)
        existing = (
            await session.execute(
                select(CallLatencyStat).where(CallLatencyStat.call_id == call.id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.turn_idx = stat.turn_idx
            existing.turns = stat.turns
            existing.stt_ms = stat.stt_ms
            existing.llm_ttfb_ms = stat.llm_ttfb_ms
            existing.tts_ttfb_ms = stat.tts_ttfb_ms
            existing.e2e_ms = stat.e2e_ms
            existing.stt_ttfb_p50_ms = stat.stt_ttfb_p50_ms
            existing.stt_ttfb_p95_ms = stat.stt_ttfb_p95_ms
            existing.llm_ttfb_p50_ms = stat.llm_ttfb_p50_ms
            existing.llm_ttfb_p95_ms = stat.llm_ttfb_p95_ms
            existing.tts_ttfb_p50_ms = stat.tts_ttfb_p50_ms
            existing.tts_ttfb_p95_ms = stat.tts_ttfb_p95_ms
            existing.e2e_p50_ms = stat.e2e_p50_ms
            existing.e2e_p95_ms = stat.e2e_p95_ms
            existing.e2e_p99_ms = stat.e2e_p99_ms
            existing.e2e_max_ms = stat.e2e_max_ms
            existing.interrupted = stat.interrupted
            existing.interruptions = stat.interruptions
            await session.flush()
            return existing

        session.add(stat)
        await session.flush()
        log.info(
            "voice.latency.persisted",
            call_id=str(call.id),
            turns=stat.turns,
            e2e_p50_ms=stat.e2e_p50_ms,
            e2e_p95_ms=stat.e2e_p95_ms,
            interruptions=stat.interruptions,
        )
        return stat


class LatencyTrackingProcessor(FrameProcessor):
    """Pass-through processor that feeds observed pipeline frames into ``LatencyObserver``."""

    def __init__(self, observer: LatencyObserver) -> None:
        super().__init__()
        self.observer = observer

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        try:
            self.observer.observe_frame(frame, source=self.name)
        except Exception as exc:
            log.warning("voice.latency.observe_failed", error=str(exc)[:160])
        await self.push_frame(frame, direction)


LatencyObserverProcessor = LatencyTrackingProcessor


async def persist_call_latency_stat(
    session: AsyncSession,
    observer: LatencyObserver,
    call: Call,
) -> CallLatencyStat:
    """Persist the `CallLatencyStat` summary produced by `observer` for `call`."""
    return await observer.persist(session, call)

