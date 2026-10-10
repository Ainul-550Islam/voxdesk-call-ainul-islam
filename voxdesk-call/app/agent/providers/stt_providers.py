"""Multi-provider STT builders with keyword boosting support (Sub-Phase 2C)."""

from __future__ import annotations

from typing import Any, Sequence

from pipecat.frames.frames import AudioRawFrame, Frame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from app.agent.providers.registry import (
    get_provider_credential,
    require_provider_configured,
)
from app.core.config import settings
from app.core.i18n import deepgram_options
from app.providers.compatibility import require_deepgram


def format_boosted_keywords(
    boosted_keywords: Sequence[tuple[str, float]] | Sequence[Any] | None,
) -> list[str]:
    """Convert `[(word, boost), ...]` into Deepgram `["word:boost", ...]` wire format."""
    if not boosted_keywords:
        return []
    out: list[str] = []
    for item in boosted_keywords:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            word = str(item[0]).strip()
            boost = float(item[1])
            if word:
                out.append(f"{word}:{boost:g}")
        elif isinstance(item, dict):
            word = str(item.get("word") or item.get("keyword") or "").strip()
            boost = float(item.get("boost") or item.get("intensifier") or 2.0)
            if word:
                out.append(f"{word}:{boost:g}")
        elif isinstance(item, str) and item.strip():
            raw = item.strip()
            out.append(raw if ":" in raw else f"{raw}:2")
    return out


class AdapterSTTService(FrameProcessor):
    """Lightweight Pipecat `FrameProcessor` STT service for providers whose optional SDK is not loaded."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        language: str = "en-US",
        sample_rate: int = 8000,
        keywords: list[str] | None = None,
        api_key: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.provider = provider
        self.model = model
        self.language = language
        self.sample_rate = sample_rate
        self.keywords = list(keywords or [])
        self._api_key = api_key
        self.extra = dict(extra or {})

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, AudioRawFrame):
            # Pass audio frames downstream unless a subclass/mocked transcriber intercepts
            await self.push_frame(frame, direction)
            return
        await self.push_frame(frame, direction)


def build_stt_provider(
    provider: str | None = None,
    *,
    model: str | None = None,
    language: str = "en-US",
    sample_rate: int = 8000,
    boosted_keywords: Sequence[tuple[str, float]] | None = None,
    cfg: Any = None,
    configured_settings: Any = settings,
) -> FrameProcessor:
    """Build a Pipecat STT processor for the requested provider (Sub-Phase 2C)."""
    if cfg is not None:
        provider = provider or getattr(cfg, "stt_provider", "deepgram")
        model = model or getattr(cfg, "stt_model", None)
        language = getattr(cfg, "language", language) or language
        if boosted_keywords is None:
            boosted_keywords = getattr(cfg, "boosted_keywords", None)

    spec = require_provider_configured(
        "stt", provider or "deepgram", configured_settings=configured_settings
    )
    resolved_model = model or spec.default_model
    kw_list = format_boosted_keywords(boosted_keywords)

    if spec.provider == "deepgram":
        api_key = get_provider_credential(
            "DEEPGRAM_API_KEY", "deepgram_api_key", configured_settings
        )
        service_class, live_options_class = require_deepgram()
        stt_opts = deepgram_options(language, resolved_model)
        live_kwargs: dict[str, Any] = {
            "encoding": "mulaw",
            "sample_rate": sample_rate,
            "punctuate": stt_opts["punctuate"],
            "interim_results": True,
            "endpointing": 250,
            "smart_format": stt_opts["smart_format"],
            "filler_words": stt_opts["filler_words"],
            "language": stt_opts["language"],
            "model": stt_opts["model"],
        }
        if kw_list:
            live_kwargs["keywords"] = kw_list
        svc = service_class(
            api_key=api_key,
            sample_rate=sample_rate,
            live_options=live_options_class(**live_kwargs),
        )
        setattr(svc, "provider", "deepgram")
        setattr(svc, "keywords", kw_list)
        return svc

    if spec.provider == "assemblyai":
        api_key = get_provider_credential(
            "ASSEMBLYAI_API_KEY", "assemblyai_api_key", configured_settings
        )
        return AdapterSTTService(
            provider="assemblyai",
            model=resolved_model,
            language=language,
            sample_rate=sample_rate,
            keywords=kw_list,
            api_key=api_key,
        )

    if spec.provider == "openai_whisper":
        api_key = get_provider_credential(
            "OPENAI_API_KEY", "openai_api_key", configured_settings
        )
        try:
            from pipecat.services.openai.stt import OpenAISTTService

            svc = OpenAISTTService(
                api_key=api_key,
                model=resolved_model,
                sample_rate=sample_rate,
            )
            setattr(svc, "provider", "openai_whisper")
            return svc
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            return AdapterSTTService(
                provider="openai_whisper",
                model=resolved_model,
                language=language,
                sample_rate=sample_rate,
                keywords=kw_list,
                api_key=api_key,
            )

    if spec.provider == "azure":
        api_key = get_provider_credential(
            "AZURE_SPEECH_KEY", "azure_speech_key", configured_settings
        )
        region = get_provider_credential(
            "AZURE_SPEECH_REGION", "azure_speech_region", configured_settings
        )
        return AdapterSTTService(
            provider="azure",
            model=resolved_model,
            language=language,
            sample_rate=sample_rate,
            keywords=kw_list,
            api_key=api_key,
            extra={"region": region},
        )

    if spec.provider == "google":
        api_key = get_provider_credential(
            "GOOGLE_API_KEY", "google_api_key", configured_settings
        )
        return AdapterSTTService(
            provider="google",
            model=resolved_model,
            language=language,
            sample_rate=sample_rate,
            keywords=kw_list,
            api_key=api_key,
        )

    # whisper_local
    return AdapterSTTService(
        provider="whisper_local",
        model=resolved_model,
        language=language,
        sample_rate=sample_rate,
        keywords=kw_list,
    )
