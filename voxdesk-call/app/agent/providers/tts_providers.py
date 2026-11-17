"""Multi-provider TTS builders with unified voice settings mapping (Sub-Phase 2C & 2G)."""

from __future__ import annotations

from dataclasses import dataclass, field
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

    if spec.provider in {"minimax", "fish_audio"}:
        api_key = get_provider_credential(
            f"{spec.provider.upper()}_API_KEY",
            spec.settings_attrs[0],
            configured_settings,
        )
        return AdapterTTSService(
            provider=spec.provider,
            model=resolved_model,
            voice_id=voice_id or "",
            sample_rate=sample_rate,
            mapped_settings=mapped,
            api_key=api_key,
            extra={
                "capabilities": capabilities_for(spec.provider),
                "group_id": getattr(configured_settings, "minimax_group_id", ""),
            },
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

# ---------------------------------------------------------------------------
# PART 12 (P12-09) — MiniMax, Fish Audio, ElevenLabs v3, Cartesia Sonic-3
# ---------------------------------------------------------------------------

#: What each provider can actually be asked to do. Emotion is not a capability every provider has:
#: sending a `style` parameter to a provider that ignores it produces a *silent* mismatch between
#: what the dashboard promises and what comes out of the speaker, so we track it explicitly.
@dataclass(frozen=True)
class TTSCapabilities:
    provider: str
    supports_emotion: bool
    supports_speed: bool
    supports_audio_tags: bool
    supports_cloning: bool
    supports_ssml: bool
    notes: str = ""


TTS_CAPABILITIES: dict[str, TTSCapabilities] = {
    "elevenlabs": TTSCapabilities(
        provider="elevenlabs",
        supports_emotion=True,
        supports_speed=True,
        # Only the v3 family reads inline audio tags like [sigh]; v2 models would read them aloud.
        supports_audio_tags=False,
        supports_cloning=True,
        supports_ssml=False,
        notes="Audio tags are v3-only; the flag is computed per model in build_tts_request().",
    ),
    "cartesia": TTSCapabilities(
        provider="cartesia",
        supports_emotion=True,
        supports_speed=True,
        supports_audio_tags=False,
        supports_cloning=True,
        supports_ssml=False,
        notes="sonic-3 adds emotion controls; sonic-2 only honours generation_config.speed.",
    ),
    "minimax": TTSCapabilities(
        provider="minimax",
        supports_emotion=True,
        supports_speed=True,
        supports_audio_tags=False,
        supports_cloning=True,
        supports_ssml=False,
        notes="Emotion is a first-class `emotion` field with a fixed enum.",
    ),
    "fish_audio": TTSCapabilities(
        provider="fish_audio",
        supports_emotion=False,
        supports_speed=True,
        supports_audio_tags=False,
        supports_cloning=True,
        supports_ssml=False,
        notes="Emotion comes from the cloned reference, not from a request field.",
    ),
    "openai": TTSCapabilities(
        provider="openai",
        supports_emotion=False,
        supports_speed=True,
        supports_audio_tags=False,
        supports_cloning=False,
        supports_ssml=False,
        notes="gpt-4o-mini-tts takes free-text instructions, not an emotion enum.",
    ),
    "deepgram": TTSCapabilities(
        provider="deepgram",
        supports_emotion=False,
        supports_speed=False,
        supports_audio_tags=False,
        supports_cloning=False,
        supports_ssml=False,
    ),
}


def capabilities_for(provider: str) -> TTSCapabilities:
    key = (provider or "").strip().lower()
    if key in TTS_CAPABILITIES:
        return TTS_CAPABILITIES[key]
    return TTSCapabilities(
        provider=key or "unknown",
        supports_emotion=False,
        supports_speed=False,
        supports_audio_tags=False,
        supports_cloning=False,
        supports_ssml=False,
        notes="provider not catalogued; assume no optional capability",
    )


#: MiniMax's emotion enum. Anything outside it is dropped rather than approximated, because
#: sending `emotion=excited` when the enum says `happy` is a 400 at call time.
MINIMAX_EMOTIONS: frozenset[str] = frozenset(
    {"happy", "sad", "angry", "fearful", "disgusted", "surprised", "neutral"}
)

#: The subset of ElevenLabs v3 audio tags we allow the expressive planner to emit.
ELEVENLABS_V3_TAGS: frozenset[str] = frozenset(
    {"sigh", "laugh", "cough", "gasp", "whisper", "long pause", "short pause", "breath"}
)


@dataclass(frozen=True)
class TTSRequest:
    """A provider request, as data.

    Built here rather than inside each SDK call so the wire shape can be asserted in a test with
    no network, and so a dashboard preview can show exactly what will be sent.
    """

    provider: str
    url: str
    headers: dict[str, str]
    payload: dict[str, Any]
    query: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "url": self.url,
            "headers": {
                key: ("***" if key.lower() in {"authorization", "xi-api-key"} else value)
                for key, value in self.headers.items()
            },
            "payload": self.payload,
            "query": self.query,
        }


def _clamp_speed(value: float, *, low: float = 0.5, high: float = 2.0) -> float:
    return max(low, min(high, float(value)))


def build_tts_request(
    provider: str,
    *,
    text: str,
    voice_id: str,
    model: str = "",
    speed: float = 1.0,
    emotion: str = "",
    api_key: str = "",
    group_id: str = "",
    language: str = "en-US",
    sample_rate: int = 8000,
) -> TTSRequest:
    """Build the HTTP/WS request for one of the PART 12 providers.

    Unknown providers raise `ValueError`: silently returning an empty payload would push the
    failure into the audio path, where it is much harder to see.
    """
    key = (provider or "").strip().lower()
    spoken = str(text or "")
    caps = capabilities_for(key)
    emitted_emotion = str(emotion or "").strip().lower()
    if not caps.supports_emotion:
        emitted_emotion = ""

    if key == "minimax":
        payload: dict[str, Any] = {
            "model": model or "speech-02-hd",
            "text": spoken,
            "stream": True,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": _clamp_speed(speed, low=0.5, high=2.0),
                "vol": 1.0,
                "pitch": 0,
            },
            "audio_setting": {
                "sample_rate": sample_rate,
                "bitrate": 128000,
                "format": "pcm",
                "channel": 1,
            },
        }
        if emitted_emotion in MINIMAX_EMOTIONS:
            payload["voice_setting"]["emotion"] = emitted_emotion
        return TTSRequest(
            provider="minimax",
            url="https://api.minimax.chat/v1/t2a_v2",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            query={"GroupId": group_id} if group_id else {},
        )

    if key == "fish_audio":
        return TTSRequest(
            provider="fish_audio",
            url="https://api.fish.audio/v1/tts",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "model": model or "fish-speech-1.5",
            },
            payload={
                "text": spoken,
                # Fish Audio clones by reference id; there is no emotion field to send.
                "reference_id": voice_id,
                "format": "pcm",
                "sample_rate": sample_rate,
                "normalize": True,
                "latency": "normal",
                **({"prosody": {"speed": _clamp_speed(speed)}} if speed != 1.0 else {}),
            },
        )

    if key == "elevenlabs":
        resolved_model = model or "eleven_turbo_v2_5"
        payload = {
            "text": spoken,
            "model_id": resolved_model,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "speed": _clamp_speed(speed, low=0.7, high=1.2),
            },
            "output_format": f"pcm_{sample_rate}",
        }
        if emitted_emotion:
            # On v2 models `style` is a 0..1 exaggeration dial, not an emotion enum.
            payload["voice_settings"]["style"] = 0.6 if emitted_emotion != "neutral" else 0.0
        if resolved_model.startswith("eleven_v3"):
            tags_used = sorted(
                tag for tag in ELEVENLABS_V3_TAGS if f"[{tag}]" in spoken.lower()
            )
            payload["model_id"] = resolved_model
            payload["enable_audio_tags"] = bool(tags_used) or caps.supports_audio_tags
            if tags_used:
                payload["audio_tags"] = tags_used
        return TTSRequest(
            provider="elevenlabs",
            url=f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream",
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            payload=payload,
        )

    if key == "cartesia":
        resolved_model = model or "sonic-3"
        payload = {
            "model_id": resolved_model,
            "transcript": spoken,
            "voice": {"mode": "id", "id": voice_id},
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": sample_rate,
            },
            "language": language.split("-")[0].lower(),
            "generation_config": {"speed": _clamp_speed(speed, low=0.6, high=1.5)},
        }
        if emitted_emotion and resolved_model.startswith("sonic-3"):
            payload["generation_config"]["emotion"] = emitted_emotion
        elif emitted_emotion:
            # sonic-2 has no emotion field; dropping it is the honest choice.
            payload["generation_config"].pop("emotion", None)
        return TTSRequest(
            provider="cartesia",
            url="https://api.cartesia.ai/tts/bytes",
            headers={
                "X-API-Key": api_key,
                "Cartesia-Version": "2025-04-16",
                "Content-Type": "application/json",
            },
            payload=payload,
        )

    raise ValueError(f"no TTS request builder for provider {provider!r}")

