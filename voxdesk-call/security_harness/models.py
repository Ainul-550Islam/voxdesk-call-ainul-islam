"""Shared result types for the security regression harness.

The harness answers one question at a time and reports it in a fixed shape, so
its output can be asserted in CI and consumed by the SOC 2 evidence collector
without a second format to maintain.

Statuses are deliberately not booleans:

``PASS``
    the property was checked and holds.
``FAIL``
    the property was checked and does not hold.
``REVIEW``
    the property could not be decided automatically; a human must look, and the
    harness records why rather than guessing.
``SKIP``
    the check needs something this run does not have (a client, credentials, a
    running stack). A skip is never reported as a pass.
``ERROR``
    the check raised. Treated as a failure by :meth:`HarnessReport.ok`.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Iterable

PASS = "PASS"
FAIL = "FAIL"
REVIEW = "REVIEW"
SKIP = "SKIP"
ERROR = "ERROR"

#: Worst-first ordering used to roll up a report.
_SEVERITY_ORDER = (ERROR, FAIL, REVIEW, SKIP, PASS)


@dataclasses.dataclass(frozen=True)
class ProbeResult:
    """The outcome of one check."""

    name: str
    status: str
    detail: str = ""
    evidence: dict[str, Any] = dataclasses.field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in _SEVERITY_ORDER:
            raise ValueError(f"unknown probe status {self.status!r}")

    @property
    def ok(self) -> bool:
        return self.status == PASS

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
        }


def worst(statuses: Iterable[str]) -> str:
    """The most serious status in ``statuses``; PASS for an empty input."""
    seen = set(statuses)
    for status in _SEVERITY_ORDER:
        if status in seen:
            return status
    return PASS


@dataclasses.dataclass
class HarnessReport:
    """All results from one harness run."""

    suite: str
    results: list[ProbeResult] = dataclasses.field(default_factory=list)

    def add(self, result: ProbeResult) -> ProbeResult:
        self.results.append(result)
        return result

    def record(self, name: str, status: str, detail: str = "", evidence: Any = None) -> ProbeResult:
        """Add one probe. ``evidence`` is a mapping so a key called ``status``
        (an HTTP response, for example) cannot collide with this signature."""
        return self.add(
            ProbeResult(name=name, status=status, detail=detail, evidence=dict(evidence or {}))
        )

    @property
    def status(self) -> str:
        return worst(result.status for result in self.results)

    @property
    def ok(self) -> bool:
        """True only when nothing failed, errored or needs review."""
        return self.status == PASS

    def by_status(self) -> dict[str, int]:
        counts = {status: 0 for status in _SEVERITY_ORDER}
        for result in self.results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return counts

    def failures(self) -> list[ProbeResult]:
        return [result for result in self.results if result.status in (FAIL, ERROR)]

    def as_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "status": self.status,
            "counts": self.by_status(),
            "checks": [result.as_dict() for result in self.results],
        }

    @property
    def exit_code(self) -> int:
        return 0 if self.status in (PASS, REVIEW, SKIP) else 1


__all__ = [
    "ERROR",
    "FAIL",
    "PASS",
    "REVIEW",
    "SKIP",
    "HarnessReport",
    "ProbeResult",
    "worst",
]
