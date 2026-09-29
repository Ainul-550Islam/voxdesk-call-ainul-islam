"""Behavioral tests for deterministic legal clause analysis."""

from __future__ import annotations

import pytest

from app.legal.clause_engine import ClauseEngine, ClausePattern
from app.legal.citations import citation_for_reference
from app.legal.review_engine import ReviewEngine
from app.legal.schemas import DocumentChunk
from app.specialized_agents.enums import LegalIssueType, Severity
from app.specialized_agents.exceptions import SourceValidationError
from app.specialized_agents.sources import SourceReference


def chunk(text: str, *, page: int = 4, start: int = 100) -> DocumentChunk:
    return DocumentChunk(
        document_id="contract-1",
        chunk_id=f"chunk-{page}",
        source_title="Master Services Agreement",
        text=text,
        page_number=page,
        character_start=start,
        character_end=start + len(text),
    )


def test_clause_detection_preserves_location_and_multiple_findings():
    chunks = [chunk("The parties may terminate on thirty days notice. Confidential information remains protected.")]
    result = ReviewEngine().review(document_id="contract-1", chunks=chunks)
    assert {item["category"] for item in result["findings"]} == {"termination", "confidentiality"}
    assert result["findings"][0]["location"]["page_number"] == 4
    assert result["findings"][0]["source_reference"]["content_fingerprint"]
    assert result["review_required"] is True
    assert result["no_legal_certainty_claim"] is True


def test_low_confidence_sets_review_required_and_no_fabricated_citation():
    low_pattern = ClausePattern(
        "custom_clause",
        (r"maybe",),
        LegalIssueType.DETECTED_LANGUAGE.value,
        Severity.LOW.value,
        0.55,
        "Human review required.",
    )
    finding = ClauseEngine((low_pattern,)).extract([chunk("Maybe this wording applies.")])[0]
    assert finding.review_required is True
    assert finding.confidence_label == "low"
    reference = SourceReference.from_text(
        document_id="other", chunk_id="chunk", source_title="Other", text="not this source"
    )
    with pytest.raises(SourceValidationError):
        citation_for_reference(reference, available=[])


def test_chunk_fingerprint_mismatch_is_rejected():
    with pytest.raises(ValueError):
        DocumentChunk(
            document_id="contract-1",
            chunk_id="chunk-1",
            source_title="Contract",
            text="actual",
            content_fingerprint="0" * 64,
        ).source_reference()
