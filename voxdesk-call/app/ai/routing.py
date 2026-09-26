"""Deterministic model routing over the existing preset catalogue.

``resolve`` remains the tenant's selection. This module only refuses a
selection the tenant policy does not allow. It does not invent a provider
and it does not read an API key.
"""

from __future__ import annotations

from app.agent.llm_factory import (
    FALLBACK_PRESETS,
    PRESETS,
    SUPPORTED_PROVIDERS,
    LLMChoice,
    llm_capabilities,
    resolve,
)
from app.ai.models import ModelDescriptor, PolicyDenied, PolicyView


def descriptor_for(
    choice: LLMChoice, *, preset: str | None, development_only: bool
) -> ModelDescriptor:
    caps = llm_capabilities(choice.provider)
    return ModelDescriptor(
        provider=choice.provider,
        model=choice.model,
        preset=preset,
        est_latency_ms=choice.est_latency_ms,
        supports_tools=bool(caps.get("supports_tool_calling", False)),
        supports_streaming=bool(caps.get("supports_streaming", False)),
        status="available",
        development_only=development_only,
    )


def catalogue() -> list[ModelDescriptor]:
    """The presets the factory can already construct. No extra providers."""
    rows = []
    for name, choice in PRESETS.items():
        rows.append(descriptor_for(choice, preset=name, development_only=False))
    return rows


def _preset_name(choice: LLMChoice) -> str | None:
    for name, preset in PRESETS.items():
        if preset.provider == choice.provider and preset.model == choice.model:
            return name
    return None


def assert_allowed(choice: LLMChoice, policy: PolicyView, *, environment_kind: str) -> str | None:
    """Raise when the choice is outside the tenant policy. Return the preset name."""
    if policy.status != "active":
        raise PolicyDenied("AI policy is disabled")
    if choice.provider not in SUPPORTED_PROVIDERS:
        raise PolicyDenied("Provider is not supported")
    if choice.provider in policy.disabled_providers:
        raise PolicyDenied("Provider is disabled for this tenant")
    preset = _preset_name(choice)
    if policy.allowed_presets and (preset is None or preset not in policy.allowed_presets):
        # A custom model is allowed only when the allowlist is empty. An
        # explicit allowlist does not silently widen to an unlisted model.
        raise PolicyDenied("Model is not on the tenant allowlist")
    if preset in policy.development_only_presets and environment_kind == "production":
        raise PolicyDenied("Development-only model cannot be used in production")
    return preset


def select_for_runtime(
    tenant,
    policy: PolicyView,
    *,
    environment_kind: str = "production",
    requested_preset: str | None = None,
) -> LLMChoice:
    """Tenant selection only. A different client preset is a denial, not a switch."""
    choice = select(tenant, policy, environment_kind=environment_kind)
    if not requested_preset:
        return choice
    requested = preset_choice(requested_preset)
    if requested.provider != choice.provider or requested.model != choice.model:
        raise PolicyDenied("Client model override is not allowed")
    return choice


def select(tenant, policy: PolicyView, *, environment_kind: str = "production") -> LLMChoice:
    """The tenant's existing selection, refused when policy says no.

    No policy row means every current preset remains allowed, so a tenant
    that never configured governance keeps ``resolve`` behaviour.
    """
    choice = resolve(tenant)
    assert_allowed(choice, policy, environment_kind=environment_kind)
    return choice


def preset_choice(name: str) -> LLMChoice:
    try:
        return PRESETS[name]
    except KeyError as exc:
        raise PolicyDenied("Unknown model preset") from exc


def fallback_choice(provider: str) -> LLMChoice:
    name = FALLBACK_PRESETS.get(provider)
    if name is None:
        raise PolicyDenied("Provider is not supported")
    return PRESETS[name]
