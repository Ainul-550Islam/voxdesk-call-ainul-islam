"""Governed legal review orchestration without legal-certainty claims."""

from __future__ import annotations

from typing import Any

from app.specialized_agents.enums import ReviewState
from app.specialized_agents.sources import SourceReference

from .citations import map_citations
from .clause_engine import ClauseEngine
from .schemas import DocumentChunk, LegalReviewResult


class ReviewEngine:
    DISCLAIMER = "AI-generated assistance for document review only; not legal advice or a determination of legal validity. A qualified human must review findings."

    def __init__(self, clause_engine: ClauseEngine | None = None):
        self.clause_engine = clause_engine or ClauseEngine()

    def review(self, *, document_id: str, chunks: list[DocumentChunk]) -> dict[str, Any]:
        if not document_id or not chunks:
            from app.specialized_agents.exceptions import SourceValidationError

            raise SourceValidationError("document_id and at least one document chunk are required")
        if any(chunk.document_id != document_id for chunk in chunks):
            from app.specialized_agents.exceptions import SourceValidationError

            raise SourceValidationError("all chunks must belong to the requested document")
        findings = self.clause_engine.extract(chunks)
        citations = map_citations(chunks)
        review_required = bool(findings) or any(finding.review_required for finding in findings)
        result = LegalReviewResult(
            document_id=document_id,
            disclaimer=self.DISCLAIMER,
            findings=findings,
            citations=citations,
            review_required=review_required,
            review_state=ReviewState.REQUIRED.value if review_required else ReviewState.NOT_REQUIRED.value,
            source_count=len(citations),
            no_legal_certainty_claim=True,
        )
        return {
            **result.model_dump(mode="json"),
            "review_required": review_required,
            "review_state": result.review_state,
            "legal_certainty": "not_claimed",
        }

    def source_references(self, chunks: list[DocumentChunk]) -> list[SourceReference]:
        return [
            SourceReference(
                document_id=chunk.source_reference().document_id,
                chunk_id=chunk.source_reference().chunk_id,
                source_title=chunk.source_reference().source_title,
                content_fingerprint=chunk.source_reference().content_fingerprint,
                page_number=chunk.source_reference().page_number,
                character_start=chunk.source_reference().character_start,
                character_end=chunk.source_reference().character_end,
                retrieval_timestamp=chunk.source_reference().retrieval_timestamp,
            )
            for chunk in chunks
        ]
