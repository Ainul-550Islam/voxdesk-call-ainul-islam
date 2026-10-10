"""Multi-provider STT, TTS, LLM, and S2S registry, builders, and circuit-breaker failover (Sub-Phase 2C)."""

from __future__ import annotations

from app.agent.providers.failover import CircuitState, FailoverServiceWrapper
from app.agent.providers.llm_providers import build_llm_provider
from app.agent.providers.registry import (
    LLM_PROVIDER_CATALOG,
    S2S_PROVIDER_CATALOG,
    STT_PROVIDER_CATALOG,
    TTS_PROVIDER_CATALOG,
    ProviderNotConfigured,
    ProviderSpec,
    get_provider_credential,
    is_provider_configured,
    list_provider_catalog,
    require_provider_configured,
)
from app.agent.providers.s2s_providers import build_s2s_provider
from app.agent.providers.stt_providers import build_stt_provider, format_boosted_keywords
from app.agent.providers.tts_providers import build_tts_provider

__all__ = [
    "CircuitState",
    "FailoverServiceWrapper",
    "LLM_PROVIDER_CATALOG",
    "ProviderNotConfigured",
    "ProviderSpec",
    "S2S_PROVIDER_CATALOG",
    "STT_PROVIDER_CATALOG",
    "TTS_PROVIDER_CATALOG",
    "build_llm_provider",
    "build_s2s_provider",
    "build_stt_provider",
    "build_tts_provider",
    "format_boosted_keywords",
    "get_provider_credential",
    "is_provider_configured",
    "list_provider_catalog",
    "require_provider_configured",
]
