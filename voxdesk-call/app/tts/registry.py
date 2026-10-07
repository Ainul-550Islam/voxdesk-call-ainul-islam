"""Immutable TTS provider registry built from application settings."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.agent.errors import ProviderConfigurationError
from app.core.config import settings as app_settings
from app.tts.provider import ElevenLabsStreamingProvider, TTSProvider


@dataclass(frozen=True)
class TTSRegistration:
    name: str
    factory: Callable[[], TTSProvider]


class TTSRegistry:
    def __init__(self, registrations: tuple[TTSRegistration, ...]) -> None:
        self._registrations = registrations
        self._by_name = {registration.name: registration for registration in registrations}

    @classmethod
    def from_settings(cls, configured_settings=app_settings) -> "TTSRegistry":
        registrations: list[TTSRegistration] = []
        if configured_settings.elevenlabs_api_key:
            registrations.append(
                TTSRegistration(
                    "elevenlabs",
                    lambda: ElevenLabsStreamingProvider(configured_settings.elevenlabs_api_key),
                )
            )
        return cls(tuple(registrations))

    def providers(self) -> tuple[str, ...]:
        return tuple(registration.name for registration in self._registrations)

    def resolve(self, provider: str) -> TTSProvider:
        registration = self._by_name.get(provider)
        if registration is None:
            raise ProviderConfigurationError(
                f"TTS provider {provider!r} is not configured", provider=provider
            )
        return registration.factory()

    def supports(self, provider: str, *, sample_rate: int, encoding: str) -> bool:
        try:
            capabilities = self.resolve(provider).capabilities
        except Exception:
            return False
        return sample_rate in capabilities.sample_rates and encoding in capabilities.encodings


def build_tts_registry(configured_settings=app_settings) -> TTSRegistry:
    """Return a fresh registry; provider instances are not global mutable state."""
    return TTSRegistry.from_settings(configured_settings)
