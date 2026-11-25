"""Multilingual & Auto-Language Detection Resolver (Sub-Phase 2G).

Extends `app/core/i18n.py` (13 locales) with `language="multi"` / `"auto"`
real-time multilingual code-switching mode (`Deepgram nova-3` with `language="multi"`
and `ElevenLabs eleven_multilingual_v2`).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.core.i18n import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    deepgram_options,
    elevenlabs_options,
    get_profile,
    llm_language_instruction,
)

MULTILINGUAL_CODES = frozenset({"multi", "auto", "multilingual"})


@dataclass(frozen=True)
class LanguageRuntimeProfile:
    code: str
    is_multilingual: bool
    stt_language: str
    stt_model: str
    tts_model: str
    llm_instruction: str


#: ``resolve_language_config`` is called at least twice per call (once for the LLM
#: instruction, once by ``requires_runtime_stt``) and is a pure function of the
#: language code: it only reads the static locale tables in ``app/core/i18n.py``.
#: It returns a frozen dataclass, so sharing one instance between calls cannot be
#: mutated by a caller. Profiling the per-call preamble of
#: ``app/agent/pipeline.py`` put this at the top of the tree after the tool filter
#: (measured: PART 17B, docs/PERF_TUNING.md §6).
@lru_cache(maxsize=256)
def resolve_language_config(language: str | None = None) -> LanguageRuntimeProfile:
    """Resolve STT, TTS, and LLM language settings, including `multi`/`auto` detection."""
    raw = (language or DEFAULT_LANGUAGE).strip()
    if raw.lower() in MULTILINGUAL_CODES:
        return LanguageRuntimeProfile(
            code="multi",
            is_multilingual=True,
            stt_language="multi",
            stt_model="nova-3",
            tts_model="eleven_multilingual_v2",
            llm_instruction=(
                "\n\nLANGUAGE: Multilingual auto-detection is enabled. Detect the language "
                "spoken by the caller on each turn and reply fluently in that same language. "
                "If the caller switches languages mid-call, switch with them seamlessly."
            ),
        )

    prof = get_profile(raw)
    dg = deepgram_options(prof.code)
    el = elevenlabs_options(prof.code)
    return LanguageRuntimeProfile(
        code=prof.code,
        is_multilingual=False,
        stt_language=str(dg["language"]),
        stt_model=str(dg["model"]),
        tts_model=str(el["model"]),
        llm_instruction=llm_language_instruction(prof.code),
    )


def supported_languages_catalog() -> list[dict[str, Any]]:
    """Return all 13 supported locales plus `multi` auto-detect mode."""
    items = [
        {
            "code": prof.code,
            "label": prof.name,
            "deepgram_language": prof.stt_language,
            "deepgram_model": deepgram_options(prof.code)["model"],
            "elevenlabs_model": elevenlabs_options(prof.code)["model"],
            "multilingual": False,
        }
        for prof in LANGUAGES.values()
    ]
    items.append(
        {
            "code": "multi",
            "label": "Multilingual (Auto-Detect)",
            "deepgram_language": "multi",
            "deepgram_model": "nova-3",
            "elevenlabs_model": "eleven_multilingual_v2",
            "multilingual": True,
        }
    )
    return items


SWITCH_LANGUAGE_TOOL_SCHEMA: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "switch_language",
        "description": (
            "Switch the active conversation language and TTS voice when the caller "
            "speaks or requests another supported language."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "language": {
                    "type": "string",
                    "description": "Target BCP-47 language code (e.g. 'es', 'fr', 'de', 'ar', 'hi').",
                },
            },
            "required": ["language"],
        },
    },
}


def resolve_voice_for_language(
    language: str,
    voice_switch_map: dict[str, str] | None = None,
    default_voice_id: str | None = None,
) -> str | None:
    """Resolve the TTS `voice_id` for `language` from `voice_switch_map`."""
    if not voice_switch_map:
        return default_voice_id
    norm = (language or "").strip().lower()
    base = norm.split("-")[0]
    for candidate in (language, norm, base):
        if candidate and candidate in voice_switch_map:
            return voice_switch_map[candidate]
    return default_voice_id


def execute_switch_language(
    target_language: str,
    *,
    allowed_languages: list[str] | None = None,
    voice_switch_map: dict[str, str] | None = None,
    default_voice_id: str | None = None,
) -> dict[str, Any]:
    """Validate and switch the call language within `allowed_languages`."""
    raw = (target_language or "").strip().lower()
    base = raw.split("-")[0]
    if allowed_languages:
        allowed_norm = {lang.strip().lower().split("-")[0] for lang in allowed_languages}
        if "multi" not in allowed_norm and "auto" not in allowed_norm and base not in allowed_norm:
            raise ValueError(
                f"Language '{target_language}' is not in agent allowed_languages {allowed_languages}"
            )
    prof = resolve_language_config(target_language)
    voice_id = resolve_voice_for_language(prof.code, voice_switch_map, default_voice_id)
    return {
        "ok": True,
        "language": prof.code,
        "stt_language": prof.stt_language,
        "stt_model": prof.stt_model,
        "tts_model": prof.tts_model,
        "voice_id": voice_id,
    }



# ============================================ P12-07: per-agent language allowlist ==========

REPLY_POLICIES = ("follow_caller", "primary_only")


@dataclass(frozen=True)
class LanguagePolicy:
    """Which languages this agent may speak, and what it does outside that set.

    ``follow_caller`` replies in whatever allowed language the caller used; ``primary_only``
    always answers in ``primary`` even if the caller swapped. A language that is *not* allowed
    gets :attr:`fallback_phrase` and a flag, never a silent reply in the wrong language.
    """

    primary: str = DEFAULT_LANGUAGE
    allowlist: tuple[str, ...] = (DEFAULT_LANGUAGE,)
    reply_policy: str = "follow_caller"
    fallback_phrase: str = "Sorry, I can only help in English."
    detect_multilingual: bool = False

    def validate(self) -> list[str]:
        problems: list[str] = []
        if self.reply_policy not in REPLY_POLICIES:
            problems.append(f"languages.reply_policy must be one of {list(REPLY_POLICIES)}")
        if not self.allowlist:
            problems.append("languages.allowlist must contain at least one language")
        if len(self.allowlist) > 13:
            problems.append("languages.allowlist may contain at most 13 languages")
        unknown = [code for code in self.allowlist if not _is_known_language(code)]
        if unknown:
            problems.append(f"languages.allowlist contains unsupported codes: {unknown}")
        if not _is_known_language(self.primary):
            problems.append(f"languages.primary {self.primary!r} is not a supported language")
        if self.primary not in {code for code in self.allowlist} and _is_known_language(self.primary):
            problems.append("languages.primary must also appear in the allowlist")
        if not self.fallback_phrase or len(self.fallback_phrase) > 200:
            problems.append("languages.fallback_phrase must be 1..200 characters")
        if self.detect_multilingual and len(self.allowlist) < 2:
            problems.append("languages.detect_multilingual needs at least two allowed languages")
        return problems

    def allows(self, code: str) -> bool:
        raw = (code or "").strip()
        if not raw:
            return False
        if raw.lower() in {"multi", "auto", "multilingual"}:
            return self.detect_multilingual
        if raw in self.allowlist:
            return True
        base = raw.split("-")[0].lower()
        return any(code.split("-")[0].lower() == base for code in self.allowlist)

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary,
            "allowlist": list(self.allowlist),
            "reply_policy": self.reply_policy,
            "fallback_phrase": self.fallback_phrase,
            "detect_multilingual": self.detect_multilingual,
        }


def _is_known_language(code: str | None) -> bool:
    if not code:
        return False
    raw = code.strip()
    if raw.lower() in MULTILINGUAL_CODES:
        return True
    if raw in LANGUAGES:
        return True
    base = raw.split("-")[0].lower()
    return any(known.split("-")[0].lower() == base for known in LANGUAGES)


@lru_cache(maxsize=512)
def _resolved_policy_fields(
    codes: tuple[str, ...],
    language: str | None,
    reply_policy: str,
    fallback_phrase: str | None,
) -> tuple[str, tuple[str, ...], str, str, bool, tuple[str, ...]]:
    """The expensive half of :func:`build_language_policy`, memoized.

    Everything here is a pure function of the four arguments: locale-table
    lookups and the allowlist validation. The public builder still constructs a
    *fresh* ``LanguagePolicy`` per call, so caching cannot hand two callers the
    same mutable object.
    """
    detect = any(code.lower() in MULTILINGUAL_CODES for code in codes)
    known = [code for code in codes if code.lower() not in MULTILINGUAL_CODES]

    primary = (language or (known[0] if known else DEFAULT_LANGUAGE)).strip()
    if not known:
        known = [primary]
    if primary not in known:
        known = [primary, *known]

    if fallback_phrase is None:
        label = get_profile(primary).name if _is_known_language(primary) else primary
        fallback_phrase = f"Sorry, I can only help in {label}."

    policy = LanguagePolicy(
        primary=primary,
        allowlist=tuple(dict.fromkeys(known)),
        reply_policy=reply_policy if reply_policy in REPLY_POLICIES else "follow_caller",
        fallback_phrase=fallback_phrase,
        detect_multilingual=detect,
    )
    return (
        policy.primary,
        policy.allowlist,
        policy.reply_policy,
        policy.fallback_phrase,
        policy.detect_multilingual,
        tuple(policy.validate()),
    )


def build_language_policy(
    *,
    languages: Any = None,
    language: str | None = None,
    reply_policy: str = "follow_caller",
    fallback_phrase: str | None = None,
) -> LanguagePolicy:
    """Build a validated policy from agent config.

    ``languages`` may be a list of BCP-47 codes, a comma-separated string, or ``None`` (which
    means "the single configured language"). ``multi``/``auto`` in the list turns on detection.
    """
    raw_codes: list[str] = []
    if isinstance(languages, str):
        raw_codes = [part.strip() for part in languages.split(",") if part.strip()]
    elif isinstance(languages, (list, tuple, set)):
        raw_codes = [str(part).strip() for part in languages if str(part).strip()]
        if not raw_codes:
            # An explicitly empty allowlist is a misconfiguration, not "use the default": an
            # agent that can speak no language would otherwise silently speak the default one.
            raise ValueError("languages.allowlist must contain at least one language")

    fields = _resolved_policy_fields(tuple(raw_codes), language, reply_policy, fallback_phrase)
    policy = LanguagePolicy(
        primary=fields[0],
        allowlist=fields[1],
        reply_policy=fields[2],
        fallback_phrase=fields[3],
        detect_multilingual=fields[4],
    )
    if fields[5]:
        raise ValueError("; ".join(fields[5]))
    return policy


@dataclass(frozen=True)
class LanguageDecision:
    """What to do with a detected language."""

    allowed: bool
    reply_language: str
    spoken_language: str
    instruction: str = ""
    fallback_phrase: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reply_language": self.reply_language,
            "spoken_language": self.spoken_language,
            "instruction": self.instruction,
            "fallback_phrase": self.fallback_phrase,
        }


def decide_language(detected: str | None, policy: LanguagePolicy) -> LanguageDecision:
    """Map a detected language onto the policy without guessing outside the allowlist."""
    candidate = (detected or "").strip()
    if not candidate or candidate.lower() in MULTILINGUAL_CODES:
        return LanguageDecision(
            allowed=True,
            reply_language=policy.primary,
            spoken_language=policy.primary,
            instruction="",
        )
    if not policy.allows(candidate):
        return LanguageDecision(
            allowed=False,
            reply_language=policy.primary,
            spoken_language=policy.primary,
            instruction=(
                "The caller is speaking a language this agent does not support. Say the fallback "
                "phrase once, in the agent's primary language, and offer to continue there."
            ),
            fallback_phrase=policy.fallback_phrase,
        )
    if policy.reply_policy == "primary_only":
        return LanguageDecision(
            allowed=True,
            reply_language=policy.primary,
            spoken_language=policy.primary,
            instruction=(
                "The caller may speak another allowed language, but always reply in "
                f"{policy.primary}."
            ),
        )
    resolved = candidate if candidate in LANGUAGES else _canonical_code(candidate)
    return LanguageDecision(allowed=True, reply_language=resolved, spoken_language=resolved)


def _canonical_code(code: str) -> str:
    base = code.split("-")[0].lower()
    for known in LANGUAGES:
        if known.split("-")[0].lower() == base:
            return known
    return code


def detection_constraints(policy: LanguagePolicy) -> dict[str, Any]:
    """STT/LLM constraints that keep detection inside the allowlist."""
    return {
        "allowed_languages": list(policy.allowlist),
        "stt_language": "multi" if (policy.detect_multilingual and len(policy.allowlist) > 1) else policy.primary,
        "stt_model": "nova-3" if (policy.detect_multilingual and len(policy.allowlist) > 1) else "nova-2",
        "llm_instruction": (
            "Only speak these languages: "
            + ", ".join(policy.allowlist)
            + ". If the caller uses another language, apologise once in "
            + policy.primary
            + " and offer to continue in it."
        ),
        "fallback_phrase": policy.fallback_phrase,
    }
