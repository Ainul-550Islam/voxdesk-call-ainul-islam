"""Provider-neutral streaming TTS contracts and the real ElevenLabs adapter."""
from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol
from urllib.parse import quote

import httpx
import websockets

from app.agent.errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderInvalidRequestError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    UnsupportedProviderFeatureError,
)
from app.core.logging import log


@dataclass(frozen=True)
class TTSProviderCapabilities:
    provider: str
    streaming: bool
    cancellation: bool
    multilingual: bool
    voice_clone_compatible: bool
    sample_rates: tuple[int, ...]
    encodings: tuple[str, ...]


@dataclass(frozen=True)
class TTSRequest:
    text: str
    voice_id: str
    provider: str = "elevenlabs"
    model: str = "eleven_flash_v2_5"
    language: str = "en-US"
    sample_rate: int = 8000
    encoding: str = "pcm_mulaw"
    speed: float = 1.0
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    tenant_id: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 10.0
    cancel_event: asyncio.Event | None = None


@dataclass(frozen=True)
class TTSChunk:
    audio: bytes
    sequence: int
    request_id: str
    provider: str
    sample_rate: int
    encoding: str
    is_final: bool = False
    provider_latency_ms: float | None = None
    usage_characters: int | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class TTSProviderHealth:
    provider: str
    healthy: bool
    latency_ms: float
    request_id: str
    message: str = ""


class TTSProvider(Protocol):
    name: str
    capabilities: TTSProviderCapabilities

    async def stream(self, request: TTSRequest) -> AsyncIterator[TTSChunk]: ...
    async def cancel(self, request_id: str) -> None: ...
    async def health(self) -> TTSProviderHealth: ...
    async def validate_voice(self, provider_voice_id: str) -> None: ...


class ElevenLabsStreamingProvider:
    """ElevenLabs WebSocket streaming adapter.

    This adapter intentionally speaks the provider protocol directly rather
    than exposing ElevenLabs response objects to the engine. The websocket is
    closed in the generator's ``finally`` path, including cancellation.
    """

    name = "elevenlabs"
    capabilities = TTSProviderCapabilities(
        provider=name,
        streaming=True,
        cancellation=True,
        multilingual=True,
        voice_clone_compatible=True,
        sample_rates=(8000, 16000, 22050, 44100),
        encodings=("pcm_mulaw", "pcm_s16le", "mp3"),
    )

    def __init__(self, api_key: str, *, endpoint: str = "wss://api.elevenlabs.io") -> None:
        if not api_key.strip():
            raise ProviderConfigurationError(
                "ElevenLabs TTS API key is not configured", provider=self.name
            )
        self._api_key = api_key
        self._endpoint = endpoint.rstrip("/")
        self._active: dict[str, Any] = {}

    def _output_format(self, request: TTSRequest) -> str:
        if request.encoding == "pcm_mulaw" and request.sample_rate == 8000:
            return "ulaw_8000"
        if request.encoding == "pcm_s16le":
            return f"pcm_{request.sample_rate}"
        if request.encoding == "mp3":
            return "mp3_44100_128"
        raise UnsupportedProviderFeatureError(
            "requested TTS encoding/sample rate is not supported",
            provider=self.name,
        )

    async def stream(self, request: TTSRequest) -> AsyncIterator[TTSChunk]:
        if not request.text.strip():
            raise ProviderInvalidRequestError("TTS text is empty", provider=self.name)
        if not request.voice_id.strip():
            raise ProviderInvalidRequestError("TTS voice id is empty", provider=self.name)
        output_format = self._output_format(request)
        url = (
            f"{self._endpoint}/v1/text-to-speech/{request.voice_id}/stream-input"
            f"?model_id={request.model}&output_format={output_format}"
        )
        started = time.perf_counter()
        first_audio: float | None = None
        sequence = 0
        websocket = None
        try:
            websocket = await asyncio.wait_for(
                websockets.connect(url, open_timeout=request.timeout_seconds, close_timeout=2),
                timeout=request.timeout_seconds,
            )
            self._active[request.request_id] = websocket
            await websocket.send(
                json.dumps(
                    {
                        "text": " ",
                        "xi_api_key": self._api_key,
                        "model_id": request.model,
                        "output_format": output_format,
                        "voice_settings": {"speed": request.speed},
                        "generation_config": {"chunk_length_schedule": [50, 120, 160, 250]},
                    }
                )
            )
            await websocket.send(json.dumps({"text": request.text, "try_trigger_generation": True}))
            await websocket.send(json.dumps({"text": ""}))
            while True:
                if request.cancel_event and request.cancel_event.is_set():
                    raise asyncio.CancelledError
                raw = await asyncio.wait_for(websocket.recv(), timeout=request.timeout_seconds)
                message = json.loads(raw)
                if message.get("error"):
                    self._raise_provider_error(message["error"])
                audio_b64 = message.get("audio")
                if audio_b64:
                    try:
                        audio = base64.b64decode(audio_b64, validate=True)
                    except (ValueError, TypeError) as exc:
                        raise ProviderUnavailableError(
                            "provider returned malformed audio", provider=self.name
                        ) from exc
                    if audio:
                        if first_audio is None:
                            first_audio = time.perf_counter()
                        yield TTSChunk(
                            audio=audio,
                            sequence=sequence,
                            request_id=request.request_id,
                            provider=self.name,
                            sample_rate=request.sample_rate,
                            encoding=request.encoding,
                            provider_latency_ms=(first_audio - started) * 1000
                            if sequence == 0
                            else None,
                            usage_characters=len(request.text) if sequence == 0 else None,
                            metadata=request.metadata,
                        )
                        sequence += 1
                if message.get("isFinal") or message.get("is_final"):
                    yield TTSChunk(
                        audio=b"",
                        sequence=sequence,
                        request_id=request.request_id,
                        provider=self.name,
                        sample_rate=request.sample_rate,
                        encoding=request.encoding,
                        is_final=True,
                        metadata=request.metadata,
                    )
                    return
        except asyncio.CancelledError:
            raise
        except asyncio.TimeoutError as exc:
            raise ProviderTimeoutError("TTS provider timed out", provider=self.name) from exc
        except (ProviderInvalidRequestError, ProviderUnavailableError, ProviderRateLimitedError):
            raise
        except websockets.exceptions.InvalidStatus as exc:
            status = getattr(exc, "response", None)
            status_code = getattr(status, "status_code", None)
            if status_code == 401:
                raise ProviderAuthenticationError("TTS credentials were rejected", provider=self.name) from exc
            if status_code == 429:
                raise ProviderRateLimitedError(provider=self.name) from exc
            raise ProviderUnavailableError("TTS provider rejected the connection", provider=self.name) from exc
        except (OSError, websockets.exceptions.WebSocketException) as exc:
            raise ProviderUnavailableError("TTS provider connection failed", provider=self.name) from exc
        finally:
            self._active.pop(request.request_id, None)
            if websocket is not None:
                try:
                    await websocket.close()
                except Exception as close_error:
                    log.debug(
                        "tts.provider_close_failed",
                        provider=self.name,
                        error_type=type(close_error).__name__,
                    )

    async def validate_voice(self, provider_voice_id: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                response = await client.get(
                    f"https://api.elevenlabs.io/v1/voices/{quote(provider_voice_id, safe='')}",
                    headers={"xi-api-key": self._api_key},
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("voice validation provider timed out", provider=self.name) from exc
        except httpx.TransportError as exc:
            raise ProviderUnavailableError("voice validation provider is unreachable", provider=self.name) from exc
        if response.status_code == 401:
            raise ProviderAuthenticationError("voice validation credentials were rejected", provider=self.name)
        if response.status_code == 404:
            raise ProviderInvalidRequestError("provider voice was not found", provider=self.name)
        if response.status_code == 429:
            raise ProviderRateLimitedError(provider=self.name)
        if response.status_code >= 500:
            raise ProviderUnavailableError("voice validation provider failed", provider=self.name)
        if response.status_code >= 400:
            raise ProviderInvalidRequestError("provider rejected voice validation", provider=self.name)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnavailableError("voice validation returned malformed JSON", provider=self.name) from exc
        if payload.get("voice_id") != provider_voice_id:
            raise ProviderUnavailableError("voice validation returned a mismatched voice id", provider=self.name)

    async def cancel(self, request_id: str) -> None:
        websocket = self._active.get(request_id)
        if websocket is not None:
            await websocket.close(code=1000, reason="cancelled")

    async def health(self) -> TTSProviderHealth:
        request_id = str(uuid.uuid4())
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                response = await client.get(
                    "https://api.elevenlabs.io/v1/voices",
                    headers={"xi-api-key": self._api_key},
                )
            healthy = response.status_code == 200
            message = "reachable" if healthy else (
                "credentials rejected" if response.status_code in {401, 403} else "provider unavailable"
            )
        except httpx.TimeoutException:
            healthy, message = False, "timeout"
        except httpx.TransportError:
            healthy, message = False, "unreachable"
        return TTSProviderHealth(
            provider=self.name,
            healthy=healthy,
            latency_ms=(time.perf_counter() - started) * 1000,
            request_id=request_id,
            message=message,
        )

    @staticmethod
    def _raise_provider_error(error: object) -> None:
        value = str(error).lower()
        if "rate" in value or "limit" in value:
            raise ProviderRateLimitedError(provider="elevenlabs")
        if "auth" in value or "key" in value:
            raise ProviderAuthenticationError("TTS credentials were rejected", provider="elevenlabs")
        raise ProviderUnavailableError("TTS provider returned an error", provider="elevenlabs")
