"""Provider registry for STT, TTS, and voice-cloning runtime operations.

Delegates provider capability truth to `app.agent.providers.registry` (Sub-Phase 2C)
while exposing `VoiceProviderRegistry` and `build_voice_provider_registry` for
streaming STT/TTS adapter factories.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.agent.providers.registry import (
    PROVIDER_SPECS,
    is_provider_configured,
    list_configured_providers,
)
from app.agent.stt_stream import DeepgramStreamingSTT, STTStreamProvider
from app.core.config import Settings, settings
from app.tts.provider import ElevenLabsTTSProvider, TTSProvider
from app.tts.registry import TTSRegistry, build_tts_registry


@dataclass(frozen=True)
class VoiceProviderRegistration:
    name: str
    kind: str  # stt | tts | clone | llm | s2s
    configured: bool
    factory: Callable[[], Any] | None = None
    capabilities: frozenset[str] = field(default_factory=frozenset)


class VoiceProviderRegistry:
    """Explicit registry for voice runtime providers backed by `app.agent.providers.registry`."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], VoiceProviderRegistration] = {}

    def register(self, registration: VoiceProviderRegistration) -> None:
        self._items[(registration.kind, registration.name.lower())] = registration

    def get(self, kind: str, name: str) -> VoiceProviderRegistration:
        key = (kind, name.lower().strip())
        if key not in self._items:
            supported = ", ".join(sorted(n for k, n in self._items if k == kind)) or "none"
            raise ValueError(f"Unsupported {kind} provider '{name}'. Registered {kind} providers: {supported}")
        return self._items[key]

    def resolve_stt(self, name: str) -> STTStreamProvider:
        reg = self.get("stt", name)
        if not reg.configured or reg.factory is None:
            raise ValueError(f"STT provider '{reg.name}' is not configured")
        return reg.factory()

    def resolve_tts(self, name: str) -> TTSProvider:
        reg = self.get("tts", name)
        if not reg.configured or reg.factory is None:
            raise ValueError(f"TTS provider '{reg.name}' is not configured")
        return reg.factory()

    def available(self, kind: str | None = None) -> list[VoiceProviderRegistration]:
        return [
            reg
            for (reg_kind, _), reg in sorted(self._items.items())
            if reg.configured and (kind is None or reg_kind == kind)
        ]

    def configured_matrix(self, configured_settings: Any = settings) -> dict[str, list[dict[str, Any]]]:
        """Return the unified provider configuration matrix from `app.agent.providers.registry`."""
        return list_configured_providers(configured_settings)


def build_voice_provider_registry(
    configured_settings: Settings = settings,
    *,
    tts_registry: TTSRegistry | None = None,
) -> VoiceProviderRegistry:
    registry = VoiceProviderRegistry()
    deepgram_key = getattr(configured_settings, "deepgram_api_key", "").strip()
    registry.register(
        VoiceProviderRegistration(
            name="deepgram",
            kind="stt",
            configured=bool(deepgram_key),
            factory=(lambda: DeepgramStreamingSTT(deepgram_key)) if deepgram_key else None,
            capabilities=frozenset({"streaming", "interim_results", "multilingual", "endpointing"}),
        )
    )
    # Register additional STT providers from unified 2C registry
    for stt_spec in PROVIDER_SPECS.get("stt", ()):
        if stt_spec.provider == "deepgram":
            continue
        is_cfg = is_provider_configured(stt_spec, configured_settings)
        registry.register(
            VoiceProviderRegistration(
                name=stt_spec.provider,
                kind="stt",
                configured=is_cfg,
                factory=None,
                capabilities=frozenset({"streaming", "multilingual"}),
            )
        )

    tts = tts_registry or build_tts_registry(configured_settings)
    eleven_key = getattr(configured_settings, "elevenlabs_api_key", "").strip()
    registry.register(
        VoiceProviderRegistration(
            name="elevenlabs",
            kind="tts",
            configured=bool(eleven_key),
            factory=(lambda: tts.resolve("elevenlabs")) if eleven_key else None,
            capabilities=frozenset({"streaming", "pcm", "mp3", "multilingual", "voice_clone"}),
        )
    )
    for tts_spec in PROVIDER_SPECS.get("tts", ()):
        if tts_spec.provider == "elevenlabs":
            continue
        is_cfg = is_provider_configured(tts_spec, configured_settings)
        registry.register(
            VoiceProviderRegistration(
                name=tts_spec.provider,
                kind="tts",
                configured=is_cfg,
                factory=(lambda p=tts_spec.provider: tts.resolve(p)) if is_cfg else None,
                capabilities=frozenset(
                    {"streaming", "pcm", "multilingual"}
                    | ({"voice_clone"} if tts_spec.supports_voice_clone else set())
                ),
            )
        )

    clone_provider = getattr(configured_settings, "voice_clone_provider", "elevenlabs").lower().strip()
    clone_key = (
        getattr(configured_settings, "voice_clone_api_key", "").strip()
        or (eleven_key if clone_provider == "elevenlabs" else "")
        or (getattr(configured_settings, "cartesia_api_key", "").strip() if clone_provider == "cartesia" else "")
    )
    registry.register(
        VoiceProviderRegistration(
            name=clone_provider,
            kind="clone",
            configured=bool(clone_key and clone_provider in {"elevenlabs", "cartesia"}),
            capabilities=frozenset({"instant_clone", "voice_validation"}),
        )
    )
    return registry


__all__ = [
    "DeepgramStreamingSTT",
    "ElevenLabsTTSProvider",
    "VoiceProviderRegistration",
    "VoiceProviderRegistry",
    "build_voice_provider_registry",
]
