"""Provider-neutral turn orchestration used by the realtime voice runtime."""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from app.agent.errors import ProviderError
from app.agent.stt_stream import STTEvent
from app.core.logging import log
from app.tts.engine import TTSEngine
from app.tts.provider import TTSChunk, TTSRequest

MAX_PENDING_TOOL_CALLS = 8
MAX_AUDIO_QUEUE = 32


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class AssistantResponse:
    text: str
    tool_calls: tuple[ToolCall, ...] = ()


class LLMResponder(Protocol):
    async def respond(self, text: str, *, context: list[dict[str, str]]) -> AssistantResponse: ...


class AudioSink(Protocol):
    async def write(self, chunk: TTSChunk) -> None: ...


@dataclass
class VoiceTurnState:
    partial_text: str = ""
    final_text: str = ""
    generation: int = 0
    interruption_count: int = 0
    provider_retries: int = 0
    turns: list[dict[str, str]] = field(default_factory=list)


class VoiceAgentLoop:
    """STT finalization → authorized tools → cancellable TTS output.

    The loop consumes an STT event iterator and writes directly to a bounded
    sink. It never creates an unbounded queue or a background task per turn.
    ``interrupt`` increments a generation so chunks from a stale assistant
    response are discarded even if a provider yields one final buffered chunk.
    """

    def __init__(
        self,
        *,
        tts: TTSEngine | None = None,
        tool_executor: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
        max_tool_calls: int = MAX_PENDING_TOOL_CALLS,
    ) -> None:
        self.tts = tts or TTSEngine()
        self.tool_executor = tool_executor
        self.max_tool_calls = max(1, min(max_tool_calls, MAX_PENDING_TOOL_CALLS))
        self._cancel_event = asyncio.Event()
        self._tts_request_id: str | None = None
        self.state = VoiceTurnState()

    async def interrupt(self) -> None:
        self.state.generation += 1
        self.state.interruption_count += 1
        self._cancel_event.set()
        if self._tts_request_id:
            await self.tts.cancel(self._tts_request_id)
        self._cancel_event = asyncio.Event()

    async def close(self) -> None:
        self._cancel_event.set()
        if self._tts_request_id:
            await self.tts.cancel(self._tts_request_id)

    async def handle_call(self, session_context: dict[str, Any]) -> VoiceTurnState:
        """Run a bounded async event source supplied by the live transport.

        ``session_context`` is intentionally explicit so the existing Twilio
        and Pipecat pipeline can remain authoritative while tests and other
        transports use the same turn semantics. Required keys are
        ``stt_events``, ``llm``, and ``audio_sink``.
        """
        events = session_context.get("stt_events")
        llm = session_context.get("llm")
        sink = session_context.get("audio_sink")
        if events is None or llm is None or sink is None:
            raise ValueError("voice session requires stt_events, llm, and audio_sink")
        turn_task: asyncio.Task | None = None
        try:
            async for event in events:
                if self._cancel_event.is_set():
                    break
                if not isinstance(event, STTEvent):
                    raise TypeError("voice runtime received an invalid STT event")
                if turn_task is not None and turn_task.done():
                    await asyncio.gather(turn_task, return_exceptions=False)
                    turn_task = None
                if not event.is_final:
                    self.state.partial_text = event.text
                    if event.text.strip() and turn_task is not None and session_context.get("barge_in", True):
                        await self._stop_turn(turn_task)
                        turn_task = None
                    continue
                if not event.text.strip() or event.text.strip() == self.state.final_text.strip():
                    continue
                if turn_task is not None:
                    await self._stop_turn(turn_task)
                    turn_task = None
                turn_task = asyncio.create_task(
                    self._run_turn(event.text, llm, sink, session_context),
                    name=f"voice-turn-{event.request_id}",
                )
            if turn_task is not None:
                await asyncio.gather(turn_task, return_exceptions=False)
        finally:
            if turn_task is not None and not turn_task.done():
                await self._stop_turn(turn_task)
            elif turn_task is not None:
                await asyncio.gather(turn_task, return_exceptions=True)
        return self.state

    async def _stop_turn(self, turn_task: asyncio.Task) -> None:
        await self.interrupt()
        if not turn_task.done():
            turn_task.cancel()
        await asyncio.gather(turn_task, return_exceptions=True)

    async def _run_turn(self, text: str, llm: LLMResponder, sink: AudioSink, session_context: dict[str, Any]) -> None:
        generation = self.state.generation
        self.state.final_text = text
        self.state.partial_text = ""
        self.state.turns.append({"role": "user", "content": text})
        started = time.perf_counter()
        try:
            response = await llm.respond(text, context=list(self.state.turns))
            if generation != self.state.generation:
                return
            tool_calls = response.tool_calls[: self.max_tool_calls]
            for call in tool_calls:
                if self.tool_executor is None:
                    tool_result = {"ok": False, "error": "tool execution is unavailable"}
                else:
                    tool_result = await self.tool_executor(call.name, call.arguments)
                self.state.turns.append(
                    {"role": "tool", "content": str({"name": call.name, "result": tool_result})}
                )
            if tool_calls:
                response = await llm.respond(text, context=list(self.state.turns))
            if generation != self.state.generation or self._cancel_event.is_set():
                return
            self.state.turns.append({"role": "assistant", "content": response.text})
            request = TTSRequest(
                text=response.text,
                voice_id=session_context["voice_id"],
                provider=session_context.get("tts_provider", "elevenlabs"),
                model=session_context.get("tts_model", "eleven_flash_v2_5"),
                language=session_context.get("language", "en-US"),
                sample_rate=session_context.get("sample_rate", 8000),
                encoding=session_context.get("encoding", "pcm_mulaw"),
                request_id=session_context.get("request_id") or str(uuid.uuid4()),
                session_id=session_context.get("session_id", ""),
                tenant_id=session_context.get("tenant_id", ""),
                metadata=session_context.get("metadata", {}),
                cancel_event=self._cancel_event,
            )
            self._tts_request_id = request.request_id
            async for chunk in self.tts.stream(request, policy=session_context.get("tts_policy")):
                if generation != self.state.generation or self._cancel_event.is_set():
                    break
                await sink.write(chunk)
        except asyncio.CancelledError:
            raise
        except ProviderError:
            raise
        finally:
            self._tts_request_id = None
            log.info(
                "voice.turn.completed",
                session_id=session_context.get("session_id", ""),
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
                interrupted=generation != self.state.generation,
            )
