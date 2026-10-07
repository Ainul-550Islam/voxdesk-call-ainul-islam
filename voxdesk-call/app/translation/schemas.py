"""Versioned translation contracts and review metadata."""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.specialized_agents.enums import QualityState, ReviewState
from app.specialized_agents.schemas import SourceReferenceIn


class TranslationModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TranslationSegment(BaseModel):
    segment_id: str = Field(min_length=1, max_length=200)
    source_text: str = Field(min_length=1, max_length=20_000)
    translated_text: str | None = Field(default=None, max_length=20_000)
    source_fingerprint: str | None = None
    target_fingerprint: str | None = None

    @model_validator(mode="after")
    def fingerprints_match(self):
        source_digest = hashlib.sha256(self.source_text.encode("utf-8")).hexdigest()
        if self.source_fingerprint and self.source_fingerprint.lower() != source_digest:
            raise ValueError("source_fingerprint does not match source_text")
        if self.translated_text is not None:
            target_digest = hashlib.sha256(self.translated_text.encode("utf-8")).hexdigest()
            if self.target_fingerprint and self.target_fingerprint.lower() != target_digest:
                raise ValueError("target_fingerprint does not match translated_text")
        return self


class GlossaryEntryInput(BaseModel):
    source_term: str = Field(min_length=1, max_length=500)
    target_term: str = Field(min_length=1, max_length=500)
    domain: str = Field(default="general", max_length=200)
    case_behavior: str = Field(default="preserve", max_length=32)
    preserve_exact: bool = False
    notes: str | None = Field(default=None, max_length=2000)
    version: str = Field(min_length=1, max_length=100)
    active: bool = True


class TranslationJobRequest(BaseModel):
    environment_id: uuid.UUID
    model_version_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    source_language: str = Field(min_length=2, max_length=32)
    target_language: str = Field(min_length=2, max_length=32)
    segments: list[TranslationSegment] = Field(min_length=1, max_length=100)
    glossary_version: str = Field(min_length=1, max_length=100)
    glossary: list[GlossaryEntryInput] = Field(default_factory=list, max_length=500)
    risk_tier: str = Field(default="moderate", min_length=1, max_length=24)
    agent_version: str = Field(default="1.0.0", min_length=1, max_length=100)
    source_references: list[SourceReferenceIn] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def validate_languages(self):
        if self.source_language.casefold() == self.target_language.casefold():
            raise ValueError("source_language and target_language must differ")
        return self


class TranslationQualityFlag(TranslationModel):
    code: str
    message: str
    segment_id: str | None = None
    review_required: bool = True


class TranslationSegmentResult(TranslationModel):
    segment_id: str
    source_fingerprint: str
    target_fingerprint: str
    translated_text: str


class TranslationJobResult(TranslationModel):
    execution_id: uuid.UUID | None = None
    source_language: str
    target_language: str
    glossary_version: str
    segments: list[TranslationSegmentResult]
    quality_status: str
    quality_flags: list[TranslationQualityFlag]
    review_required: bool
    review_state: str
    provider: str | None = None
    model: str | None = None
    methodology: str = "Provider-generated translation followed by deterministic integrity checks; no human certification is implied."


class TranslationMetadata(BaseModel):
    source_language: str
    target_language: str
    glossary_version: str
    quality_status: str = QualityState.DEGRADED.value
    review_state: str = ReviewState.REQUIRED.value
    metadata: dict[str, Any] = Field(default_factory=dict)
