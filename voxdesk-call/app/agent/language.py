"""Multilingual & Auto-Language Detection Resolver (Sub-Phase 2G).

Extends `app/core/i18n.py` (13 locales) with `language="multi"` / `"auto"`
real-time multilingual code-switching mode (`Deepgram nova-3` with `language="multi"`
and `ElevenLabs eleven_multilingual_v2`).
"""

from __future__ import annotations

from dataclasses import dataclass
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

