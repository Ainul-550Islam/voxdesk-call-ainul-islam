# File: app/telephony/auto_actions.py — signal -> action rules with a real loop guard
"""Automatic actions on a live call (PART 15, P15-03).

A rule maps a live signal (score, sentiment, keyword, loop detector, latency)
onto an action (transfer, webhook, alert, end_call). The hard part is not
matching -- it is *not* matching twice.

Three independent guards, because each one alone has failed in production
somewhere:

1. **`max_per_call`** -- the rule's own budget of applications per call.
2. **`sequence`** -- a legitimate second application gets sequence 2, so
   `max_per_call > 1` still terminates.
3. **The unique key** `(tenant, call, rule, sequence)` -- two workers that both
   decide to transfer land on the same key; the second insert loses, and the
   loser records ``duplicate`` instead of transferring again.

A suppressed match is still recorded. A silent skip is indistinguishable from a
rule that never fired, which is exactly the ambiguity an incident review cannot
afford.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.monitoring_models import AutoActionRule, AutoActionRun

TRIGGERS: tuple[str, ...] = (
    "score_below",
    "sentiment_below",
    "keyword",
    "loop_detected",
    "latency_above",
)
ACTIONS: tuple[str, ...] = ("transfer", "webhook", "alert", "end_call")

STATUS_PENDING = "pending"
STATUS_APPLIED = "applied"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"

#: ``handler(call_id=..., config=..., context=...) -> dict``
ActionHandler = Callable[..., Awaitable[Mapping[str, Any] | None]]


@dataclass(frozen=True, slots=True)
class Signal:
    """Everything a rule may need to look at, sampled once per evaluation."""

    score: int | None = None
    sentiment: float | None = None
    latency_ms: float | None = None
    text: str = ""
    loop_detected: bool = False
    agent_id: str | None = None

    def keywords_in_text(self, keywords: Sequence[str]) -> list[str]:
        haystack = self.text.lower()
        return [word for word in keywords if word and word.lower() in haystack]


@dataclass(frozen=True, slots=True)
class RuleView:
    """A rule stripped down to what matching needs.

    Tests build these directly; the engine never depends on a live session to
    decide whether a rule matched.
    """

    id: uuid.UUID
    name: str
    trigger: str
    action: str
    threshold: float | None = None
    keywords: tuple[str, ...] = ()
    action_config: Mapping[str, Any] = field(default_factory=dict)
    scope_agent_id: str | None = None
    enabled: bool = True
    max_per_call: int = 1

    @classmethod
    def from_row(cls, row: AutoActionRule) -> "RuleView":
        return cls(
            id=row.id,
            name=row.name,
            trigger=row.trigger,
            action=row.action,
            threshold=row.threshold,
            keywords=tuple(row.keywords or ()),
            action_config=dict(row.action_config or {}),
            scope_agent_id=row.scope_agent_id,
            enabled=bool(row.enabled),
            max_per_call=int(row.max_per_call or 1),
        )


@dataclass(frozen=True, slots=True)
class Decision:
    """One rule's verdict for one evaluation."""

    rule_id: uuid.UUID | None
    rule_name: str
    action: str
    sequence: int
    status: str  # pending (to be executed) | skipped | applied | failed
    reason: str
    action_config: Mapping[str, Any] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def should_execute(self) -> bool:
        return self.status == STATUS_PENDING


def match(rule: RuleView, signal: Signal) -> str | None:
    """Return the matched reason, or ``None`` when the rule does not fire."""
    if not rule.enabled:
        return None
    scope = rule.scope_agent_id
    if scope and scope != "*" and scope != (signal.agent_id or ""):
        return None

    if rule.trigger == "score_below":
        if signal.score is None or rule.threshold is None:
            return None
        if signal.score < rule.threshold:
            return f"score {signal.score} < {rule.threshold:g}"
        return None

    if rule.trigger == "sentiment_below":
        if signal.sentiment is None or rule.threshold is None:
            return None
        if signal.sentiment < rule.threshold:
            return f"sentiment {signal.sentiment:+.2f} < {rule.threshold:g}"
        return None

    if rule.trigger == "keyword":
        hits = signal.keywords_in_text(rule.keywords)
        return f"keyword {hits[0]!r}" if hits else None

    if rule.trigger == "loop_detected":
        return "loop detector fired" if signal.loop_detected else None

    if rule.trigger == "latency_above":
        if signal.latency_ms is None or rule.threshold is None:
            return None
        if signal.latency_ms > rule.threshold:
            return f"latency {signal.latency_ms:g} ms > {rule.threshold:g}"
        return None

    # An unknown trigger cannot be guessed at; storing it was already a
    # validation failure, so here it simply never matches.
    return None


def decide(
    rules: Iterable[RuleView],
    signal: Signal,
    *,
    applied_counts: Mapping[uuid.UUID, int] | None = None,
) -> list[Decision]:
    """Verdicts for every matching rule, including the ones the budget blocks.

    `applied_counts` is the number of runs already recorded per rule for this
    call. A blocked match is returned as ``skipped`` with reason
    ``loop_guard`` -- the caller records it, and the incident review can see
    that the rule *wanted* to fire again and was stopped on purpose.
    """
    counts = dict(applied_counts or {})
    out: list[Decision] = []
    for rule in rules:
        reason = match(rule, signal)
        if reason is None:
            continue
        used = counts.get(rule.id, 0)
        sequence = used + 1
        if sequence > max(1, int(rule.max_per_call or 1)):
            out.append(
                Decision(
                    rule_id=rule.id,
                    rule_name=rule.name,
                    action=rule.action,
                    sequence=sequence,
                    status=STATUS_SKIPPED,
                    reason="loop_guard",
                    action_config=dict(rule.action_config or {}),
                    detail={"matched": reason, "max_per_call": rule.max_per_call},
                )
            )
            continue
        counts[rule.id] = sequence
        out.append(
            Decision(
                rule_id=rule.id,
                rule_name=rule.name,
                action=rule.action,
                sequence=sequence,
                status=STATUS_PENDING,
                reason=reason,
                action_config=dict(rule.action_config or {}),
                detail={"matched": reason},
            )
        )
    return out


async def applied_counts(
    session: AsyncSession, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    """How many runs each rule already has for this call (applied or failed)."""
    rows = await session.execute(
        select(AutoActionRun.rule_id, func.count())
        .where(
            AutoActionRun.tenant_id == tenant_id,
            AutoActionRun.call_id == call_id,
            AutoActionRun.rule_id.is_not(None),
            AutoActionRun.status.in_((STATUS_APPLIED, STATUS_FAILED, STATUS_PENDING)),
        )
        .group_by(AutoActionRun.rule_id)
    )
    return {rule_id: int(count) for rule_id, count in rows.all() if rule_id is not None}


async def record(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    decision: Decision,
    status: str,
    detail: Mapping[str, Any] | None = None,
    last_error: str | None = None,
) -> AutoActionRun | None:
    """Insert one run row. ``None`` means another worker won the same key.

    The insert is wrapped in a savepoint so a lost race does not poison the
    caller's transaction -- the whole live call is one transaction in some
    code paths, and a rollback there would drop unrelated work.
    """
    row = AutoActionRun(
        tenant_id=tenant_id,
        call_id=call_id,
        rule_id=decision.rule_id,
        action=decision.action,
        status=status,
        sequence=decision.sequence,
        detail={**dict(decision.detail), **dict(detail or {})},
        last_error=(last_error or None),
    )
    savepoint = await session.begin_nested()
    try:
        session.add(row)
        await savepoint.commit()
    except IntegrityError:
        await savepoint.rollback()
        log.info(
            "auto_actions.duplicate_suppressed",
            call_id=str(call_id),
            rule_id=str(decision.rule_id),
            sequence=decision.sequence,
        )
        return None
    return row


async def execute(
    decision: Decision,
    *,
    call_id: uuid.UUID,
    handlers: Mapping[str, ActionHandler],
    context: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any], str | None]:
    """Run one action. Returns ``(status, detail, error)``.

    An action with no registered handler is ``skipped``, never ``applied``: a
    transfer that nobody performed must not look like a transfer that worked.
    """
    handler = handlers.get(decision.action)
    if handler is None:
        return (
            STATUS_SKIPPED,
            {"reason": "no_handler_registered", "action": decision.action},
            f"no handler registered for action {decision.action!r}",
        )
    try:
        result = await handler(
            call_id=call_id,
            config=dict(decision.action_config),
            context={**dict(context or {}), "rule": decision.rule_name},
        )
    except Exception as exc:  # an action failure is data, not a crash
        log.warning(
            "auto_actions.handler_failed",
            call_id=str(call_id),
            action=decision.action,
            error_type=type(exc).__name__,
        )
        return STATUS_FAILED, {"action": decision.action}, f"{type(exc).__name__}: {exc}"[:500]
    return STATUS_APPLIED, dict(result or {}), None


async def run_auto_actions(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    call_id: uuid.UUID,
    rules: Sequence[RuleView],
    signal: Signal,
    handlers: Mapping[str, ActionHandler] | None = None,
    context: Mapping[str, Any] | None = None,
) -> list[Decision]:
    """Evaluate every rule once, persist every verdict, execute the allowed ones.

    Returns the decisions in their final state so the caller can report what
    happened without re-reading the table.
    """
    counts = await applied_counts(session, tenant_id, call_id)
    decisions = decide(rules, signal, applied_counts=counts)
    resolved: list[Decision] = []

    for decision in decisions:
        if not decision.should_execute:
            stored = await record(
                session,
                tenant_id=tenant_id,
                call_id=call_id,
                decision=decision,
                status=STATUS_SKIPPED,
                detail={"reason": decision.reason},
            )
            if stored is None:
                continue
            resolved.append(decision)
            continue

        status, detail, error = await execute(
            decision, call_id=call_id, handlers=handlers or {}, context=context
        )
        stored = await record(
            session,
            tenant_id=tenant_id,
            call_id=call_id,
            decision=decision,
            status=status,
            detail=detail,
            last_error=error,
        )
        if stored is None:
            # Another worker already applied this exact (rule, sequence).
            resolved.append(
                Decision(
                    rule_id=decision.rule_id,
                    rule_name=decision.rule_name,
                    action=decision.action,
                    sequence=decision.sequence,
                    status=STATUS_SKIPPED,
                    reason="duplicate",
                    action_config=decision.action_config,
                    detail={**dict(decision.detail), "reason": "duplicate"},
                )
            )
            continue
        resolved.append(
            Decision(
                rule_id=decision.rule_id,
                rule_name=decision.rule_name,
                action=decision.action,
                sequence=decision.sequence,
                status=status,
                reason=decision.reason,
                action_config=decision.action_config,
                detail={**dict(decision.detail), **dict(detail)},
            )
        )
    return resolved


async def load_rules(
    session: AsyncSession, tenant_id: uuid.UUID, *, agent_id: str | None = None
) -> list[RuleView]:
    """Enabled rules for a tenant, scoped to an agent (plus wildcard rules)."""
    query = select(AutoActionRule).where(
        AutoActionRule.tenant_id == tenant_id, AutoActionRule.enabled.is_(True)
    )
    if agent_id:
        query = query.where(
            (AutoActionRule.scope_agent_id.is_(None))
            | (AutoActionRule.scope_agent_id.in_(("*", agent_id)))
        )
    rows = (await session.scalars(query)).all()
    return [RuleView.from_row(row) for row in rows]


def summarise(decisions: Sequence[Decision]) -> dict[str, int]:
    """Counts by final status, for the response body of a manual evaluation."""
    out = {STATUS_APPLIED: 0, STATUS_SKIPPED: 0, STATUS_FAILED: 0, STATUS_PENDING: 0}
    for decision in decisions:
        out[decision.status] = out.get(decision.status, 0) + 1
    return out
