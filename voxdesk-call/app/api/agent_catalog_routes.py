"""Agent Studio catalog routes.

Serves the three pickers the VoxDesk Agent Studio needs before it can render a
truthful configuration screen:

* ``GET /api/agents/voices``          — TTS voice providers and configured voices
* ``GET /api/agents/models``          — LLM providers and runtime model presets
* ``GET /api/agents/tools/catalog``   — the tool/function contracts the runtime dispatches

Why this module exists
----------------------
``dashboard/src/api/agents.ts`` already calls ``GET /api/agents/voices``
(``getVoiceProviders()``) and ``GET /api/agents/models``
(``getModelProviders()``), but **no backend route served them**. Both calls sit
inside a ``try { ... } catch { return [] }``, so the failure was invisible: the
Agent Studio rendered empty voice and model pickers instead of an error. This
module implements those endpoints against authoritative in-repo sources.

Truthfulness rules (enforced here, and asserted by
``tests/test_agent_catalog_routes.py``)
--------------------------------------------------
1. **Every catalog entry is derived from a source that already exists in this
   repository.** Nothing here is a hardcoded list of third-party product names
   this deployment has not verified. The sources are:

   * provider allow-lists — ``app.domain.agent_models.ALLOWED_VOICE_PROVIDERS``
     and ``ALLOWED_LLM_PROVIDERS``: the exact sets the domain model validates
     against when an agent configuration is saved, so the catalog can never
     advertise a provider that saving would reject;
   * model names — ``app.agent.llm_factory.PRESETS`` (the model/provider pairs
     the runtime actually constructs) plus the configured
     ``settings.deepgram_model`` / ``settings.elevenlabs_model``;
   * tool contracts — ``app.agent.functions.TOOL_CONTRACTS`` and
     ``TOOL_SCHEMAS`` (the allowlist ``dispatch`` resolves against);
   * install state — ``app.providers.compatibility.distribution_state``.

2. **``configured`` means "an API key is present in settings", nothing more.**
   It does not mean the provider answered, accepted the key, or is reachable.
   ``reachable`` and ``authenticated`` are therefore always the literal string
   ``"not_checked"``: this module issues **no** network call. Configuration is
   not connectivity.

3. **No provider SDK is imported in the request path.** Proving an SDK's API
   surface (``capabilities_for``) requires importing it, and several provider
   SDKs perform credential/metadata discovery during import that blocks on a
   network timeout where no metadata service exists — a settings screen must
   never be able to hang on that. So this module uses the import-free
   ``distribution_state`` probe, reports ``runtime_probe: "not_performed"``, and
   leaves the deep SDK check to ``require_capability`` at the moment a call is
   actually built.

4. **A provider's full voice library is not invented.** Voice lists live behind
   each provider's API and cannot be known without calling it, so this module
   returns only the voice IDs this deployment has itself configured, and sets
   ``voice_library_fetched: false`` plus a ``voice_library_note`` explaining
   that the remainder must be fetched from the provider on demand.

5. **``selectable`` is the only field the UI should use to decide what a user
   can pick.** It is ``configured and installed and contract_declared`` — an
   entry that is merely allowed by the domain model but has no credential or no
   installed SDK is reported as unselectable with a machine-readable ``reason``,
   never hidden and never dressed up as available.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.agent.functions import TOOL_CONTRACTS, TOOL_SCHEMAS
from app.agent.llm_factory import PRESETS
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.config import settings
from app.domain.agent_models import (
    ALLOWED_LLM_PROVIDERS,
    ALLOWED_TOOLS,
    ALLOWED_VOICE_PROVIDERS,
)
from app.providers.compatibility import distribution_state
from app.providers.contracts import CAPABILITIES

router = APIRouter(prefix="/api/agents", tags=["agent-catalog"])


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", protected_namespaces=())


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ProviderStateOut(_Strict):
    """The install/configure/contract state of one provider.

    ``configured``, ``installed`` and ``contract_declared`` are three different
    questions and are deliberately reported as three different booleans.
    Collapsing them is how a UI ends up promising a voice it cannot synthesise.
    """

    id: str
    label: str
    allowed_by_domain: bool = Field(
        description="True when saving an agent with this provider would pass domain validation."
    )
    configured: bool = Field(description="An API key is present in deployment settings.")
    installed: bool = Field(description="The provider's pip distribution is present. Read from package metadata, not by importing it.")
    contract_declared: bool = Field(
        description="True when app.providers.contracts declares a capability contract for this provider."
    )
    buildable: bool = Field(description="installed AND contract_declared: this system can construct a client for it.")
    selectable: bool = Field(description="configured AND installed AND contract_declared: safe to offer in the picker.")
    distribution: str | None = Field(default=None, description="The pip distribution that supplies this provider's SDK.")
    sdk_version: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    runtime_probe: str = Field(
        default="not_performed",
        description="Always 'not_performed': no provider SDK is imported while serving this catalog.",
    )
    reachable: str = Field(default="not_checked", description="Always 'not_checked'; no network call is made.")
    authenticated: str = Field(default="not_checked", description="Always 'not_checked'; no network call is made.")
    reason: str | None = Field(default=None, description="Why the provider is not selectable, when it is not.")


class VoiceOut(_Strict):
    """A single voice this deployment can name truthfully."""

    voice_id: str
    provider: str
    source: str = Field(
        description="Where the ID came from: 'deployment_settings' when configured here, never 'provider_api'."
    )
    label: str = ""


class VoicesCatalogOut(_Strict):
    providers: list[ProviderStateOut]
    voices: list[VoiceOut]
    default_provider: str
    default_voice_id: str
    voice_library_fetched: bool = Field(
        default=False,
        description="False: provider voice libraries are not queried by this endpoint.",
    )
    voice_library_note: str
    generated_at: str
    notes: list[str]


class ModelPresetOut(_Strict):
    """One runtime LLM preset from ``app.agent.llm_factory.PRESETS``."""

    preset: str
    provider: str
    model: str
    est_latency_ms: int = Field(description="Reference first-token latency, as declared by llm_factory.")
    notes: str
    configured: bool
    installed: bool
    selectable: bool


class ModelsCatalogOut(_Strict):
    providers: list[ProviderStateOut]
    presets: list[ModelPresetOut]
    default_provider: str | None = Field(
        default=None,
        description="Provider of the first selectable preset, or None when no preset is selectable in this deployment.",
    )
    default_provider_source: str = Field(
        default="first_selectable_preset",
        description="How default_provider was derived; never a hardcoded vendor preference.",
    )
    default_preset: str | None
    configured_stt_model: str
    configured_tts_model: str
    generated_at: str
    notes: list[str]


class ToolContractOut(_Strict):
    """One tool contract from ``app.agent.functions.TOOL_CONTRACTS``."""

    name: str
    description: str
    effect: str
    scope: str
    agent_runtime: bool
    allowed_by_domain: bool
    dispatchable: bool
    schema_available: bool
    parameters: list[str] = Field(default_factory=list)
    required_parameters: list[str] = Field(default_factory=list)


class ToolCatalogOut(_Strict):
    tools: list[ToolContractOut]
    dispatchable_count: int
    requires_tool_calling: bool = Field(
        description="True: these tools only work on an LLM provider that supports tool calling."
    )
    tool_calling_providers: list[str] = Field(
        default_factory=list,
        description="Configured LLM providers whose capability contract declares supports_tool_calling.",
    )
    generated_at: str
    notes: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


#: Display labels. These are human-readable names for the identifiers the
#: domain model already allows; they are not product claims about any vendor.
_PROVIDER_LABELS: dict[str, str] = {
    "elevenlabs": "ElevenLabs",
    "openai": "OpenAI",
    "deepgram": "Deepgram",
    "cartesia": "Cartesia",
    "playht": "PlayHT",
    "azure": "Azure Cognitive Speech",
    "polly": "Amazon Polly",
    "google": "Google Cloud TTS",
    "anthropic": "Anthropic",
    "groq": "Groq",
    "azure_openai": "Azure OpenAI",
    "bedrock": "Amazon Bedrock",
    "custom": "Custom endpoint",
}


def _provider_state(provider: str, *, allowed_by_domain: bool) -> ProviderStateOut:
    """Report one provider's real state without importing its SDK.

    ``distribution_state`` reads package metadata and settings only, so
    ``runtime_probe`` stays ``"not_performed"`` and ``reachable`` /
    ``authenticated`` stay ``"not_checked"`` by construction.
    """
    state = distribution_state(provider)
    contract_declared = provider in CAPABILITIES
    buildable = bool(state.installed and contract_declared)
    selectable = bool(state.configured and buildable)

    reason: str | None = None
    if not selectable:
        if not state.configured and not state.installed:
            reason = (
                f"no {provider} API key configured and no {provider} SDK installed in this deployment"
                if state.distribution
                else f"no {provider} API key configured and this system has no client for {provider}"
            )
        elif not state.configured:
            reason = f"no {provider} API key configured in this deployment"
        elif not state.installed:
            reason = f"{state.distribution or provider} SDK is not installed in this deployment"
        elif not contract_declared:
            reason = f"this system declares no capability contract for {provider}"

    return ProviderStateOut(
        id=provider,
        label=_PROVIDER_LABELS.get(provider, provider),
        allowed_by_domain=allowed_by_domain,
        configured=bool(state.configured),
        installed=bool(state.installed),
        contract_declared=contract_declared,
        buildable=buildable,
        selectable=selectable,
        distribution=state.distribution,
        sdk_version=state.sdk_version,
        capabilities=sorted(CAPABILITIES.get(provider, {})),
        runtime_probe="not_performed",
        reachable="not_checked",
        authenticated="not_checked",
        reason=reason,
    )


def _configured_voices() -> tuple[list[VoiceOut], str, str]:
    """Return only the voice IDs this deployment has itself configured.

    A provider's catalogue of thousands of voices is not knowable without
    calling that provider, so it is deliberately not returned here.
    """
    voices: list[VoiceOut] = []
    elevenlabs_voice = (getattr(settings, "elevenlabs_voice_id", "") or "").strip()
    if elevenlabs_voice:
        voices.append(
            VoiceOut(
                voice_id=elevenlabs_voice,
                provider="elevenlabs",
                source="deployment_settings",
                label="Configured ElevenLabs voice",
            )
        )
    tts_provider = (getattr(settings, "tts_provider", "") or "").strip() or "elevenlabs"
    return voices, tts_provider, elevenlabs_voice


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/voices", response_model=VoicesCatalogOut)
async def list_voice_providers(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
) -> VoicesCatalogOut:
    """``GET /api/agents/voices`` — TTS providers and the voices configured here.

    Tenant-scoped and RBAC-gated, but the catalog itself is deployment-wide: a
    provider's availability is a property of this installation's settings and
    installed packages, not of the calling tenant. No secret is ever returned —
    only whether a key is present.
    """
    providers = [
        _provider_state(name, allowed_by_domain=name in ALLOWED_VOICE_PROVIDERS)
        for name in sorted(ALLOWED_VOICE_PROVIDERS)
    ]
    voices, default_provider, default_voice_id = _configured_voices()
    return VoicesCatalogOut(
        providers=providers,
        voices=voices,
        default_provider=default_provider,
        default_voice_id=default_voice_id,
        voice_library_fetched=False,
        voice_library_note=(
            "Provider voice libraries are not queried by this endpoint. Only voices "
            "configured in this deployment are listed; fetch the remainder from the "
            "provider API once a key is configured."
        ),
        generated_at=_now_iso(),
        notes=[
            "configured means an API key exists in deployment settings; it does not mean the provider was reached.",
            "installed is read from installed-package metadata; no provider SDK is imported while serving this catalog.",
            "reachable and authenticated are 'not_checked': this endpoint performs no network call.",
            "selectable is configured AND installed AND contract_declared; use it to decide what a user may pick.",
        ],
    )


@router.get("/models", response_model=ModelsCatalogOut)
async def list_model_providers(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
) -> ModelsCatalogOut:
    """``GET /api/agents/models`` — LLM providers and the runtime's model presets.

    Model names come from ``app.agent.llm_factory.PRESETS``, i.e. the exact
    provider/model pairs the voice runtime constructs. No model name is
    guessed.
    """
    providers = [
        _provider_state(name, allowed_by_domain=name in ALLOWED_LLM_PROVIDERS)
        for name in sorted(ALLOWED_LLM_PROVIDERS)
    ]
    state_by_provider = {p.id: p for p in providers}

    presets: list[ModelPresetOut] = []
    for preset_name in sorted(PRESETS):
        choice = PRESETS[preset_name]
        state = state_by_provider.get(choice.provider)
        configured = bool(state.configured) if state else False
        installed = bool(state.installed) if state else False
        presets.append(
            ModelPresetOut(
                preset=preset_name,
                provider=choice.provider,
                model=choice.model,
                est_latency_ms=int(choice.est_latency_ms),
                notes=choice.notes,
                configured=configured,
                installed=installed,
                selectable=configured and installed,
            )
        )

    # The default is the first selectable preset in the runtime's own fallback
    # order ("fast" -> "natural" -> "cheap" -> "smart"). It is never a
    # hardcoded vendor preference, and it is None when this deployment has no
    # selectable preset at all — the UI must then show "not configured", not a
    # fake selection.
    default_preset: str | None = None
    default_provider: str | None = None
    for preset_name in ("fast", "natural", "cheap", "smart"):
        match = next((p for p in presets if p.preset == preset_name and p.selectable), None)
        if match is not None:
            default_preset = match.preset
            default_provider = match.provider
            break

    return ModelsCatalogOut(
        providers=providers,
        presets=presets,
        default_provider=default_provider,
        default_provider_source="first_selectable_preset",
        default_preset=default_preset,
        configured_stt_model=(getattr(settings, "deepgram_model", "") or "").strip(),
        configured_tts_model=(getattr(settings, "elevenlabs_model", "") or "").strip(),
        generated_at=_now_iso(),
        notes=[
            "Model names come from app.agent.llm_factory.PRESETS — the presets the runtime actually builds.",
            "configured means an API key exists in deployment settings; it does not mean the provider was reached.",
            "installed is read from installed-package metadata; no provider SDK is imported while serving this catalog.",
            "reachable and authenticated are 'not_checked': this endpoint performs no network call.",
        ],
    )


def _schema_for(name: str) -> dict[str, Any]:
    for entry in TOOL_SCHEMAS:
        function = entry.get("function") if isinstance(entry, dict) else None
        if isinstance(function, dict) and function.get("name") == name:
            return function
    return {}


@router.get("/tools/catalog", response_model=ToolCatalogOut)
async def list_tool_contracts(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
) -> ToolCatalogOut:
    """``GET /api/agents/tools/catalog`` — the tool contracts the runtime dispatches.

    Built from ``app.agent.functions.TOOL_CONTRACTS`` (effect/scope/allowlist)
    and ``TOOL_SCHEMAS`` (description and parameter schema), so the picker can
    never advertise a tool that ``dispatch`` would refuse, and can never
    describe a tool differently than the model is told.
    """
    tools: list[ToolContractOut] = []
    for name in sorted(TOOL_CONTRACTS):
        contract = TOOL_CONTRACTS[name] or {}
        schema = _schema_for(name)
        parameters_block = schema.get("parameters") if isinstance(schema, dict) else None
        properties = (
            parameters_block.get("properties")
            if isinstance(parameters_block, dict) and isinstance(parameters_block.get("properties"), dict)
            else {}
        )
        required = (
            [str(item) for item in parameters_block.get("required", [])]
            if isinstance(parameters_block, dict) and isinstance(parameters_block.get("required"), list)
            else []
        )
        tools.append(
            ToolContractOut(
                name=name,
                description=str(schema.get("description") or ""),
                effect=str(contract.get("effect") or "deny"),
                scope=str(contract.get("scope") or ""),
                agent_runtime=bool(contract.get("agent_runtime", False)),
                allowed_by_domain=name in ALLOWED_TOOLS,
                dispatchable=name in TOOL_CONTRACTS and bool(contract.get("agent_runtime", False)),
                schema_available=bool(schema),
                parameters=sorted(str(key) for key in properties),
                required_parameters=sorted(required),
            )
        )

    tool_calling_providers = sorted(
        name
        for name in ALLOWED_LLM_PROVIDERS
        if CAPABILITIES.get(name, {}).get("supports_tool_calling", False)
        and distribution_state(name).configured
    )

    return ToolCatalogOut(
        tools=tools,
        dispatchable_count=sum(1 for tool in tools if tool.dispatchable),
        requires_tool_calling=True,
        tool_calling_providers=tool_calling_providers,
        generated_at=_now_iso(),
        notes=[
            "Tool names, effects and scopes come from app.agent.functions.TOOL_CONTRACTS.",
            "Descriptions and parameter schemas come from app.agent.functions.TOOL_SCHEMAS — the same text the model sees.",
            "allowed_by_domain is False for a tool the agent domain model would reject when saving; dispatchable is False for one the runtime would refuse.",
            "These tools require an LLM provider that supports tool calling; see tool_calling_providers for the configured ones.",
        ],
    )
