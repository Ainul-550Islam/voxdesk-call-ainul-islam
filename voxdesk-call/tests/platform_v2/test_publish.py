"""Schedules, versions, publishing, diffing and the payroll export.

A published rota is a promise, so the invariants here are the ones a dispute
lands on: versions are immutable, publishing moves a pointer rather than
rewriting history, an unmeasured rota cannot be published, and the change set is
per person so only the people whose week moved are notified.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserRole
from app.db.platform_v2_models import WfmShift
from app.platform_v2.errors import Conflict, NotFound, ValidationFailed
from app.platform_v2.wfm import publish
from app.platform_v2.wfm.scheduler import (
    EmployeeSpec,
    LaborRules,
    ScheduleRequest,
    ShiftSpec,
    SolveResult,
    solve,
    week_days,
)
from tests.conftest import make_user
from tests.platform_v2.conftest import PlatScope

MONDAY = date(2026, 11, 2)
ZONE = "UTC"


def employees(count: int) -> tuple[EmployeeSpec, ...]:
    return tuple(EmployeeSpec(user_id=uuid.uuid4()) for _ in range(count))


def templates(*shapes: tuple[int, int]) -> tuple[ShiftSpec, ...]:
    return tuple(
        ShiftSpec(
            template_id=uuid.uuid4(),
            name=f"shift-{index}",
            start_minute=start,
            length_minutes=length,
            unpaid_break_minutes=30,
        )
        for index, (start, length) in enumerate(shapes)
    )


def curve(day_need: int = 1, *, start_hour: int = 9, end_hour: int = 17) -> tuple[int, ...]:
    """Hourly demand for a whole week, ``day_need`` agents between two hours."""
    slots: list[int] = []
    for _day in range(7):
        for hour in range(24):
            slots.append(day_need if start_hour <= hour < end_hour else 0)
    return tuple(slots)


def solve_week(
    *,
    staff: int = 5,
    day_need: int = 1,
    shapes: tuple[tuple[int, int], ...] = ((540, 480),),
) -> SolveResult:
    request = ScheduleRequest(
        week_start=MONDAY,
        timezone_name=ZONE,
        interval_minutes=60,
        employees=employees(staff),
        templates=templates(*shapes),
        rules=LaborRules(),
        requirement=curve(day_need),
        days=week_days(MONDAY),
        allow_shortfall=True,
    )
    return solve(request)


async def make_schedule(
    db: AsyncSession, scope: PlatScope, *, name: str = "Week", week: date = MONDAY
):
    manager = await make_user(
        db, scope.tenant, UserRole.MANAGER, email=f"pub-{uuid.uuid4().hex[:8]}@example.com"
    )
    row = await publish.create_schedule(
        db,
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        name=name,
        week_start=week,
        timezone_name=ZONE,
        created_by_user_id=manager.id,
    )
    return row, manager


async def record(db: AsyncSession, schedule, manager, **kwargs):
    result = solve_week(**kwargs)
    return await publish.record_version(
        db, schedule=schedule, result=result, created_by_user_id=manager.id
    )


# --- the week container --------------------------------------------------------


async def test_a_schedule_must_start_on_a_monday(db: AsyncSession, plat_scope: PlatScope):
    manager = await make_user(
        db, plat_scope.tenant, UserRole.MANAGER, email="pub-mon@example.com"
    )
    with pytest.raises(ValidationFailed):
        await publish.create_schedule(
            db,
            tenant_id=plat_scope.tenant.id,
            environment_id=plat_scope.environment.id,
            name="Week",
            week_start=MONDAY + timedelta(days=2),
            timezone_name=ZONE,
            created_by_user_id=manager.id,
        )


async def test_one_schedule_per_week_environment(db: AsyncSession, plat_scope: PlatScope):
    first, manager = await make_schedule(db, plat_scope)
    assert first.status == "draft"
    assert first.week_start == MONDAY
    with pytest.raises(Conflict) as caught:
        await publish.create_schedule(
            db,
            tenant_id=plat_scope.tenant.id,
            environment_id=plat_scope.environment.id,
            name="Another name",
            week_start=MONDAY,
            timezone_name=ZONE,
            created_by_user_id=manager.id,
        )
    assert caught.value.detail["schedule_id"] == str(first.id)


async def test_an_archived_week_can_be_recreated(db: AsyncSession, plat_scope: PlatScope):
    first, manager = await make_schedule(db, plat_scope)
    await publish.archive_schedule(db, schedule=first)
    second = await publish.create_schedule(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        name="Replacement",
        week_start=MONDAY,
        timezone_name=ZONE,
        created_by_user_id=manager.id,
    )
    assert second.id != first.id


async def test_archiving_twice_is_a_conflict(db: AsyncSession, plat_scope: PlatScope):
    schedule, _manager = await make_schedule(db, plat_scope)
    await publish.archive_schedule(db, schedule=schedule)
    with pytest.raises(Conflict):
        await publish.archive_schedule(db, schedule=schedule)


# --- versions ------------------------------------------------------------------


async def test_an_unsolved_rota_cannot_be_recorded(db: AsyncSession, plat_scope: PlatScope):
    schedule, manager = await make_schedule(db, plat_scope)
    infeasible = SolveResult(
        status="INFEASIBLE",
        assignments=[],
        hours_by_user={},
        coverage=[],
        requirement=[],
        shortfall=0,
        overcoverage=0,
        objective=0.0,
        wall_time_seconds=0.0,
    )
    with pytest.raises(ValidationFailed):
        await publish.record_version(
            db, schedule=schedule, result=infeasible, created_by_user_id=manager.id
        )


async def test_recording_a_draft_materialises_its_shifts(db: AsyncSession, plat_scope: PlatScope):
    schedule, manager = await make_schedule(db, plat_scope)
    version = await record(db, schedule, manager)
    assert version.version_number == 1
    assert version.status == "draft"
    assert len(version.assignments) == 7, "one shift a day for a week of nine-to-five demand"
    shifts = await publish.shifts_for_version(
        db, tenant_id=plat_scope.tenant.id, version_id=version.id
    )
    assert len(shifts) == len(version.assignments)
    assert {shift.status for shift in shifts} == {"scheduled"}
    assert all(shift.schedule_version_id == version.id for shift in shifts)


async def test_version_numbers_are_sequential_per_schedule(
    db: AsyncSession, plat_scope: PlatScope
):
    schedule, manager = await make_schedule(db, plat_scope)
    first = await record(db, schedule, manager)
    second = await record(db, schedule, manager, day_need=2)
    assert (first.version_number, second.version_number) == (1, 2)
    assert (
        await publish.next_version_number(
            db, tenant_id=plat_scope.tenant.id, schedule_id=schedule.id
        )
        == 3
    )


async def test_a_version_is_scoped_to_its_tenant(
    db: AsyncSession, plat_scope: PlatScope, tenant_b
):
    schedule, manager = await make_schedule(db, plat_scope)
    version = await record(db, schedule, manager)
    with pytest.raises(NotFound):
        await publish.version_by_id(db, tenant_id=tenant_b.id, version_id=version.id)


# --- publishing ----------------------------------------------------------------


async def test_publishing_promotes_the_version_and_its_shifts(
    db: AsyncSession, plat_scope: PlatScope
):
    schedule, manager = await make_schedule(db, plat_scope)
    version = await record(db, schedule, manager)
    outcome = await publish.publish_version(
        db, schedule=schedule, version=version, published_by_user_id=manager.id
    )
    assert outcome["schedule_id"] == str(schedule.id)
    assert outcome["version_number"] == 1
    assert outcome["diff"]["changed_assignments"] == len(version.assignments)
    assert schedule.current_version_id == version.id
    assert schedule.status == "published"
    assert version.status == "published"
    shifts = await publish.shifts_for_version(
        db, tenant_id=plat_scope.tenant.id, version_id=version.id
    )
    assert {shift.status for shift in shifts} == {"published"}


async def test_publishing_tells_only_the_people_who_moved(
    db: AsyncSession, plat_scope: PlatScope
):
    schedule, manager = await make_schedule(db, plat_scope)
    first = await record(db, schedule, manager)
    outcome = await publish.publish_version(
        db, schedule=schedule, version=first, published_by_user_id=manager.id
    )
    notified = {row["user_id"] for row in outcome["notifications"]}
    assigned = {row["user_id"] for row in first.assignments}
    assert notified == assigned
    assert all(row["changes"] == len(row["items"]) for row in outcome["notifications"])


async def test_republishing_the_same_version_is_a_conflict(
    db: AsyncSession, plat_scope: PlatScope
):
    schedule, manager = await make_schedule(db, plat_scope)
    version = await record(db, schedule, manager)
    await publish.publish_version(
        db, schedule=schedule, version=version, published_by_user_id=manager.id
    )
    with pytest.raises(Conflict):
        await publish.publish_version(
            db, schedule=schedule, version=version, published_by_user_id=manager.id
        )


async def test_a_new_version_supersedes_the_previous_one(
    db: AsyncSession, plat_scope: PlatScope
):
    schedule, manager = await make_schedule(db, plat_scope)
    first = await record(db, schedule, manager)
    await publish.publish_version(
        db, schedule=schedule, version=first, published_by_user_id=manager.id
    )
    second = await record(db, schedule, manager, day_need=2)
    outcome = await publish.publish_version(
        db, schedule=schedule, version=second, published_by_user_id=manager.id
    )
    assert first.status == "superseded"
    assert second.status == "published"
    assert schedule.current_version_id == second.id
    assert outcome["version_number"] == 2


async def test_superseded_shifts_are_dropped_but_swaps_survive(
    db: AsyncSession, plat_scope: PlatScope
):
    schedule, manager = await make_schedule(db, plat_scope)
    first = await record(db, schedule, manager)
    await publish.publish_version(
        db, schedule=schedule, version=first, published_by_user_id=manager.id
    )
    shifts = await publish.shifts_for_version(
        db, tenant_id=plat_scope.tenant.id, version_id=first.id
    )
    kept = shifts[0]
    kept.status = "swapped"
    await db.commit()

    second = await record(db, schedule, manager, day_need=2)
    await publish.publish_version(
        db, schedule=schedule, version=second, published_by_user_id=manager.id
    )
    refreshed = await publish.shifts_for_version(
        db, tenant_id=plat_scope.tenant.id, version_id=first.id
    )
    statuses = {shift.id: shift.status for shift in refreshed}
    assert statuses[kept.id] == "swapped", "a swap is a human decision, not a property of a version"
    assert all(
        status == "dropped" for shift_id, status in statuses.items() if shift_id != kept.id
    )


async def test_a_version_without_a_solver_report_cannot_be_published(
    db: AsyncSession, plat_scope: PlatScope
):
    schedule, manager = await make_schedule(db, plat_scope)
    version = await record(db, schedule, manager)
    version.solver_report = {}
    await db.commit()
    with pytest.raises(Conflict) as caught:
        await publish.publish_version(
            db, schedule=schedule, version=version, published_by_user_id=manager.id
        )
    assert "solver report" in str(caught.value)


async def test_a_coverage_floor_is_enforced(db: AsyncSession, plat_scope: PlatScope):
    schedule, manager = await make_schedule(db, plat_scope)
    version = await record(db, schedule, manager, staff=1, day_need=3)
    coverage = float(version.coverage["coverage_ratio"])
    assert coverage < 1.0, "one person cannot cover three positions"
    with pytest.raises(Conflict) as caught:
        await publish.publish_version(
            db,
            schedule=schedule,
            version=version,
            published_by_user_id=manager.id,
            min_coverage_ratio=1.0,
        )
    assert caught.value.detail["coverage_ratio"] == coverage
    assert caught.value.detail["shortfall_agent_intervals"] > 0
    # With no floor the same version publishes: the operator has seen the number.
    outcome = await publish.publish_version(
        db, schedule=schedule, version=version, published_by_user_id=manager.id
    )
    assert outcome["version_id"] == str(version.id)


async def test_a_version_belonging_to_another_schedule_is_not_found(
    db: AsyncSession, plat_scope: PlatScope
):
    schedule, manager = await make_schedule(db, plat_scope)
    version = await record(db, schedule, manager)
    other, other_manager = await make_schedule(
        db, plat_scope, name="Other week", week=MONDAY + timedelta(days=7)
    )
    with pytest.raises(NotFound):
        await publish.publish_version(
            db, schedule=other, version=version, published_by_user_id=other_manager.id
        )


# --- diffing -------------------------------------------------------------------


def test_a_diff_reports_added_removed_and_unchanged():
    before = [
        {"user_id": "a", "starts_at": "2026-11-02T09:00:00+00:00", "template_id": "t1"},
        {"user_id": "b", "starts_at": "2026-11-02T09:00:00+00:00", "template_id": "t1"},
    ]
    after = [
        {"user_id": "b", "starts_at": "2026-11-02T09:00:00+00:00", "template_id": "t1"},
        {"user_id": "c", "starts_at": "2026-11-02T09:00:00+00:00", "template_id": "t2"},
    ]
    diff = publish.diff_assignments(before, after)
    assert [row["user_id"] for row in diff.added] == ["c"]
    assert [row["user_id"] for row in diff.removed] == ["a"]
    assert diff.unchanged == 1
    assert diff.as_dict()["changed_assignments"] == 2


def test_the_same_person_at_the_same_minute_in_a_different_shape_is_a_change():
    """The key includes the template: a different shift shape is a different shift."""
    before = [{"user_id": "a", "starts_at": "2026-11-02T09:00:00+00:00", "template_id": "t1"}]
    after = [{"user_id": "a", "starts_at": "2026-11-02T09:00:00+00:00", "template_id": "t2"}]
    diff = publish.diff_assignments(before, after)
    assert len(diff.added) == 1 and len(diff.removed) == 1


def test_an_unchanged_rota_produces_no_change_set():
    rows = [{"user_id": "a", "starts_at": "2026-11-02T09:00:00+00:00", "template_id": "t1"}]
    diff = publish.diff_assignments(rows, list(rows))
    assert diff.as_dict()["changed_assignments"] == 0
    assert diff.unchanged == 1


def test_an_assignment_key_copes_with_a_missing_template():
    assert publish.assignment_key({"user_id": "a", "starts_at": "x"}) == ("a", "x", "None")


async def test_diffing_two_versions_uses_the_stored_assignments(
    db: AsyncSession, plat_scope: PlatScope
):
    schedule, manager = await make_schedule(db, plat_scope)
    first = await record(db, schedule, manager)
    second = await record(db, schedule, manager, day_need=2, staff=7)
    diff = await publish.diff_versions(
        db,
        tenant_id=plat_scope.tenant.id,
        before_version_id=first.id,
        after_version_id=second.id,
    )
    assert diff.added, "the second rota assigns more shifts"
    assert diff.as_dict()["changed_assignments"] >= len(diff.added)


# --- the export ----------------------------------------------------------------


def shift_row(**overrides) -> WfmShift:
    payload = {
        "tenant_id": uuid.uuid4(),
        "environment_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "starts_at": datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
        "ends_at": datetime(2026, 11, 2, 17, 0, tzinfo=UTC),
        "break_minutes": 30,
        "status": "published",
        "skill_key": "voice",
    }
    payload.update(overrides)
    return WfmShift(**payload)


def test_the_export_carries_utc_and_local_times():
    row = publish.export_rows([shift_row()], timezone_name="Asia/Dhaka")[0]
    assert row["starts_at_utc"] == "2026-11-02T09:00:00+00:00"
    assert row["starts_at_local"] == "2026-11-02T15:00:00+06:00"
    assert row["ends_at_local"].startswith("2026-11-02T23:00:00")
    assert row["skill_key"] == "voice"


def test_paid_hours_exclude_the_unpaid_break():
    row = publish.export_rows([shift_row()], timezone_name="UTC")[0]
    assert row["paid_hours"] == pytest.approx(7.5)
    assert row["break_minutes"] == 30


def test_a_manual_shift_exports_an_empty_template_id():
    row = publish.export_rows([shift_row(template_id=None)], timezone_name="UTC")[0]
    assert row["template_id"] == ""


def test_the_export_never_reports_negative_paid_hours():
    row = publish.export_rows(
        [shift_row(break_minutes=600)], timezone_name="UTC"
    )[0]
    assert row["paid_hours"] == 0.0


def test_the_export_is_in_the_rota_zone_not_the_server_zone():
    """A rota built in Dhaka must export Dhaka wall-clock times."""
    row = publish.export_rows([shift_row()], timezone_name="Asia/Dhaka")[0]
    local = datetime.fromisoformat(row["starts_at_local"])
    assert (local.hour, local.minute) == (15, 0)
    assert local.utcoffset() == timedelta(hours=6)


def test_an_empty_rota_exports_nothing():
    assert publish.export_rows([], timezone_name="UTC") == []


def test_a_version_summary_is_counts_not_payload():
    version = type(
        "Version",
        (),
        {
            "id": uuid.uuid4(),
            "version_number": 3,
            "status": "published",
            "assignments": [{"user_id": "a"}, {"user_id": "b"}],
            "coverage": {"coverage_ratio": 0.5},
            "solver_report": {
                "status": "FEASIBLE",
                "objective": 12.0,
                "wall_time_seconds": 0.5,
                "coverage_ratio": 0.5,
            },
            "created_at": datetime(2026, 11, 2, tzinfo=UTC),
            "published_at": None,
        },
    )()
    summary = publish.summarise_version(version)
    assert summary["assignments"] == 2
    assert summary["solver"] == {
        "status": "FEASIBLE",
        "objective": 12.0,
        "wall_time_seconds": 0.5,
        "coverage_ratio": 0.5,
    }
    assert summary["published_at"] is None


def test_a_week_is_seven_days_from_the_monday():
    days = week_days(MONDAY)
    assert len(days) == 7
    assert days[0] == MONDAY and days[-1] == MONDAY + timedelta(days=6)
    assert all(day.weekday() == 0 for day in days[:1])


def test_a_solver_result_reports_its_coverage_ratio():
    result = solve_week()
    report = result.as_report()
    assert report["coverage_ratio"] == pytest.approx(1.0)
    assert report["intervals"] == 7 * 24
    assert time(9, 0) is not None
