# File: tests/telephony/test_auto_actions.py — matching, the loop guard, and the audit row
"""Auto-action tests (PART 15, P15-03).

The feature is only safe if it cannot fire twice, so most of these tests are
about *not* acting:

* one action per call when ``max_per_call`` is 1,
* a second, legitimate application when it is 2,
* a ``skipped`` row when the budget is gone (a silent skip is not acceptable),
* ``duplicate`` when two workers race the same key,
* ``skipped`` -- never ``applied`` -- when no handler is registered.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

import app.db.models  # noqa: F401
from app.db.models import Tenant
from app.db.monitoring_models import AutoActionRule, AutoActionRun
from app.telephony import auto_actions as aa
from tests.conftest import make_tenant


def _rule(**overrides) -> aa.RuleView:
    payload = dict(
        id=uuid.uuid4(),
        name="Transfer when the caller is furious",
        trigger="score_below",
        action="transfer",
        threshold=30.0,
        max_per_call=1,
    )
    payload.update(overrides)
    return aa.RuleView(**payload)


# ---------------------------------------------------------------- matching ---


def test_score_trigger_matches_below_only():
    rule = _rule(threshold=30.0)
    assert aa.match(rule, aa.Signal(score=29)) is not None
    assert aa.match(rule, aa.Signal(score=30)) is None
    assert aa.match(rule, aa.Signal(score=None)) is None


def test_sentiment_and_latency_triggers():
    sentiment_rule = _rule(trigger="sentiment_below", threshold=-0.4)
    assert aa.match(sentiment_rule, aa.Signal(sentiment=-0.5)) is not None
    assert aa.match(sentiment_rule, aa.Signal(sentiment=-0.1)) is None

    latency_rule = _rule(trigger="latency_above", threshold=1500.0)
    assert aa.match(latency_rule, aa.Signal(latency_ms=1600.0)) is not None
    assert aa.match(latency_rule, aa.Signal(latency_ms=1400.0)) is None


def test_keyword_and_loop_triggers():
    keyword_rule = _rule(trigger="keyword", keywords=("cancel", "refund"))
    assert aa.match(keyword_rule, aa.Signal(text="I want to CANCEL my order")) is not None
    assert aa.match(keyword_rule, aa.Signal(text="book me in")) is None

    loop_rule = _rule(trigger="loop_detected")
    assert aa.match(loop_rule, aa.Signal(loop_detected=True)) is not None
    assert aa.match(loop_rule, aa.Signal(loop_detected=False)) is None


def test_disabled_and_out_of_scope_rules_never_match():
    assert aa.match(_rule(enabled=False), aa.Signal(score=1)) is None
    scoped = _rule(scope_agent_id="agent-a")
    assert aa.match(scoped, aa.Signal(score=1, agent_id="agent-b")) is None
    assert aa.match(scoped, aa.Signal(score=1, agent_id="agent-a")) is not None
    wildcard = _rule(scope_agent_id="*")
    assert aa.match(wildcard, aa.Signal(score=1, agent_id="agent-b")) is not None


def test_unknown_trigger_never_matches():
    assert aa.match(_rule(trigger="teleport"), aa.Signal(score=1)) is None


# --------------------------------------------------------------- loop guard ---


def test_loop_guard_blocks_the_second_application():
    rule = _rule(max_per_call=1)
    decisions = aa.decide([rule], aa.Signal(score=5))
    assert [d.status for d in decisions] == [aa.STATUS_PENDING]

    blocked = aa.decide([rule], aa.Signal(score=5), applied_counts={rule.id: 1})
    assert [d.status for d in blocked] == [aa.STATUS_SKIPPED]
    assert blocked[0].reason == "loop_guard"
    assert blocked[0].detail["max_per_call"] == 1


def test_max_per_call_two_allows_a_second_sequence():
    rule = _rule(max_per_call=2)
    first = aa.decide([rule], aa.Signal(score=5), applied_counts={rule.id: 1})
    assert first[0].status == aa.STATUS_PENDING
    assert first[0].sequence == 2
    third = aa.decide([rule], aa.Signal(score=5), applied_counts={rule.id: 2})
    assert third[0].status == aa.STATUS_SKIPPED


# ------------------------------------------------------------------ execution ---


@pytest_asyncio.fixture
async def tenant_a(db) -> Tenant:
    return await make_tenant(db, "Actions A")


async def _seed_rule(db, tenant_id: uuid.UUID, **kwargs) -> AutoActionRule:
    row = AutoActionRule(
        tenant_id=tenant_id,
        name=kwargs.pop("name", f"rule-{uuid.uuid4().hex[:6]}"),
        trigger=kwargs.pop("trigger", "score_below"),
        threshold=kwargs.pop("threshold", 30.0),
        action=kwargs.pop("action", "transfer"),
        max_per_call=kwargs.pop("max_per_call", 1),
        **kwargs,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@pytest.mark.asyncio
async def test_applied_action_is_audited(db, tenant_a):
    call_id = uuid.uuid4()
    rule = await _seed_rule(db, tenant_a.id)
    calls: list[dict] = []

    async def handler(*, call_id, config, context):
        calls.append({"call_id": call_id, "context": dict(context)})
        return {"transferred_to": "+15550000000"}

    decisions = await aa.run_auto_actions(
        db,
        tenant_id=tenant_a.id,
        call_id=call_id,
        rules=[aa.RuleView.from_row(rule)],
        signal=aa.Signal(score=10, agent_id="agent-a"),
        handlers={"transfer": handler},
    )
    await db.commit()

    assert len(calls) == 1
    assert decisions[0].status == aa.STATUS_APPLIED
    rows = (
        await db.scalars(select(AutoActionRun).where(AutoActionRun.call_id == call_id))
    ).all()
    assert len(rows) == 1
    assert rows[0].status == aa.STATUS_APPLIED
    assert rows[0].sequence == 1
    assert rows[0].detail["matched"].startswith("score 10")


@pytest.mark.asyncio
async def test_second_evaluation_is_blocked_and_recorded(db, tenant_a):
    call_id = uuid.uuid4()
    rule = await _seed_rule(db, tenant_a.id)
    executed: list[int] = []

    async def handler(**kwargs):
        executed.append(1)
        return {}

    view = aa.RuleView.from_row(rule)
    await aa.run_auto_actions(
        db,
        tenant_id=tenant_a.id,
        call_id=call_id,
        rules=[view],
        signal=aa.Signal(score=5),
        handlers={"transfer": handler},
    )
    await db.commit()

    second = await aa.run_auto_actions(
        db,
        tenant_id=tenant_a.id,
        call_id=call_id,
        rules=[view],
        signal=aa.Signal(score=5),
        handlers={"transfer": handler},
    )
    await db.commit()

    assert len(executed) == 1, "the second evaluation must not call the handler"
    assert second[0].status == aa.STATUS_SKIPPED
    assert second[0].reason == "loop_guard"
    # The skip is a row, not a silence.
    rows = (
        await db.scalars(
            select(AutoActionRun)
            .where(AutoActionRun.call_id == call_id)
            .order_by(AutoActionRun.sequence)
        )
    ).all()
    assert [row.status for row in rows] == [aa.STATUS_APPLIED, aa.STATUS_SKIPPED]


@pytest.mark.asyncio
async def test_missing_handler_is_skipped_not_applied(db, tenant_a):
    call_id = uuid.uuid4()
    rule = await _seed_rule(db, tenant_a.id)
    decisions = await aa.run_auto_actions(
        db,
        tenant_id=tenant_a.id,
        call_id=call_id,
        rules=[aa.RuleView.from_row(rule)],
        signal=aa.Signal(score=1),
        handlers={},
    )
    await db.commit()
    assert decisions[0].status == aa.STATUS_SKIPPED
    # The reason is in the row, so the UI can say why nothing happened.
    assert decisions[0].detail.get("reason") == "no_handler_registered"
    row = await db.scalar(select(AutoActionRun).where(AutoActionRun.call_id == call_id))
    assert row is not None and row.status == aa.STATUS_SKIPPED
    assert "no handler registered" in (row.last_error or "")


@pytest.mark.asyncio
async def test_failing_handler_is_failed_and_the_budget_is_still_consumed(db, tenant_a):
    call_id = uuid.uuid4()
    rule = await _seed_rule(db, tenant_a.id)

    async def broken(**kwargs):
        raise RuntimeError("sip trunk refused")

    decisions = await aa.run_auto_actions(
        db,
        tenant_id=tenant_a.id,
        call_id=call_id,
        rules=[aa.RuleView.from_row(rule)],
        signal=aa.Signal(score=1),
        handlers={"transfer": broken},
    )
    await db.commit()
    assert decisions[0].status == aa.STATUS_FAILED
    row = await db.scalar(select(AutoActionRun).where(AutoActionRun.call_id == call_id))
    assert row is not None and row.status == aa.STATUS_FAILED
    assert "sip trunk refused" in (row.last_error or "")


@pytest.mark.asyncio
async def test_duplicate_key_loses_quietly(db, tenant_a):
    """Two workers, one key: the loser reports ``duplicate`` and writes nothing."""
    call_id = uuid.uuid4()
    rule = await _seed_rule(db, tenant_a.id)
    view = aa.RuleView.from_row(rule)
    decision = aa.Decision(
        rule_id=view.id,
        rule_name=view.name,
        action="transfer",
        sequence=1,
        status=aa.STATUS_PENDING,
        reason="score 1 < 30",
    )
    first = await aa.record(
        db, tenant_id=tenant_a.id, call_id=call_id, decision=decision, status=aa.STATUS_APPLIED
    )
    second = await aa.record(
        db, tenant_id=tenant_a.id, call_id=call_id, decision=decision, status=aa.STATUS_APPLIED
    )
    await db.commit()
    assert first is not None
    assert second is None
    rows = (
        await db.scalars(select(AutoActionRun).where(AutoActionRun.call_id == call_id))
    ).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_load_rules_scopes_to_agent(db, tenant_a):
    await _seed_rule(db, tenant_a.id, name="tenant-wide", scope_agent_id=None)
    await _seed_rule(db, tenant_a.id, name="other-agent", scope_agent_id="agent-z")
    await _seed_rule(db, tenant_a.id, name="my-agent", scope_agent_id="agent-a")
    await _seed_rule(db, tenant_a.id, name="disabled", scope_agent_id="agent-a", enabled=False)

    views = await aa.load_rules(db, tenant_a.id, agent_id="agent-a")
    names = sorted(view.name for view in views)
    assert names == ["my-agent", "tenant-wide"]


def test_summarise_counts_each_status():
    decisions = [
        aa.Decision(uuid.uuid4(), "a", "transfer", 1, aa.STATUS_APPLIED, "x"),
        aa.Decision(uuid.uuid4(), "b", "webhook", 1, aa.STATUS_SKIPPED, "loop_guard"),
        aa.Decision(uuid.uuid4(), "c", "alert", 1, aa.STATUS_FAILED, "boom"),
    ]
    summary = aa.summarise(decisions)
    assert summary[aa.STATUS_APPLIED] == 1
    assert summary[aa.STATUS_SKIPPED] == 1
    assert summary[aa.STATUS_FAILED] == 1
