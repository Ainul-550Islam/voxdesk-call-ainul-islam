"""Multi-provider TTS builders with unified voice settings mapping (Sub-Phase 2C & 2G)."""

from __future__ import annotations

from typing import Any

from pipecat.frames.frames import Frame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService

from app.agent.providers.registry import (
    get_provider_credential,
    require_provider_configured,
)
from app.agent.tts import SpeedAwareElevenLabsTTSService
from app.agent.voice_settings import map_voice_settings, normalize_speech_speed
from app.core.config import settings
from app.core.i18n import elevenlabs_options


class AdapterTTSService(FrameProcessor):
    """Pipecat `FrameProcessor` TTS adapter storing normalized provider settings."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        voice_id: str,
        sample_rate: int = 8000,
        mapped_settings: dict[str, Any] | None = None,
        api_key: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.model = model
        self.voice_id = voice_id
        self.sample_rate = sample_rate
        self.mapped_settings = dict(mapped_settings or {})
        self._api_key = api_key
        self.extra = dict(extra or {})

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        await self.push_frame(frame, direction)


def build_tts_provider(
    provider: str | None = None,
    *,
    model: str | None = None,
    voice_id: str | None = None,
    language: str = "en-US",
    sample_rate: int = 8000,
    voice_settings: dict[str, Any] | None = None,
    cfg: Any = None,
    configured_settings: Any = settings,
) -> FrameProcessor:
    """Build a Pipecat TTS processor for the requested provider (Sub-Phase 2C)."""
    if cfg is not None:
        provider = provider or getattr(cfg, "tts_provider", "elevenlabs")
        model = model or getattr(cfg, "tts_model", None)
        voice_id = voice_id if voice_id is not None else getattr(cfg, "voice_id", "")
        language = getattr(cfg, "language", language) or language
        if voice_settings is None:
            voice_settings = getattr(cfg, "voice_settings", None)

    spec = require_provider_configured(
        "tts", provider or "elevenlabs", configured_settings=configured_settings
    )
    resolved_model = model or spec.default_model
    raw_vs = dict(voice_settings or {})
    mapped = map_voice_settings(spec.provider, raw_vs)

    if spec.provider == "elevenlabs":
        api_key = get_provider_credential(
            "ELEVENLABS_API_KEY", "elevenlabs_api_key", configured_settings
        )
        opts = elevenlabs_options(language, voice_id or "")
        speed, _ = normalize_speech_speed(mapped.get("speed", 1.0))
        svc = SpeedAwareElevenLabsTTSService(
            api_key=api_key,
            voice_id=opts["voice_id"] or configured_settings.elevenlabs_voice_id,
            model=resolved_model or opts["model"],
            sample_rate=sample_rate,
            params=ElevenLabsTTSService.InputParams(
                stability=float(mapped.get("stability", 0.5)),
                similarity_boost=float(mapped.get("similarity_boost", 0.75)),
                style=float(mapped.get("style", 0.0)),
                use_speaker_boost=bool(mapped.get("use_speaker_boost", True)),
            ),
            speed=speed,
        )
        setattr(svc, "provider", "elevenlabs")
        setattr(svc, "mapped_settings", mapped)
        return svc

    if spec.provider == "openai":
        api_key = get_provider_credential(
            "OPENAI_API_KEY", "openai_api_key", configured_settings
        )
        resolved_voice = voice_id or "alloy"
        try:
            from pipecat.services.openai.tts import OpenAITTSService

            svc = OpenAITTSService(
                api_key=api_key,
                voice=resolved_voice,
                model=resolved_model,
                sample_rate=sample_rate,
            )
            setattr(svc, "provider", "openai")
            setattr(svc, "mapped_settings", mapped)
            return svc
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            return AdapterTTSService(
                provider="openai",
                model=resolved_model,
                voice_id=resolved_voice,
                sample_rate=sample_rate,
                mapped_settings=mapped,
                api_key=api_key,
            )

    if spec.provider == "deepgram":
        api_key = get_provider_credential(
            "DEEPGRAM_API_KEY", "deepgram_api_key", configured_settings
        )
        resolved_voice = voice_id or resolved_model or "aura-asteria-en"
        try:
            from pipecat.services.deepgram.tts import DeepgramTTSService

            svc = DeepgramTTSService(
                api_key=api_key,
                voice=resolved_voice,
                sample_rate=sample_rate,
            )
            setattr(svc, "provider", "deepgram")
            setattr(svc, "mapped_settings", mapped)
            return svc
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            return AdapterTTSService(
                provider="deepgram",
                model=resolved_model,
                voice_id=resolved_voice,
                sample_rate=sample_rate,
                mapped_settings=mapped,
                api_key=api_key,
            )

    if spec.provider == "cartesia":
        api_key = get_provider_credential(
            "CARTESIA_API_KEY", "cartesia_api_key", configured_settings
        )
        return AdapterTTSService(
            provider="cartesia",
            model=resolved_model,
            voice_id=voice_id or "a0e99841-438c-4a64-b679-ae501e7d6091",
            sample_rate=sample_rate,
            mapped_settings=mapped,
            api_key=api_key,
        )

    if spec.provider == "playht":
        api_key = get_provider_credential(
            "PLAYHT_API_KEY", "playht_api_key", configured_settings
        )
        user_id = get_provider_credential(
            "PLAYHT_USER_ID", "playht_user_id", configured_settings
        )
        return AdapterTTSService(
            provider="playht",
            model=resolved_model,
            voice_id=voice_id or "s3://voice-cloning-zero-shot/default/manifest.json",
            sample_rate=sample_rate,
            mapped_settings=mapped,
            api_key=api_key,
            extra={"user_id": user_id},
        )

    if spec.provider == "azure":
        api_key = get_provider_credential(
            "AZURE_SPEECH_KEY", "azure_speech_key", configured_settings
        )
        region = get_provider_credential(
            "AZURE_SPEECH_REGION", "azure_speech_region", configured_settings
        )
        return AdapterTTSService(
            provider="azure",
            model=resolved_model,
            voice_id=voice_id or resolved_model,
            sample_rate=sample_rate,
            mapped_settings=mapped,
            api_key=api_key,
            extra={"region": region},
        )

    # google
    api_key = get_provider_credential(
        "GOOGLE_API_KEY", "google_api_key", configured_settings
    )
    return AdapterTTSService(
        provider="google",
        model=resolved_model,
        voice_id=voice_id or resolved_model,
        sample_rate=sample_rate,
        mapped_settings=mapped,
        api_key=api_key,
    )
