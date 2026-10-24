"""OpenAI Realtime API client (speech-to-speech).

The Realtime API is a live WebSocket: the client streams microphone audio
in, the server streams audio and transcripts back, and the model can call
tools in the middle of a turn. This client implements the parts VoxDesk
needs:

* ``session.update`` with the voice, instructions, tools and turn detection;
* ``input_audio_buffer.append`` / ``commit`` / ``clear`` for the audio path;
* ``response.create`` / ``response.cancel`` for turns and barge-in;
* ``conversation.item.create`` to return a tool result;
* the server event vocabulary, translated into provider-neutral
  :class:`~app.agent.s2s.s2s_session.S2SEvent` objects.

The socket is re-established with bounded backoff when the provider drops
it, and the session refuses to run past its maximum duration -- a live call
must not hang on a half-open socket forever.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from typing import Any, AsyncIterator, Callable

from app.agent.s2s.s2s_audio import OPENAI_REALTIME_SAMPLE_RATE
from app.agent.s2s.s2s_session import (
    ReconnectPolicy,
    S2SConfigurationError,
    S2SEvent,
    S2SEventType,
    S2SProviderError,
    S2SSession,
    S2SSessionTimeout,
)
from app.core.logging import log

DEFAULT_URL = "wss://api.openai.com/v1/realtime"


def _default_connect(url: str, headers: dict[str, str], **kwargs):
    """Open the provider socket (injectable so tests can substitute it)."""
    import websockets

    return websockets.connect(url, extra_headers=headers, **kwargs)


class OpenAIRealtimeSession(S2SSession):
    """A live OpenAI Realtime conversation."""

    provider = "openai_realtime"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-realtime-preview",
        system_prompt: str = "",
        voice: str = "alloy",
        tools: list[dict[str, Any]] | None = None,
        sample_rate: int = OPENAI_REALTIME_SAMPLE_RATE,
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

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if not self.api_key:
            raise S2SConfigurationError(
                "The OpenAI Realtime session needs an API key", provider=self.provider
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
                f"The OpenAI Realtime socket could not be opened: {exc}",
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
                log.debug("s2s.openai.close_failed", error=str(exc)[:120])
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except asyncio.CancelledError:
                pass
            except Exception as exc:  # pragma: no cover - best effort close
                log.debug("s2s.openai.reader_stopped", error=str(exc)[:120])
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
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm).decode("ascii"),
                }
            )
        )

    async def commit(self) -> None:
        """Ask the provider to answer the buffered caller audio."""
        websocket = self._require_socket()
        await websocket.send(json.dumps({"type": "input_audio_buffer.commit"}))
        await websocket.send(json.dumps({"type": "response.create"}))

    async def interrupt(self) -> None:
        """Cancel the provider's current response (barge-in)."""
        websocket = self._ws
        if websocket is None:
            return
        try:
            await websocket.send(json.dumps({"type": "response.cancel"}))
            await websocket.send(json.dumps({"type": "input_audio_buffer.clear"}))
        except Exception as exc:  # pragma: no cover - the socket may be closing
            log.debug("s2s.openai.interrupt_failed", error=str(exc)[:120])

    async def send_tool_result(
        self, call_id: str, name: str, result: dict[str, Any]
    ) -> None:
        websocket = self._require_socket()
        await websocket.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": json.dumps(result, separators=(",", ":")),
                    },
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
                "The OpenAI Realtime socket is not connected", provider=self.provider
            )
        return self._ws

    def _session_payload(self) -> dict[str, Any]:
        turn_detection = (
            {"type": "server_vad", "threshold": 0.5, "prefix_padding_ms": 300,
             "silence_duration_ms": 200}
            if self.vad_mode != "manual"
            else None
        )
        session: dict[str, Any] = {
            "modalities": ["text", "audio"],
            "instructions": self.system_prompt,
            "voice": self.voice,
            "input_audio_format": "pcm16",
            "output_audio_format": "pcm16",
            "input_audio_transcription": {"model": "whisper-1"},
        }
        if turn_detection is not None:
            session["turn_detection"] = turn_detection
        if self.tools:
            session["tools"] = self.tools
            session["tool_choice"] = "auto"
        return session

    async def _connect(self) -> Any:
        factory = self.connect_factory or _default_connect
        url = self.url
        if "{model}" in url:
            url = url.replace("{model}", self.model)
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
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
                    "s2s.openai.connect_failed",
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
                log.warning("s2s.openai.socket_error", error=str(exc)[:160])
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
            log.warning("s2s.openai.reconnecting", attempt=attempt, delay=delay)
            await asyncio.sleep(delay)

    async def _configure(self, websocket: Any) -> None:
        await websocket.send(
            json.dumps({"type": "session.update", "session": self._session_payload()})
        )

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
            event = self._translate(message)
            if event is not None:
                self.emit(event)

    def _translate(self, message: dict[str, Any]) -> S2SEvent | None:
        kind = str(message.get("type") or "")
        if kind == "response.output_audio.delta":
            delta = message.get("delta") or ""
            try:
                audio = base64.b64decode(delta)
            except (TypeError, ValueError):
                return None
            self._output_bytes += len(audio)
            return S2SEvent(
                type=S2SEventType.AUDIO_OUT,
                audio=audio,
                sample_rate=self.sample_rate,
                provider=self.provider,
                model=self.model,
                final=False,
            )
        if kind == "response.output_audio_transcript.done":
            return S2SEvent(
                type=S2SEventType.TRANSCRIPT_OUT,
                text=str(message.get("transcript") or ""),
                provider=self.provider,
                model=self.model,
            )
        if kind == "response.output_audio_transcript.delta":
            text = str(message.get("delta") or "")
            if not text:
                return None
            return S2SEvent(
                type=S2SEventType.TRANSCRIPT_OUT,
                text=text,
                provider=self.provider,
                model=self.model,
                final=False,
            )
        if kind == "conversation.item.input_audio_transcription.completed":
            return S2SEvent(
                type=S2SEventType.TRANSCRIPT_IN,
                text=str(message.get("transcript") or ""),
                provider=self.provider,
                model=self.model,
            )
        if kind == "response.function_call_arguments.done":
            raw_arguments = message.get("arguments")
            arguments: dict[str, Any] = {}
            if isinstance(raw_arguments, str) and raw_arguments.strip():
                try:
                    parsed = json.loads(raw_arguments)
                except ValueError:
                    parsed = {}
                if isinstance(parsed, dict):
                    arguments = parsed
            elif isinstance(raw_arguments, dict):
                arguments = raw_arguments
            return S2SEvent(
                type=S2SEventType.TOOL_CALL,
                tool_call_id=str(message.get("call_id") or message.get("id") or ""),
                tool_name=str(message.get("name") or ""),
                arguments=arguments,
                provider=self.provider,
                model=self.model,
            )
        if kind == "input_audio_buffer.speech_started":
            return S2SEvent(
                type=S2SEventType.INTERRUPTED,
                provider=self.provider,
                model=self.model,
            )
        if kind == "response.done":
            response = message.get("response") if isinstance(message.get("response"), dict) else {}
            usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
            self._usage["input_tokens"] = int(usage.get("input_tokens") or 0)
            self._usage["output_tokens"] = int(usage.get("output_tokens") or 0)
            return S2SEvent(
                type=S2SEventType.TURN_END,
                provider=self.provider,
                model=self.model,
            )
        if kind == "error":
            error = message.get("error") if isinstance(message.get("error"), dict) else {}
            return S2SEvent(
                type=S2SEventType.ERROR,
                error=str(error.get("message") or "openai_realtime_error"),
                provider=self.provider,
                model=self.model,
            )
        return None


__all__ = ["DEFAULT_URL", "OpenAIRealtimeSession", "S2SSessionTimeout"]
