"""Structured contracts for AI-assisted legal document analysis."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.specialized_agents.enums import Confidence
from app.specialized_agents.schemas import SourceReferenceIn


class LegalModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DocumentChunk(BaseModel):
    document_id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=200)
    source_title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=50_000)
    page_number: int | None = Field(default=None, ge=1)
    character_start: int | None = Field(default=None, ge=0)
    character_end: int | None = Field(default=None, ge=0)
    retrieval_timestamp: dt.datetime | None = None
    content_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")

    @model_validator(mode="after")
    def validate_boundaries(self):
        if self.character_start is not None and self.character_end is not None:
            if self.character_end < self.character_start:
                raise ValueError("character_end cannot precede character_start")
            if self.character_end - self.character_start != len(self.text):
                raise ValueError("character offsets must cover the supplied chunk")
        return self

    def source_reference(self) -> SourceReferenceIn:
        import hashlib

        fingerprint = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.content_fingerprint and self.content_fingerprint.lower() != fingerprint:
            raise ValueError("content_fingerprint does not match the supplied chunk")
        return SourceReferenceIn(
            document_id=self.document_id,
            chunk_id=self.chunk_id,
            source_title=self.source_title,
            content_fingerprint=fingerprint,
            page_number=self.page_number,
            character_start=self.character_start,
            character_end=self.character_end,
            retrieval_timestamp=self.retrieval_timestamp,
        )


class LegalReviewRequest(BaseModel):
    environment_id: uuid.UUID
    model_version_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    risk_tier: str = Field(default="high", min_length=1, max_length=24)
    agent_version: str = Field(default="1.0.0", min_length=1, max_length=100)
    document_id: str = Field(min_length=1, max_length=200)
    chunks: list[DocumentChunk] = Field(min_length=1, max_length=100)
    source_references: list[SourceReferenceIn] = Field(default_factory=list, max_length=100)


class ClauseLocation(LegalModel):
    document_id: str
    chunk_id: str
    page_number: int | None
    character_start: int | None
    character_end: int | None


class LegalCitation(LegalModel):
    document_id: str
    chunk_id: str
    page_number: int | None
    character_start: int | None
    character_end: int | None
    source_title: str
    retrieval_timestamp: dt.datetime | None
    content_fingerprint: str


class LegalFinding(LegalModel):
    clause: str
    category: str
    location: ClauseLocation
    extracted_text_reference: str
    issue_type: str
    severity: str
    rationale: str
    recommended_follow_up: str
    source_reference: LegalCitation
    confidence: float = Field(ge=0.0, le=1.0)
    confidence_label: str
    review_required: bool


class LegalReviewResult(LegalModel):
    execution_id: uuid.UUID | None = None
    document_id: str
    disclaimer: str
    findings: list[LegalFinding]
    citations: list[LegalCitation]
    review_required: bool
    review_state: str
    source_count: int
    no_legal_certainty_claim: bool = True
    evidence_references: list[str] = Field(default_factory=list)


class LegalSourceMap(BaseModel):
    source_references: list[SourceReferenceIn] = Field(default_factory=list)
    finding_index: int = Field(ge=0)


def confidence_label(value: float) -> str:
    if value < 0.4:
        return Confidence.VERY_LOW.value
    if value < 0.6:
        return Confidence.LOW.value
    if value < 0.8:
        return Confidence.MODERATE.value
    if value < 0.95:
        return Confidence.HIGH.value
    return Confidence.VERY_HIGH.value
