# File: tests/services/test_batch_tests.py — bulk run, import/export round trip, copy on duplicate
"""Batch test service tests (PART 15, P15-08).

The executor is injected, so these tests exercise the service -- matching,
verdicts, totals, import idempotency -- rather than a model's output. The two
behaviours that protect the product from itself get their own tests:

* an executor that raises produces a **failed** result, never a pass;
* re-importing the same case **updates** it, never duplicates it.
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select

import app.db.models  # noqa: F401
from app.db.models import Tenant
from app.db.monitoring_models import BatchTestCase, BatchTestResult, BatchTestRun
from app.services import batch_test_service as svc
from tests.conftest import make_tenant


@pytest_asyncio.fixture
async def tenant_a(db) -> Tenant:
    return await make_tenant(db, "Batch A")


def _case(name: str = "refund request", **overrides) -> BatchTestCase:
    payload = dict(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        agent_id="agent-1",
        name=name,
        user_message="I want a refund",
        expected_contains=["refund"],
        forbidden_contains=["guarantee"],
        max_latency_ms=2000,
    )
    payload.update(overrides)
    return BatchTestCase(**payload)


# ------------------------------------------------------------------ verdicts ---


def test_passing_reply():
    verdict = svc.check_case(_case(), svc.CaseOutcome(reply="I can start a refund today.", latency_ms=400))
    assert verdict.passed is True
    assert verdict.reason is None


def test_missing_expected_phrase_fails():
    verdict = svc.check_case(_case(), svc.CaseOutcome(reply="I can help with that."))
    assert verdict.passed is False
    assert verdict.reason.startswith("missing_phrase:refund")


def test_forbidden_phrase_fails_before_the_expected_one():
    verdict = svc.check_case(
        _case(), svc.CaseOutcome(reply="I guarantee a refund and no refund will be denied.")
    )
    assert verdict.passed is False
    assert verdict.reason.startswith("forbidden_phrase:guarantee")


def test_latency_budget_fails():
    verdict = svc.check_case(
        _case(), svc.CaseOutcome(reply="refund is on its way", latency_ms=9000)
    )
    assert verdict.passed is False
    assert verdict.reason.startswith("latency:")


def test_executor_error_never_passes():
    verdict = svc.check_case(_case(), svc.CaseOutcome(reply="", error="RuntimeError: provider down"))
    assert verdict.passed is False
    assert verdict.reason.startswith("executor_error:")


def test_empty_reply_never_passes():
    verdict = svc.check_case(_case(expected_contains=[]), svc.CaseOutcome(reply="   "))
    assert verdict.passed is False
    assert verdict.reason == "empty_reply"


# -------------------------------------------------------------- case storage ---


@pytest.mark.asyncio
async def test_upsert_is_idempotent_per_name(db, tenant_a):
    row, action = await svc.upsert_case(
        db,
        tenant_a.id,
        agent_id="agent-1",
        name="refund request",
        user_message="I want a refund",
        expected_contains=["refund"],
    )
    await db.commit()
    assert action == "created"

    again, second = await svc.upsert_case(
        db,
        tenant_a.id,
        agent_id="agent-1",
        name="refund request",
        user_message="I want my money back",
        expected_contains=["refund", "money"],
    )
    await db.commit()
    assert second == "updated"
    assert again.id == row.id
    assert again.user_message == "I want my money back"
    rows = (
        await db.scalars(select(BatchTestCase).where(BatchTestCase.tenant_id == tenant_a.id))
    ).all()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_upsert_requires_a_message(db, tenant_a):
    with pytest.raises(svc.BatchTestError) as excinfo:
        await svc.upsert_case(
            db, tenant_a.id, agent_id="agent-1", name="empty", user_message="   "
        )
    assert excinfo.value.status_code == 422


# ------------------------------------------------------------ import/export ---


@pytest.mark.asyncio
async def test_import_export_round_trip(db, tenant_a):
    payload = {
        "cases": [
            {
                "name": "refund",
                "user_message": "I want a refund",
                "expected_contains": ["refund"],
                "max_latency_ms": 1500,
            },
            {
                "name": "cancel",
                "user_message": "cancel my appointment",
                "forbidden_contains": ["cannot"],
            },
        ]
    }
    report = await svc.import_cases(db, tenant_a.id, agent_id="agent-1", payload=payload)
    await db.commit()
    assert report.created == ["refund", "cancel"]
    assert report.errors == []

    exported = await svc.export_cases(db, tenant_a.id, "agent-1")
    assert exported["version"] == 1
    names = [case["name"] for case in exported["cases"]]
    assert names == ["cancel", "refund"]  # sorted by name on read

    # Re-importing the export changes nothing (same names, same content).
    second = await svc.import_cases(db, tenant_a.id, agent_id="agent-1", payload=exported)
    await db.commit()
    assert second.created == []
    assert sorted(second.updated) == ["cancel", "refund"]
    rows = await svc.list_cases(db, tenant_a.id, "agent-1")
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_import_skips_bad_cases_and_keeps_the_good_ones(db, tenant_a):
    report = await svc.import_cases(
        db,
        tenant_a.id,
        agent_id="agent-1",
        payload=[{"name": "good", "user_message": "hello"}, {"name": "bad", "user_message": ""}],
    )
    await db.commit()
    assert report.created == ["good"]
    assert len(report.errors) == 1
    assert report.errors[0]["name"] == "bad"


@pytest.mark.asyncio
async def test_import_rejects_a_flood(db, tenant_a):
    with pytest.raises(svc.BatchTestError) as excinfo:
        await svc.import_cases(
            db,
            tenant_a.id,
            agent_id="agent-1",
            payload=[{"name": f"c{i}", "user_message": "x"} for i in range(svc.MAX_CASES_PER_IMPORT + 1)],
        )
    assert excinfo.value.status_code == 413


# -------------------------------------------------------------------- runs ---


@pytest.mark.asyncio
async def test_run_records_every_case_and_the_totals(db, tenant_a):
    await svc.upsert_case(
        db, tenant_a.id, agent_id="agent-1", name="ok", user_message="refund", expected_contains=["refund"]
    )
    await svc.upsert_case(
        db, tenant_a.id, agent_id="agent-1", name="bad", user_message="refund", expected_contains=["impossible"]
    )
    await db.commit()

    run = await svc.start_run(db, tenant_a.id, agent_id="agent-1", created_by="tester")
    assert run.status == svc.STATUS_PENDING
    assert run.total == 2

    async def executor(case):
        if case.name == "ok":
            return svc.CaseOutcome(reply="your refund is approved", latency_ms=200)
        return svc.CaseOutcome(reply="no idea", latency_ms=250)

    run = await svc.execute_run(db, run, executor=executor)
    await db.commit()

    assert run.status == svc.STATUS_COMPLETED
    assert (run.passed, run.failed, run.total) == (1, 1, 2)
    assert run.started_at is not None and run.finished_at is not None
    results = await svc.list_results(db, run.id)
    assert {row.case_name for row in results} == {"ok", "bad"}
    stored = {row.case_name: row for row in results}
    assert stored["ok"].passed is True
    assert stored["bad"].passed is False
    assert stored["bad"].failure_reason == "missing_phrase:impossible"


@pytest.mark.asyncio
async def test_executor_crash_does_not_end_the_batch(db, tenant_a):
    await svc.upsert_case(db, tenant_a.id, agent_id="agent-1", name="one", user_message="hi")
    await svc.upsert_case(db, tenant_a.id, agent_id="agent-1", name="two", user_message="hi")
    await db.commit()
    run = await svc.start_run(db, tenant_a.id, agent_id="agent-1")
    seen: list[str] = []

    async def executor(case):
        seen.append(case.name)
        if case.name == "one":
            raise RuntimeError("provider exploded")
        return svc.CaseOutcome(reply="hello")

    run = await svc.execute_run(db, run, executor=executor)
    await db.commit()
    assert seen == ["one", "two"]  # order preserved, second case still ran
    assert run.failed == 1 and run.passed == 1
    results = {row.case_name: row for row in await svc.list_results(db, run.id)}
    assert results["one"].failure_reason.startswith("executor_error:RuntimeError")


@pytest.mark.asyncio
async def test_start_run_without_cases_is_refused(db, tenant_a):
    with pytest.raises(svc.BatchTestError) as excinfo:
        await svc.start_run(db, tenant_a.id, agent_id="no-cases")
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_completed_run_is_not_rerun(db, tenant_a):
    await svc.upsert_case(db, tenant_a.id, agent_id="agent-1", name="one", user_message="hi")
    await db.commit()
    run = await svc.start_run(db, tenant_a.id, agent_id="agent-1")

    async def executor(case):
        return svc.CaseOutcome(reply="hello")

    run = await svc.execute_run(db, run, executor=executor)
    await db.commit()
    calls: list[str] = []

    async def second_executor(case):
        calls.append(case.name)
        return svc.CaseOutcome(reply="hello")

    again = await svc.execute_run(db, run, executor=second_executor)
    assert again.status == svc.STATUS_COMPLETED
    assert calls == [], "a finished run must not execute again"


@pytest.mark.asyncio
async def test_cancel_marks_the_run(db, tenant_a):
    await svc.upsert_case(db, tenant_a.id, agent_id="agent-1", name="one", user_message="hi")
    await db.commit()
    run = await svc.start_run(db, tenant_a.id, agent_id="agent-1")
    cancelled = await svc.cancel_run(db, tenant_a.id, run.id)
    await db.commit()
    assert cancelled.status == svc.STATUS_CANCELLED
    with pytest.raises(svc.BatchTestError):
        await svc.cancel_run(db, tenant_a.id, run.id)


@pytest.mark.asyncio
async def test_disabled_cases_are_skipped(db, tenant_a):
    await svc.upsert_case(db, tenant_a.id, agent_id="agent-1", name="on", user_message="hi")
    await svc.upsert_case(
        db, tenant_a.id, agent_id="agent-1", name="off", user_message="hi", enabled=False
    )
    await db.commit()
    run = await svc.start_run(db, tenant_a.id, agent_id="agent-1")
    assert run.total == 1


# --------------------------------------------------------- copy on duplicate ---


@pytest.mark.asyncio
async def test_copy_cases_onto_a_cloned_agent(db, tenant_a):
    await svc.upsert_case(
        db, tenant_a.id, agent_id="source", name="refund", user_message="refund me"
    )
    await svc.upsert_case(
        db, tenant_a.id, agent_id="source", name="cancel", user_message="cancel me"
    )
    await db.commit()

    copied = await svc.copy_cases(
        db, tenant_a.id, from_agent_id="source", to_agent_id="clone"
    )
    await db.commit()
    assert copied == 2
    clone_cases = await svc.list_cases(db, tenant_a.id, "clone")
    assert [case.name for case in clone_cases] == ["cancel", "refund"]

    # Copying twice does not double the set, because the name is the key.
    await svc.copy_cases(db, tenant_a.id, from_agent_id="source", to_agent_id="clone")
    await db.commit()
    assert len(await svc.list_cases(db, tenant_a.id, "clone")) == 2


@pytest.mark.asyncio
async def test_copy_into_itself_is_a_noop(db, tenant_a):
    await svc.upsert_case(db, tenant_a.id, agent_id="agent-1", name="one", user_message="hi")
    await db.commit()
    assert await svc.copy_cases(db, tenant_a.id, from_agent_id="agent-1", to_agent_id="agent-1") == 0


@pytest.mark.asyncio
async def test_tenant_isolation_on_runs(db, tenant_a):
    other = await make_tenant(db, "Batch B")
    await svc.upsert_case(db, tenant_a.id, agent_id="agent-1", name="one", user_message="hi")
    await db.commit()
    run = await svc.start_run(db, tenant_a.id, agent_id="agent-1")

    async def executor(case):
        return svc.CaseOutcome(reply="hello")

    await svc.execute_run(db, run, executor=executor)
    await db.commit()
    with pytest.raises(svc.BatchTestError) as excinfo:
        await svc.get_run(db, other.id, run.id)
    assert excinfo.value.status_code == 404
    assert await svc.list_runs(db, other.id) == []


@pytest.mark.asyncio
async def test_results_are_stored_per_run(db, tenant_a):
    await svc.upsert_case(db, tenant_a.id, agent_id="agent-1", name="one", user_message="hi")
    await db.commit()
    run = await svc.start_run(db, tenant_a.id, agent_id="agent-1")

    async def executor(case):
        return svc.CaseOutcome(reply="hello")

    await svc.execute_run(db, run, executor=executor)
    await db.commit()
    count = (await db.scalars(select(BatchTestResult).where(BatchTestResult.run_id == run.id))).all()
    assert len(count) == 1
    stored_run = await db.get(BatchTestRun, run.id)
    assert stored_run is not None and stored_run.total == 1
