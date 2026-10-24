"""Gemini Live (Multimodal Live) client (speech-to-speech).

Google's Gemini Live API is a live WebSocket with a JSON envelope per
message. This client speaks the parts VoxDesk needs:

* the ``setup`` message (model, system instruction, tools, session
  resumption);
* ``realtimeInput`` audio chunks;
* ``serverContent`` audio, turn completion and interruption;
* ``toolCall`` / ``toolResponse`` for function calling;
* ``sessionResumptionUpdate`` handles, so a dropped socket can be resumed
  instead of restarted.

As with the OpenAI client, the wire protocol is translated into
provider-neutral :class:`~app.agent.s2s.s2s_session.S2SEvent` objects, the
socket is re-established with bounded backoff, and the session refuses to
run past its maximum duration.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, AsyncIterator, Callable

from app.agent.s2s.s2s_audio import GEMINI_LIVE_SAMPLE_RATE
from app.agent.s2s.s2s_session import (
    ReconnectPolicy,
    S2SConfigurationError,
    S2SEvent,
    S2SEventType,
    S2SProviderError,
    S2SSession,
)
from app.core.logging import log

DEFAULT_URL = (
    "wss://generativelanguage.googleapis.com/ws/google.ai.generativelanguage."
    "v1beta.GenerativeService.BidiGenerateContent"
)


def _default_connect(url: str, headers: dict[str, str], **kwargs):
    """Open the provider socket (injectable so tests can substitute it)."""
    import websockets

    return websockets.connect(url, extra_headers=headers, **kwargs)


class GeminiLiveSession(S2SSession):
    """A live Gemini Multimodal Live conversation."""

    provider = "gemini_live"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-2.0-flash-exp",
        system_prompt: str = "",
        voice: str = "Puck",
        tools: list[dict[str, Any]] | None = None,
        sample_rate: int = GEMINI_LIVE_SAMPLE_RATE,
        vad_mode: str = "server",
        max_session_seconds: int = 600,
        url: str = DEFAULT_URL,
        reconnect_policy: ReconnectPolicy | None = None,
        connect_factory: Callable[..., Any] | None = None,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            system_prompt=system_prompt,
            voice=voice,
            tools=tools or [],
            sample_rate=sample_rate,
            vad_mode=vad_mode,
            max_session_seconds=max_session_seconds,
            reconnect_policy=reconnect_policy,
            connect_factory=connect_factory,
        )
        self.url = url
        self._ws: Any | None = None
        self._pending_socket: Any | None = None
        self._reader: asyncio.Task | None = None
        self._input_bytes = 0
        self._output_bytes = 0
        self._usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        #: The handle the provider hands out for session resumption.
        self.resumption_handle: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if not self.api_key:
            raise S2SConfigurationError(
                "The Gemini Live session needs an API key", provider=self.provider
            )
        self.started_at = time.monotonic()
        self._reader = asyncio.create_task(self._reader_loop())

    async def connect(self) -> None:
        """Open the socket now, so a dead provider is known before audio flows."""
        if self._ws is not None or self._pending_socket is not None:
            return
        try:
            self._pending_socket = await self._connect()
        except Exception as exc:
            raise S2SProviderError(
                f"The Gemini Live socket could not be opened: {exc}",
                provider=self.provider,
                retryable=True,
            ) from exc

    async def close(self) -> None:
        self._closed = True
        self.ended_at = time.monotonic()
        websocket = self._ws
        self._ws = None
        if websocket is not None:
            try:
                await websocket.close()
            except Exception as exc:  # pragma: no cover - best effort close
                log.debug("s2s.gemini.close_failed", error=str(exc)[:120])
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover - best effort close
                log.debug("s2s.gemini.reader_stopped", error=str(exc)[:120])
            self._reader = None
        self.finish()

    # ------------------------------------------------------------------
    # Audio path
    # ------------------------------------------------------------------
    async def send_audio(self, pcm: bytes, *, sample_rate: int) -> None:
        if not pcm:
            return
        websocket = self._require_socket()
        self._input_bytes += len(pcm)
        await websocket.send(
            json.dumps(
                {
                    "realtime_input": {
                        "media_chunks": [
                            {
                                "mime_type": f"audio/pcm;rate={self.sample_rate}",
                                "data": base64.b64encode(pcm).decode("ascii"),
                            }
                        ]
                    }
                }
            )
        )

    async def commit(self) -> None:
        """Gemini's server VAD decides when a turn ends; nothing to commit."""
        return None

    async def interrupt(self) -> None:
        """Cancel the provider's current response (barge-in)."""
        websocket = self._ws
        if websocket is None:
            return
        try:
            await websocket.send(json.dumps({"clientContent": {"turnComplete": True}}))
        except Exception as exc:  # pragma: no cover - the socket may be closing
            log.debug("s2s.gemini.interrupt_failed", error=str(exc)[:120])

    async def send_tool_result(
        self, call_id: str, name: str, result: dict[str, Any]
    ) -> None:
        websocket = self._require_socket()
        await websocket.send(
            json.dumps(
                {
                    "tool_response": {
                        "function_responses": [
                            {
                                "id": call_id,
                                "name": name,
                                "response": result,
                            }
                        ]
                    }
                }
            )
        )

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    async def events(self) -> AsyncIterator[S2SEvent]:
        while True:
            event = await self._queue.get()
            if event is None:
                return
            yield event

    @property
    def usage(self) -> dict[str, int]:
        return dict(self._usage)

    @property
    def input_bytes(self) -> int:
        return self._input_bytes

    @property
    def output_bytes(self) -> int:
        return self._output_bytes

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _require_socket(self) -> Any:
        if self._ws is None:
            raise S2SProviderError(
                "The Gemini Live socket is not connected", provider=self.provider
            )
        return self._ws

    def _setup_payload(self) -> dict[str, Any]:
        setup: dict[str, Any] = {
            "model": f"models/{self.model}",
            "generation_config": {
                "response_modalities": ["AUDIO"],
                "speech_config": {
                    "voice_config": {"prebuilt_voice_config": {"voice_name": self.voice}}
                },
            },
        }
        if self.system_prompt:
            setup["system_instruction"] = {
                "parts": [{"text": self.system_prompt}]
            }
        if self.tools:
            setup["tools"] = self.tools
        if self.resumption_handle:
            setup["session_resumption"] = {"handle": self.resumption_handle}
        else:
            setup["session_resumption"] = {}
        return {"setup": setup}

    async def _connect(self) -> Any:
        factory = self.connect_factory or _default_connect
        url = self.url
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        return await factory(url, headers)

    async def _reader_loop(self) -> None:
        attempt = 0
        while not self._closed:
            if self.session_expired():
                self.emit(
                    S2SEvent(
                        type=S2SEventType.ERROR,
                        provider=self.provider,
                        model=self.model,
                        error="s2s_session_max_duration_exceeded",
                    )
                )
                self.finish()
                return
            try:
                websocket = self._pending_socket or await self._connect()
                self._pending_socket = None
            except Exception as exc:
                attempt += 1
                if attempt > self.reconnect_policy.max_attempts:
                    self.emit(
                        S2SEvent(
                            type=S2SEventType.ERROR,
                            provider=self.provider,
                            model=self.model,
                            error=f"connect_failed:{type(exc).__name__}",
                        )
                    )
                    self.finish()
                    return
                delay = self.reconnect_policy.delay_for(attempt)
                log.warning(
                    "s2s.gemini.connect_failed",
                    attempt=attempt,
                    delay=delay,
                    error=str(exc)[:160],
                )
                await asyncio.sleep(delay)
                continue

            self._ws = websocket
            attempt = 0
            try:
                await self._configure(websocket)
                await self._consume(websocket)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("s2s.gemini.socket_error", error=str(exc)[:160])
            finally:
                self._ws = None
                try:
                    await websocket.close()
                except Exception:  # pragma: no cover - best effort close
                    pass

            if self._closed:
                return
            attempt += 1
            if attempt > self.reconnect_policy.max_attempts:
                self.emit(
                    S2SEvent(
                        type=S2SEventType.ERROR,
                        provider=self.provider,
                        model=self.model,
                        error="reconnect_exhausted",
                    )
                )
                self.finish()
                return
            delay = self.reconnect_policy.delay_for(attempt)
            log.warning("s2s.gemini.reconnecting", attempt=attempt, delay=delay)
            await asyncio.sleep(delay)

    async def _configure(self, websocket: Any) -> None:
        await websocket.send(json.dumps(self._setup_payload()))

    async def _consume(self, websocket: Any) -> None:
        async for raw in websocket:
            if self._closed:
                return
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(message, dict):
                continue
            for event in self._translate(message):
                self.emit(event)

    def _translate(self, message: dict[str, Any]) -> list[S2SEvent]:
        events: list[S2SEvent] = []
        if "sessionResumptionUpdate" in message:
            update = message.get("sessionResumptionUpdate") or {}
            handle = str(update.get("newHandle") or "")
            if handle:
                self.resumption_handle = handle
            return events
        if "serverContent" in message:
            content = message.get("serverContent") or {}
            if content.get("interrupted"):
                events.append(
                    S2SEvent(
                        type=S2SEventType.INTERRUPTED,
                        provider=self.provider,
                        model=self.model,
                    )
                )
            turn = content.get("modelTurn") or {}
            for part in turn.get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data") or {}
                data = inline.get("data")
                if not data:
                    continue
                try:
                    audio = base64.b64decode(data)
                except (TypeError, ValueError):
                    continue
                self._output_bytes += len(audio)
                events.append(
                    S2SEvent(
                        type=S2SEventType.AUDIO_OUT,
                        audio=audio,
                        sample_rate=self.sample_rate,
                        provider=self.provider,
                        model=self.model,
                        final=False,
                    )
                )
            if content.get("turnComplete"):
                events.append(
                    S2SEvent(
                        type=S2SEventType.TURN_END,
                        provider=self.provider,
                        model=self.model,
                    )
                )
            return events
        if "toolCall" in message:
            call = message.get("toolCall") or {}
            for function_call in call.get("functionCalls") or call.get("function_calls") or []:
                arguments = function_call.get("args")
                if not isinstance(arguments, dict):
                    arguments = {}
                events.append(
                    S2SEvent(
                        type=S2SEventType.TOOL_CALL,
                        tool_call_id=str(function_call.get("id") or ""),
                        tool_name=str(function_call.get("name") or ""),
                        arguments=arguments,
                        provider=self.provider,
                        model=self.model,
                    )
                )
            return events
        if "inputTranscription" in message:
            transcription = message.get("inputTranscription") or {}
            events.append(
                S2SEvent(
                    type=S2SEventType.TRANSCRIPT_IN,
                    text=str(transcription.get("text") or ""),
                    provider=self.provider,
                    model=self.model,
                )
            )
            return events
        if "error" in message:
            error = message.get("error") or {}
            events.append(
                S2SEvent(
                    type=S2SEventType.ERROR,
                    error=str(error.get("message") or "gemini_live_error"),
                    provider=self.provider,
                    model=self.model,
                )
            )
        return events


__all__ = ["DEFAULT_URL", "GeminiLiveSession"]
