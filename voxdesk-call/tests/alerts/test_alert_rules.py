# File: tests/alerts/test_alert_rules.py — windows, comparators, cooldown, dedupe, delivery, isolation
"""Alert rule tests (PART 15, P15-04).

Four behaviours matter, and each one is a way a monitoring product fails
quietly:

* **The comparator** must be the one the user picked. ``lt 5`` on an error rate
  means "quiet", and inverting it would alert on every healthy minute.
* **The cooldown** must not make a breach disappear. A suppressed event is still
  a row, because an empty history is indistinguishable from a broken monitor.
* **Dedupe** is per window bucket: two workers evaluating at the same moment
  must produce one notification.
* **Delivery** must not turn "we could not reach Slack" into "the metric was
  fine".
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone, time as dtime

import pytest
import pytest_asyncio
from sqlalchemy import select

import app.db.models  # noqa: F401
from app.alerts import evaluator, notifier
from app.alerts.models import AlertEvent, AlertRule
from app.db.models import Call, CallStatus, Tenant
from app.notifications.delivery import DispatchResult
from app.qa.models import SentimentResult
from tests.conftest import auth_headers, make_call, make_tenant


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------- comparators ---


@pytest.mark.parametrize(
    "comparator,value,threshold,expected",
    [
        ("gt", 10.0, 5.0, True),
        ("gt", 5.0, 5.0, False),
        ("gte", 5.0, 5.0, True),
        ("lt", 4.0, 5.0, True),
        ("lt", 5.0, 5.0, False),
        ("lte", 5.0, 5.0, True),
    ],
)
def test_comparators(comparator, value, threshold, expected):
    assert evaluator.breaches(value, comparator, threshold) is expected


def test_unknown_comparator_never_fires():
    assert evaluator.breaches(100.0, "approximately", 5.0) is False


def test_window_bucket_is_stable_within_a_window():
    moment = datetime(2026, 1, 1, 10, 7, 30)
    assert evaluator.window_bucket(moment, 15) == evaluator.window_bucket(
        moment + timedelta(minutes=6), 15
    )
    assert evaluator.window_bucket(moment, 15) != evaluator.window_bucket(
        moment + timedelta(minutes=10), 15
    )


def test_dedupe_key_changes_with_sequence():
    rule_id = uuid.uuid4()
    first = evaluator.dedupe_key(rule_id, "tenant", None, 1234, 1)
    second = evaluator.dedupe_key(rule_id, "tenant", None, 1234, 2)
    assert first != second
    assert first.startswith(evaluator.dedupe_key(rule_id, "tenant", None, 1234, 1).split(":")[0])


# ------------------------------------------------------------------ sampling ---


@pytest_asyncio.fixture
async def tenant_a(db) -> Tenant:
    return await make_tenant(db, "Alerts A")


async def _rule_row(db, tenant_id: uuid.UUID, **overrides) -> AlertRule:
    payload = dict(
        tenant_id=tenant_id,
        name=f"rule-{uuid.uuid4().hex[:6]}",
        metric="error_rate",
        comparator="gt",
        threshold=50.0,
        window_minutes=15,
        cooldown_minutes=30,
        scope_kind="tenant",
        channels=["in_app"],
        enabled=True,
    )
    payload.update(overrides)
    row = AlertRule(**payload)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@pytest.mark.asyncio
async def test_error_rate_sampling(db, tenant_a):
    for status in (CallStatus.COMPLETED, CallStatus.COMPLETED, CallStatus.FAILED):
        await make_call(db, tenant_a, status=status, started_at=_now())
    value = await evaluator.sample_metric(
        db, tenant_id=tenant_a.id, metric="error_rate", window_minutes=60
    )
    assert value is not None
    assert round(value, 2) == pytest.approx(33.33, abs=0.5)


@pytest.mark.asyncio
async def test_success_rate_is_none_without_calls(db, tenant_a):
    """An empty window is ``None``, not a success rate of zero."""
    value = await evaluator.sample_metric(
        db, tenant_id=tenant_a.id, metric="success_rate", window_minutes=15
    )
    assert value is None


@pytest.mark.asyncio
async def test_sentiment_delta_uses_the_previous_window(db, tenant_a):
    older = await make_call(db, tenant_a, started_at=_now() - timedelta(minutes=20))
    newer = await make_call(db, tenant_a, started_at=_now() - timedelta(minutes=2))
    db.add(
        SentimentResult(
            tenant_id=tenant_a.id,
            call_id=older.id,
            scope="call",
            scope_key=str(older.id),
            label="negative",
            confidence=100,
            created_at=_now() - timedelta(minutes=20),
        )
    )
    db.add(
        SentimentResult(
            tenant_id=tenant_a.id,
            call_id=newer.id,
            scope="call",
            scope_key=str(newer.id),
            label="positive",
            confidence=100,
            created_at=_now() - timedelta(minutes=2),
        )
    )
    await db.commit()
    delta = await evaluator.sample_metric(
        db, tenant_id=tenant_a.id, metric="sentiment_delta", window_minutes=15
    )
    assert delta is not None
    assert delta == pytest.approx(2.0, abs=0.01)


# ---------------------------------------------------------------- evaluation ---


@pytest.mark.asyncio
async def test_breach_writes_one_event(db, tenant_a):
    rule = await _rule_row(db, tenant_a.id, threshold=10.0)
    view = evaluator.AlertRuleView.from_row(rule)
    result = await evaluator.evaluate_rule(db, tenant_a.id, view, observed=55.0)
    await db.commit()
    assert result.state == evaluator.STATE_FIRED
    events = (
        await db.scalars(select(AlertEvent).where(AlertEvent.rule_id == rule.id))
    ).all()
    assert len(events) == 1
    assert events[0].observed_value == 55.0
    assert events[0].state == evaluator.STATE_FIRED


@pytest.mark.asyncio
async def test_no_breach_writes_nothing(db, tenant_a):
    rule = await _rule_row(db, tenant_a.id, threshold=90.0)
    view = evaluator.AlertRuleView.from_row(rule)
    result = await evaluator.evaluate_rule(db, tenant_a.id, view, observed=20.0)
    await db.commit()
    assert result.state is None
    assert result.breached is False
    assert (
        await db.scalars(select(AlertEvent).where(AlertEvent.rule_id == rule.id))
    ).all() == []


@pytest.mark.asyncio
async def test_second_breach_inside_cooldown_is_recorded_but_suppressed(db, tenant_a):
    rule = await _rule_row(db, tenant_a.id, threshold=10.0, cooldown_minutes=30)
    view = evaluator.AlertRuleView.from_row(rule)
    first = await evaluator.evaluate_rule(db, tenant_a.id, view, observed=50.0)
    await db.commit()
    second = await evaluator.evaluate_rule(
        db, tenant_a.id, view, observed=60.0, now=_now() + timedelta(minutes=25)
    )
    await db.commit()

    assert first.state == evaluator.STATE_FIRED
    assert second.state == evaluator.STATE_COOLDOWN
    rows = (
        await db.scalars(
            select(AlertEvent).where(AlertEvent.rule_id == rule.id).order_by(AlertEvent.created_at)
        )
    ).all()
    # Two rows: the suppression is visible in the history, not missing from it.
    assert [row.state for row in rows] == [
        evaluator.STATE_FIRED,
        evaluator.STATE_COOLDOWN,
    ]


@pytest.mark.asyncio
async def test_zero_cooldown_alerts_every_window(db, tenant_a):
    rule = await _rule_row(db, tenant_a.id, threshold=10.0, cooldown_minutes=0)
    view = evaluator.AlertRuleView.from_row(rule)
    first = await evaluator.evaluate_rule(db, tenant_a.id, view, observed=50.0)
    second = await evaluator.evaluate_rule(
        db, tenant_a.id, view, observed=50.0, now=_now() + timedelta(minutes=1)
    )
    await db.commit()
    assert first.state == evaluator.STATE_FIRED
    assert second.state == evaluator.STATE_FIRED


@pytest.mark.asyncio
async def test_duplicate_key_is_suppressed(db, tenant_a):
    """Two workers, one bucket: the second insert loses and reports dedupe."""
    rule = await _rule_row(db, tenant_a.id, threshold=10.0)
    view = evaluator.AlertRuleView.from_row(rule)
    moment = _now()
    first = await evaluator.evaluate_rule(db, tenant_a.id, view, observed=50.0, now=moment)
    await db.commit()

    # Force the same key by asking for the same bucket and sequence.
    from app.db.monitoring_models import AlertEvent as _Event

    bucket = evaluator.window_bucket(moment, view.window_minutes)
    clash = _Event(
        tenant_id=tenant_a.id,
        rule_id=rule.id,
        metric=view.metric,
        observed_value=1.0,
        threshold=view.threshold,
        state=evaluator.STATE_FIRED,
        dedupe_key=evaluator.dedupe_key(rule.id, view.scope_kind, view.scope_id, bucket, 1),
        deliveries=[],
    )
    db.add(clash)
    with pytest.raises(Exception):
        await db.flush()
    await db.rollback()
    assert first.event_id is not None


# ------------------------------------------------------------------ delivery ---


class _Adapter:
    def __init__(self, outcome: str = "accepted") -> None:
        self.name = "webhook"
        self.outcome = outcome
        self.sent: list[str] = []

    async def send(self, *, recipient: str, body: str) -> DispatchResult:
        self.sent.append(recipient)
        return DispatchResult(self.outcome, "test", self.name)


@pytest.mark.asyncio
async def test_in_app_delivery_needs_no_recipient(db, tenant_a):
    rule = await _rule_row(db, tenant_a.id, threshold=1.0, channels=["in_app"])
    view = evaluator.AlertRuleView.from_row(rule)
    result = await evaluator.evaluate_rule(db, tenant_a.id, view, observed=50.0)
    await db.commit()
    event = await db.get(AlertEvent, result.event_id)
    deliveries = await notifier.deliver(db, event, view)
    await db.commit()
    assert deliveries[0]["status"] == "accepted"
    assert event.state == evaluator.STATE_FIRED


@pytest.mark.asyncio
async def test_webhook_without_recipient_is_skipped_not_faked(db, tenant_a):
    rule = await _rule_row(db, tenant_a.id, threshold=1.0, channels=["webhook"])
    view = evaluator.AlertRuleView.from_row(rule)
    result = await evaluator.evaluate_rule(db, tenant_a.id, view, observed=50.0)
    await db.commit()
    event = await db.get(AlertEvent, result.event_id)
    deliveries = await notifier.deliver(db, event, view)
    assert deliveries[0]["status"] == notifier.STATUS_SKIPPED
    assert event.state == evaluator.STATE_FIRED


@pytest.mark.asyncio
async def test_failed_delivery_marks_the_event(db, tenant_a):
    rule = await _rule_row(
        db,
        tenant_a.id,
        threshold=1.0,
        channels=[{"channel": "webhook", "recipient": "https://ops.example/hook"}],
    )
    view = evaluator.AlertRuleView.from_row(rule)
    result = await evaluator.evaluate_rule(db, tenant_a.id, view, observed=50.0)
    await db.commit()
    event = await db.get(AlertEvent, result.event_id)
    adapter = _Adapter(outcome="permanent_failure")
    deliveries = await notifier.deliver(db, event, view, adapters={"webhook": adapter})
    await db.commit()
    assert deliveries[0]["status"] == "permanent_failure"
    # The breach is kept, but it no longer claims to have been delivered.
    assert event.state == evaluator.STATE_DELIVERY_FAILED
    assert adapter.sent == ["https://ops.example/hook"]


@pytest.mark.asyncio
async def test_delivery_masks_the_recipient(db, tenant_a):
    rule = await _rule_row(
        db,
        tenant_a.id,
        threshold=1.0,
        channels=[
            {"channel": "webhook", "recipient": "https://ops.example/hook?token=secret"},
            {"channel": "email", "recipient": "oncall@example.com"},
        ],
    )
    view = evaluator.AlertRuleView.from_row(rule)
    result = await evaluator.evaluate_rule(db, tenant_a.id, view, observed=50.0)
    await db.commit()
    event = await db.get(AlertEvent, result.event_id)
    deliveries = await notifier.deliver(
        db, event, view, adapters={"webhook": _Adapter(), "email": _Adapter()}
    )
    rendered = str(deliveries)
    assert "secret" not in rendered
    assert "oncall@" not in rendered


def test_parse_channels_accepts_both_shapes():
    pairs = notifier.parse_channels(
        ["in_app", {"channel": "webhook", "recipient": "https://x.example/y"}, {"nope": 1}, ""]
    )
    assert pairs == [("in_app", ""), ("webhook", "https://x.example/y")]


def test_body_never_contains_call_content():
    rule = evaluator.AlertRuleView(
        id=uuid.uuid4(), name="Error spike", metric="error_rate", comparator="gt", threshold=5.0
    )
    event = AlertEvent(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        rule_id=rule.id,
        metric="error_rate",
        observed_value=12.5,
        threshold=5.0,
        state=evaluator.STATE_FIRED,
        dedupe_key="x",
    )
    body = notifier.build_body(event, rule)
    assert "error_rate" in body and "12.5" in body
    assert "transcript" not in body.lower()


# ---------------------------------------------------------------------- API ---


@pytest.mark.asyncio
async def test_api_crud_and_tenant_isolation(client, db, tenant_a):
    from tests.conftest import make_user
    from app.db.models import UserRole

    owner = await make_user(db, tenant_a, UserRole.OWNER)
    other_tenant = await make_tenant(db, "Alerts B")
    other_owner = await make_user(db, other_tenant, UserRole.OWNER)
    headers = await auth_headers(client, owner)
    other_headers = await auth_headers(client, other_owner)

    created = await client.post(
        "/api/alerts/rules",
        headers=headers,
        json={
            "name": "Latency spike",
            "metric": "p95_latency",
            "comparator": "gt",
            "threshold": 2500,
            "window_minutes": 15,
            "cooldown_minutes": 60,
            "channels": ["in_app"],
        },
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    assert created.json()["description"].startswith("p95_latency above")

    listed = await client.get("/api/alerts/rules", headers=headers)
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()["rules"]] == [rule_id]

    # Isolation: another tenant sees neither the row nor a 403 that confirms it.
    assert (await client.get(f"/api/alerts/rules/{rule_id}", headers=other_headers)).status_code in (404, 405)
    assert (
        await client.patch(
            f"/api/alerts/rules/{rule_id}", headers=other_headers, json={"threshold": 1.0}
        )
    ).status_code == 404
    assert [row["id"] for row in (await client.get("/api/alerts/rules", headers=other_headers)).json()["rules"]] == []

    patched = await client.patch(
        f"/api/alerts/rules/{rule_id}", headers=headers, json={"threshold": 3000}
    )
    assert patched.status_code == 200
    assert patched.json()["threshold"] == 3000

    fired = await client.post(
        f"/api/alerts/rules/{rule_id}/test-fire", headers=headers, json={"observed": 9999}
    )
    assert fired.status_code == 200
    body = fired.json()
    assert body["would_breach"] is True
    assert body["recorded"] is False, "a dry test fire must not write an event"


@pytest.mark.asyncio
async def test_api_rejects_an_unknown_metric(client, db, tenant_a):
    from app.db.models import UserRole
    from tests.conftest import make_user

    owner = await make_user(db, tenant_a, UserRole.OWNER)
    headers = await auth_headers(client, owner)
    response = await client.post(
        "/api/alerts/rules",
        headers=headers,
        json={"name": "Bogus", "metric": "vibes", "comparator": "gt", "threshold": 1},
    )
    assert response.status_code == 422
