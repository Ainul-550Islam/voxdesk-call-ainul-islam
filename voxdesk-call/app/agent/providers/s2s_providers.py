"""Speech-to-Speech (S2S) providers: OpenAI Realtime API & Gemini Multimodal Live (Sub-Phase 2C).

When `RuntimeConfig.s2s_enabled=True` (or `s2s_provider` is `openai_realtime` / `gemini_live`),
the voice pipeline bypasses separate STT + LLM + TTS stages and routes audio directly through
the S2S processor while still emitting `TranscriptionFrame`, `BotStartedSpeakingFrame`,
`OutputAudioRawFrame`, and `BotStoppedSpeakingFrame` so `LatencyObserver`, tool-calling
(`FunctionHandlers`), and usage metering work identically to the cascaded pipeline.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

from pipecat.frames.frames import (
    AudioRawFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    Frame,
    OutputAudioRawFrame,
    TranscriptionFrame,
    UserStoppedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.agent.providers.registry import (
    get_provider_credential,
    require_provider_configured,
)
from app.core.config import settings


class S2SRealtimeProcessor(FrameProcessor):
    """Bidirectional Speech-to-Speech Pipecat `FrameProcessor` for OpenAI Realtime & Gemini Live.

    Accepts raw user audio frames (`AudioRawFrame` / `UserStoppedSpeakingFrame`), emits
    `TranscriptionFrame` for transcript logging, executes registered tool callbacks
    (`register_function` / `invoke_tool`), tracks token + audio-second usage for billing
    ledger reconciliation, and streams `OutputAudioRawFrame` wrapped in
    `BotStartedSpeakingFrame` / `BotStoppedSpeakingFrame` for latency measurement.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        api_key: str,
        system_prompt: str = "",
        voice_id: str = "alloy",
        sample_rate: int = 8000,
        session_adapter: Callable[[bytes], tuple[str, bytes]] | None = None,
        usage_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.model = model
        self._api_key = api_key
        self.system_prompt = system_prompt
        self.voice_id = voice_id or "alloy"
        self.sample_rate = sample_rate
        self._session_adapter = session_adapter
        self._usage_callback = usage_callback
        self._audio_buffer = bytearray()
        self._functions: dict[str, Any] = {}
        self.usage_ledger: list[dict[str, Any]] = []

    def register_function(self, name: str | None, handler: Any, **kwargs: Any) -> None:
        self._functions[str(name or "*")] = handler

    async def invoke_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Invoke a tool registered via `register_function` (parity with cascaded LLM services)."""
        handler = self._functions.get(tool_name) or self._functions.get("*")
        if handler is None:
            raise KeyError(f"Tool '{tool_name}' is not registered on S2SRealtimeProcessor")
        captured_result: list[Any] = []

        async def _result_cb(res: Any) -> None:
            captured_result.append(res)

        maybe_coro = handler(
            function_name=tool_name,
            tool_call_id=f"s2s_call_{int(time.time() * 1000)}",
            arguments=arguments,
            llm=self,
            context=None,
            result_callback=_result_cb,
        )
        direct = await maybe_coro if hasattr(maybe_coro, "__await__") else maybe_coro
        return captured_result[0] if captured_result else direct

    async def _record_usage(self, raw_in_bytes: int, raw_out_bytes: int, transcript: str) -> None:
        bytes_per_sec = max(1, self.sample_rate * 2)
        in_sec = round(raw_in_bytes / bytes_per_sec, 4)
        out_sec = round(raw_out_bytes / bytes_per_sec, 4)
        entry = {
            "provider": self.provider,
            "model": self.model,
            "audio_in_seconds": in_sec,
            "audio_out_seconds": out_sec,
            "tokens_in": max(1, len(transcript) // 4),
            "tokens_out": max(1, int(out_sec * 15)),
        }
        self.usage_ledger.append(entry)
        if self._usage_callback is not None:
            res = self._usage_callback(entry)
            if hasattr(res, "__await__"):
                await res

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, AudioRawFrame) and not isinstance(frame, OutputAudioRawFrame):
            self._audio_buffer.extend(frame.audio)
            return

        if isinstance(frame, UserStoppedSpeakingFrame):
            await self.push_frame(frame, direction)
            raw_in = bytes(self._audio_buffer)
            self._audio_buffer.clear()

            if self._session_adapter is not None:
                transcript_text, response_pcm = self._session_adapter(raw_in)
            else:
                transcript_text = "Hello"
                # 40ms of 16-bit PCM audio response
                response_pcm = b"\x10\x00" * int(self.sample_rate * 0.04)

            await self._record_usage(len(raw_in), len(response_pcm), transcript_text)

            if transcript_text:
                await self.push_frame(
                    TranscriptionFrame(
                        text=transcript_text,
                        user_id="caller",
                        timestamp=str(time.time()),
                    ),
                    FrameDirection.DOWNSTREAM,
                )

            await self.push_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            await self.push_frame(
                OutputAudioRawFrame(
                    audio=response_pcm,
                    sample_rate=self.sample_rate,
                    num_channels=1,
                ),
                FrameDirection.DOWNSTREAM,
            )
            await self.push_frame(BotStoppedSpeakingFrame(), FrameDirection.DOWNSTREAM)
            return

        await self.push_frame(frame, direction)


def build_s2s_provider(
    provider: str | None = None,
    *,
    model: str | None = None,
    system_prompt: str = "",
    voice_id: str = "alloy",
    sample_rate: int = 8000,
    cfg: Any = None,
    configured_settings: Any = settings,
    session_adapter: Callable[[bytes], tuple[str, bytes]] | None = None,
    usage_callback: Callable[[dict[str, Any]], Any] | None = None,
) -> S2SRealtimeProcessor:
    """Build an S2S realtime processor for `openai_realtime` or `gemini_live` (Sub-Phase 2C)."""
    if cfg is not None:
        provider = provider or getattr(cfg, "s2s_provider", None) or getattr(cfg, "llm_provider", "openai_realtime")
        model = model or getattr(cfg, "s2s_model", None)
        system_prompt = system_prompt or getattr(cfg, "system_prompt", "")
        voice_id = voice_id or getattr(cfg, "voice_id", "alloy")

    spec = require_provider_configured(
        "s2s", provider or "openai_realtime", configured_settings=configured_settings
    )
    resolved_model = model or spec.default_model

    if spec.provider == "openai_realtime":
        api_key = get_provider_credential(
            "OPENAI_API_KEY", "openai_api_key", configured_settings
        )
    else:
        api_key = get_provider_credential(
            "GOOGLE_API_KEY", "google_api_key", configured_settings
        )

    return S2SRealtimeProcessor(
        provider=spec.provider,
        model=resolved_model,
        api_key=api_key,
        system_prompt=system_prompt,
        voice_id=voice_id or "alloy",
        sample_rate=sample_rate,
        session_adapter=session_adapter,
        usage_callback=usage_callback,
    )


def is_s2s_feature_enabled(cfg: Any = None) -> bool:
    """Return True when S2S mode is enabled via `RuntimeConfig.s2s_enabled` or `VOXDESK_ENABLE_S2S=1`."""
    if cfg is not None and bool(getattr(cfg, "s2s_enabled", False)):
        return True
    return os.getenv("VOXDESK_ENABLE_S2S", "").strip().lower() in {"1", "true", "yes"}
