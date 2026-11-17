# File: app/alerts/evaluator.py — sample a metric, compare it, decide whether to notify
"""Alert evaluation (PART 15, P15-04).

The rule is the user's sentence: *"if p95 latency is above 2500 ms over fifteen
minutes, tell me, but not more than once an hour."* This module turns that into
four independent decisions:

``sample``    what the metric actually was, from the real tables. When there is
              no data the sample is ``None`` and the rule is not evaluated --
              an empty window is not a success rate of zero.
``compare``   the threshold, by comparator.
``cooldown``  a breach inside the cooldown is recorded as
              ``suppressed_cooldown``: a silent alert is indistinguishable from
              a broken monitor, so the suppression itself is data.
``dedupe``    the unique key ``(tenant, dedupe_key)`` where the key is
              ``(rule, scope, window bucket, sequence)``. Two workers that both
              fire land on the same key; the loser writes nothing and reports
              ``suppressed_dedupe``.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import log
from app.db.models import Call, CallStatus
from app.db.monitoring_models import AlertEvent, AlertRule

#: Every metric a rule may name. Kept in one place so the route validators and
#: the sampler cannot drift apart.
METRICS: tuple[str, ...] = (
    "cost_burn_per_minute",
    "success_rate",
    "sentiment_delta",
    "p95_latency",
    "error_rate",
    "concurrency_saturation",
    "consent_breach",
    "anomaly_zscore",
)

COMPARATORS: dict[str, Callable[[float, float], bool]] = {
    "gt": lambda value, threshold: value > threshold,
    "lt": lambda value, threshold: value < threshold,
    "gte": lambda value, threshold: value >= threshold,
    "lte": lambda value, threshold: value <= threshold,
}

STATE_FIRED = "fired"
STATE_COOLDOWN = "suppressed_cooldown"
STATE_DEDUPE = "suppressed_dedupe"
STATE_DELIVERY_FAILED = "delivery_failed"

#: Metrics where "higher is worse". Used only for messages, never for logic.
_HIGHER_IS_WORSE = {
    "cost_burn_per_minute",
    "p95_latency",
    "error_rate",
    "consent_breach",
    "anomaly_zscore",
    "concurrency_saturation",
}


def utcnow_naive() -> datetime:
    """Naive UTC.

    The tables in this codebase store naive UTC datetimes; mixing an aware
    value into a comparison raises at query time on SQLite and silently
    shifts on Postgres. One helper, one convention.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True, slots=True)
class AlertRuleView:
    """Matching-relevant slice of an alert rule."""

    id: uuid.UUID
    name: str
    metric: str
    comparator: str
    threshold: float
    window_minutes: int = 15
    cooldown_minutes: int = 30
    scope_kind: str = "tenant"
    scope_id: str | None = None
    channels: tuple[Any, ...] = ()
    enabled: bool = True

    @classmethod
    def from_row(cls, row: AlertRule) -> "AlertRuleView":
        return cls(
            id=row.id,
            name=row.name,
            metric=row.metric,
            comparator=row.comparator,
            threshold=float(row.threshold),
            window_minutes=int(row.window_minutes or 15),
            cooldown_minutes=int(row.cooldown_minutes or 0),
            scope_kind=row.scope_kind or "tenant",
            scope_id=row.scope_id,
            channels=tuple(row.channels or ()),
            enabled=bool(row.enabled),
        )


@dataclass(frozen=True, slots=True)
class Evaluation:
    """The outcome of evaluating one rule once."""

    rule_id: uuid.UUID
    rule_name: str
    metric: str
    observed: float | None
    threshold: float
    state: str | None  # None = below/above threshold, nothing to report
    event_id: uuid.UUID | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)

    @property
    def breached(self) -> bool:
        return self.state is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": str(self.rule_id),
            "rule_name": self.rule_name,
            "metric": self.metric,
            "observed": self.observed,
            "threshold": self.threshold,
            "state": self.state,
            "breached": self.breached,
            "event_id": str(self.event_id) if self.event_id else None,
            "detail": dict(self.detail),
        }


def breaches(value: float, comparator: str, threshold: float) -> bool:
    """Apply a comparator. An unknown comparator never fires."""
    fn = COMPARATORS.get(comparator)
    return bool(fn and fn(float(value), float(threshold)))


def window_bucket(now: datetime, window_minutes: int) -> int:
    """The integer bucket a moment belongs to, for the dedupe key."""
    minutes = max(1, int(window_minutes))
    return int(now.timestamp()) // (minutes * 60)


def dedupe_key(rule_id: uuid.UUID, scope_kind: str, scope_id: str | None, bucket: int, seq: int) -> str:
    """Stable, bounded key: ``sha256(...)`` plus the in-bucket sequence."""
    raw = f"{rule_id}|{scope_kind}|{scope_id or ''}|{bucket}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]
    return f"{digest}:{seq}"


# --------------------------------------------------------------------------
# Metric sampling
# --------------------------------------------------------------------------


def _scope_filter(scope_kind: str, scope_id: str | None):
    """Extra predicate on ``Call`` for an agent- or number-scoped rule."""
    if not scope_id:
        return None
    if scope_kind == "agent":
        # `calls.agent_id` is a UUID column: an agent scope that is not a UUID
        # matches nothing rather than raising.
        try:
            return Call.agent_id == uuid.UUID(str(scope_id))
        except (ValueError, TypeError):
            return Call.id.is_(None)
    if scope_kind == "number":
        return or_(Call.from_number == scope_id, Call.to_number == scope_id)
    return None


async def _call_rows(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    start: datetime,
    end: datetime,
    *,
    scope_kind: str = "tenant",
    scope_id: str | None = None,
) -> list[Call]:
    query = select(Call).where(
        Call.tenant_id == tenant_id, Call.started_at >= start, Call.started_at < end
    )
    extra = _scope_filter(scope_kind, scope_id)
    if extra is not None:
        query = query.where(extra)
    return list((await session.scalars(query)).all())


def _status(call: Call) -> str:
    return (call.status.value if hasattr(call.status, "value") else str(call.status)).lower()


def _success_rate(calls: Sequence[Call]) -> float | None:
    answered = [
        c for c in calls if _status(c) in (CallStatus.COMPLETED.value, CallStatus.TRANSFERRED.value)
        or bool(c.escalated)
        or bool(c.booked)
    ]
    if not answered:
        return None
    won = sum(
        1
        for c in answered
        if bool(c.booked) or _status(c) in (CallStatus.COMPLETED.value, CallStatus.TRANSFERRED.value)
    )
    return round(won / len(answered) * 100.0, 4)


def _error_rate(calls: Sequence[Call]) -> float | None:
    if not calls:
        return None
    failed = sum(1 for c in calls if _status(c) == CallStatus.FAILED.value)
    return round(failed / len(calls) * 100.0, 4)


async def _p95_latency(
    session: AsyncSession, tenant_id: uuid.UUID, start: datetime, end: datetime, call_ids: Sequence[uuid.UUID]
) -> float | None:
    """p95 of the per-call end-to-end p95, over the calls in the window.

    ``call_latency_stats.e2e_p95_ms`` is already the p95 inside a call; taking
    the p95 across calls answers the question the rule asks ("is the tail bad")
    without pretending the raw frames are stored here.
    """
    from app.db.telephony_models import CallLatencyStat

    if not call_ids:
        return None
    rows = await session.scalars(
        select(CallLatencyStat.e2e_p95_ms).where(
            CallLatencyStat.tenant_id == tenant_id,
            CallLatencyStat.call_id.in_(list(call_ids)),
            CallLatencyStat.e2e_p95_ms.is_not(None),
        )
    )
    values = sorted(float(v) for v in rows if v is not None)
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 4)
    position = (len(values) - 1) * 0.95
    lower, upper = int(position), min(int(position) + 1, len(values) - 1)
    weight = position - lower
    return round(values[lower] * (1 - weight) + values[upper] * weight, 4)


_SENTIMENT_WEIGHT = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}


async def _mean_sentiment(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    start: datetime,
    end: datetime,
    *,
    scope_kind: str = "tenant",
    scope_id: str | None = None,
) -> float | None:
    from app.qa.models import SentimentResult

    query = (
        select(SentimentResult.label, SentimentResult.confidence)
        .join(Call, Call.id == SentimentResult.call_id)
        .where(
            SentimentResult.tenant_id == tenant_id,
            SentimentResult.scope == "call",
            SentimentResult.created_at >= start,
            SentimentResult.created_at < end,
        )
    )
    extra = _scope_filter(scope_kind, scope_id)
    if extra is not None:
        query = query.where(extra)
    rows = list((await session.execute(query)).all())
    if not rows:
        return None
    total_weight = 0.0
    weighted = 0.0
    for label, confidence in rows:
        weight = max(1, int(confidence or 0)) / 100.0
        weighted += _SENTIMENT_WEIGHT.get(str(label).lower(), 0.0) * weight
        total_weight += weight
    if total_weight <= 0:
        return None
    return round(weighted / total_weight, 4)


async def _cost_burn(
    session: AsyncSession, tenant_id: uuid.UUID, start: datetime, end: datetime, window_minutes: int
) -> float:
    """Provider cost in USD per minute over the window.

    Usage events already carry the metered quantities, so this is arithmetic on
    stored numbers rather than an estimate built from call durations.
    """
    from app.services.analytics_service import cost_kpis

    kpi = await cost_kpis(session, tenant_id, start=start, end=end)
    usd = float(kpi.estimated_cost_millicents) / 100_000.0
    minutes = max(1.0, float(window_minutes))
    return round(usd / minutes, 6)


async def _concurrency_saturation(session: AsyncSession, tenant_id: uuid.UUID) -> float | None:
    from app.telephony.concurrency_policy import load_limit

    limit, _row = await load_limit(session, tenant_id)
    ceiling = int(getattr(limit, "ceiling", 0) or 0)
    if ceiling <= 0:
        return None
    live = await session.scalar(
        select(func.count())
        .select_from(Call)
        .where(Call.tenant_id == tenant_id, Call.status == CallStatus.IN_PROGRESS)
    )
    return round(float(live or 0) / ceiling * 100.0, 4)


async def _consent_breach(
    session: AsyncSession, tenant_id: uuid.UUID, start: datetime, end: datetime
) -> float | None:
    """Count of calls whose recording consent was evaluated as *not granted*.

    A breach is a call that was recorded with the consent state ``missing`` or
    ``denied`` on file -- i.e. the decision was a refusal that the recorder had
    to honour. Rows with ``granted`` are counted as clean.
    """
    from app.telephony.consent import RecordingConsent

    rows = await session.execute(
        select(RecordingConsent.state, func.count())
        .where(
            RecordingConsent.tenant_id == tenant_id,
            RecordingConsent.recorded_at >= start,
            RecordingConsent.recorded_at < end,
        )
        .group_by(RecordingConsent.state)
    )
    counts = {str(state or "").lower(): int(n) for state, n in rows.all()}
    if not counts:
        return None
    return float(counts.get("missing", 0) + counts.get("denied", 0))


async def _anomaly_zscore(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    start: datetime,
    end: datetime,
    *,
    call_ids: Sequence[uuid.UUID],
    baseline_hours: int = 24,
) -> float | None:
    """How far the window's latency sits from a rolling daily baseline.

    Reuses the deterministic detector in :mod:`app.anomaly.detectors`; the value
    reported is the absolute z-score, so ``gt 3`` means "three sigma".
    """
    from app.anomaly.detectors import z_score_detect
    from app.anomaly.schemas import DetectorConfig, Observation
    from app.db.telephony_models import CallLatencyStat

    if not call_ids:
        return None
    baseline_start = start - timedelta(hours=baseline_hours)
    rows = await session.execute(
        select(CallLatencyStat.e2e_p95_ms, CallLatencyStat.created_at).where(
            CallLatencyStat.tenant_id == tenant_id,
            CallLatencyStat.e2e_p95_ms.is_not(None),
            CallLatencyStat.created_at >= baseline_start,
            CallLatencyStat.created_at < end,
        )
    )
    series = [
        Observation(metric="e2e_p95_ms", observed_at=observed_at, value=float(value))
        for value, observed_at in rows.all()
        if value is not None
    ]
    series.sort(key=lambda o: o.observed_at)
    if len(series) < 5:
        return None
    result = z_score_detect(series, DetectorConfig(threshold=3.0))
    if result.score is None:
        return None
    return round(abs(float(result.score)), 4)


async def sample_metric(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    metric: str,
    window_minutes: int,
    now: datetime | None = None,
    scope_kind: str = "tenant",
    scope_id: str | None = None,
) -> float | None:
    """Current value of one metric, or ``None`` when the window has no data."""
    moment = now or utcnow_naive()
    minutes = max(1, int(window_minutes))
    start = moment - timedelta(minutes=minutes)
    end = moment

    if metric == "cost_burn_per_minute":
        return await _cost_burn(session, tenant_id, start, end, minutes)
    if metric == "concurrency_saturation":
        return await _concurrency_saturation(session, tenant_id)
    if metric == "consent_breach":
        return await _consent_breach(session, tenant_id, start, end)
    if metric == "sentiment_delta":
        current = await _mean_sentiment(
            session, tenant_id, start, end, scope_kind=scope_kind, scope_id=scope_id
        )
        if current is None:
            return None
        previous = await _mean_sentiment(
            session,
            tenant_id,
            start - timedelta(minutes=minutes),
            start,
            scope_kind=scope_kind,
            scope_id=scope_id,
        )
        if previous is None:
            return None
        return round(current - previous, 4)

    calls = await _call_rows(
        session, tenant_id, start, end, scope_kind=scope_kind, scope_id=scope_id
    )
    if metric == "success_rate":
        return _success_rate(calls)
    if metric == "error_rate":
        return _error_rate(calls)
    if metric == "p95_latency":
        return await _p95_latency(session, tenant_id, start, end, [c.id for c in calls])
    if metric == "anomaly_zscore":
        return await _anomaly_zscore(session, tenant_id, start, end, call_ids=[c.id for c in calls])

    # Unknown metric: not evaluable, and saying so beats inventing a zero.
    return None


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------


async def cooldown_active(
    session: AsyncSession, rule: AlertRuleView, now: datetime | None = None
) -> tuple[bool, datetime | None]:
    """Was this rule's last *delivered* breach inside its cooldown window?"""
    cooldown = int(rule.cooldown_minutes or 0)
    if cooldown <= 0:
        return False, None
    moment = now or utcnow_naive()
    last = await session.scalar(
        select(func.max(AlertEvent.created_at)).where(
            AlertEvent.rule_id == rule.id,
            AlertEvent.state.in_((STATE_FIRED, STATE_DELIVERY_FAILED)),
        )
    )
    if last is None:
        return False, None
    elapsed = moment - last
    return elapsed < timedelta(minutes=cooldown), last


async def evaluate_rule(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    rule: AlertRuleView,
    *,
    now: datetime | None = None,
    observed: float | None = None,
) -> Evaluation:
    """Evaluate one rule, persist the event and return the verdict.

    ``observed`` lets a caller (a test, or a manual "test fire" from the UI)
    supply the value; otherwise it is sampled from the tables.
    """
    moment = now or utcnow_naive()
    value = observed
    if value is None:
        value = await sample_metric(
            session,
            tenant_id=tenant_id,
            metric=rule.metric,
            window_minutes=rule.window_minutes,
            now=moment,
            scope_kind=rule.scope_kind,
            scope_id=rule.scope_id,
        )
    base = Evaluation(
        rule_id=rule.id,
        rule_name=rule.name,
        metric=rule.metric,
        observed=value,
        threshold=rule.threshold,
        state=None,
    )
    if value is None or not rule.enabled:
        return base
    if not breaches(value, rule.comparator, rule.threshold):
        return base

    bucket = window_bucket(moment, rule.window_minutes)
    bucket_start = datetime.fromtimestamp(bucket * rule.window_minutes * 60, tz=timezone.utc).replace(
        tzinfo=None
    )
    prior = await session.scalar(
        select(func.count())
        .select_from(AlertEvent)
        .where(
            AlertEvent.rule_id == rule.id,
            AlertEvent.created_at >= bucket_start,
            AlertEvent.created_at <= moment,
        )
    )
    sequence = int(prior or 0) + 1

    cooling, last_fired = await cooldown_active(session, rule, moment)
    state = STATE_COOLDOWN if cooling else STATE_FIRED

    event = AlertEvent(
        tenant_id=tenant_id,
        rule_id=rule.id,
        metric=rule.metric,
        observed_value=float(value),
        threshold=float(rule.threshold),
        state=state,
        dedupe_key=dedupe_key(rule.id, rule.scope_kind, rule.scope_id, bucket, sequence),
        deliveries=[],
    )
    savepoint = await session.begin_nested()
    try:
        session.add(event)
        await savepoint.commit()
    except IntegrityError:
        await savepoint.rollback()
        log.info(
            "alerts.dedupe_suppressed",
            rule_id=str(rule.id),
            metric=rule.metric,
            bucket=bucket,
        )
        return Evaluation(
            rule_id=rule.id,
            rule_name=rule.name,
            metric=rule.metric,
            observed=value,
            threshold=rule.threshold,
            state=STATE_DEDUPE,
            detail={"bucket": bucket, "sequence": sequence},
        )

    detail: dict[str, Any] = {"bucket": bucket, "sequence": sequence}
    if last_fired is not None:
        detail["last_fired_at"] = last_fired.isoformat()
    return Evaluation(
        rule_id=rule.id,
        rule_name=rule.name,
        metric=rule.metric,
        observed=value,
        threshold=rule.threshold,
        state=state,
        event_id=event.id,
        detail=detail,
    )


async def load_rules(
    session: AsyncSession, tenant_id: uuid.UUID, *, enabled_only: bool = True
) -> list[AlertRuleView]:
    query = select(AlertRule).where(AlertRule.tenant_id == tenant_id)
    if enabled_only:
        query = query.where(AlertRule.enabled.is_(True))
    rows = (await session.scalars(query)).all()
    return [AlertRuleView.from_row(row) for row in rows]


async def evaluate_tenant(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
    rules: Sequence[AlertRuleView] | None = None,
) -> list[Evaluation]:
    """Evaluate every enabled rule for a tenant once.

    One rule raising does not stop the others: a bad metric name on rule three
    must not silence rules four through ten.
    """
    moment = now or utcnow_naive()
    chosen = list(rules) if rules is not None else await load_rules(session, tenant_id)
    results: list[Evaluation] = []
    for rule in chosen:
        try:
            results.append(await evaluate_rule(session, tenant_id, rule, now=moment))
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(
                "alerts.evaluate_failed",
                rule_id=str(rule.id),
                metric=rule.metric,
                error_type=type(exc).__name__,
            )
    return results


def describe(rule: AlertRuleView) -> str:
    """One line a human can read in a chat channel."""
    direction = "above" if rule.comparator in ("gt", "gte") else "below"
    scope = rule.scope_kind if rule.scope_kind == "tenant" else f"{rule.scope_kind} {rule.scope_id}"
    return (
        f"{rule.metric} {direction} {rule.threshold:g} over {rule.window_minutes}m "
        f"(scope: {scope})"
    )
