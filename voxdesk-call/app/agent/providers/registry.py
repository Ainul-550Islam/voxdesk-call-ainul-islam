"""Central catalog & credential validator for STT, TTS, LLM, and S2S providers (Sub-Phase 2C)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from app.agent.errors import ProviderConfigurationError
from app.core.config import settings


class ProviderNotConfigured(ProviderConfigurationError):
    """Raised when a requested STT/TTS/LLM/S2S provider has no API key or required env vars."""

    def __init__(
        self,
        message: str,
        *,
        provider: str = "unknown",
        category: str = "unknown",
        missing_env: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message, provider=provider)
        self.category = category
        self.missing_env = missing_env


@dataclass(frozen=True)
class ProviderSpec:
    category: str  # stt | tts | llm | s2s
    provider: str
    display_name: str
    default_model: str
    models: tuple[str, ...]
    required_env: tuple[str, ...]
    settings_attrs: tuple[str, ...]
    supports_streaming: bool = True
    supports_tool_calling: bool = False
    supports_multilingual: bool = True
    supports_voice_cloning: bool = False
    est_ttfb_ms: int = 200
    notes: str = ""
    aliases: tuple[str, ...] = field(default_factory=tuple)


STT_PROVIDER_CATALOG: dict[str, ProviderSpec] = {
    "deepgram": ProviderSpec(
        category="stt",
        provider="deepgram",
        display_name="Deepgram Nova",
        default_model="nova-3",
        models=("nova-3", "nova-2", "nova-2-medical", "nova-2-phonecall"),
        required_env=("DEEPGRAM_API_KEY",),
        settings_attrs=("deepgram_api_key",),
        est_ttfb_ms=95,
        notes="Lowest latency streaming WebSocket STT with keyword boosting (`keywords=word:boost`).",
    ),
    "assemblyai": ProviderSpec(
        category="stt",
        provider="assemblyai",
        display_name="AssemblyAI Universal Streaming",
        default_model="universal-2",
        models=("universal-2", "best", "nano"),
        required_env=("ASSEMBLYAI_API_KEY",),
        settings_attrs=("assemblyai_api_key",),
        est_ttfb_ms=140,
        notes="High-accuracy streaming transcription with built-in endpointing.",
    ),
    "openai_whisper": ProviderSpec(
        category="stt",
        provider="openai_whisper",
        display_name="OpenAI Whisper / GPT-4o Transcribe",
        default_model="gpt-4o-transcribe",
        models=("gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"),
        required_env=("OPENAI_API_KEY",),
        settings_attrs=("openai_api_key",),
        est_ttfb_ms=220,
        aliases=("openai", "whisper"),
        notes="OpenAI segmented/streaming transcription across 50+ languages.",
    ),
    "azure": ProviderSpec(
        category="stt",
        provider="azure",
        display_name="Azure Cognitive Services Speech STT",
        default_model="default",
        models=("default", "conversation", "dictation"),
        required_env=("AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"),
        settings_attrs=("azure_speech_key", "azure_speech_region"),
        est_ttfb_ms=150,
        notes="Enterprise Azure Speech real-time recognition.",
    ),
    "google": ProviderSpec(
        category="stt",
        provider="google",
        display_name="Google Cloud Speech-to-Text v2",
        default_model="chirp_2",
        models=("chirp_2", "latest_short", "telephony"),
        required_env=("GOOGLE_API_KEY",),
        settings_attrs=("google_api_key",),
        est_ttfb_ms=165,
        notes="Google Cloud Speech / Chirp 2 streaming recognition.",
    ),
    "whisper_local": ProviderSpec(
        category="stt",
        provider="whisper_local",
        display_name="Local Whisper (Faster-Whisper / MLX)",
        default_model="base",
        models=("tiny", "base", "small", "medium", "large-v3-turbo"),
        required_env=("WHISPER_LOCAL_ENABLED",),
        settings_attrs=(),
        est_ttfb_ms=260,
        aliases=("local_whisper",),
        notes="Self-hosted air-gapped Whisper transcription.",
    ),
}

TTS_PROVIDER_CATALOG: dict[str, ProviderSpec] = {
    "elevenlabs": ProviderSpec(
        category="tts",
        provider="elevenlabs",
        display_name="ElevenLabs",
        default_model="eleven_flash_v2_5",
        models=("eleven_flash_v2_5", "eleven_turbo_v2_5", "eleven_multilingual_v2"),
        required_env=("ELEVENLABS_API_KEY",),
        settings_attrs=("elevenlabs_api_key",),
        supports_voice_cloning=True,
        est_ttfb_ms=135,
        notes="Ultra-low-latency expressive WebSocket TTS with instant voice cloning.",
    ),
    "openai": ProviderSpec(
        category="tts",
        provider="openai",
        display_name="OpenAI Audio TTS",
        default_model="gpt-4o-mini-tts",
        models=("gpt-4o-mini-tts", "tts-1", "tts-1-hd"),
        required_env=("OPENAI_API_KEY",),
        settings_attrs=("openai_api_key",),
        est_ttfb_ms=180,
        notes="OpenAI natural voices (alloy, ash, coral, echo, fable, onyx, nova, sage, shimmer).",
    ),
    "deepgram": ProviderSpec(
        category="tts",
        provider="deepgram",
        display_name="Deepgram Aura",
        default_model="aura-asteria-en",
        models=("aura-asteria-en", "aura-luna-en", "aura-stella-en", "aura-orion-en"),
        required_env=("DEEPGRAM_API_KEY",),
        settings_attrs=("deepgram_api_key",),
        est_ttfb_ms=110,
        notes="Sub-120ms TTFB conversational telephony TTS.",
    ),
    "playht": ProviderSpec(
        category="tts",
        provider="playht",
        display_name="PlayHT",
        default_model="Play3.0-mini",
        models=("Play3.0-mini", "PlayDialog", "PlayHT2.0-turbo"),
        required_env=("PLAYHT_API_KEY", "PLAYHT_USER_ID"),
        settings_attrs=("playht_api_key", "playht_user_id"),
        supports_voice_cloning=True,
        est_ttfb_ms=160,
        notes="Expressive conversational TTS and instant voice cloning.",
    ),
    "cartesia": ProviderSpec(
        category="tts",
        provider="cartesia",
        display_name="Cartesia Sonic",
        default_model="sonic-2",
        models=("sonic-2", "sonic-english", "sonic-multilingual"),
        required_env=("CARTESIA_API_KEY",),
        settings_attrs=("cartesia_api_key",),
        supports_voice_cloning=True,
        est_ttfb_ms=90,
        notes="State-space model TTS with sub-100ms first-byte latency.",
    ),
    "azure": ProviderSpec(
        category="tts",
        provider="azure",
        display_name="Azure Neural TTS",
        default_model="en-US-AvaMultilingualNeural",
        models=("en-US-AvaMultilingualNeural", "en-US-AndrewMultilingualNeural", "en-US-JennyNeural"),
        required_env=("AZURE_SPEECH_KEY", "AZURE_SPEECH_REGION"),
        settings_attrs=("azure_speech_key", "azure_speech_region"),
        est_ttfb_ms=155,
        notes="Microsoft Azure Neural Speech synthesis with SSML prosody controls.",
    ),
    "google": ProviderSpec(
        category="tts",
        provider="google",
        display_name="Google Cloud Text-to-Speech",
        default_model="en-US-Journey-F",
        models=("en-US-Journey-F", "en-US-Journey-D", "en-US-Neural2-F"),
        required_env=("GOOGLE_API_KEY",),
        settings_attrs=("google_api_key",),
        est_ttfb_ms=170,
        notes="Google Journey & Neural2 streaming synthesis.",
    ),
}

LLM_PROVIDER_CATALOG: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        category="llm",
        provider="openai",
        display_name="OpenAI",
        default_model="gpt-4o-mini",
        models=("gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"),
        required_env=("OPENAI_API_KEY",),
        settings_attrs=("openai_api_key",),
        supports_tool_calling=True,
        est_ttfb_ms=210,
        notes="Fastest tool-calling and structured output for voice agents.",
    ),
    "anthropic": ProviderSpec(
        category="llm",
        provider="anthropic",
        display_name="Anthropic Claude",
        default_model="claude-haiku-4-5-20251001",
        models=("claude-haiku-4-5-20251001", "claude-3-5-haiku-latest", "claude-3-5-sonnet-latest", "claude-sonnet-4-5"),
        required_env=("ANTHROPIC_API_KEY",),
        settings_attrs=("anthropic_api_key",),
        supports_tool_calling=True,
        est_ttfb_ms=240,
        notes="Natural conversational tone and strong instruction adherence.",
    ),
    "google": ProviderSpec(
        category="llm",
        provider="google",
        display_name="Google Gemini",
        default_model="gemini-2.0-flash",
        models=("gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro"),
        required_env=("GOOGLE_API_KEY",),
        settings_attrs=("google_api_key",),
        supports_tool_calling=True,
        est_ttfb_ms=220,
        notes="Low-cost multimodal & multilingual reasoning.",
    ),
    "groq": ProviderSpec(
        category="llm",
        provider="groq",
        display_name="Groq LPU",
        default_model="llama-3.3-70b-versatile",
        models=("llama-3.3-70b-versatile", "llama-3.1-8b-instant", "qwen-2.5-32b"),
        required_env=("GROQ_API_KEY",),
        settings_attrs=("groq_api_key",),
        supports_tool_calling=True,
        est_ttfb_ms=110,
        notes="Ultra-low-latency LPU inference with OpenAI-compatible tool calling.",
    ),
    "azure_openai": ProviderSpec(
        category="llm",
        provider="azure_openai",
        display_name="Azure OpenAI",
        default_model="gpt-4o-mini",
        models=("gpt-4o-mini", "gpt-4o"),
        required_env=("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"),
        settings_attrs=("azure_openai_api_key", "azure_openai_endpoint"),
        supports_tool_calling=True,
        est_ttfb_ms=220,
        aliases=("azure",),
        notes="Enterprise Azure-hosted OpenAI deployments.",
    ),
    "bedrock": ProviderSpec(
        category="llm",
        provider="bedrock",
        display_name="AWS Bedrock",
        default_model="anthropic.claude-3-5-haiku-20241022-v1:0",
        models=(
            "anthropic.claude-3-5-haiku-20241022-v1:0",
            "anthropic.claude-3-5-sonnet-20241022-v2:0",
            "amazon.nova-lite-v1:0",
        ),
        required_env=("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),
        settings_attrs=("aws_access_key_id", "aws_secret_access_key"),
        supports_tool_calling=True,
        est_ttfb_ms=260,
        aliases=("aws_bedrock",),
        notes="AWS Bedrock Converse streaming API.",
    ),
    "custom_openai": ProviderSpec(
        category="llm",
        provider="custom_openai",
        display_name="Custom OpenAI-Compatible Endpoint",
        default_model="default",
        models=("default",),
        required_env=("CUSTOM_OPENAI_API_KEY", "CUSTOM_OPENAI_BASE_URL"),
        settings_attrs=("custom_openai_api_key", "custom_openai_base_url"),
        supports_tool_calling=True,
        est_ttfb_ms=250,
        aliases=("custom", "vllm", "together", "fireworks"),
        notes="Any vLLM, Together, Fireworks, or self-hosted OpenAI-compatible server.",
    ),
}

S2S_PROVIDER_CATALOG: dict[str, ProviderSpec] = {
    "openai_realtime": ProviderSpec(
        category="s2s",
        provider="openai_realtime",
        display_name="OpenAI Realtime API (Speech-to-Speech)",
        default_model="gpt-4o-realtime-preview",
        models=("gpt-4o-realtime-preview", "gpt-4o-mini-realtime-preview"),
        required_env=("OPENAI_API_KEY",),
        settings_attrs=("openai_api_key",),
        supports_tool_calling=True,
        est_ttfb_ms=160,
        aliases=("openai_s2s",),
        notes="Direct audio-in/audio-out WebSocket Speech-to-Speech model.",
    ),
    "gemini_live": ProviderSpec(
        category="s2s",
        provider="gemini_live",
        display_name="Google Gemini Multimodal Live (Speech-to-Speech)",
        default_model="gemini-2.0-flash-exp",
        models=("gemini-2.0-flash-exp",),
        required_env=("GOOGLE_API_KEY",),
        settings_attrs=("google_api_key",),
        supports_tool_calling=True,
        est_ttfb_ms=180,
        aliases=("google_s2s", "gemini_multimodal_live"),
        notes="Bidirectional low-latency audio streaming with Gemini 2.0.",
    ),
}

_CATALOGS: dict[str, dict[str, ProviderSpec]] = {
    "stt": STT_PROVIDER_CATALOG,
    "tts": TTS_PROVIDER_CATALOG,
    "llm": LLM_PROVIDER_CATALOG,
    "s2s": S2S_PROVIDER_CATALOG,
}


def resolve_provider_spec(category: str, provider: str) -> ProviderSpec:
    cat = (category or "").strip().lower()
    prov = (provider or "").strip().lower()
    catalog = _CATALOGS.get(cat)
    if catalog is None:
        raise ProviderNotConfigured(
            f"Unknown provider category {category!r}",
            provider=prov,
            category=cat,
        )
    if prov in catalog:
        return catalog[prov]
    for spec in catalog.values():
        if prov in spec.aliases:
            return spec
    raise ProviderNotConfigured(
        f"Unsupported {cat.upper()} provider {provider!r}; supported: {sorted(catalog)}",
        provider=prov,
        category=cat,
    )


def get_provider_credential(env_name: str, settings_attr: str | None = None, configured_settings: Any = settings) -> str:
    """Resolve a provider credential from `settings` or `os.environ`."""
    if settings_attr and hasattr(configured_settings, settings_attr):
        val = getattr(configured_settings, settings_attr, "")
        if isinstance(val, str) and val.strip():
            return val.strip()
    env_val = os.environ.get(env_name, "")
    if isinstance(env_val, str) and env_val.strip():
        return env_val.strip()
    return ""


def missing_provider_env(
    category: str,
    provider: str,
    *,
    configured_settings: Any = settings,
) -> tuple[str, ...]:
    spec = resolve_provider_spec(category, provider)
    missing: list[str] = []
    for idx, env_name in enumerate(spec.required_env):
        attr = spec.settings_attrs[idx] if idx < len(spec.settings_attrs) else None
        if not get_provider_credential(env_name, attr, configured_settings):
            missing.append(env_name)
    return tuple(missing)


def is_provider_configured(
    category: str,
    provider: str,
    *,
    configured_settings: Any = settings,
) -> bool:
    try:
        return len(missing_provider_env(category, provider, configured_settings=configured_settings)) == 0
    except ProviderNotConfigured:
        return False


def require_provider_configured(
    category: str,
    provider: str,
    *,
    configured_settings: Any = settings,
) -> ProviderSpec:
    spec = resolve_provider_spec(category, provider)
    missing = missing_provider_env(category, spec.provider, configured_settings=configured_settings)
    if missing:
        raise ProviderNotConfigured(
            f"{spec.display_name} ({spec.category}:{spec.provider}) is not configured; "
            f"missing environment variable(s): {', '.join(missing)}",
            provider=spec.provider,
            category=spec.category,
            missing_env=missing,
        )
    return spec


def list_provider_catalog(
    category: str | None = None,
    *,
    configured_settings: Any = settings,
) -> list[dict[str, Any]]:
    categories = [category.lower()] if category else ["stt", "tts", "llm", "s2s"]
    items: list[dict[str, Any]] = []
    for cat in categories:
        catalog = _CATALOGS.get(cat, {})
        for spec in catalog.values():
            missing = missing_provider_env(cat, spec.provider, configured_settings=configured_settings)
            items.append(
                {
                    "category": spec.category,
                    "provider": spec.provider,
                    "display_name": spec.display_name,
                    "default_model": spec.default_model,
                    "models": list(spec.models),
                    "required_env": list(spec.required_env),
                    "missing_env": list(missing),
                    "configured": len(missing) == 0,
                    "supports_streaming": spec.supports_streaming,
                    "supports_tool_calling": spec.supports_tool_calling,
                    "supports_multilingual": spec.supports_multilingual,
                    "supports_voice_cloning": spec.supports_voice_cloning,
                    "est_ttfb_ms": spec.est_ttfb_ms,
                    "notes": spec.notes,
                }
            )
    return items
