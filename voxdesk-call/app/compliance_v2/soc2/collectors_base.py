"""Collector contract for evidence automation.

A collector answers a narrow question against a real source and reports one of
three things:

* **configured with evidence** — the source exists and produced facts;
* **configured but failing** — the facts contradict the control (an admin
  account with no second factor, an aged backup, a dormant session);
* **not configured** — the source does not exist in this deployment (no GitHub
  token, no vulnerability scan output, no DR drill record).

Only the first two are assertions about the control. ``NOT_CONFIGURED`` never
becomes ``PASS``: the runner maps it to ``NOT_CONFIGURED`` and an operator sees
exactly which evidence is missing. Each item carries the SHA-256 of its own
payload, so a snapshot can be checked later without trusting the collector that
produced it.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
from abc import ABC, abstractmethod
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

EVIDENCE_PASS = "PASS"
EVIDENCE_FAIL = "FAIL"
EVIDENCE_NOT_CONFIGURED = "NOT_CONFIGURED"
EVIDENCE_ERROR = "ERROR"

STATUSES = (EVIDENCE_PASS, EVIDENCE_FAIL, EVIDENCE_NOT_CONFIGURED, EVIDENCE_ERROR)


def _iso(moment: dt.datetime | None = None) -> str:
    value = moment or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclasses.dataclass(frozen=True)
class EvidenceItem:
    """One fact collected for one control, with a content hash."""

    control_id: str
    collector: str
    source: str
    payload: dict[str, Any]
    collected_at: str = ""
    sha256: str = ""

    def __post_init__(self) -> None:
        if not self.control_id:
            raise ValueError("an evidence item must name its control")
        if not self.collected_at:
            object.__setattr__(self, "collected_at", _iso())
        if not self.sha256:
            object.__setattr__(self, "sha256", self.compute_sha256())

    def compute_sha256(self) -> str:
        body = json.dumps(self.payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "control_id": self.control_id,
            "collector": self.collector,
            "source": self.source,
            "collected_at": self.collected_at,
            "sha256": self.sha256,
            "payload": self.payload,
        }


@dataclasses.dataclass
class CollectorResult:
    """What one collector produced in one run."""

    collector: str
    control_ids: tuple[str, ...]
    configured: bool
    items: list[EvidenceItem] = dataclasses.field(default_factory=list)
    #: Human-readable findings that make a control FAIL. A finding is a fact,
    #: not an opinion: "3 administrators without a second factor", not "risk".
    findings: list[str] = dataclasses.field(default_factory=list)
    #: Why the collector could not run at all (no token, no source, no file).
    not_configured_reason: str = ""
    error: str = ""

    def status_for(self, control_id: str) -> str:
        if self.error:
            return EVIDENCE_ERROR
        if not self.configured:
            return EVIDENCE_NOT_CONFIGURED
        if self.findings:
            return EVIDENCE_FAIL
        covered = [item for item in self.items if item.control_id == control_id]
        return EVIDENCE_PASS if covered else EVIDENCE_NOT_CONFIGURED

    def items_for(self, control_id: str) -> list[EvidenceItem]:
        return [item for item in self.items if item.control_id == control_id]

    def as_dict(self) -> dict[str, Any]:
        return {
            "collector": self.collector,
            "controls": list(self.control_ids),
            "configured": self.configured,
            "items": [item.as_dict() for item in self.items],
            "findings": list(self.findings),
            "not_configured_reason": self.not_configured_reason,
            "error": self.error,
        }


class EvidenceCollector(ABC):
    """Base class: one source, a bounded number of controls."""

    name: str = ""
    control_ids: tuple[str, ...] = ()

    @abstractmethod
    async def collect(self, session: AsyncSession) -> CollectorResult:
        """Gather evidence. Must never raise for a missing source."""

    def _result(self, **kwargs) -> CollectorResult:
        kwargs.setdefault("collector", self.name)
        kwargs.setdefault("control_ids", self.control_ids)
        return CollectorResult(**kwargs)


def item(
    collector: str, control_id: str, source: str, payload: dict[str, Any]
) -> EvidenceItem:
    return EvidenceItem(
        control_id=control_id, collector=collector, source=source, payload=payload
    )


def summarise(results: Sequence[CollectorResult], control_ids: Sequence[str]) -> dict[str, str]:
    """Fold collector results into one status per control.

    A control owned by several collectors takes the worst status: a FAIL or
    ERROR anywhere dominates, and NOT_CONFIGURED is reported when no collector
    produced evidence for it at all.
    """
    statuses: dict[str, str] = {}
    for control_id in control_ids:
        observed: list[str] = []
        for result in results:
            if control_id in result.control_ids:
                observed.append(result.status_for(control_id))
        if not observed:
            statuses[control_id] = EVIDENCE_NOT_CONFIGURED
        elif EVIDENCE_ERROR in observed:
            statuses[control_id] = EVIDENCE_ERROR
        elif EVIDENCE_FAIL in observed:
            statuses[control_id] = EVIDENCE_FAIL
        elif all(status == EVIDENCE_NOT_CONFIGURED for status in observed):
            statuses[control_id] = EVIDENCE_NOT_CONFIGURED
        else:
            statuses[control_id] = EVIDENCE_PASS
    return statuses


__all__ = [
    "EVIDENCE_ERROR",
    "EVIDENCE_FAIL",
    "EVIDENCE_NOT_CONFIGURED",
    "EVIDENCE_PASS",
    "STATUSES",
    "CollectorResult",
    "EvidenceCollector",
    "EvidenceItem",
    "item",
    "summarise",
]
