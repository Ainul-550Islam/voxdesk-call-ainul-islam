"""Immutable, versioned glossary validation and terminology rules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from app.specialized_agents.exceptions import DataValidationError

from .schemas import GlossaryEntryInput


@dataclass(frozen=True)
class GlossaryEntry:
    source_term: str
    target_term: str
    domain: str
    case_behavior: str
    preserve_exact: bool
    notes: str | None
    version: str
    active: bool

    @classmethod
    def from_input(cls, item: GlossaryEntryInput) -> "GlossaryEntry":
        if item.case_behavior not in {"preserve", "insensitive", "lower", "upper"}:
            raise DataValidationError("unsupported glossary case_behavior")
        return cls(
            source_term=item.source_term,
            target_term=item.target_term,
            domain=item.domain,
            case_behavior=item.case_behavior,
            preserve_exact=item.preserve_exact,
            notes=item.notes,
            version=item.version,
            active=item.active,
        )


@dataclass(frozen=True)
class Glossary:
    version: str
    entries: tuple[GlossaryEntry, ...]

    def __post_init__(self) -> None:
        if not self.version:
            raise DataValidationError("glossary version is required")
        if any(entry.version != self.version for entry in self.entries):
            raise DataValidationError("all glossary entries must match the requested version")
        terms = [entry.source_term.casefold() for entry in self.entries if entry.active]
        if len(terms) != len(set(terms)):
            raise DataValidationError("duplicate active source terms are not allowed")

    @classmethod
    def from_inputs(cls, version: str, entries: Iterable[GlossaryEntryInput]) -> "Glossary":
        return cls(version, tuple(GlossaryEntry.from_input(entry) for entry in entries))

    def active_entries(self) -> tuple[GlossaryEntry, ...]:
        return tuple(entry for entry in self.entries if entry.active)

    def prompt_terms(self) -> list[dict[str, str]]:
        return [
            {"source_term": entry.source_term, "target_term": entry.target_term, "domain": entry.domain}
            for entry in self.active_entries()
        ]
