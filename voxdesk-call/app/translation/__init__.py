"""Translation specialized-agent exports."""

from .engine import TranslationEngine
from .glossary import Glossary, GlossaryEntry
from .quality import QualityReport, validate_translation
from .schemas import TranslationJobRequest, TranslationJobResult, TranslationSegment

__all__ = [
    "Glossary",
    "GlossaryEntry",
    "QualityReport",
    "TranslationEngine",
    "TranslationJobRequest",
    "TranslationJobResult",
    "TranslationSegment",
    "validate_translation",
]
