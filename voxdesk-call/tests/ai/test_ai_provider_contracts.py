"""Provider selection and capability contract tests; all local and deterministic."""

from __future__ import annotations

import pytest

from app.agent.errors import ProviderConfigurationError, UnsupportedProviderFeatureError
from app.agent.llm_factory import (
    LLM_CAPABILITIES,
    PRESETS,
    build_llm,
    llm_capabilities,
    resolve,
)
from app.core.config import settings
from app.providers.contracts import CAPABILITIES


def test_factory_uses_the_shared_provider_capability_contract():
    for provider in ("openai", "anthropic", "google"):
        assert LLM_CAPABILITIES[provider] is CAPABILITIES[provider]
        assert llm_capabilities(provider).get("supports_tool_calling") is True
    assert llm_capabilities("unregistered") == {}
    assert "api_key" not in LLM_CAPABILITIES["openai"]


def test_tenant_selection_is_not_a_credential(monkeypatch):
    tenant = type(
        "TenantSelection",
        (),
        {"llm_preset": "fast", "llm_provider": None, "llm_model": None},
    )()
    monkeypatch.setattr(settings, "openai_api_key", "  sk-test  ")
    selection = resolve(tenant)
    assert selection.provider == PRESETS["fast"].provider
    assert selection.model == PRESETS["fast"].model
    assert not hasattr(selection, "api_key")


def test_missing_selected_provider_key_fails_without_fallback(monkeypatch):
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-other")
    with pytest.raises(ProviderConfigurationError):
        build_llm("openai", "gpt-4o-mini", allow_key_fallback=False)


def test_unknown_provider_is_not_silently_rerouted():
    with pytest.raises(UnsupportedProviderFeatureError):
        build_llm("unknown", "unknown-model")
