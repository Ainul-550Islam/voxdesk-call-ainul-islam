"""Deterministic clause detection over normalized document chunks."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from app.specialized_agents.enums import LegalIssueType, Severity
from app.specialized_agents.exceptions import SourceValidationError

from .citations import citation_for_chunk
from .schemas import DocumentChunk, LegalFinding, ClauseLocation, confidence_label


@dataclass(frozen=True)
class ClausePattern:
    category: str
    expressions: tuple[str, ...]
    issue_type: str
    severity: str
    confidence: float
    follow_up: str


PATTERNS: tuple[ClausePattern, ...] = (
    ClausePattern("termination", (r"terminate", r"termination", r"notice period"), LegalIssueType.DETECTED_LANGUAGE.value, Severity.MODERATE.value, 0.91, "Have counsel compare notice, cure, and post-termination obligations."),
    ClausePattern("limitation_of_liability", (r"limitation of liability", r"liable.*(?:cap|maximum|limit)"), LegalIssueType.DETECTED_LANGUAGE.value, Severity.HIGH.value, 0.88, "Have counsel review the cap, exclusions, and applicable damages language."),
    ClausePattern("indemnification", (r"indemnif", r"hold harmless"), LegalIssueType.DETECTED_LANGUAGE.value, Severity.HIGH.value, 0.86, "Have counsel review indemnity triggers, procedures, and carve-outs."),
    ClausePattern("confidentiality", (r"confidential", r"non-disclosure"), LegalIssueType.DETECTED_LANGUAGE.value, Severity.MODERATE.value, 0.90, "Have counsel verify permitted disclosures, duration, and exceptions."),
    ClausePattern("data_protection", (r"personal data", r"data protection", r"privacy", r"processor"), LegalIssueType.DETECTED_LANGUAGE.value, Severity.HIGH.value, 0.84, "Have privacy counsel review roles, processing instructions, and transfer controls."),
    ClausePattern("governing_law", (r"governing law", r"laws of", r"jurisdiction"), LegalIssueType.DETECTED_LANGUAGE.value, Severity.MODERATE.value, 0.89, "Have counsel verify governing law and venue against the transaction."),
    ClausePattern("payment", (r"payment terms", r"invoice", r"net\s+\d+", r"fees"), LegalIssueType.DETECTED_LANGUAGE.value, Severity.MODERATE.value, 0.87, "Have counsel verify payment timing, disputes, taxes, and late charges."),
    ClausePattern("renewal", (r"auto(?:matic)? renewal", r"renewal", r"renews"), LegalIssueType.DETECTED_LANGUAGE.value, Severity.MODERATE.value, 0.85, "Have counsel verify renewal notice windows and opt-out mechanics."),
    ClausePattern("intellectual_property", (r"intellectual property", r"work product", r"ownership of"), LegalIssueType.DETECTED_LANGUAGE.value, Severity.HIGH.value, 0.83, "Have counsel verify ownership, licenses, and pre-existing materials."),
    ClausePattern("service_levels", (r"service level", r"uptime", r"service credits"), LegalIssueType.DETECTED_LANGUAGE.value, Severity.MODERATE.value, 0.82, "Have counsel verify measurement, remedies, exclusions, and credits."),
)


class ClauseEngine:
    def __init__(self, patterns: tuple[ClausePattern, ...] = PATTERNS):
        self.patterns = patterns

    def extract(self, chunks: list[DocumentChunk]) -> list[LegalFinding]:
        findings: list[LegalFinding] = []
        for chunk in chunks:
            if not chunk.text.strip():
                raise SourceValidationError("document chunk text cannot be empty")
            citation = citation_for_chunk(chunk)
            lowered = chunk.text.lower()
            for pattern in self.patterns:
                matches = [re.search(expression, lowered, flags=re.IGNORECASE) for expression in pattern.expressions]
                match = next((candidate for candidate in matches if candidate is not None), None)
                if match is None:
                    continue
                start = (chunk.character_start or 0) + match.start()
                end = (chunk.character_start or 0) + match.end()
                confidence = pattern.confidence
                finding = LegalFinding(
                    clause=pattern.category,
                    category=pattern.category,
                    location=ClauseLocation(
                        document_id=chunk.document_id,
                        chunk_id=chunk.chunk_id,
                        page_number=chunk.page_number,
                        character_start=start,
                        character_end=end,
                    ),
                    extracted_text_reference=hashlib.sha256(match.group(0).encode("utf-8")).hexdigest(),
                    issue_type=pattern.issue_type,
                    severity=pattern.severity,
                    rationale="Configured deterministic language was detected in the supplied source chunk; this is AI-assisted analysis, not a legal conclusion.",
                    recommended_follow_up=pattern.follow_up,
                    source_reference=citation,
                    confidence=confidence,
                    confidence_label=confidence_label(confidence),
                    review_required=confidence < 0.8 or pattern.severity in {Severity.HIGH.value, Severity.CRITICAL.value},
                )
                findings.append(finding)
        return findings
