"""Provider-neutral STT streaming contract used by the voice runtime."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Protocol

import websockets

from app.providers.compatibility import websocket_header_keyword
from app.providers.errors import ProviderCompatibilityError
from app.observability.runtime import observe_provider_error
from app.agent.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderInvalidRequestError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)


@dataclass(frozen=True)
class STTProviderCapabilities:
    provider: str
    streaming: bool
    interim_results: bool
    interruption: bool
    languages: tuple[str, ...]


@dataclass(frozen=True)
class STTStreamRequest:
    language: str = "en-US"
    model: str = "nova-3"
    sample_rate: int = 8000
    encoding: str = "mulaw"
    provider: str = "deepgram"
    request_id: str = ""
    session_id: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 10.0
    cancel_event: asyncio.Event | None = None
    replay_audio: Callable[[], AsyncIterator[bytes]] | None = None


@dataclass(frozen=True)
class STTEvent:
    text: str
    is_final: bool
    confidence: float | None
    start_seconds: float | None
    end_seconds: float | None
    language: str
    provider: str
    request_id: str
    speech_final: bool = False
    provider_latency_ms: float | None = None
    metadata: dict[str, str] = field(default_factory=dict)


class STTProvider(Protocol):
    name: str
    capabilities: STTProviderCapabilities

    async def stream(
        self, audio: AsyncIterator[bytes], request: STTStreamRequest
    ) -> AsyncIterator[STTEvent]: ...

    async def cancel(self, request_id: str) -> None: ...


class DeepgramStreamingProvider:
    name = "deepgram"
    capabilities = STTProviderCapabilities(
        provider=name,
        streaming=True,
        interim_results=True,
        interruption=True,
        languages=("en", "en-US", "en-GB", "es", "fr", "de", "bn"),
    )

    def __init__(self, api_key: str, *, endpoint: str = "wss://api.deepgram.com") -> None:
        if not api_key.strip():
            raise ProviderConfigurationError("Deepgram STT API key is not configured", provider=self.name)
        try:
            self._headers_keyword = websocket_header_keyword(websockets.connect)
        except ProviderCompatibilityError as exc:
            raise ProviderConfigurationError("Deepgram WebSocket client API is incompatible", provider=self.name) from exc
        self._api_key = api_key
        self._endpoint = endpoint.rstrip("/")
        self._active: dict[str, object] = {}

    async def stream(
        self, audio: AsyncIterator[bytes], request: STTStreamRequest
    ) -> AsyncIterator[STTEvent]:
        if request.encoding not in {"mulaw", "linear16"}:
            raise ProviderInvalidRequestError("unsupported STT audio encoding", provider=self.name)
        params = (
            f"encoding={request.encoding}&sample_rate={request.sample_rate}"
            f"&language={request.language}&model={request.model}"
            "&interim_results=true&smart_format=true&endpointing=250"
        )
        websocket = None
        request_id = request.request_id or str(uuid.uuid4())
        started = time.perf_counter()
        try:
            websocket = await asyncio.wait_for(
                websockets.connect(
                    f"{self._endpoint}/v1/listen?{params}",
                    **{self._headers_keyword: {"Authorization": f"Token {self._api_key}"}},
                    open_timeout=request.timeout_seconds,
                    close_timeout=2,
                ),
                timeout=request.timeout_seconds,
            )
            self._active[request_id] = websocket
            producer = asyncio.create_task(self._send_audio(websocket, audio, request.cancel_event))
            try:
                while True:
                    if request.cancel_event and request.cancel_event.is_set():
                        raise asyncio.CancelledError
                    raw = await asyncio.wait_for(websocket.recv(), timeout=request.timeout_seconds)
                    message = json.loads(raw)
                    if message.get("type") == "Metadata":
                        continue
                    if message.get("type") == "Error":
                        raise ProviderUnavailableError("STT provider returned an error", provider=self.name)
                    channel = (message.get("channel") or {}).get("alternatives") or []
                    alternative = channel[0] if channel else {}
                    text = str(alternative.get("transcript") or "")
                    if not text:
                        if message.get("type") == "UtteranceEnd":
                            yield STTEvent("", True, None, None, None, request.language, self.name, request_id, True)
                        continue
                    words = alternative.get("words") or []
                    start = words[0].get("start") if words else None
                    end = words[-1].get("end") if words else None
                    yield STTEvent(
                        text=text,
                        is_final=bool(message.get("is_final")),
                        confidence=alternative.get("confidence"),
                        start_seconds=start,
                        end_seconds=end,
                        language=request.language,
                        provider=self.name,
                        request_id=request_id,
                        speech_final=bool(message.get("speech_final")),
                        provider_latency_ms=(time.perf_counter() - started) * 1000,
                        metadata={"session_id": request.session_id} if request.session_id else {},
                    )
                    if message.get("type") == "Close":
                        return
            finally:
                producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            error = ProviderTimeoutError("STT provider timed out", provider=self.name)
            observe_provider_error(self.name, error)
            raise error from exc
        except websockets.exceptions.InvalidStatus as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 401:
                error = ProviderAuthenticationError("STT credentials were rejected", provider=self.name)
            elif status == 429:
                error = ProviderRateLimitedError(provider=self.name)
            else:
                error = ProviderUnavailableError("STT provider rejected the connection", provider=self.name)
            observe_provider_error(self.name, error)
            raise error from exc
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            error = ProviderUnavailableError("STT provider connection failed", provider=self.name)
            observe_provider_error(self.name, error)
            raise error from exc
        finally:
            self._active.pop(request_id, None)
            if websocket is not None:
                try:
                    await websocket.close()
                except Exception as close_error:
                    # Closing is best effort; never mask the provider failure.
                    _ = close_error

    async def _send_audio(self, websocket, audio: AsyncIterator[bytes], cancel_event: asyncio.Event | None) -> None:
        async for frame in audio:
            if cancel_event and cancel_event.is_set():
                return
            if frame:
                await websocket.send(frame)
        await websocket.send(json.dumps({"type": "CloseStream"}))

    async def cancel(self, request_id: str) -> None:
        websocket = self._active.get(request_id)
        if websocket is not None:
            await websocket.close(code=1000, reason="cancelled")
