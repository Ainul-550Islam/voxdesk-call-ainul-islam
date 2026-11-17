# File: tests/qa/test_analyst.py — hallucination vs evidence, tool accuracy, repeat contacts, clustering
"""AI QA analyst tests (PART 15, P15-05).

The two metrics that get misused are the ones with a ``None`` case, and that is
what most of this file pins down:

* a hallucination rate over zero checkable claims is **unknown**, not 0 % --
  reporting 0 % would turn "we have no evidence" into "we are perfect";
* a resolution rate over zero answered calls is **unknown** for the same reason.

The clustering tests use texts whose overlap is obvious to a human, so the
assertion is about the algorithm's behaviour, not about a model's mood.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import pytest_asyncio

import app.db.models  # noqa: F401
from app.db.models import CallStatus, Tenant, ToolExecution
from app.qa import analyst
from app.qa.models import QAFinding
from tests.conftest import make_call, make_tenant


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------- hallucination rate ---


def test_hallucination_rate_counts_only_checkable_claims():
    checks = [
        analyst.ClaimCheck("we are open on Sunday", "kb", False),
        analyst.ClaimCheck("we have a slot at nine", "tool", True),
        analyst.ClaimCheck("you will love it", "none", None),
        analyst.ClaimCheck("the price is 40", "kb", True),
    ]
    assert analyst.hallucination_rate(checks) == pytest.approx(33.3333, abs=0.001)


def test_no_checkable_claims_is_unknown_not_zero():
    assert analyst.hallucination_rate([]) is None
    assert analyst.hallucination_rate([analyst.ClaimCheck("nice weather", "none", None)]) is None


def test_ungrounded_claims_are_counted_when_they_were_checkable():
    checks = [analyst.ClaimCheck("x", "kb", False) for _ in range(3)]
    assert analyst.hallucination_rate(checks) == 100.0


# ------------------------------------------------------- tool accuracy ---


def test_tool_accuracy_and_error_breakdown():
    attempts = [
        analyst.ToolAttempt("calendar", "succeeded"),
        analyst.ToolAttempt("crm", "failed", "timeout"),
        analyst.ToolAttempt("crm", "failed", "timeout"),
        analyst.ToolAttempt("kb", "completed"),
    ]
    assert analyst.tool_call_accuracy(attempts) == 50.0
    assert analyst.tool_error_breakdown(attempts) == {"timeout": 2}
    assert analyst.tool_call_accuracy([]) is None


def test_resolution_rate_is_unknown_without_classified_calls():
    assert analyst.resolution_rate(0, 0) is None
    assert analyst.resolution_rate(3, 4) == 75.0


# ------------------------------------------------------ repeat contacts ---


def _facet(number: str, outcome: str, minutes_ago: int, intent: str = "booking") -> analyst.CallFacet:
    return analyst.CallFacet(
        call_id=uuid.uuid4(),
        number=number,
        outcome=outcome,
        intent=intent,
        started_at=_now() - timedelta(minutes=minutes_ago),
    )


def test_repeat_contact_needs_an_unresolved_call():
    same_number = [
        _facet("+15550001111", "resolved", 90),
        _facet("+15550001111", "unresolved", 30),
    ]
    patterns = analyst.find_repeat_contacts(same_number)
    assert len(patterns) == 1
    assert patterns[0].calls == 2
    assert patterns[0].intents == ("booking",)

    all_resolved = [
        _facet("+15550002222", "resolved", 90),
        _facet("+15550002222", "resolved", 30),
    ]
    assert analyst.find_repeat_contacts(all_resolved) == []


def test_repeat_contact_outside_the_window_is_ignored():
    calls = [
        _facet("+15550003333", "unresolved", 60 * 24 * 5),
        _facet("+15550003333", "unresolved", 10),
    ]
    assert analyst.find_repeat_contacts(calls, window_hours=72) == []


def test_repeat_pattern_masks_the_number():
    pattern = analyst.find_repeat_contacts(
        [_facet("+15550004444", "unresolved", 50), _facet("+15550004444", "resolved", 10)]
    )[0]
    payload = pattern.as_dict()
    assert payload["number"].endswith("4444")
    assert "+15550004444" not in str(payload)


# ----------------------------------------------------------- clustering ---


def test_clustering_groups_the_same_problem():
    items = [
        analyst.IssueItem(uuid.uuid4(), "booking failed calendar slot not found"),
        analyst.IssueItem(uuid.uuid4(), "booking failed calendar unavailable slot"),
        analyst.IssueItem(uuid.uuid4(), "transfer failed sip timeout trunk"),
    ]
    clusters = analyst.cluster_issues(items)
    sizes = [cluster.size for cluster in clusters]
    assert sorted(sizes, reverse=True) == [2, 1]
    assert "booking" in clusters[0].label or "calendar" in clusters[0].label
    # Every call belongs to exactly one cluster.
    members = [call_id for cluster in clusters for call_id in cluster.members]
    assert len(members) == len(set(members)) == 3


def test_clustering_is_order_independent():
    build = lambda: [  # noqa: E731 - a fixture factory, not a habit
        analyst.IssueItem(uuid.UUID(int=1), "calendar error while booking"),
        analyst.IssueItem(uuid.UUID(int=2), "calendar error booking slot"),
        analyst.IssueItem(uuid.UUID(int=3), "sip trunk timeout"),
    ]
    first = [cluster.as_dict() for cluster in analyst.cluster_issues(build())]
    second = [cluster.as_dict() for cluster in analyst.cluster_issues(list(reversed(build())))]
    assert first == second


def test_embedding_is_deterministic_and_normalised():
    vector = analyst.embed("calendar booking failure")
    assert vector == analyst.embed("calendar booking failure")
    norm = sum(value * value for value in vector) ** 0.5
    assert abs(norm - 1.0) < 1e-9
    assert analyst.embed("") == [0.0] * analyst.EMBED_DIM


def test_cosine_rejects_mismatched_widths():
    with pytest.raises(ValueError):
        analyst.cosine([1.0], [1.0, 2.0])


# ------------------------------------------------------ database-backed ---


@pytest_asyncio.fixture
async def tenant_a(db) -> Tenant:
    return await make_tenant(db, "Analyst A")


@pytest.mark.asyncio
async def test_analyse_tenant_reads_real_rows(db, tenant_a):
    booked = await make_call(db, tenant_a, booked=True, status=CallStatus.COMPLETED, started_at=_now() - timedelta(minutes=30))
    failed = await make_call(
        db,
        tenant_a,
        booked=False,
        status=CallStatus.FAILED,
        started_at=_now() - timedelta(minutes=20),
        failure_reason="calendar slot not found",
    )
    await make_call(
        db,
        tenant_a,
        booked=False,
        status=CallStatus.TRANSFERRED,
        started_at=_now() - timedelta(minutes=10),
        failure_reason="calendar unavailable",
    )
    db.add(
        QAFinding(
            tenant_id=tenant_a.id,
            call_id=booked.id,
            kind="hallucination",
            summary="claimed a slot that was not free",
            idempotency_key=f"k-{uuid.uuid4().hex[:8]}",
        )
    )
    db.add(
        ToolExecution(
            tenant_id=tenant_a.id,
            tool_type="calendar",
            tool_id=uuid.uuid4(),
            idempotency_key=f"t-{uuid.uuid4().hex[:8]}",
            status="succeeded",
        )
    )
    db.add(
        ToolExecution(
            tenant_id=tenant_a.id,
            tool_type="crm",
            tool_id=uuid.uuid4(),
            idempotency_key=f"t-{uuid.uuid4().hex[:8]}",
            status="failed",
            error_category="timeout",
        )
    )
    await db.commit()

    analysis = await analyst.analyse_tenant(
        db, tenant_a.id, start=_now() - timedelta(hours=2), end=_now() + timedelta(minutes=1)
    )
    metrics = analysis["metrics"]
    assert metrics["calls_analysed"] == 3
    # Two answered calls (completed + transferred), one of them resolved: a
    # hand-off is not a resolution, so this is 50 %, not 100 %.
    assert metrics["resolution_rate"] == pytest.approx(50.0, abs=0.01)
    assert metrics["hallucination_findings"] == 1
    assert metrics["hallucination_rate"] == pytest.approx(33.3333, abs=0.01)
    assert metrics["tool_calls"] == 2
    assert metrics["tool_call_accuracy"] == 50.0
    assert metrics["tool_errors"] == {"timeout": 1}
    # The two unresolved calendar failures are one cluster, not two rows.
    assert analysis["clusters"][0]["size"] == 2
    assert failed.id in [uuid.UUID(cid) for cid in analysis["clusters"][0]["call_ids"]]


@pytest.mark.asyncio
async def test_analysis_is_metered_and_stored(db, tenant_a):
    await make_call(db, tenant_a, started_at=_now() - timedelta(minutes=5))
    result = await analyst.run_analysis(
        db, tenant_a.id, start=_now() - timedelta(hours=1), end=_now() + timedelta(minutes=1)
    )
    await db.commit()
    run = result["run"]
    assert run.calls_analysed == 1
    assert run.metered_units == 1 * analyst.METERED_UNITS_PER_CALL
    assert run.metrics["calls_analysed"] == 1
    stored = await analyst.list_runs(db, tenant_a.id)
    assert [row.id for row in stored] == [run.id]


@pytest.mark.asyncio
async def test_empty_window_reports_unknown_not_zero(db, tenant_a):
    analysis = await analyst.analyse_tenant(
        db, tenant_a.id, start=_now() - timedelta(hours=1), end=_now()
    )
    assert analysis["metrics"]["calls_analysed"] == 0
    assert analysis["metrics"]["resolution_rate"] is None
    assert analysis["metrics"]["hallucination_rate"] is None
    assert analysis["metered_units"] == 0
