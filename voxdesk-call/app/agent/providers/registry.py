"""Central catalog & credential validator for STT, TTS, LLM, and S2S providers (Sub-Phase 2C)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Mapping

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


def _catalog(category: str, *rows: tuple) -> dict[str, ProviderSpec]:
    """Build a provider catalogue from rows.

    Row shape: ``(provider, display_name, default_model, models, required_env, settings_attrs,
    {extras})``. A table instead of eleven near-identical ``ProviderSpec(category=..., ...)``
    blocks: the repetition tripped the repository's own anti-filler guard in
    ``tests/truth/test_no_filler.py``, and the guard was right.
    """
    catalog: dict[str, ProviderSpec] = {}
    for row in rows:
        provider, display_name, default_model, models, required_env, settings_attrs, *rest = row
        extras = dict(rest[0]) if rest and isinstance(rest[0], Mapping) else {}
        catalog[provider] = ProviderSpec(
            category=category,
            provider=provider,
            display_name=display_name,
            default_model=default_model,
            models=tuple(models),
            required_env=tuple(required_env),
            settings_attrs=tuple(settings_attrs),
            **extras,
        )
    return catalog


STT_PROVIDER_CATALOG: dict[str, ProviderSpec] = _catalog(
    "stt",
    ('deepgram', 'Deepgram Nova', 'nova-3', ('nova-3', 'nova-2', 'nova-2-medical', 'nova-2-phonecall'), ('DEEPGRAM_API_KEY',), ('deepgram_api_key',), {'est_ttfb_ms': 95, 'notes': 'Lowest latency streaming WebSocket STT with keyword boosting (`keywords=word:boost`).'}),
    ('assemblyai', 'AssemblyAI Universal Streaming', 'universal-2', ('universal-2', 'best', 'nano'), ('ASSEMBLYAI_API_KEY',), ('assemblyai_api_key',), {'est_ttfb_ms': 140, 'notes': 'High-accuracy streaming transcription with built-in endpointing.'}),
    ('openai_whisper', 'OpenAI Whisper / GPT-4o Transcribe', 'gpt-4o-transcribe', ('gpt-4o-transcribe', 'gpt-4o-mini-transcribe', 'whisper-1'), ('OPENAI_API_KEY',), ('openai_api_key',), {'est_ttfb_ms': 220, 'notes': 'OpenAI segmented/streaming transcription across 50+ languages.', 'aliases': ('openai', 'whisper')}),
    ('azure', 'Azure Cognitive Services Speech STT', 'default', ('default', 'conversation', 'dictation'), ('AZURE_SPEECH_KEY', 'AZURE_SPEECH_REGION'), ('azure_speech_key', 'azure_speech_region'), {'est_ttfb_ms': 150, 'notes': 'Enterprise Azure Speech real-time recognition.'}),
    ('google', 'Google Cloud Speech-to-Text v2', 'chirp_2', ('chirp_2', 'latest_short', 'telephony'), ('GOOGLE_API_KEY',), ('google_api_key',), {'est_ttfb_ms': 165, 'notes': 'Google Cloud Speech / Chirp 2 streaming recognition.'}),
    ('soniox', 'Soniox real-time', 'stt-rt-v3', ('stt-rt-v3', 'stt-rt-v3-medical'), ('SONIOX_API_KEY',), ('soniox_api_key',), {'est_ttfb_ms': 110, 'notes': 'Real-time WebSocket STT with explicit endpoint tokens and medical vocabulary hints.'}),
    ('whisper_local', 'Local Whisper (Faster-Whisper / MLX)', 'base', ('tiny', 'base', 'small', 'medium', 'large-v3-turbo'), ('WHISPER_LOCAL_ENABLED',), (), {'est_ttfb_ms': 260, 'notes': 'Self-hosted air-gapped Whisper transcription.', 'aliases': ('local_whisper',)}),
)

TTS_PROVIDER_CATALOG: dict[str, ProviderSpec] = _catalog(
    "tts",
    ('elevenlabs', 'ElevenLabs', 'eleven_flash_v2_5', ('eleven_flash_v2_5', 'eleven_turbo_v2_5', 'eleven_multilingual_v2', 'eleven_v3'), ('ELEVENLABS_API_KEY',), ('elevenlabs_api_key',), {'supports_voice_cloning': True, 'est_ttfb_ms': 135, 'notes': 'Ultra-low-latency expressive WebSocket TTS with instant voice cloning.'}),
    ('openai', 'OpenAI Audio TTS', 'gpt-4o-mini-tts', ('gpt-4o-mini-tts', 'tts-1', 'tts-1-hd'), ('OPENAI_API_KEY',), ('openai_api_key',), {'est_ttfb_ms': 180, 'notes': 'OpenAI natural voices (alloy, ash, coral, echo, fable, onyx, nova, sage, shimmer).'}),
    ('deepgram', 'Deepgram Aura', 'aura-asteria-en', ('aura-asteria-en', 'aura-luna-en', 'aura-stella-en', 'aura-orion-en'), ('DEEPGRAM_API_KEY',), ('deepgram_api_key',), {'est_ttfb_ms': 110, 'notes': 'Sub-120ms TTFB conversational telephony TTS.'}),
    ('playht', 'PlayHT', 'Play3.0-mini', ('Play3.0-mini', 'PlayDialog', 'PlayHT2.0-turbo'), ('PLAYHT_API_KEY', 'PLAYHT_USER_ID'), ('playht_api_key', 'playht_user_id'), {'supports_voice_cloning': True, 'est_ttfb_ms': 160, 'notes': 'Expressive conversational TTS and instant voice cloning.'}),
    ('cartesia', 'Cartesia Sonic', 'sonic-2', ('sonic-2', 'sonic-3', 'sonic-english', 'sonic-multilingual'), ('CARTESIA_API_KEY',), ('cartesia_api_key',), {'supports_voice_cloning': True, 'est_ttfb_ms': 90, 'notes': 'State-space model TTS with sub-100ms first-byte latency.'}),
    ('minimax', 'MiniMax Speech', 'speech-02-hd', ('speech-02-hd', 'speech-02-turbo', 'speech-01-turbo'), ('MINIMAX_API_KEY',), ('minimax_api_key',), {'est_ttfb_ms': 180, 'notes': 'MiniMax speech-02 with emotion parameter; requires a group id in the URL.'}),
    ('fish_audio', 'Fish Audio', 'fish-speech-1.5', ('fish-speech-1.5', 'fish-speech-1'), ('FISH_AUDIO_API_KEY',), ('fish_audio_api_key',), {'supports_voice_cloning': True, 'est_ttfb_ms': 210, 'notes': 'Fish Audio speech with reference-id cloning; emotion via reference, not a parameter.'}),
    ('azure', 'Azure Neural TTS', 'en-US-AvaMultilingualNeural', ('en-US-AvaMultilingualNeural', 'en-US-AndrewMultilingualNeural', 'en-US-JennyNeural'), ('AZURE_SPEECH_KEY', 'AZURE_SPEECH_REGION'), ('azure_speech_key', 'azure_speech_region'), {'est_ttfb_ms': 155, 'notes': 'Microsoft Azure Neural Speech synthesis with SSML prosody controls.'}),
    ('google', 'Google Cloud Text-to-Speech', 'en-US-Journey-F', ('en-US-Journey-F', 'en-US-Journey-D', 'en-US-Neural2-F'), ('GOOGLE_API_KEY',), ('google_api_key',), {'est_ttfb_ms': 170, 'notes': 'Google Journey & Neural2 streaming synthesis.'}),
)

LLM_PROVIDER_CATALOG: dict[str, ProviderSpec] = _catalog(
    "llm",
    ('openai', 'OpenAI', 'gpt-4o-mini', ('gpt-4o-mini', 'gpt-4o', 'gpt-4.1-mini', 'gpt-4.1'), ('OPENAI_API_KEY',), ('openai_api_key',), {'supports_tool_calling': True, 'est_ttfb_ms': 210, 'notes': 'Fastest tool-calling and structured output for voice agents.'}),
    ('anthropic', 'Anthropic Claude', 'claude-haiku-4-5-20251001', ('claude-haiku-4-5-20251001', 'claude-3-5-haiku-latest', 'claude-3-5-sonnet-latest', 'claude-sonnet-4-5'), ('ANTHROPIC_API_KEY',), ('anthropic_api_key',), {'supports_tool_calling': True, 'est_ttfb_ms': 240, 'notes': 'Natural conversational tone and strong instruction adherence.'}),
    ('google', 'Google Gemini', 'gemini-2.0-flash', ('gemini-2.0-flash', 'gemini-2.0-flash-lite', 'gemini-1.5-pro'), ('GOOGLE_API_KEY',), ('google_api_key',), {'supports_tool_calling': True, 'est_ttfb_ms': 220, 'notes': 'Low-cost multimodal & multilingual reasoning.'}),
    ('groq', 'Groq LPU', 'llama-3.3-70b-versatile', ('llama-3.3-70b-versatile', 'llama-3.1-8b-instant', 'qwen-2.5-32b'), ('GROQ_API_KEY',), ('groq_api_key',), {'supports_tool_calling': True, 'est_ttfb_ms': 110, 'notes': 'Ultra-low-latency LPU inference with OpenAI-compatible tool calling.'}),
    ('azure_openai', 'Azure OpenAI', 'gpt-4o-mini', ('gpt-4o-mini', 'gpt-4o'), ('AZURE_OPENAI_API_KEY', 'AZURE_OPENAI_ENDPOINT'), ('azure_openai_api_key', 'azure_openai_endpoint'), {'supports_tool_calling': True, 'est_ttfb_ms': 220, 'notes': 'Enterprise Azure-hosted OpenAI deployments.', 'aliases': ('azure',)}),
    ('bedrock', 'AWS Bedrock', 'anthropic.claude-3-5-haiku-20241022-v1:0', ('anthropic.claude-3-5-haiku-20241022-v1:0', 'anthropic.claude-3-5-sonnet-20241022-v2:0', 'amazon.nova-lite-v1:0'), ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY'), ('aws_access_key_id', 'aws_secret_access_key'), {'supports_tool_calling': True, 'est_ttfb_ms': 260, 'notes': 'AWS Bedrock Converse streaming API.', 'aliases': ('aws_bedrock',)}),
    ('custom_openai', 'Custom OpenAI-Compatible Endpoint', 'default', ('default',), ('CUSTOM_OPENAI_API_KEY', 'CUSTOM_OPENAI_BASE_URL'), ('custom_openai_api_key', 'custom_openai_base_url'), {'supports_tool_calling': True, 'est_ttfb_ms': 250, 'notes': 'Any vLLM, Together, Fireworks, or self-hosted OpenAI-compatible server.', 'aliases': ('custom', 'vllm', 'together', 'fireworks')}),
)

S2S_PROVIDER_CATALOG: dict[str, ProviderSpec] = _catalog(
    "s2s",
    ('openai_realtime', 'OpenAI Realtime API (Speech-to-Speech)', 'gpt-4o-realtime-preview', ('gpt-4o-realtime-preview', 'gpt-4o-mini-realtime-preview'), ('OPENAI_API_KEY',), ('openai_api_key',), {'supports_tool_calling': True, 'est_ttfb_ms': 160, 'notes': 'Direct audio-in/audio-out WebSocket Speech-to-Speech model.', 'aliases': ('openai_s2s',)}),
    ('gemini_live', 'Google Gemini Multimodal Live (Speech-to-Speech)', 'gemini-2.0-flash-exp', ('gemini-2.0-flash-exp',), ('GOOGLE_API_KEY',), ('google_api_key',), {'supports_tool_calling': True, 'est_ttfb_ms': 180, 'notes': 'Bidirectional low-latency audio streaming with Gemini 2.0.', 'aliases': ('google_s2s', 'gemini_multimodal_live')}),
)

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
