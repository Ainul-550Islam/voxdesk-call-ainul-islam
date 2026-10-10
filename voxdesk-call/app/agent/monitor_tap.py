# File: app/agent/monitor_tap.py — FrameProcessor tap for live call listen & whisper-to-AI guidance
"""Live monitoring pipeline tap and whisper-to-AI guidance injector (Part 4 / Gate G5).

``MonitorTap`` is a Pipecat ``FrameProcessor`` inserted into the live voice pipeline:
  1. When no monitoring sessions or WebSocket subscribers exist for ``call_id``
     (``monitor_bus.has_listeners(call_id) is False``), ``process_frame`` performs a
     single boolean check and pushes the frame downstream with zero overhead.
  2. While a supervisor session is active, caller and agent PCM16 frames and
     transcripts are teed to ``monitor_bus``.
  3. ``inject_guidance(text)`` constructs an ``LLMMessagesAppendFrame`` (plus
     ``LLMRunFrame`` when ``run_llm=True``) and injects the supervisor's whisper
     into the AI agent's conversation context so the next model turn reflects it.
"""

from __future__ import annotations

import uuid
from typing import Any, Awaitable, Callable

from pipecat.frames.frames import (
    AudioRawFrame,
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterimTranscriptionFrame,
    LLMMessagesAppendFrame,
    LLMRunFrame,
    LLMTextFrame,
    OutputAudioRawFrame,
    TextFrame,
    TranscriptionFrame,
    TTSAudioRawFrame,
    TTSTextFrame,
)
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.core.logging import log
from app.telephony.monitor_bus import MonitorBus, get_monitor_bus

FramePusher = Callable[[Frame], Awaitable[None]]

_ACTIVE_TAPS: dict[str, "MonitorTap"] = {}


def get_active_monitor_tap(call_id: str | uuid.UUID) -> "MonitorTap | None":
    return _ACTIVE_TAPS.get(str(call_id))


def register_active_monitor_tap(call_id: str | uuid.UUID, tap: "MonitorTap") -> None:
    _ACTIVE_TAPS[str(call_id)] = tap


def unregister_active_monitor_tap(call_id: str | uuid.UUID, tap: "MonitorTap | None" = None) -> None:
    cid = str(call_id)
    current = _ACTIVE_TAPS.get(cid)
    if tap is None or current is tap:
        _ACTIVE_TAPS.pop(cid, None)


class MonitorOutputTap(FrameProcessor):
    """Companion processor placed after TTS to tee outbound agent PCM and spoken text."""

    def __init__(self, parent: "MonitorTap", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._parent = parent

    async def process_frame(
        self,
        frame: Frame,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
    ) -> None:
        await super().process_frame(frame, direction)
        if self._parent.has_active_session():
            await self._parent.publish_agent_output_frame(frame)
        else:
            self._parent.skipped_frames += 1
        await self.push_frame(frame, direction)


class MonitorTap(FrameProcessor):
    """Pipeline FrameProcessor that tees caller/agent audio + transcripts and injects AI guidance."""

    def __init__(
        self,
        call_id: str | uuid.UUID,
        *,
        tenant_id: str | uuid.UUID = "",
        bus: MonitorBus | None = None,
        context: OpenAILLMContext | None = None,
        frame_pusher: FramePusher | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.call_id = str(call_id)
        self.tenant_id = str(tenant_id or "")
        self._bus: MonitorBus = bus or get_monitor_bus()
        self._context = context
        self._frame_pusher = frame_pusher
        self._output_tap: MonitorOutputTap | None = None
        self.published_audio_frames: int = 0
        self.published_transcripts: int = 0
        self.skipped_frames: int = 0
        self.injected_guidances: list[dict[str, Any]] = []

        register_active_monitor_tap(self.call_id, self)
        self._bus.register_guidance_handler(self.call_id, self._on_bus_guidance)

    @property
    def bus(self) -> MonitorBus:
        return self._bus

    def has_active_session(self) -> bool:
        """Fast check: True only when at least one supervisor session/subscriber is listening."""
        return self._bus.has_listeners(self.call_id)

    def output_tap(self) -> MonitorOutputTap:
        """Return the companion post-TTS output processor for the pipeline."""
        if self._output_tap is None:
            self._output_tap = MonitorOutputTap(self)
        return self._output_tap

    async def _on_bus_guidance(self, text: str, meta: dict[str, Any]) -> None:
        run_llm = bool(meta.get("run_llm", True))
        await self.inject_guidance(text, run_llm=run_llm)

    async def publish_agent_output_frame(self, frame: Frame) -> None:
        """Publish an outbound agent audio or text frame when monitoring is active."""
        if not self.has_active_session():
            self.skipped_frames += 1
            return

        if isinstance(frame, (TTSAudioRawFrame, OutputAudioRawFrame)):
            audio = getattr(frame, "audio", b"") or b""
            if audio:
                await self._bus.publish_audio(
                    self.call_id,
                    audio,
                    speaker="agent",
                    sample_rate=int(getattr(frame, "sample_rate", 16000) or 16000),
                    num_channels=int(getattr(frame, "num_channels", 1) or 1),
                )
                self.published_audio_frames += 1
        elif (
            isinstance(frame, (TTSTextFrame, TextFrame))
            and not isinstance(
                frame,
                (TranscriptionFrame, InterimTranscriptionFrame, LLMTextFrame),
            )
        ):
            text = (getattr(frame, "text", "") or "").strip()
            if text:
                await self._bus.publish_transcript(
                    self.call_id,
                    text,
                    speaker="agent",
                    is_final=True,
                )
                self.published_transcripts += 1

    async def process_frame(
        self,
        frame: Frame,
        direction: FrameDirection = FrameDirection.DOWNSTREAM,
    ) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, (EndFrame, CancelFrame)):
            await self.push_frame(frame, direction)
            await self.close()
            return

        # Fast-path zero overhead when no supervisor session/listener is active
        if not self.has_active_session():
            self.skipped_frames += 1
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InputAudioRawFrame):
            audio = getattr(frame, "audio", b"") or b""
            if audio:
                await self._bus.publish_audio(
                    self.call_id,
                    audio,
                    speaker="caller",
                    sample_rate=int(getattr(frame, "sample_rate", 16000) or 16000),
                    num_channels=int(getattr(frame, "num_channels", 1) or 1),
                )
                self.published_audio_frames += 1
        elif isinstance(frame, (TTSAudioRawFrame, OutputAudioRawFrame)):
            audio = getattr(frame, "audio", b"") or b""
            if audio:
                await self._bus.publish_audio(
                    self.call_id,
                    audio,
                    speaker="agent",
                    sample_rate=int(getattr(frame, "sample_rate", 16000) or 16000),
                    num_channels=int(getattr(frame, "num_channels", 1) or 1),
                )
                self.published_audio_frames += 1
        elif isinstance(frame, AudioRawFrame):
            audio = getattr(frame, "audio", b"") or b""
            if audio:
                await self._bus.publish_audio(
                    self.call_id,
                    audio,
                    speaker="caller",
                    sample_rate=int(getattr(frame, "sample_rate", 16000) or 16000),
                    num_channels=int(getattr(frame, "num_channels", 1) or 1),
                )
                self.published_audio_frames += 1
        elif isinstance(frame, TranscriptionFrame) and not isinstance(
            frame, InterimTranscriptionFrame
        ):
            text = (getattr(frame, "text", "") or "").strip()
            if text:
                await self._bus.publish_transcript(
                    self.call_id,
                    text,
                    speaker="caller",
                    is_final=True,
                )
                self.published_transcripts += 1
        elif (
            isinstance(frame, (TTSTextFrame, TextFrame))
            and not isinstance(
                frame,
                (TranscriptionFrame, InterimTranscriptionFrame, LLMTextFrame),
            )
            and self._output_tap is None
        ):
            text = (getattr(frame, "text", "") or "").strip()
            if text:
                await self._bus.publish_transcript(
                    self.call_id,
                    text,
                    speaker="agent",
                    is_final=True,
                )
                self.published_transcripts += 1

        await self.push_frame(frame, direction)

    async def inject_guidance(
        self,
        text: str,
        *,
        run_llm: bool = True,
        role: str = "system",
    ) -> list[Frame]:
        """Inject supervisor whisper-to-AI guidance into the agent context.

        Emits ``LLMMessagesAppendFrame`` (and ``LLMRunFrame`` when ``run_llm=True``)
        downstream so ``LLMUserContextAggregator`` appends the supervisor instruction
        and triggers the next model turn when requested.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            raise ValueError("Guidance text must not be empty")

        guidance_message = {
            "role": role,
            "content": f"[Supervisor guidance]: {cleaned}",
        }
        append_frame = LLMMessagesAppendFrame(
            messages=[guidance_message],
            run_llm=run_llm,
        )
        emitted: list[Frame] = [append_frame]
        if run_llm:
            emitted.append(LLMRunFrame())

        self.injected_guidances.append(guidance_message)

        # Push frames downstream (or via task frame_pusher if explicitly wired).
        if self._frame_pusher is not None:
            for item in emitted:
                await self._frame_pusher(item)
        else:
            for item in emitted:
                await self.push_frame(item, FrameDirection.DOWNSTREAM)

        # Ensure the OpenAILLMContext reflects the guidance immediately when no
        # running LLMUserContextAggregator has processed the frame yet.
        if self._context is not None and getattr(self, "_next", None) is None:
            existing = self._context.get_messages()
            if not existing or existing[-1] != guidance_message:
                self._context.add_message(guidance_message)

        log.info(
            "monitor_tap.guidance_injected",
            call_id=self.call_id,
            run_llm=run_llm,
            char_length=len(cleaned),
        )
        return emitted

    async def close(self) -> None:
        self._bus.unregister_guidance_handler(self.call_id)
        unregister_active_monitor_tap(self.call_id, self)
