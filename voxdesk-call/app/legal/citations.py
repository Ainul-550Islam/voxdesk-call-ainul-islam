"""Citation mapping that only emits references backed by supplied chunks."""

from __future__ import annotations

from collections.abc import Iterable

from app.specialized_agents.exceptions import SourceValidationError
from app.specialized_agents.sources import SourceReference

from .schemas import DocumentChunk, LegalCitation


def citation_for_chunk(chunk: DocumentChunk) -> LegalCitation:
    reference = chunk.source_reference()
    return LegalCitation(
        document_id=reference.document_id,
        chunk_id=reference.chunk_id,
        page_number=reference.page_number,
        character_start=reference.character_start,
        character_end=reference.character_end,
        source_title=reference.source_title,
        retrieval_timestamp=reference.retrieval_timestamp,
        content_fingerprint=reference.content_fingerprint,
    )


def map_citations(chunks: Iterable[DocumentChunk]) -> list[LegalCitation]:
    citations: list[LegalCitation] = []
    seen: set[tuple[str, str, str]] = set()
    for chunk in chunks:
        citation = citation_for_chunk(chunk)
        key = (citation.document_id, citation.chunk_id, citation.content_fingerprint)
        if key in seen:
            continue
        seen.add(key)
        citations.append(citation)
    return citations


def citation_for_reference(reference: SourceReference, *, available: Iterable[SourceReference]) -> dict:
    available_keys = {item.key() for item in available}
    if reference.key() not in available_keys:
        raise SourceValidationError("citation does not correspond to an available source")
    return reference.as_dict()
