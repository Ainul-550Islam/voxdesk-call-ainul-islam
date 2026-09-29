"""Provider SDK/API capability detection; never equates configuration with connectivity."""
from __future__ import annotations

from dataclasses import dataclass, replace
from importlib import import_module, metadata
from inspect import Parameter, signature
from typing import Any, Callable

from app.providers.errors import ProviderCompatibilityError, ProviderDependencyMissingError


@dataclass(frozen=True)
class CapabilityReport:
    provider: str
    available: bool
    sdk_version: str | None
    capabilities: frozenset[str]
    reason: str | None = None
    configured: bool = False
    installed: bool = False
    capable: bool = False
    api_surface: str | None = None
    reachable: str = "not_checked"
    authenticated: str = "not_checked"

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "configured": self.configured,
            "installed": self.installed,
            "sdk_version": self.sdk_version,
            "capable": self.capable,
            "capabilities": sorted(self.capabilities),
            "api_surface": self.api_surface,
            "reachable": self.reachable,
            "authenticated": self.authenticated,
            "reason": self.reason,
        }


def _version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def websocket_header_keyword(connect: Callable[..., Any] | None = None) -> str:
    """Return the supported custom-header keyword for the installed websockets API."""
    if connect is None:
        websocket_module = import_module("websockets")
        connect = websocket_module.connect
    try:
        parameters = signature(connect).parameters
    except (TypeError, ValueError) as exc:
        raise ProviderCompatibilityError("websockets connect signature cannot be inspected", provider="deepgram") from exc
    for name in ("additional_headers", "extra_headers"):
        if name in parameters:
            return name
    if any(parameter.kind is Parameter.VAR_KEYWORD for parameter in parameters.values()):
        version = _version("websockets")
        if version is not None:
            try:
                major = int(version.split(".", 1)[0])
            except ValueError as exc:
                raise ProviderCompatibilityError("websockets version is not recognized", provider="deepgram") from exc
            return "additional_headers" if major >= 14 else "extra_headers"
    raise ProviderCompatibilityError("websockets connect API has no supported custom-header parameter", provider="deepgram")


def _deepgram_surface() -> tuple[Any, Any]:
    sdk = import_module("deepgram")
    pipecat = import_module("pipecat.services.deepgram.stt")
    if not hasattr(sdk, "LiveOptions") or not hasattr(pipecat, "DeepgramSTTService"):
        raise AttributeError("required Deepgram SDK/Pipecat symbols are missing")
    live_fields = getattr(sdk.LiveOptions, "__dataclass_fields__", {})
    required = {"encoding", "sample_rate", "language", "model", "interim_results"}
    if not required.issubset(live_fields):
        raise AttributeError("Deepgram LiveOptions fields do not satisfy the voice contract")
    if "live_options" not in signature(pipecat.DeepgramSTTService).parameters:
        raise AttributeError("Pipecat Deepgram service has no live_options parameter")
    websocket_header_keyword()
    return pipecat.DeepgramSTTService, sdk.LiveOptions


def detect_deepgram() -> CapabilityReport:
    version = _version("deepgram-sdk")
    try:
        if version is None:
            raise ImportError("deepgram-sdk is not installed")
        _deepgram_surface()
    except (ImportError, AttributeError, TypeError, ValueError, ProviderCompatibilityError) as exc:
        return CapabilityReport("deepgram", False, version, frozenset(), f"incompatible SDK surface: {type(exc).__name__}", installed=version is not None)
    return CapabilityReport("deepgram", True, version, frozenset({"streaming", "language_selection", "model_selection", "interim_results", "punctuation", "endpointing"}), installed=True, capable=True, api_surface="deepgram.LiveOptions + pipecat.services.deepgram.stt + websockets.connect")


def require_deepgram() -> tuple[Any, Any]:
    report = detect_deepgram()
    if not report.capable:
        if not report.installed:
            raise ProviderDependencyMissingError("Deepgram SDK/Pipecat STT dependency is unavailable", provider="deepgram")
        raise ProviderCompatibilityError("Installed Deepgram/Pipecat SDK does not expose the required streaming API", provider="deepgram")
    return _deepgram_surface()


def detect_provider(module: str, distribution: str, *, capabilities: set[str], required_symbol: str) -> CapabilityReport:
    version = _version(distribution)
    if version is None:
        return CapabilityReport(distribution, False, None, frozenset(), f"{distribution} is not installed")
    try:
        sdk = import_module(module)
        if not hasattr(sdk, required_symbol):
            raise AttributeError(f"required SDK symbol {required_symbol} is missing")
    except (ImportError, AttributeError, OSError, RuntimeError) as exc:
        return CapabilityReport(distribution, False, version, frozenset(), f"SDK surface unavailable: {type(exc).__name__}", installed=True)
    return CapabilityReport(distribution, True, version, frozenset(capabilities), installed=True, capable=True, api_surface=f"{module}.{required_symbol}")


def _configuration(provider: str) -> bool:
    from app.core.config import settings
    if provider == "deepgram":
        return bool((settings.deepgram_api_key or "").strip())
    if provider == "elevenlabs":
        return bool((settings.elevenlabs_api_key or "").strip())
    if provider in {"openai", "anthropic", "google"}:
        return bool((getattr(settings, f"{provider}_api_key", "") or "").strip())
    return False


def capabilities_for(provider: str) -> CapabilityReport:
    """Return separate configuration, installed-SDK, and API-capability states."""
    if provider == "deepgram":
        report = detect_deepgram()
    elif provider == "elevenlabs":
        version = _version("pipecat-ai")
        try:
            if version is None:
                raise ImportError("pipecat-ai is not installed")
            module = import_module("pipecat.services.elevenlabs.tts")
            symbol = "ElevenLabsTTSService"
            if not hasattr(module, symbol):
                raise AttributeError("Pipecat ElevenLabs TTS service is missing")
            report = CapabilityReport(provider, True, version, frozenset({"tts", "streaming", "voice_selection"}), installed=True, capable=True, api_surface="pipecat.services.elevenlabs.tts.ElevenLabsTTSService")
        except (ImportError, AttributeError, OSError, RuntimeError) as exc:
            report = CapabilityReport(provider, False, version, frozenset(), f"SDK surface unavailable: {type(exc).__name__}", installed=version is not None)
    else:
        candidates = {
            "openai": ("openai", "openai", {"chat", "embeddings"}, "AsyncOpenAI"),
            "anthropic": ("anthropic", "anthropic", {"messages"}, "AsyncAnthropic"),
            "google": ("google.genai", "google-genai", {"generate_content"}, "Client"),
        }
        if provider not in candidates:
            report = CapabilityReport(provider, False, None, frozenset(), "provider is not registered")
        else:
            module, dist, caps, symbol = candidates[provider]
            report = detect_provider(module, dist, capabilities=caps, required_symbol=symbol)
    return replace(report, configured=_configuration(provider))


def capability_matrix() -> dict[str, dict[str, Any]]:
    """Matrix for supported LLM, STT, and TTS integrations, with no network probes."""
    matrix = {name: capabilities_for(name).as_dict() for name in ("openai", "anthropic", "google", "deepgram", "elevenlabs")}
    llm_configured = any(matrix[name]["configured"] for name in ("openai", "anthropic", "google"))
    matrix["llm"] = {
        "provider": "llm",
        "configured": llm_configured,
        "installed": any(matrix[name]["installed"] for name in ("openai", "anthropic", "google")),
        "sdk_version": None,
        "capable": any(matrix[name]["capable"] and matrix[name]["configured"] for name in ("openai", "anthropic", "google")),
        "capabilities": sorted(set().union(*(matrix[name]["capabilities"] for name in ("openai", "anthropic", "google")))),
        "api_surface": "provider-specific SDKs",
        "reachable": "not_checked",
        "authenticated": "not_checked",
        "reason": None if llm_configured else "no LLM credentials configured",
    }
    return matrix


def require_capability(provider: str, capability: str) -> CapabilityReport:
    report = capabilities_for(provider)
    if not report.installed:
        raise ProviderDependencyMissingError(f"{provider} SDK is unavailable", provider=provider)
    if not report.capable:
        raise ProviderCompatibilityError(f"{provider} does not expose the required API", provider=provider)
    if capability not in report.capabilities:
        raise ProviderCompatibilityError(f"{provider} does not expose {capability}", provider=provider)
    return report
