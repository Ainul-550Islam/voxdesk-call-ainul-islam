"""Adherence: worked-versus-scheduled minutes, the exceptions that follow, and the
decision on each exception.

``adherence`` is capped at 1.0 while ``conformance`` is not, and that asymmetry is the
main thing asserted here — an agent who stayed two hours late did not adhere better
than one who was on time, and a report that says otherwise is unusable in a review.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.db.contact_center_v2_models import AgentStateInterval
from app.db.models import User
from app.db.platform_v2_models import WfmShift
from app.platform_v2.errors import Conflict, NotFound
from app.platform_v2.wfm import adherence
from tests.platform_v2.conftest import PlatScope, seed_shift

DAY = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


def state(*, user_id: uuid.UUID, name: str, start: datetime, minutes: float) -> AgentStateInterval:
    return AgentStateInterval(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        user_id=user_id,
        state=name,
        started_at=start,
        ended_at=start + timedelta(minutes=minutes),
    )


def shift_row(*, user_id: uuid.UUID, start: datetime = DAY, hours: float = 8.0, break_minutes: int = 60) -> WfmShift:
    return WfmShift(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        environment_id=uuid.uuid4(),
        user_id=user_id,
        starts_at=start,
        ends_at=start + timedelta(hours=hours),
        break_minutes=break_minutes,
        status="published",
        skill_key="",
    )


# --- the arithmetic -----------------------------------------------------------


def test_totals_are_clipped_to_the_window():
    user = uuid.uuid4()
    intervals = [state(user_id=user, name="available", start=DAY - timedelta(hours=1), minutes=120)]
    totals = adherence.totals_for(intervals, start=DAY, end=DAY + timedelta(hours=1))
    assert totals.state("available") == pytest.approx(60.0)
    assert totals.on_queue == pytest.approx(60.0)
    assert totals.off_queue == 0.0


def test_an_open_interval_is_clipped_at_the_window_end():
    user = uuid.uuid4()
    open_interval = state(user_id=user, name="busy", start=DAY, minutes=600)
    open_interval.ended_at = None
    totals = adherence.totals_for([open_interval], start=DAY, end=DAY + timedelta(hours=2))
    assert totals.state("busy") == pytest.approx(120.0)


def test_states_are_split_between_on_queue_and_off_queue():
    user = uuid.uuid4()
    intervals = [
        state(user_id=user, name="available", start=DAY, minutes=60),
        state(user_id=user, name="busy", start=DAY + timedelta(hours=1), minutes=60),
        state(user_id=user, name="away", start=DAY + timedelta(hours=2), minutes=30),
        state(user_id=user, name="wrap_up", start=DAY + timedelta(hours=3), minutes=30),
    ]
    totals = adherence.totals_for(intervals, start=DAY, end=DAY + timedelta(hours=8))
    assert totals.on_queue == pytest.approx(120.0)
    assert totals.off_queue == pytest.approx(60.0)
    assert totals.logged_in == pytest.approx(180.0)
    assert totals.as_dict()["busy"] == pytest.approx(60.0)


def test_a_perfect_shift_adheres_completely():
    user = uuid.uuid4()
    shift = shift_row(user_id=user, break_minutes=60)
    states = [
        state(user_id=user, name="available", start=DAY, minutes=420),
        state(user_id=user, name="away", start=DAY + timedelta(hours=7), minutes=60),
    ]
    result = adherence.evaluate_shift(shift, states)
    assert result.scheduled_minutes == pytest.approx(480.0)
    # The granted break is logged-in time but not worked time, and the required
    # minutes already exclude it, so a shift spent exactly as planned is 100%.
    assert result.worked_minutes == pytest.approx(420.0)
    assert result.on_queue_minutes == pytest.approx(420.0)
    assert result.break_minutes == pytest.approx(60.0)
    assert result.adherence == pytest.approx(1.0)
    assert result.conformance == pytest.approx(1.0)


def test_a_short_shift_is_less_than_full_adherence_but_not_negative():
    user = uuid.uuid4()
    shift = shift_row(user_id=user, break_minutes=60)
    states = [state(user_id=user, name="available", start=DAY, minutes=210)]
    result = adherence.evaluate_shift(shift, states)
    assert result.adherence == pytest.approx(0.5)
    assert result.conformance == pytest.approx(0.5)


def test_staying_late_raises_conformance_but_not_adherence():
    user = uuid.uuid4()
    shift = shift_row(user_id=user, break_minutes=60)
    states = [state(user_id=user, name="available", start=DAY, minutes=540)]
    result = adherence.evaluate_shift(shift, states)
    assert result.adherence == pytest.approx(1.0), "a cap, not a reward"
    assert result.conformance == pytest.approx(540 / 420)


def test_a_shift_with_no_states_is_zero_not_an_error():
    result = adherence.evaluate_shift(shift_row(user_id=uuid.uuid4()), [])
    assert result.worked_minutes == 0.0
    assert result.adherence == 0.0
    assert result.first_state_at is None


# --- the exceptions -----------------------------------------------------------


def test_a_shift_with_no_state_at_all_is_a_no_show():
    shift = shift_row(user_id=uuid.uuid4())
    result = adherence.evaluate_shift(shift, [])
    found = adherence.detect_exceptions(shift, result)
    assert [item.kind for item in found] == ["no_show"]
    assert found[0].minutes == pytest.approx(480.0)


def test_a_late_start_is_measured_from_the_shift_start():
    user = uuid.uuid4()
    shift = shift_row(user_id=user)
    states = [state(user_id=user, name="available", start=DAY + timedelta(minutes=20), minutes=400)]
    result = adherence.evaluate_shift(shift, states)
    found = adherence.detect_exceptions(shift, result)
    kinds = {item.kind: item for item in found}
    assert kinds["late_start"].minutes == pytest.approx(20.0)
    # The tolerance is an argument: 25 minutes of grace makes the same shift clean.
    assert "late_start" not in {
        item.kind
        for item in adherence.detect_exceptions(shift, result, late_tolerance_minutes=25)
    }


def test_leaving_early_is_reported():
    user = uuid.uuid4()
    shift = shift_row(user_id=user)
    states = [state(user_id=user, name="available", start=DAY, minutes=360)]
    result = adherence.evaluate_shift(shift, states)
    kinds = {item.kind: item for item in adherence.detect_exceptions(shift, result)}
    assert kinds["early_leave"].minutes == pytest.approx(120.0)


def test_a_long_break_beyond_the_granted_break_is_reported():
    user = uuid.uuid4()
    shift = shift_row(user_id=user, break_minutes=30)
    states = [
        state(user_id=user, name="available", start=DAY, minutes=300),
        state(user_id=user, name="away", start=DAY + timedelta(hours=5), minutes=90),
    ]
    result = adherence.evaluate_shift(shift, states)
    kinds = {item.kind: item for item in adherence.detect_exceptions(shift, result)}
    assert kinds["over_break"].minutes == pytest.approx(60.0)


def test_an_unscheduled_break_is_its_own_exception():
    user = uuid.uuid4()
    shift = shift_row(user_id=user, break_minutes=0)
    states = [
        state(user_id=user, name="available", start=DAY, minutes=300),
        state(user_id=user, name="paused", start=DAY + timedelta(hours=5), minutes=45),
    ]
    result = adherence.evaluate_shift(shift, states)
    kinds = {item.kind for item in adherence.detect_exceptions(shift, result)}
    assert "unapproved_break" in kinds


def test_a_long_time_off_queue_is_reported():
    user = uuid.uuid4()
    shift = shift_row(user_id=user, break_minutes=60)
    states = [
        state(user_id=user, name="available", start=DAY, minutes=120),
        state(user_id=user, name="away", start=DAY + timedelta(hours=2), minutes=210),
    ]
    result = adherence.evaluate_shift(shift, states)
    kinds = {item.kind for item in adherence.detect_exceptions(shift, result)}
    assert "off_queue" in kinds
    assert kinds <= set(adherence.EXCEPTION_KINDS)


def test_exception_details_serialise():
    shift = shift_row(user_id=uuid.uuid4())
    found = adherence.detect_exceptions(shift, adherence.evaluate_shift(shift, []))
    body = found[0].as_dict()
    assert set(body) == {"kind", "minutes", "detail"}
    assert isinstance(body["detail"], str) and body["detail"]


# --- the report ---------------------------------------------------------------


def test_the_report_aggregates_per_person_and_flags_unplanned_time():
    alice, bob = uuid.uuid4(), uuid.uuid4()
    shifts = [shift_row(user_id=alice, break_minutes=0), shift_row(user_id=bob, break_minutes=0)]
    states = [
        state(user_id=alice, name="available", start=DAY, minutes=480),
        state(user_id=bob, name="available", start=DAY, minutes=240),
        # Half an hour before anyone's shift starts: the rota's blind spot.
        state(user_id=bob, name="available", start=DAY - timedelta(hours=1), minutes=30),
    ]
    report = adherence.build_report(
        shifts, states, start=DAY - timedelta(hours=2), end=DAY + timedelta(hours=9)
    )
    assert report.as_dict(include_shifts=False)["shifts"] == 2
    assert report.mean_adherence == pytest.approx((1.0 + 0.5) / 2)
    # Nothing was logged outside the rota, so there is no unplanned time: the
    # half-hour before the shift is inside the planned window of nobody, but the
    # rota's own 960 minutes exceed the 750 logged, which floors the figure at 0.
    assert report.unplanned_minutes == pytest.approx(0.0)
    by_user = report.by_user()
    assert by_user[str(alice)]["adherence"] == pytest.approx(1.0)
    assert by_user[str(bob)]["adherence"] == pytest.approx(0.5)
    assert by_user[str(alice)]["worked_minutes"] == pytest.approx(480.0)


def test_time_logged_by_somebody_with_no_shift_shows_as_unplanned():
    alice, eve = uuid.uuid4(), uuid.uuid4()
    shifts = [shift_row(user_id=alice, break_minutes=0)]
    states = [
        state(user_id=alice, name="available", start=DAY, minutes=480),
        state(user_id=eve, name="available", start=DAY, minutes=60),
    ]
    report = adherence.build_report(shifts, states, start=DAY, end=DAY + timedelta(hours=8))
    assert report.unplanned_minutes == pytest.approx(60.0)
    assert str(eve) not in report.by_user()


def test_a_report_with_no_shifts_is_empty_rather_than_dividing_by_zero():
    report = adherence.build_report([], [], start=DAY, end=DAY + timedelta(hours=8))
    assert report.mean_adherence == 0.0
    assert report.mean_conformance == 0.0
    assert report.by_user() == {}


def test_the_shrinkage_and_working_state_sets_are_named():
    assert "away" in adherence.shrinkage_states()
    assert "available" in adherence.working_states()
    assert set(adherence.OFF_QUEUE_STATES) <= set(adherence.shrinkage_states())
    assert set(adherence.ON_QUEUE_STATES) <= set(adherence.working_states())


# --- persistence --------------------------------------------------------------


async def _stated_shift(db, scope: PlatScope, user: User, *, minutes: int = 480) -> WfmShift:
    shift = await seed_shift(db, scope, user, starts_at=DAY, hours=8)
    db.add(
        AgentStateInterval(
            tenant_id=scope.tenant.id,
            environment_id=scope.environment.id,
            user_id=user.id,
            state="available",
            started_at=DAY + timedelta(minutes=20),
            ended_at=DAY + timedelta(minutes=20 + minutes),
        )
    )
    await db.commit()
    return shift


async def test_exceptions_are_stored_once_per_shift_and_kind(
    db, plat_scope: PlatScope, wfm_agent: User
):
    shift = await _stated_shift(db, plat_scope, wfm_agent, minutes=400)
    states = list(
        (
            await db.execute(
                __import__("sqlalchemy").select(AgentStateInterval).where(
                    AgentStateInterval.user_id == wfm_agent.id
                )
            )
        ).scalars()
    )
    report = adherence.build_report([shift], states, start=DAY, end=DAY + timedelta(hours=8))
    assert report.exceptions, "a 20-minute late start must be detected"

    first = await adherence.persist_exceptions(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        shift=shift,
        detected=report.exceptions,
        day=DAY,
    )
    second = await adherence.persist_exceptions(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        shift=shift,
        detected=report.exceptions,
        day=DAY,
    )
    assert len(first) == len(report.exceptions)
    assert second == [], "an hourly sweep must not re-accuse the same breach"


async def test_an_exception_can_be_excused_and_only_once(
    db, plat_scope: PlatScope, wfm_agent: User, wfm_manager: User
):
    shift = await _stated_shift(db, plat_scope, wfm_agent, minutes=400)
    report = adherence.build_report([shift], [], start=DAY, end=DAY + timedelta(hours=8))
    stored = await adherence.persist_exceptions(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        shift=shift,
        detected=report.exceptions,
        day=DAY,
    )
    excused = await adherence.decide_exception(
        db,
        tenant_id=plat_scope.tenant.id,
        exception_id=stored[0].id,
        approved=True,
        decided_by_user_id=wfm_manager.id,
        note="traffic",
    )
    assert excused.status == "approved"
    assert excused.detail["decision_note"] == "traffic"
    with pytest.raises(Conflict):
        await adherence.decide_exception(
            db,
            tenant_id=plat_scope.tenant.id,
            exception_id=stored[0].id,
            approved=False,
            decided_by_user_id=wfm_manager.id,
        )


async def test_exceptions_are_listed_by_status_and_tenant(
    db, plat_scope: PlatScope, plat_scope_b: PlatScope, wfm_agent: User, wfm_manager: User
):
    shift = await _stated_shift(db, plat_scope, wfm_agent, minutes=400)
    report = adherence.build_report([shift], [], start=DAY, end=DAY + timedelta(hours=8))
    stored = await adherence.persist_exceptions(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        shift=shift,
        detected=report.exceptions,
        day=DAY,
    )
    open_rows = await adherence.list_exceptions(db, tenant_id=plat_scope.tenant.id)
    assert [row.id for row in open_rows] == [stored[0].id]
    await adherence.decide_exception(
        db,
        tenant_id=plat_scope.tenant.id,
        exception_id=stored[0].id,
        approved=False,
        decided_by_user_id=wfm_manager.id,
    )
    assert await adherence.list_exceptions(db, tenant_id=plat_scope.tenant.id) == []
    assert len(await adherence.list_exceptions(db, tenant_id=plat_scope.tenant.id, statuses=("denied",))) == 1
    assert await adherence.list_exceptions(db, tenant_id=plat_scope_b.tenant.id) == []


async def test_an_exception_from_another_tenant_cannot_be_decided(
    db, plat_scope: PlatScope, plat_scope_b: PlatScope, wfm_agent: User, wfm_manager: User
):
    shift = await _stated_shift(db, plat_scope, wfm_agent, minutes=400)
    report = adherence.build_report([shift], [], start=DAY, end=DAY + timedelta(hours=8))
    stored = await adherence.persist_exceptions(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        shift=shift,
        detected=report.exceptions,
        day=DAY,
    )
    with pytest.raises(NotFound):
        await adherence.decide_exception(
            db,
            tenant_id=plat_scope_b.tenant.id,
            exception_id=stored[0].id,
            approved=True,
            decided_by_user_id=wfm_manager.id,
        )


async def test_the_shifts_and_states_readers_are_scoped(
    db, plat_scope: PlatScope, wfm_agent: User
):
    shift = await _stated_shift(db, plat_scope, wfm_agent)
    shifts = await adherence.shifts_between(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        start=DAY,
        end=DAY + timedelta(days=1),
    )
    states = await adherence.states_between(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        start=DAY,
        end=DAY + timedelta(days=1),
    )
    assert [row.id for row in shifts] == [shift.id]
    assert len(states) == 1
