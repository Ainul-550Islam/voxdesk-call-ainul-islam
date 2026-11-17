# File: app/qa/analyst.py — the AI QA analyst: five questions, answered from real rows
"""AI QA analyst (PART 15, P15-05).

The analyst answers five questions over a time window, and every one of them is
computed from rows the platform already stores. Nothing here asks a model to
grade itself:

``hallucination rate``     QA findings of kind ``hallucination`` /
                          ``ungrounded_claim`` divided by the calls analysed.
                          A claim is only counted when there was evidence to
                          check it against -- see :func:`hallucination_rate`,
                          which returns ``None`` rather than ``0.0`` when no
                          claim was checkable. "No data" and "no hallucinations"
                          are different sentences.
``tool-call accuracy``     successful tool executions / all executions in the
                          window, from ``tool_executions``.
``resolution rate``        calls that booked or completed / answered calls.
``repeat contacts``        the same number calling back within
                          ``repeat_window_hours`` with the same intent -- the
                          signature of a problem the first call did not solve.
``issue clusters``         call facets (intent, failure reason, agent) embedded
                          with a deterministic hashed bag-of-words and grouped
                          by cosine similarity, so "these 40 failures are one
                          bug" becomes a row instead of an anecdote.

Metering: a run costs ``METERED_UNITS_PER_CALL`` per analysed call and the run
row stores both the units and the window, so the analyst's spend is attributable
without a separate billing pipeline.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.models import Call, CallStatus, ToolExecution
from app.db.monitoring_models import QaAnalystRun
from app.qa.models import QAFinding

#: Billed units per analysed call. One unit is one call's worth of analysis;
#: keeping it an integer avoids float drift in the run totals.
METERED_UNITS_PER_CALL = 1

#: How far back a repeat contact counts.
REPEAT_WINDOW_HOURS = 72

#: Cosine threshold for two call facets to be the same issue.
CLUSTER_THRESHOLD = 0.55

#: Embedding width. 64 is small enough to stay readable in a test and wide
#: enough that three or four shared words separate cleanly.
EMBED_DIM = 64

HALLUCINATION_KINDS = ("hallucination", "ungrounded_claim")

TOOL_STATUS_OK = ("succeeded", "success", "completed", "ok")


def _utc(moment: datetime | None) -> datetime:
    """Naive UTC, matching the columns in this database."""
    base = moment or datetime.now(timezone.utc)
    if base.tzinfo is not None:
        base = base.astimezone(timezone.utc).replace(tzinfo=None)
    return base


def _pct(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 4)


# --------------------------------------------------------------------------
# Pure metric helpers (no session; unit-testable on their own)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ClaimCheck:
    """One claim and whether the evidence supported it."""

    claim: str
    evidence_kind: str  # "kb" | "tool" | "none"
    supported: bool | None  # None = not checkable

    @property
    def checkable(self) -> bool:
        return self.supported is not None and self.evidence_kind != "none"


def hallucination_rate(checks: Iterable[ClaimCheck]) -> float | None:
    """Unsupported claims as a percentage of *checkable* claims.

    Uncheckable claims are excluded from both numerator and denominator: a claim
    with no KB article and no tool result behind it says nothing about accuracy,
    and counting it as a hallucination would make the metric rise whenever the
    knowledge base shrank.
    """
    checkable = [c for c in checks if c.checkable]
    if not checkable:
        return None
    bad = sum(1 for c in checkable if not c.supported)
    return _pct(bad, len(checkable))


@dataclass(frozen=True, slots=True)
class ToolAttempt:
    """One tool call and its outcome."""

    tool_type: str
    status: str
    error_category: str | None = None

    @property
    def ok(self) -> bool:
        return self.status.lower() in TOOL_STATUS_OK


def tool_call_accuracy(attempts: Iterable[ToolAttempt]) -> float | None:
    rows = list(attempts)
    if not rows:
        return None
    return _pct(sum(1 for row in rows if row.ok), len(rows))


def tool_error_breakdown(attempts: Iterable[ToolAttempt]) -> dict[str, int]:
    """Failures by category, largest first. Empty when everything worked."""
    counts = Counter(
        (row.error_category or "unclassified") for row in attempts if not row.ok
    )
    return dict(counts.most_common())


def resolution_rate(resolved: int, classified: int) -> float | None:
    return _pct(resolved, classified)


# --------------------------------------------------------------------------
# Repeat contacts
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CallFacet:
    """The minimum a pattern needs from a call."""

    call_id: uuid.UUID
    number: str
    outcome: str  # "resolved" | "unresolved"
    intent: str = ""
    started_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RepeatPattern:
    """A caller who had to come back."""

    number: str
    calls: int
    intents: tuple[str, ...]
    first_call_id: uuid.UUID
    last_call_id: uuid.UUID

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": masked_number(self.number),
            "calls": self.calls,
            "intents": list(self.intents),
            "first_call_id": str(self.first_call_id),
            "last_call_id": str(self.last_call_id),
        }


def masked_number(number: str) -> str:
    """Last four digits. A repeat-contact report is a report, not a call list."""
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"


def find_repeat_contacts(
    calls: Sequence[CallFacet], *, window_hours: int = REPEAT_WINDOW_HOURS, minimum: int = 2
) -> list[RepeatPattern]:
    """Numbers that called back inside the window. Unresolved-first is sorted last.

    A pattern needs at least ``minimum`` calls, and at least one of them
    unresolved: two resolved calls are a customer, not a defect.
    """
    by_number: dict[str, list[CallFacet]] = defaultdict(list)
    for call in calls:
        if call.number:
            by_number[call.number].append(call)

    patterns: list[RepeatPattern] = []
    for number, group in by_number.items():
        ordered = sorted(group, key=lambda c: (c.started_at or datetime.min, str(c.call_id)))
        if len(ordered) < minimum:
            continue
        if not any(call.outcome == "unresolved" for call in ordered):
            continue
        if window_hours > 0 and ordered[0].started_at and ordered[-1].started_at:
            span = ordered[-1].started_at - ordered[0].started_at
            if span > timedelta(hours=window_hours):
                continue
        intents = tuple(dict.fromkeys(call.intent for call in ordered if call.intent))
        patterns.append(
            RepeatPattern(
                number=number,
                calls=len(ordered),
                intents=intents,
                first_call_id=ordered[0].call_id,
                last_call_id=ordered[-1].call_id,
            )
        )
    patterns.sort(key=lambda p: (-p.calls, p.number))
    return patterns


# --------------------------------------------------------------------------
# Clustering
# --------------------------------------------------------------------------


def embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic hashed bag-of-words embedding, L2-normalised.

    No model, no vocabulary file, no network: the same text always lands on the
    same vector, which is what makes the clustering test reproducible. It is
    coarser than a sentence transformer and it says so.
    """
    vector = [0.0] * dim
    tokens = [token for token in _tokenise(text)]
    if not tokens:
        return vector
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 0:
        return vector
    return [value / norm for value in vector]


_STOPWORDS = frozenset(
    "the a an and or of to in on for with by is was were be been being it this that "
    "at from as i you he she they we my your our their".split()
)


def _tokenise(text: str) -> list[str]:
    out: list[str] = []
    current = ""
    for char in (text or "").lower():
        if char.isalnum():
            current += char
            continue
        if current:
            if current not in _STOPWORDS and len(current) > 2:
                out.append(current)
            current = ""
    if current and current not in _STOPWORDS and len(current) > 2:
        out.append(current)
    return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError("vectors must have the same width")
    return sum(x * y for x, y in zip(a, b))


@dataclass(frozen=True, slots=True)
class IssueItem:
    """One observation to cluster: a facet of a call."""

    call_id: uuid.UUID
    text: str
    kind: str = "failure"


@dataclass(frozen=True, slots=True)
class IssueCluster:
    """A group of observations that look like one problem."""

    label: str
    members: tuple[uuid.UUID, ...]
    size: int
    sample: tuple[str, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "size": self.size,
            "call_ids": [str(call_id) for call_id in self.members],
            "samples": list(self.sample),
        }


def cluster_issues(
    items: Sequence[IssueItem],
    *,
    threshold: float = CLUSTER_THRESHOLD,
    limit: int = 20,
) -> list[IssueCluster]:
    """Group observations whose embeddings are closer than ``threshold``.

    Leader clustering: a new item joins the first cluster it is similar enough
    to, otherwise it starts one. Deterministic for a given input order, and the
    input is sorted here so callers cannot change the answer by reordering.
    """
    ordered = sorted(items, key=lambda item: (item.kind, item.text, str(item.call_id)))
    leaders: list[tuple[list[float], list[IssueItem]]] = []
    for item in ordered:
        vector = embed(item.text)
        if not any(vector):
            continue
        placed = False
        for leader_vector, members in leaders:
            if cosine(vector, leader_vector) >= threshold:
                members.append(item)
                placed = True
                break
        if not placed:
            leaders.append((vector, [item]))

    clusters: list[IssueCluster] = []
    for _, members in leaders:
        tokens = Counter()
        for member in members:
            tokens.update(set(_tokenise(member.text)))
        label = " ".join(token for token, _ in tokens.most_common(3)) or members[0].kind
        clusters.append(
            IssueCluster(
                label=label,
                members=tuple(member.call_id for member in members),
                size=len(members),
                sample=tuple(member.text[:120] for member in members[:3]),
            )
        )
    clusters.sort(key=lambda cluster: (-cluster.size, cluster.label))
    return clusters[:limit]


# --------------------------------------------------------------------------
# Database-backed analysis
# --------------------------------------------------------------------------


def _status(call: Call) -> str:
    return (call.status.value if hasattr(call.status, "value") else str(call.status)).lower()


def _is_answered(call: Call) -> bool:
    return _status(call) in (CallStatus.COMPLETED.value, CallStatus.TRANSFERRED.value) or bool(
        call.escalated
    )


def _is_resolved(call: Call) -> bool:
    """Did *this agent* finish the job?

    A transferred call is answered but not resolved -- a human took over, which
    is a different outcome and a different coaching signal. Counting transfers as
    resolutions would make the metric improve exactly when the agent gives up.
    """
    return bool(call.booked) or _status(call) == CallStatus.COMPLETED.value


def _facet_text(call: Call) -> str:
    parts = [
        str(call.intent or ""),
        str(call.failure_reason or ""),
        str(call.transfer_reason or ""),
        str(call.agent_id or ""),
        " ".join(sorted(set(str(call.summary or "").lower().split()))[:8]),
    ]
    return " ".join(part for part in parts if part).strip() or "unclassified call"


def facet_text(call: Call) -> str:
    """Public name for the facet text a cluster is built from."""
    return _facet_text(call)


def is_resolved(call: Call) -> bool:
    """Public name for the resolution rule used by the metrics and the clusters."""
    return _is_resolved(call)


async def analyse_tenant(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    agent_id: str | None = None,
    repeat_window_hours: int = REPEAT_WINDOW_HOURS,
) -> dict[str, Any]:
    """Compute the analyst's metrics for one window.

    Returns the metrics and the clusters, plus ``calls_analysed`` and
    ``metered_units`` so the caller can store the run without recomputing them.
    """
    end_at = _utc(end)
    start_at = _utc(start) if start is not None else end_at - timedelta(hours=24)

    query = select(Call).where(
        Call.tenant_id == tenant_id, Call.started_at >= start_at, Call.started_at < end_at
    )
    if agent_id:
        query = query.where(Call.agent_id == agent_id)
    calls = list((await session.scalars(query)).all())

    answered = [call for call in calls if _is_answered(call)]
    resolved = [call for call in answered if _is_resolved(call)]

    # Hallucination findings, restricted to the calls in this window.
    findings = 0
    if calls:
        call_ids = [call.id for call in calls]
        findings = int(
            await session.scalar(
                select(func.count())
                .select_from(QAFinding)
                .where(
                    QAFinding.tenant_id == tenant_id,
                    QAFinding.call_id.in_(call_ids),
                    QAFinding.kind.in_(HALLUCINATION_KINDS),
                )
            )
            or 0
        )

    # Tool executions in the window (they carry their own tenant scope).
    attempt_rows = await session.execute(
        select(ToolExecution.tool_type, ToolExecution.status, ToolExecution.error_category).where(
            ToolExecution.tenant_id == tenant_id,
            ToolExecution.created_at >= start_at,
            ToolExecution.created_at < end_at,
        )
    )
    attempts = [
        ToolAttempt(tool_type=str(tool_type), status=str(status), error_category=error)
        for tool_type, status, error in attempt_rows.all()
    ]

    facets = [
        CallFacet(
            call_id=call.id,
            number=str(call.from_number or ""),
            outcome="resolved" if _is_resolved(call) else "unresolved",
            intent=str(call.intent or ""),
            started_at=call.started_at,
        )
        for call in calls
    ]
    open_facets = [facet for facet in facets if facet.outcome == "unresolved"]

    catalog = {call.id: call for call in calls}
    issues = [
        IssueItem(call_id=facet.call_id, text=_facet_text(catalog[facet.call_id]))
        for facet in open_facets
    ]
    clusters = cluster_issues(issues)
    repeat_patterns = find_repeat_contacts(facets, window_hours=repeat_window_hours)

    metrics = {
        "calls_analysed": len(calls),
        "answered_calls": len(answered),
        "resolution_rate": resolution_rate(len(resolved), len(answered)),
        "hallucination_findings": findings,
        "hallucination_rate": _pct(findings, len(calls)) if calls else None,
        "tool_calls": len(attempts),
        "tool_call_accuracy": tool_call_accuracy(attempts),
        "tool_errors": tool_error_breakdown(attempts),
        "repeat_contacts": len(repeat_patterns),
        "unresolved_calls": len(open_facets),
        "window_hours": round((end_at - start_at).total_seconds() / 3600.0, 3),
        "window_start": start_at.isoformat(),
        "window_end": end_at.isoformat(),
    }
    return {
        "metrics": metrics,
        "clusters": [cluster.as_dict() for cluster in clusters],
        "repeat_patterns": [pattern.as_dict() for pattern in repeat_patterns],
        "calls_analysed": len(calls),
        "metered_units": len(calls) * METERED_UNITS_PER_CALL,
    }


async def record_run(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    analysis: Mapping[str, Any],
    *,
    agent_id: str | None = None,
) -> QaAnalystRun:
    """Persist one analyst run, so the cost and the findings travel together."""
    metrics = dict(analysis.get("metrics") or {})
    row = QaAnalystRun(
        tenant_id=tenant_id,
        agent_id=agent_id,
        calls_analysed=int(analysis.get("calls_analysed") or 0),
        metrics=metrics,
        clusters=list(analysis.get("clusters") or []),
        metered_units=int(analysis.get("metered_units") or 0),
        window_start=_parse(metrics.get("window_start")),
        window_end=_parse(metrics.get("window_end")),
    )
    session.add(row)
    await session.flush()
    log.info(
        "qa.analyst_run_recorded",
        tenant_id=str(tenant_id),
        run_id=str(row.id),
        calls=row.calls_analysed,
        metered_units=row.metered_units,
    )
    return row


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


async def run_analysis(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Analyse and record in one call; returns the stored run and its analysis."""
    analysis = await analyse_tenant(
        session, tenant_id, start=start, end=end, agent_id=agent_id
    )
    row = await record_run(session, tenant_id, analysis, agent_id=agent_id)
    return {**analysis, "run_id": str(row.id), "run": row}


async def list_runs(
    session: AsyncSession, tenant_id: uuid.UUID, *, limit: int = 20
) -> list[QaAnalystRun]:
    rows = await session.scalars(
        select(QaAnalystRun)
        .where(QaAnalystRun.tenant_id == tenant_id)
        .order_by(QaAnalystRun.created_at.desc())
        .limit(max(1, min(limit, 100)))
    )
    return list(rows.all())
