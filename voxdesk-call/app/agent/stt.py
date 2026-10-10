"""Deepgram STT service construction (Step 3 voice-provider lifecycle).

The speech pipeline needs one thing from an STT provider: a pipecat service
that turns raw audio frames into transcription frames. This module owns the
Deepgram specifics — API-key validation, model/language resolution, and the
streaming configuration — so the pipeline stays provider-agnostic and a
misconfigured deployment fails fast with a typed error instead of a confusing
downstream crash.

Two defects fixed here (both were latent, never exercised by the test suite):

* pipecat 0.0.55's ``DeepgramSTTService`` requires ``live_options`` to be a
  ``LiveOptions`` instance (it calls ``live_options.to_dict()``). The old
  pipeline passed a plain dict, which raised ``AttributeError`` the moment a
  live call constructed the service.
* The same service has no ``model`` constructor parameter: the resolved model
  (nova-3 vs nova-2 fallback) was passed as a keyword that pipecat silently
  dropped, so every call used Deepgram's default model regardless of tenant
  language. The model now travels inside ``live_options`` and actually reaches
  Deepgram.

STEP 10 (pipecat 0.0.55 -> 0.0.94) import change: ``LiveOptions`` is no
longer defined by pipecat; pipecat 0.0.94 imports it from ``deepgram-sdk``.
The field set used here (encoding, sample_rate, punctuate, interim_results,
endpointing, smart_format, filler_words, language, model) is identical in
deepgram-sdk 4.7's dataclass, so the wire payload does not change.

The provider SDK is deliberately imported only when ``build_stt`` actually
constructs a live provider service (through ``require_deepgram``). Queueing a
transcript job and validating configuration do not need Pipecat or Deepgram;
importing them there initializes HTTP/TLS machinery on the request path and can
block or consume substantial memory. The historical ``DeepgramSTTService``
and ``LiveOptions`` module attributes remain available through ``__getattr__``
and are resolved lazily only when a caller explicitly requests them.

Capabilities mirror the contract style introduced for TTS in Step 2
(``app/agent/tts.py``): the pipeline checks ``supports_*`` before forwarding a
setting, so an unsupported setting is never passed and silently dropped.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipecat.services.deepgram.stt import DeepgramSTTService

from app.agent.errors import ProviderConfigurationError
from app.core.config import settings
from app.core.i18n import deepgram_options
from app.providers.compatibility import require_deepgram

#: What this Deepgram integration can and cannot map from tenant settings.
DEEPGRAM_CAPABILITIES = {
    "supports_streaming": True,        # websocket live transcription
    "supports_language": True,         # per-language model + language code
    "supports_interim_results": True,  # used for fast turn detection
    "supports_model_selection": True,  # nova-3 vs nova-2 fallback
    "supports_punctuation": True,
    "supports_filler_words": True,     # English models only
    "supports_pitch": False,           # not a transcription concept
}


def validate_stt_config() -> None:
    """Refuse to build an STT service with no Deepgram key configured.

    Without a key the websocket connect fails downstream in a way pipecat
    logs but does not surface clearly; failing here turns it into a typed
    ``configuration_error`` before the pipeline starts. This check intentionally
    does not import or probe the Deepgram/Pipecat SDK.
    """
    if not (settings.deepgram_api_key or "").strip():
        raise ProviderConfigurationError(
            "Deepgram STT API key is not configured",
            provider="deepgram",
        )


def build_stt(tenant) -> DeepgramSTTService:
    """Build the STT service for a tenant.

    Model/language resolution reuses ``deepgram_options`` — the same
    abstraction the pipeline used before this change, so a language that
    nova-3 does not cover still falls back to nova-2. Validation happens
    first so a missing key raises ``ProviderConfigurationError`` rather than
    constructing a service that can never connect. The SDK is imported only
    after configuration has been validated and a live service is requested.
    """
    validate_stt_config()
    # Check the actual installed SDK surfaces before constructing a service;
    # configured credentials alone are not proof that this runtime can stream.
    service_class, live_options_class = require_deepgram()

    # ভাষা অনুযায়ী STT সেটিং। nova-3 সব ভাষা কভার করে না -> nova-2 fallback।
    stt_opts = deepgram_options(tenant.language, settings.deepgram_model)
    return service_class(
        api_key=settings.deepgram_api_key,
        sample_rate=8000,
        live_options=live_options_class(
            encoding="mulaw",
            sample_rate=8000,
            punctuate=stt_opts["punctuate"],
            interim_results=True,     # দ্রুত turn detection-এর জন্য বাধ্যতামূলক
            endpointing=250,
            smart_format=stt_opts["smart_format"],
            # filler_words শুধু ইংরেজি মডেলে আছে
            filler_words=stt_opts["filler_words"],
            language=stt_opts["language"],
            model=stt_opts["model"],
        ),
    )


def build_stt_for_runtime(cfg, *, configured_settings=settings):
    """Build a Pipecat STT processor from `RuntimeConfig`, wrapping fallbacks in `FailoverServiceWrapper` (2C)."""
    from app.agent.providers.failover import FailoverServiceWrapper
    from app.agent.providers.registry import is_provider_configured
    from app.agent.providers.stt_providers import build_stt_provider

    primary = build_stt_provider(cfg=cfg, configured_settings=configured_settings)
    fallbacks = []
    for fb_prov in getattr(cfg, "stt_fallback_providers", ()) or ():
        if is_provider_configured("stt", fb_prov, configured_settings=configured_settings):
            fallbacks.append(
                (
                    fb_prov,
                    lambda p=fb_prov: build_stt_provider(
                        provider=p,
                        language=getattr(cfg, "language", "en-US"),
                        boosted_keywords=getattr(cfg, "boosted_keywords", None),
                        configured_settings=configured_settings,
                    ),
                )
            )
    if fallbacks:
        return FailoverServiceWrapper(
            stage="stt",
            primary=(str(getattr(cfg, "stt_provider", "deepgram")), primary),
            fallbacks=fallbacks,
        )
    return primary


def __getattr__(name: str):
    """Resolve legacy SDK class exports only when explicitly requested."""
    if name not in {"DeepgramSTTService", "LiveOptions"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    service_class, live_options_class = require_deepgram()
    globals()["DeepgramSTTService"] = service_class
    globals()["LiveOptions"] = live_options_class
    return globals()[name]


# Public contract exports. The existing Pipecat builder above remains the
# authoritative live-call integration; direct streaming consumers use the
# same typed provider contract without rewriting the current pipeline.
from app.agent.stt_stream import (  # noqa: E402
    DeepgramStreamingProvider,
    STTEvent,
    STTProvider,
    STTProviderCapabilities,
    STTStreamRequest,
)

__all__ = [
    "DEEPGRAM_CAPABILITIES",
    "DeepgramSTTService",
    "DeepgramStreamingProvider",
    "STTEvent",
    "STTProvider",
    "STTProviderCapabilities",
    "STTStreamRequest",
    "build_stt",
    "build_stt_for_runtime",
    "validate_stt_config",
]
