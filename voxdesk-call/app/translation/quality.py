"""Deterministic translation integrity and review checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from app.specialized_agents.enums import QualityState

from .glossary import Glossary
from .schemas import TranslationQualityFlag, TranslationSegment

_URL_RE = re.compile(r"https?://[^\s]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
_NUMBER_RE = re.compile(r"(?<!\w)\d+(?:[.,]\d+)?(?:%|\b)")
_PLACEHOLDER_RE = re.compile(r"(?:\{\{[^{}]+\}\}|\{\d+\}|%[a-zA-Z]|<[^>]+>)")


@dataclass(frozen=True)
class QualityReport:
    status: str
    flags: tuple[TranslationQualityFlag, ...]
    review_required: bool


def _flag(code: str, message: str, segment_id: str | None = None) -> TranslationQualityFlag:
    return TranslationQualityFlag(code=code, message=message, segment_id=segment_id, review_required=True)


def check_segment(source: str, target: str, segment_id: str, glossary: Glossary) -> list[TranslationQualityFlag]:
    flags: list[TranslationQualityFlag] = []
    if not target.strip():
        flags.append(_flag("empty_target", "Target output is empty", segment_id))
        return flags
    if source.strip() == target.strip():
        flags.append(_flag("untranslated_source", "Target is identical to the source", segment_id))
    source_placeholders = sorted(_PLACEHOLDER_RE.findall(source))
    target_placeholders = sorted(_PLACEHOLDER_RE.findall(target))
    if source_placeholders != target_placeholders:
        flags.append(_flag("placeholder_corruption", "Placeholders were not preserved exactly", segment_id))
    if _NUMBER_RE.findall(source) != _NUMBER_RE.findall(target):
        flags.append(_flag("number_corruption", "Numeric tokens were not preserved", segment_id))
    if _URL_RE.findall(source) != _URL_RE.findall(target):
        flags.append(_flag("url_corruption", "URLs were not preserved", segment_id))
    if _EMAIL_RE.findall(source) != _EMAIL_RE.findall(target):
        flags.append(_flag("email_corruption", "Email addresses were not preserved", segment_id))
    for entry in glossary.active_entries():
        if entry.preserve_exact and entry.source_term.casefold() in source.casefold():
            if entry.target_term not in target:
                flags.append(_flag("glossary_term_missing", f"Required glossary term is missing: {entry.target_term}", segment_id))
    return flags


def validate_translation(
    segments: Iterable[TranslationSegment],
    glossary: Glossary,
) -> QualityReport:
    flags: list[TranslationQualityFlag] = []
    seen: set[str] = set()
    for segment in segments:
        if segment.segment_id in seen:
            flags.append(_flag("segment_mismatch", "Segment identifiers are duplicated", segment.segment_id))
        seen.add(segment.segment_id)
        flags.extend(check_segment(segment.source_text, segment.translated_text or "", segment.segment_id, glossary))
    status = QualityState.DEGRADED.value if flags else QualityState.VERIFIED.value
    return QualityReport(status=status, flags=tuple(flags), review_required=bool(flags))
