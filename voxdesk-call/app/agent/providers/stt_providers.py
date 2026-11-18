"""Multi-provider STT builders with keyword boosting support (Sub-Phase 2C)."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Callable, Sequence

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
    endpointing_mode: str | None = None,
    medical: bool = False,
) -> FrameProcessor:
    """Build a Pipecat STT processor for the requested provider (Sub-Phase 2C)."""
    medical_terms: Sequence[str] | None = None
    if cfg is not None:
        provider = provider or getattr(cfg, "stt_provider", "deepgram")
        model = model or getattr(cfg, "stt_model", None)
        language = getattr(cfg, "stt_language", None) or getattr(cfg, "language", language) or language
        endpointing_mode = endpointing_mode or getattr(cfg, "stt_endpointing_mode", None)
        medical = bool(medical or getattr(cfg, "stt_medical_vocabulary", False))
        medical_terms = getattr(cfg, "stt_medical_terms", None)
        if boosted_keywords is None:
            boosted_keywords = getattr(cfg, "boosted_keywords", None)

    spec = require_provider_configured(
        "stt", provider or "deepgram", configured_settings=configured_settings
    )
    resolved_model = model or spec.default_model
    kw_list = format_boosted_keywords(boosted_keywords)
    endpoint_ms = endpointing_ms(endpointing_mode)
    medical_terms = medical_vocabulary(medical, language=language, extra_terms=medical_terms)

    if spec.provider == "deepgram":
        api_key = get_provider_credential(
            "DEEPGRAM_API_KEY", "deepgram_api_key", configured_settings
        )
        service_class, live_options_class = require_deepgram()
        stt_opts = deepgram_options(language, resolved_model)
        if str(language).strip().lower() in {"multi", "auto", "multilingual"}:
            stt_opts = {**stt_opts, "model": resolved_model or "nova-3", "language": "multi", "filler_words": False}
        live_kwargs: dict[str, Any] = {
            "encoding": "mulaw",
            "sample_rate": sample_rate,
            "punctuate": stt_opts["punctuate"],
            "interim_results": True,
            "endpointing": endpoint_ms,
            "smart_format": stt_opts["smart_format"],
            "filler_words": stt_opts["filler_words"],
            "language": stt_opts["language"],
            "model": stt_opts["model"],
        }
        if medical_terms and str(resolved_model).startswith("nova-3"):
            # nova-3 uses `keyterm` prompting; nova-2 uses `keywords=word:boost`.
            live_kwargs["keyterm"] = medical_terms
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

    if spec.provider == "soniox":
        api_key = get_provider_credential("SONIOX_API_KEY", "soniox_api_key", configured_settings)
        endpoint_preset = soniox_endpointing_preset(endpointing_mode)
        allowed = getattr(cfg, "allowed_languages", ()) if cfg is not None else ()
        language_hints = tuple(
            dict.fromkeys(
                str(code).split("-")[0].lower()
                for code in allowed
                if str(code).strip().lower() not in {"multi", "auto", "multilingual"}
            )
        )
        if not language_hints:
            language_hints = (language.split("-")[0].lower(),)
        return SonioxSTTProvider(
            api_key=api_key,
            config=SonioxSessionConfig(
                model=resolved_model,
                sample_rate=sample_rate,
                language_hints=language_hints,
                endpoint_sensitivity=endpoint_preset["endpoint_sensitivity"],
                max_endpoint_delay_ms=endpoint_preset["max_endpoint_delay_ms"],
                terms=tuple(medical_terms) or tuple(kw_list),
            ),
        )

    # whisper_local
    return AdapterSTTService(
        provider="whisper_local",
        model=resolved_model,
        language=language,
        sample_rate=sample_rate,
        keywords=kw_list,
    )

# ---------------------------------------------------------------------------
# PART 12 (P12-08) — endpointing presets, medical vocabulary, Soniox
# ---------------------------------------------------------------------------

#: Deepgram's `endpointing` parameter is silence in milliseconds before an utterance is closed.
#: These are the three positions we expose; they are deliberately a small set, because every extra
#: preset is another combination nobody tests. `default` is the value the pipeline shipped with.
ENDPOINTING_PRESETS: dict[str, int] = {
    "default": 250,
    "accuracy": 400,
    "latency": 100,
}


def endpointing_ms(mode: str | None) -> int:
    """Resolve an endpointing preset to the Deepgram millisecond value.

    An unknown mode raises rather than falling back: a typo in a config should fail the publish,
    not silently pick a different responsiveness than the customer asked for.
    """
    key = (mode or "default").strip().lower()
    if key not in ENDPOINTING_PRESETS:
        raise ValueError(
            f"unknown stt_endpointing_mode {mode!r}; expected one of {sorted(ENDPOINTING_PRESETS)}"
        )
    return ENDPOINTING_PRESETS[key]


SONIOX_ENDPOINTING_PRESETS: dict[str, dict[str, float | int]] = {
    "default": {"endpoint_sensitivity": 0.7, "max_endpoint_delay_ms": 1000},
    "accuracy": {"endpoint_sensitivity": 0.55, "max_endpoint_delay_ms": 1500},
    "latency": {"endpoint_sensitivity": 0.85, "max_endpoint_delay_ms": 600},
}


def soniox_endpointing_preset(mode: str | None) -> dict[str, float | int]:
    """Resolve the common endpointing mode to Soniox's sensitivity and max-delay controls."""
    key = (mode or "default").strip().lower()
    if key not in SONIOX_ENDPOINTING_PRESETS:
        raise ValueError(
            f"unknown stt_endpointing_mode {mode!r}; "
            f"expected one of {sorted(SONIOX_ENDPOINTING_PRESETS)}"
        )
    return dict(SONIOX_ENDPOINTING_PRESETS[key])


#: A deliberately short, English-only list. Deepgram's medical models boost these without extra
#: configuration; for `keyterm` prompting we send them explicitly. Anything longer starts to
#: bias the decoder towards medical words in ordinary conversation, which is the failure mode
#: customers actually complain about ("it thought I said hypertension when I said tension").
MEDICAL_VOCABULARY_EN: tuple[str, ...] = (
    "metformin",
    "atorvastatin",
    "lisinopril",
    "levothyroxine",
    "albuterol",
    "omeprazole",
    "metoprolol",
    "gabapentin",
    "acetaminophen",
    "ibuprofen",
    "hypertension",
    "hypotension",
    "tachycardia",
    "bradycardia",
    "myocardial infarction",
    "pulmonary embolism",
    "deep vein thrombosis",
    "HbA1c",
    "creatinine",
    "milligrams",
    "milliliters",
    "subcutaneous",
    "intramuscular",
    "pro re nata",
)


def medical_vocabulary(
    enabled: bool,
    *,
    language: str = "en-US",
    extra_terms: Sequence[str] | None = None,
) -> list[str]:
    """Return the boosted-term list for a medical deployment, or `[]` when disabled.

    English only: the same word can be an ordinary word in another language, and boosting it there
    costs accuracy on normal conversation with nothing to show for it.
    """
    if not enabled:
        return []
    if not str(language).lower().startswith("en"):
        return []
    terms = list(MEDICAL_VOCABULARY_EN)
    for term in extra_terms or ():
        cleaned = str(term).strip()
        if cleaned and cleaned not in terms:
            terms.append(cleaned)
    return terms


SONIOX_WS_URL = "wss://stt-rt.soniox.com/transcribe-websocket"


@dataclass(frozen=True)
class SonioxSessionConfig:
    """The first WebSocket message Soniox expects, built from our own settings.

    Kept as data so the request shape can be asserted in a test without a socket. Soniox reports
    tokens with `is_final` and closes an utterance with an explicit `<end>` token; both are handled
    in :class:`SonioxSTTProvider` rather than guessed at.
    """

    model: str = "stt-rt-v3"
    sample_rate: int = 8000
    encoding: str = "mulaw"
    language_hints: tuple[str, ...] = ("en",)
    enable_endpoint_detection: bool = True
    endpoint_sensitivity: float = 0.7
    max_endpoint_delay_ms: int = 1000
    client_reference_id: str = ""
    terms: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not (0.0 <= self.endpoint_sensitivity <= 1.0):
            raise ValueError("endpoint_sensitivity must be between 0 and 1")
        if self.sample_rate not in (8000, 16000):
            raise ValueError("Soniox real-time accepts 8000 or 16000 Hz")

    def to_wire(self, *, api_key: str) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "api_key": api_key,
            "model": self.model,
            "audio_format": self.encoding,
            "sample_rate": self.sample_rate,
            "num_channels": 1,
            "enable_endpoint_detection": self.enable_endpoint_detection,
            "endpoint_sensitivity": self.endpoint_sensitivity,
            "max_endpoint_delay_ms": self.max_endpoint_delay_ms,
            "language_hints": list(self.language_hints),
        }
        if self.client_reference_id:
            payload["client_reference_id"] = self.client_reference_id
        if self.terms:
            payload["context"] = {"terms": list(self.terms)}
        return payload


class SonioxSTTProvider(FrameProcessor):
    """A Soniox real-time STT processor driven through an injected transport.

    The transport is injected (`send` / `recv`) instead of opened here for two reasons: the request
    shape and the transcript assembly are then testable without a socket, and a reconnect policy
    belongs to the pipeline, not to a leaf provider.
    """

    def __init__(
        self,
        *,
        api_key: str,
        config: SonioxSessionConfig | None = None,
        send: Callable[[str], Any] | None = None,
        on_transcript: Callable[[str, bool], Any] | None = None,
    ) -> None:
        super().__init__()
        self._api_key = api_key
        self.config = config or SonioxSessionConfig()
        self._send = send
        self._on_transcript = on_transcript
        self.opened = False
        self.final_transcripts: list[str] = []
        self.utterances = 0

    @property
    def provider(self) -> str:
        return "soniox"

    def open_message(self) -> str:
        if not self._api_key:
            raise ValueError("soniox requires an API key")
        self.opened = True
        return json.dumps(self.config.to_wire(api_key=self._api_key), separators=(",", ":"))

    async def push_audio(self, chunk: bytes) -> None:
        """Send audio, opening the session first so the service never sees a bare frame."""
        if self._send is None:
            raise RuntimeError("SonioxSTTProvider needs a `send` transport to push audio")
        if not self.opened:
            await self._send(self.open_message())
        await self._send(base64.b64encode(chunk).decode("ascii"))

    def handle_message(self, raw: str | bytes | dict[str, Any]) -> str:
        """Fold a Soniox event into transcript text and return what became final this time.

        Final tokens are concatenated until the `<end>` marker, which closes the utterance; that is
        the point at which a `TranscriptionFrame` should be emitted downstream. Returning the final
        text (rather than pushing frames) keeps this function synchronous and testable.
        """
        data = json.loads(raw) if isinstance(raw, (str, bytes)) else dict(raw)
        tokens = data.get("tokens") or []
        finals: list[str] = []
        ended = False
        for token in tokens:
            text = str(token.get("text", ""))
            if text == "<end>":
                ended = True
                continue
            if token.get("is_final"):
                finals.append(text)
        if not finals and not ended:
            return ""
        text = "".join(finals).strip()
        if text and self._on_transcript is not None:
            self._on_transcript(text, ended)
        if text:
            self.final_transcripts.append(text)
        if ended:
            self.utterances += 1
        return text

