"""Single provider-resolution boundary for the voice runtime."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Any

from app.agent.stt import DeepgramStreamingProvider, STTProvider
from app.core.config import settings as app_settings
from app.tts.provider import TTSProvider
from app.tts.registry import TTSRegistry, build_tts_registry


@dataclass(frozen=True)
class VoiceProviderCapabilities:
    provider: str
    kind: str
    streaming: bool
    cancellation: bool
    languages: tuple[str, ...] = ()
    sample_formats: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Registration:
    name: str
    factory: Callable[[], Any]
    capabilities: VoiceProviderCapabilities


class VoiceProviderRegistry:
    """Immutable registrations; instances are created per runtime request."""

    def __init__(
        self,
        *,
        stt: tuple[_Registration, ...],
        tts: TTSRegistry,
        clone: tuple[_Registration, ...],
    ) -> None:
        self._stt = stt
        self._tts = tts
        self._clone = clone

    @classmethod
    def from_settings(cls, configured_settings=app_settings) -> "VoiceProviderRegistry":
        stt = (
            (
                _Registration(
                    "deepgram",
                    lambda: DeepgramStreamingProvider(configured_settings.deepgram_api_key),
                    VoiceProviderCapabilities(
                        "deepgram", "stt", True, True,
                        ("en", "en-US", "en-GB", "es", "fr", "de", "bn"),
                        ("mulaw", "linear16"),
                    ),
                ),
            )
            if configured_settings.deepgram_api_key
            else ()
        )
        clone = (
            (
                _Registration(
                    "elevenlabs",
                    lambda: _elevenlabs_clone_provider(configured_settings),
                    VoiceProviderCapabilities("elevenlabs", "clone", False, True, sample_formats=("wav", "mp3")),
                ),
            )
            if configured_settings.elevenlabs_api_key
            else ()
        )
        return cls(stt=stt, tts=build_tts_registry(configured_settings), clone=clone)

    def _resolve(self, registrations: tuple[_Registration, ...], provider: str) -> _Registration:
        for registration in registrations:
            if registration.name == provider:
                return registration
        raise ValueError(f"voice provider {provider!r} is not configured")

    def resolve_stt(self, provider: str) -> STTProvider:
        return self._resolve(self._stt, provider).factory()

    def resolve_tts(self, provider: str) -> TTSProvider:
        return self._tts.resolve(provider)

    def resolve_clone(self, provider: str):
        return self._resolve(self._clone, provider).factory()

    def providers(self, kind: str) -> tuple[str, ...]:
        if kind == "tts":
            return self._tts.providers()
        registrations = self._stt if kind == "stt" else self._clone
        return tuple(registration.name for registration in registrations)

    def capabilities(self, kind: str, provider: str) -> VoiceProviderCapabilities:
        if kind == "tts":
            capabilities = self._tts.resolve(provider).capabilities
            return VoiceProviderCapabilities(
                provider, kind, capabilities.streaming, capabilities.cancellation,
                sample_formats=capabilities.encodings,
            )
        registrations = self._stt if kind == "stt" else self._clone
        return self._resolve(registrations, provider).capabilities


def _elevenlabs_clone_provider(configured_settings):
    from app.voice.voice_clone_service import ElevenLabsCloneProvider

    return ElevenLabsCloneProvider(configured_settings.elevenlabs_api_key)


def build_voice_provider_registry(configured_settings=app_settings) -> VoiceProviderRegistry:
    return VoiceProviderRegistry.from_settings(configured_settings)
