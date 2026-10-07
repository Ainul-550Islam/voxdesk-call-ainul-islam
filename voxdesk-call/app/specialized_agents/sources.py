"""Safe, fingerprinted source references for knowledge-backed agents."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .exceptions import SourceValidationError

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class SourceReference:
    document_id: str
    chunk_id: str
    source_title: str
    content_fingerprint: str
    page_number: int | None = None
    character_start: int | None = None
    character_end: int | None = None
    retrieval_timestamp: dt.datetime | None = None

    def __post_init__(self) -> None:
        for field_name in ("document_id", "chunk_id", "source_title", "content_fingerprint"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise SourceValidationError(f"{field_name} is required")
        if not _HEX64.fullmatch(self.content_fingerprint.lower()):
            raise SourceValidationError("content_fingerprint must be a lowercase SHA-256 digest")
        if self.page_number is not None and self.page_number < 1:
            raise SourceValidationError("page_number must be positive")
        if self.character_start is not None and self.character_start < 0:
            raise SourceValidationError("character_start must not be negative")
        if self.character_end is not None and self.character_end < 0:
            raise SourceValidationError("character_end must not be negative")
        if (
            self.character_start is not None
            and self.character_end is not None
            and self.character_end < self.character_start
        ):
            raise SourceValidationError("character_end cannot precede character_start")

    @classmethod
    def from_text(
        cls,
        *,
        document_id: str,
        chunk_id: str,
        source_title: str,
        text: str,
        page_number: int | None = None,
        character_start: int | None = None,
        character_end: int | None = None,
        retrieval_timestamp: dt.datetime | None = None,
    ) -> "SourceReference":
        if not text:
            raise SourceValidationError("source text cannot be empty")
        fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()
        return cls(
            document_id=document_id,
            chunk_id=chunk_id,
            source_title=source_title,
            content_fingerprint=fingerprint,
            page_number=page_number,
            character_start=character_start,
            character_end=character_end,
            retrieval_timestamp=retrieval_timestamp,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "chunk_id": self.chunk_id,
            "source_title": self.source_title,
            "page_number": self.page_number,
            "character_start": self.character_start,
            "character_end": self.character_end,
            "retrieval_timestamp": self.retrieval_timestamp.isoformat() if self.retrieval_timestamp else None,
            "content_fingerprint": self.content_fingerprint,
        }

    def key(self) -> str:
        return f"{self.document_id}:{self.chunk_id}:{self.content_fingerprint}"


@dataclass(frozen=True)
class SafeSourceContext:
    references: tuple[SourceReference, ...]

    def __post_init__(self) -> None:
        if len(self.references) > 100:
            raise SourceValidationError("source count exceeds the configured limit")
        keys = [reference.key() for reference in self.references]
        if len(keys) != len(set(keys)):
            raise SourceValidationError("duplicate source references are not allowed")

    @property
    def fingerprints(self) -> tuple[str, ...]:
        return tuple(reference.content_fingerprint for reference in self.references)

    def as_dicts(self) -> list[dict[str, Any]]:
        return [reference.as_dict() for reference in self.references]


def normalize_sources(items: Iterable[SourceReference | dict[str, Any]]) -> SafeSourceContext:
    references: list[SourceReference] = []
    for item in items:
        if isinstance(item, SourceReference):
            references.append(item)
            continue
        if not isinstance(item, dict):
            raise SourceValidationError("source reference must be an object")
        if "content" in item or "text" in item:
            raise SourceValidationError("raw source content is not accepted in evidence references")
        values = dict(item)
        timestamp = values.get("retrieval_timestamp")
        if isinstance(timestamp, str):
            try:
                values["retrieval_timestamp"] = dt.datetime.fromisoformat(timestamp)
            except ValueError as exc:
                raise SourceValidationError("retrieval_timestamp is invalid") from exc
        try:
            references.append(SourceReference(**values))
        except TypeError as exc:
            raise SourceValidationError("source reference fields are invalid") from exc
    return SafeSourceContext(tuple(references))
