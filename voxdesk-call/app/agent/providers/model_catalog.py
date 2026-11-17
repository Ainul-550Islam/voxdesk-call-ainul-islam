"""Model catalogue: one place that says what each provider model can do (P12-10).

Three problems this solves, all of which currently live as scattered string literals:

1. **Capability drift.** "Does this model support tools?" was answered by whichever module
   happened to hard-code it. Here each :class:`ModelSpec` carries the answer once.
2. **Deprecation.** Providers retire model ids silently. A spec with ``deprecated_on`` in the
   past fails a test instead of failing a customer call.
3. **Cost.** ``usd_per_minute`` is an **estimate from published list prices**, and it is
   ``None`` wherever nobody has verified a number. A blank is honest; a made-up figure in a
   pricing deck is not.

``scripts/refresh_model_catalog.py`` diffs this file against provider model lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

KINDS = ("llm", "stt", "tts", "s2s")
LATENCY_TIERS = ("fast", "balanced", "quality")


@dataclass(frozen=True)
class ModelSpec:
    """What one provider model is and what it can do."""

    provider: str
    model_id: str
    kind: str
    latency_tier: str = "balanced"
    context_window: int | None = None
    supports_tools: bool = False
    supports_structured: bool = False
    supports_realtime: bool = False
    supports_streaming: bool = True
    usd_per_minute: float | None = None
    deprecated_on: str | None = None
    notes: str = ""

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.kind not in KINDS:
            problems.append(f"{self.provider}/{self.model_id}: kind must be one of {list(KINDS)}")
        if self.latency_tier not in LATENCY_TIERS:
            problems.append(f"{self.provider}/{self.model_id}: unknown latency tier {self.latency_tier!r}")
        if self.supports_structured and not self.supports_tools and self.kind == "llm":
            problems.append(
                f"{self.provider}/{self.model_id}: structured output without tool support is "
                "not a combination any provider offers"
            )
        if self.deprecated_on:
            try:
                date.fromisoformat(self.deprecated_on)
            except ValueError:
                problems.append(f"{self.provider}/{self.model_id}: deprecated_on must be an ISO date")
        if self.usd_per_minute is not None and self.usd_per_minute < 0:
            problems.append(f"{self.provider}/{self.model_id}: usd_per_minute cannot be negative")
        return problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "kind": self.kind,
            "latency_tier": self.latency_tier,
            "context_window": self.context_window,
            "supports_tools": self.supports_tools,
            "supports_structured": self.supports_structured,
            "supports_realtime": self.supports_realtime,
            "supports_streaming": self.supports_streaming,
            "usd_per_minute": self.usd_per_minute,
            "deprecated_on": self.deprecated_on,
            "notes": self.notes,
        }

    def is_deprecated(self, today: date | None = None) -> bool:
        if not self.deprecated_on:
            return False
        return date.fromisoformat(self.deprecated_on) <= (today or date.today())


#: The catalogue. ``usd_per_minute`` is left ``None`` where no price has been verified in this
#: repository; the values that are present come from ``docs/CAPACITY_MODEL.md`` list prices and
#: describe a *continuous* call, so they are per-concurrent-call-minute, not per-token.
CATALOG: tuple[ModelSpec, ...] = (
    # ---- LLM -------------------------------------------------------------------------------
    ModelSpec("openai", "gpt-4o-mini", "llm", "fast", 128_000, supports_tools=True, supports_structured=True,
              usd_per_minute=0.00300, notes="default cascaded LLM; price from CAPACITY_MODEL.md"),
    ModelSpec("openai", "gpt-4o", "llm", "quality", 128_000, supports_tools=True, supports_structured=True),
    ModelSpec("anthropic", "claude-3-5-haiku-latest", "llm", "fast", 200_000,
              supports_tools=True,
              supports_structured=True),
    ModelSpec("anthropic", "claude-3-7-sonnet-latest", "llm", "quality", 200_000,
              supports_tools=True,
              supports_structured=True),
    ModelSpec("google", "gemini-1.5-flash", "llm", "fast", 1_000_000, supports_tools=True, supports_structured=True),
    # ---- STT -------------------------------------------------------------------------------
    ModelSpec("deepgram", "nova-2", "stt", "fast", None, notes="default streaming STT"),
    ModelSpec("deepgram", "nova-3", "stt", "fast", None, supports_realtime=True,
              notes="multilingual code-switching (`language=multi`)"),
    ModelSpec("soniox", "stt-rt-v3", "stt", "fast", None, supports_realtime=True,
              notes="P12-08; request shape in tests/agent/test_stt_soniox.py"),
    # ---- TTS -------------------------------------------------------------------------------
    ModelSpec("elevenlabs", "eleven_turbo_v2_5", "tts", "fast", None,
              notes="default streaming TTS; ~400 chars/min list price in CAPACITY_MODEL.md"),
    ModelSpec("elevenlabs", "eleven_multilingual_v2", "tts", "balanced"),
    ModelSpec("elevenlabs", "eleven_v3", "tts", "quality", None,
              notes="P12-09 expressive tags"),
    ModelSpec("cartesia", "sonic-english", "tts", "fast"),
    ModelSpec("cartesia", "sonic-3", "tts", "fast", None, notes="P12-09 emotion parameter"),
    ModelSpec("minimax", "speech-02-hd", "tts", "balanced", None, notes="P12-09"),
    ModelSpec("fish_audio", "fish-speech-1.5", "tts", "balanced", None, notes="P12-09"),
    # ---- Speech-to-speech ------------------------------------------------------------------
    ModelSpec("openai", "gpt-4o-realtime-preview", "s2s", "balanced", None, supports_tools=True,
              supports_realtime=True, notes="live-tested only with provider keys (PART 11)"),
    ModelSpec("google", "models/gemini-2.0-flash-exp", "s2s", "balanced", None,
              supports_realtime=True),
)

_BY_KEY: dict[tuple[str, str], ModelSpec] = {(spec.provider, spec.model_id): spec for spec in CATALOG}


class UnknownModelError(KeyError):
    """Raised when a model id is not in the catalogue — never silently defaulted."""


def validate_catalog(specs: Iterable[ModelSpec] = CATALOG) -> list[str]:
    problems: list[str] = []
    seen: set[tuple[str, str]] = set()
    for spec in specs:
        key = (spec.provider, spec.model_id)
        if key in seen:
            problems.append(f"duplicate catalogue entry {spec.provider}/{spec.model_id}")
        seen.add(key)
        problems.extend(spec.validate())
    return problems


def get_model(provider: str, model_id: str) -> ModelSpec:
    try:
        return _BY_KEY[(provider, model_id)]
    except KeyError as exc:
        known = sorted(model for prov, model in _BY_KEY if prov == provider)
        raise UnknownModelError(
            f"No catalogue entry for {provider}/{model_id}. Known {provider} models: {known}"
        ) from exc


def models_for(*, provider: str | None = None, kind: str | None = None,
               include_deprecated: bool = True) -> list[ModelSpec]:
    specs = [
        spec
        for spec in CATALOG
        if (provider is None or spec.provider == provider) and (kind is None or spec.kind == kind)
    ]
    if not include_deprecated:
        specs = [spec for spec in specs if not spec.is_deprecated()]
    return specs


def capabilities(provider: str, model_id: str) -> dict[str, Any]:
    spec = get_model(provider, model_id)
    return {
        "tools": spec.supports_tools,
        "structured": spec.supports_structured,
        "realtime": spec.supports_realtime,
        "streaming": spec.supports_streaming,
        "context_window": spec.context_window,
        "latency_tier": spec.latency_tier,
        "deprecated": spec.is_deprecated(),
    }


def default_for(provider: str, kind: str) -> ModelSpec:
    """The best non-deprecated model for a provider/kind, preferring the ``fast`` tier."""
    candidates = [spec for spec in models_for(provider=provider, kind=kind, include_deprecated=False)]
    if not candidates:
        raise UnknownModelError(f"No non-deprecated {kind} model catalogued for {provider}")
    order = {"fast": 0, "balanced": 1, "quality": 2}
    candidates.sort(key=lambda spec: (order[spec.latency_tier], spec.model_id))
    return candidates[0]


def deprecated_models(today: date | None = None) -> list[ModelSpec]:
    return [spec for spec in CATALOG if spec.is_deprecated(today)]


def catalog_as_dicts() -> list[dict[str, Any]]:
    return [spec.as_dict() for spec in CATALOG]


def catalog_index() -> dict[str, list[str]]:
    """``{provider: [model ids]}`` for docs and for the refresh script's diff."""
    index: dict[str, list[str]] = {}
    for spec in CATALOG:
        index.setdefault(spec.provider, []).append(spec.model_id)
    return {provider: sorted(ids) for provider, ids in sorted(index.items())}
