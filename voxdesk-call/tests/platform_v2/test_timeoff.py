"""Time off: hours, balances, approvals, and the coverage answer approvers see.

Employment law meets payroll here, so the tests are about arithmetic that has to be
right the first time:

* hours come from the window clipped to the working day, never from a number the
  requester typed;
* approving is the moment the business commits, so the balance moves on approval
  and on cancellation, and on nothing else;
* an approval that would overdraw a vacation balance is refused — unless the caller
  explicitly grants the exception, in which case the hours are still booked.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserRole
from app.platform_v2.errors import Conflict, NotFound, ValidationFailed
from app.platform_v2.wfm import timeoff
from tests.conftest import make_user
from tests.platform_v2.conftest import PlatScope, seed_shift, seed_templates

DHAKA = "Asia/Dhaka"
MONDAY = date(2026, 11, 2)


def local(day: date, hour: int, minute: int = 0, zone: str = "UTC") -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.combine(day, time(0, 0), tzinfo=ZoneInfo(zone)) + timedelta(
        hours=hour, minutes=minute
    )


async def agent(db: AsyncSession, scope: PlatScope, tag: str = "a"):
    return await make_user(
        db, scope.tenant, UserRole.AGENT, email=f"leave-{tag}-{uuid.uuid4().hex[:6]}@example.com"
    )


async def manager(db: AsyncSession, scope: PlatScope):
    return await make_user(
        db, scope.tenant, UserRole.MANAGER, email=f"leave-mgr-{uuid.uuid4().hex[:6]}@example.com"
    )


# --- the hours arithmetic ------------------------------------------------------


def test_a_full_working_day_costs_the_working_day():
    hours = timeoff.working_minutes(
        local(MONDAY, 9), local(MONDAY, 17), zone_name="UTC", workday_start_minute=540, workday_minutes=480
    )
    assert hours == pytest.approx(8.0)


def test_an_afternoon_request_costs_the_afternoon():
    hours = timeoff.working_minutes(
        local(MONDAY, 13), local(MONDAY, 17), zone_name="UTC", workday_start_minute=540, workday_minutes=480
    )
    assert hours == pytest.approx(4.0)


def test_time_before_the_working_day_is_clipped_not_charged():
    """Asking from midnight must not bill the night."""
    hours = timeoff.working_minutes(
        local(MONDAY, 0), local(MONDAY, 10), zone_name="UTC", workday_start_minute=540, workday_minutes=480
    )
    assert hours == pytest.approx(1.0), "only 09:00-10:00 falls inside the working day"


def test_time_after_the_working_day_is_clipped_too():
    hours = timeoff.working_minutes(
        local(MONDAY, 16), local(MONDAY, 23), zone_name="UTC", workday_start_minute=540, workday_minutes=480
    )
    assert hours == pytest.approx(1.0)


def test_a_window_entirely_outside_working_hours_costs_nothing():
    assert (
        timeoff.working_minutes(
            local(MONDAY, 19),
            local(MONDAY, 22),
            zone_name="UTC",
            workday_start_minute=540,
            workday_minutes=480,
        )
        == 0.0
    )


def test_a_multi_day_window_sums_each_day():
    hours = timeoff.working_minutes(
        local(MONDAY, 9),
        local(MONDAY + timedelta(days=2), 17),
        zone_name="UTC",
        workday_start_minute=540,
        workday_minutes=480,
    )
    assert hours == pytest.approx(24.0)


def test_a_weekend_costs_two_days_not_seven():
    """There is no weekday calendar here: two weekend days cost two working days.

    The alternative — seven days off charging five — comes from the rota, which
    ``request_hours`` prefers when the person actually has shifts in the window.
    """
    friday = MONDAY + timedelta(days=4)
    monday = MONDAY + timedelta(days=7)
    hours = timeoff.working_minutes(
        local(friday, 17),
        local(monday, 9),
        zone_name="UTC",
        workday_start_minute=540,
        workday_minutes=480,
    )
    assert hours == pytest.approx(16.0)


def test_the_working_day_is_evaluated_in_the_tenant_zone():
    """09:00 Dhaka is 03:00 UTC; a UTC-clipped window would bill the wrong hours."""
    hours = timeoff.working_minutes(
        local(MONDAY, 0, zone=DHAKA),
        local(MONDAY, 23, zone=DHAKA),
        zone_name=DHAKA,
        workday_start_minute=540,
        workday_minutes=480,
    )
    assert hours == pytest.approx(8.0)


def test_an_inverted_window_is_refused():
    with pytest.raises(ValidationFailed):
        timeoff.working_minutes(local(MONDAY, 17), local(MONDAY, 9), zone_name="UTC")


@pytest.mark.parametrize(
    "start_minute,workday",
    [(24 * 60, 480), (-1, 480), (540, 0), (540, 24 * 60 + 1)],
    ids=["start-at-day-end", "negative-start", "zero-duration", "duration-over-day"],
)
def test_an_impossible_working_day_is_refused(start_minute: int, workday: int):
    with pytest.raises(ValidationFailed):
        timeoff.working_minutes(
            local(MONDAY, 9),
            local(MONDAY, 17),
            zone_name="UTC",
            workday_start_minute=start_minute,
            workday_minutes=workday,
        )


def test_span_days_counts_calendar_days_touched():
    assert timeoff.span_days(local(MONDAY, 9), local(MONDAY, 17), "UTC") == 1
    assert timeoff.span_days(local(MONDAY, 9), local(MONDAY + timedelta(days=2), 17), "UTC") == 3


def test_span_days_uses_the_local_calendar():
    """A window inside one UTC day can still cross local midnight."""
    start = local(MONDAY, 17, 30)
    end = local(MONDAY, 18, 30)
    assert timeoff.span_days(start, end, "UTC") == 1
    assert timeoff.span_days(start, end, DHAKA) == 2, "23:30-00:30 in Dhaka"


# --- accrual and balances ------------------------------------------------------


async def test_accrual_creates_the_balance_row(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "accrue")
    balance = await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=20.0
    )
    assert float(balance.accrued_hours) == pytest.approx(20.0)
    assert float(balance.balance_hours) == pytest.approx(20.0)
    assert float(balance.used_hours) == 0.0


async def test_accrual_accumulates(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "accrue2")
    await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=20.0
    )
    second = await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=5.5
    )
    assert float(second.balance_hours) == pytest.approx(25.5)
    assert float(second.accrued_hours) == pytest.approx(25.5)


async def test_a_negative_accrual_is_refused(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "accrue3")
    with pytest.raises(ValidationFailed):
        await timeoff.accrue(
            db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=-1.0
        )


async def test_balances_are_per_kind(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "kinds")
    await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=10.0
    )
    sick = await timeoff.balance_row(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="sick"
    )
    assert sick is not None and float(sick.balance_hours) == 0.0


async def test_the_balance_summary_is_arithmetic_not_a_stored_total(
    db: AsyncSession, plat_scope: PlatScope
):
    person = await agent(db, plat_scope, "summary")
    balance = await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=12.0
    )
    row = await timeoff.create_request(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=person.id,
        kind="vacation",
        starts_at=local(MONDAY, 9),
        ends_at=local(MONDAY, 17),
        zone_name="UTC",
    )
    await timeoff.decide(
        db,
        tenant_id=plat_scope.tenant.id,
        request_id=row.id,
        approved=True,
        decided_by_user_id=person.id,
    )
    await db.refresh(balance)
    summary = timeoff.summarise_balance(balance)
    assert summary["accrued_hours"] == pytest.approx(12.0)
    assert summary["used_hours"] == pytest.approx(8.0)
    assert summary["balance_hours"] == pytest.approx(4.0)


# --- requests ------------------------------------------------------------------


async def test_a_request_prices_itself_from_the_working_day(
    db: AsyncSession, plat_scope: PlatScope
):
    person = await agent(db, plat_scope, "price")
    row = await timeoff.create_request(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=person.id,
        kind="vacation",
        starts_at=local(MONDAY, 9),
        ends_at=local(MONDAY, 17),
        zone_name="UTC",
    )
    assert float(row.hours) == pytest.approx(8.0)
    assert row.status == "requested"
    assert row.coverage["hours_source"] == "working_day"
    assert row.coverage["days"] == 1


async def test_a_request_with_no_rota_uses_the_configured_working_day(
    db: AsyncSession, plat_scope: PlatScope
):
    person = await agent(db, plat_scope, "norota")
    row = await timeoff.create_request(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=person.id,
        kind="vacation",
        starts_at=local(MONDAY, 0),
        ends_at=local(MONDAY, 23, 59),
        zone_name="UTC",
    )
    assert float(row.hours) == pytest.approx(8.0), "a full day off costs one working day"


async def test_a_request_prices_itself_from_the_rota_when_one_exists(
    db: AsyncSession, plat_scope: PlatScope
):
    person = await agent(db, plat_scope, "rota")
    await seed_shift(db, plat_scope, person, starts_at=local(MONDAY, 9), hours=8)
    row = await timeoff.create_request(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=person.id,
        kind="vacation",
        starts_at=local(MONDAY, 0),
        ends_at=local(MONDAY, 23, 59),
        zone_name="UTC",
    )
    assert row.coverage["hours_source"] == "scheduled_shifts"
    # seed_shift books a 45-minute break, and a break is not paid leave.
    assert float(row.hours) == pytest.approx(7.25)


async def test_an_unknown_kind_is_refused(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "kind")
    with pytest.raises(ValidationFailed):
        await timeoff.create_request(
            db,
            tenant_id=plat_scope.tenant.id,
            user_id=person.id,
            kind="duvet-day",
            starts_at=local(MONDAY, 9),
            ends_at=local(MONDAY, 17),
            zone_name="UTC",
        )


async def test_create_request_refuses_an_inverted_window(
    db: AsyncSession, plat_scope: PlatScope
):
    person = await agent(db, plat_scope, "invert")
    with pytest.raises(ValidationFailed):
        await timeoff.create_request(
            db,
            tenant_id=plat_scope.tenant.id,
            user_id=person.id,
            kind="vacation",
            starts_at=local(MONDAY, 17),
            ends_at=local(MONDAY, 9),
            zone_name="UTC",
        )


async def test_an_overlapping_request_is_refused_with_the_clashing_id(
    db: AsyncSession, plat_scope: PlatScope
):
    person = await agent(db, plat_scope, "overlap")
    first = await timeoff.create_request(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=person.id,
        kind="vacation",
        starts_at=local(MONDAY, 9),
        ends_at=local(MONDAY, 17),
        zone_name="UTC",
    )
    with pytest.raises(Conflict) as caught:
        await timeoff.create_request(
            db,
            tenant_id=plat_scope.tenant.id,
            user_id=person.id,
            kind="sick",
            starts_at=local(MONDAY, 12),
            ends_at=local(MONDAY, 18),
            zone_name="UTC",
        )
    assert caught.value.detail["conflicting_request_id"] == str(first.id)


async def test_back_to_back_requests_do_not_overlap(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "adjacent")
    await timeoff.create_request(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=person.id,
        kind="vacation",
        starts_at=local(MONDAY, 9),
        ends_at=local(MONDAY, 13),
        zone_name="UTC",
    )
    second = await timeoff.create_request(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=person.id,
        kind="vacation",
        starts_at=local(MONDAY, 13),
        ends_at=local(MONDAY, 17),
        zone_name="UTC",
    )
    assert float(second.hours) == pytest.approx(4.0)


async def test_a_cancelled_request_no_longer_blocks_the_window(
    db: AsyncSession, plat_scope: PlatScope
):
    person = await agent(db, plat_scope, "cancelfree")
    first = await timeoff.create_request(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=person.id,
        kind="vacation",
        starts_at=local(MONDAY, 9),
        ends_at=local(MONDAY, 17),
        zone_name="UTC",
    )
    await timeoff.cancel(
        db, tenant_id=plat_scope.tenant.id, request_id=first.id, user_id=person.id
    )
    again = await timeoff.create_request(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=person.id,
        kind="vacation",
        starts_at=local(MONDAY, 9),
        ends_at=local(MONDAY, 17),
        zone_name="UTC",
    )
    assert again.id != first.id


async def test_an_explicit_hour_override_is_kept_and_labelled(
    db: AsyncSession, plat_scope: PlatScope
):
    person = await agent(db, plat_scope, "override")
    row = await timeoff.create_request(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=person.id,
        kind="unpaid",
        starts_at=local(MONDAY, 9),
        ends_at=local(MONDAY, 17),
        zone_name="UTC",
        hours=3.5,
    )
    assert float(row.hours) == pytest.approx(3.5)
    assert row.coverage["hours_source"] == "provided"


async def test_an_absurd_hour_override_is_refused(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "absurd")
    with pytest.raises(ValidationFailed):
        await timeoff.create_request(
            db,
            tenant_id=plat_scope.tenant.id,
            user_id=person.id,
            kind="unpaid",
            starts_at=local(MONDAY, 9),
            ends_at=local(MONDAY, 17),
            zone_name="UTC",
            hours=timeoff.MAX_REQUEST_HOURS + 1,
        )


async def test_the_coverage_answer_names_the_people_already_off(
    db: AsyncSession, plat_scope: PlatScope
):
    first = await agent(db, plat_scope, "cov1")
    second = await agent(db, plat_scope, "cov2")
    await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=first.id, kind="vacation", hours=40
    )
    approved = await timeoff.create_request(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=first.id,
        kind="vacation",
        starts_at=local(MONDAY, 9),
        ends_at=local(MONDAY, 17),
        zone_name="UTC",
        environment_id=plat_scope.environment.id,
    )
    await timeoff.decide(
        db,
        tenant_id=plat_scope.tenant.id,
        request_id=approved.id,
        approved=True,
        decided_by_user_id=first.id,
    )
    await seed_shift(db, plat_scope, second, starts_at=local(MONDAY, 9), hours=8)
    row = await timeoff.create_request(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=second.id,
        kind="vacation",
        starts_at=local(MONDAY, 9),
        ends_at=local(MONDAY, 17),
        zone_name="UTC",
        environment_id=plat_scope.environment.id,
    )
    coverage = row.coverage
    assert coverage["days"][MONDAY.isoformat()]["on_leave"] == 1
    assert coverage["days"][MONDAY.isoformat()]["scheduled"] == 1
    assert coverage["total_on_leave"] == 1


async def test_the_coverage_answer_flags_a_vacation_that_would_overdraw(
    db: AsyncSession, plat_scope: PlatScope
):
    person = await agent(db, plat_scope, "overdraw")
    await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=2.0
    )
    row = await timeoff.create_request(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=person.id,
        kind="vacation",
        starts_at=local(MONDAY, 9),
        ends_at=local(MONDAY, 17),
        zone_name="UTC",
    )
    assert row.coverage["balance_hours"] == pytest.approx(2.0)
    assert row.coverage["would_go_negative"] is True


async def test_sick_leave_is_not_flagged_as_overdrawing(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "sickflag")
    row = await timeoff.create_request(
        db,
        tenant_id=plat_scope.tenant.id,
        user_id=person.id,
        kind="sick",
        starts_at=local(MONDAY, 9),
        ends_at=local(MONDAY, 17),
        zone_name="UTC",
    )
    assert row.coverage["would_go_negative"] is False


# --- decisions -----------------------------------------------------------------


async def _request(db: AsyncSession, scope: PlatScope, person, *, kind="vacation", hours=None):
    return await timeoff.create_request(
        db,
        tenant_id=scope.tenant.id,
        user_id=person.id,
        kind=kind,
        starts_at=local(MONDAY, 9),
        ends_at=local(MONDAY, 17),
        zone_name="UTC",
        hours=hours,
    )


async def test_approval_books_the_hours(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "approve")
    boss = await manager(db, plat_scope)
    balance = await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=20.0
    )
    row = await _request(db, plat_scope, person)
    decided = await timeoff.decide(
        db,
        tenant_id=plat_scope.tenant.id,
        request_id=row.id,
        approved=True,
        decided_by_user_id=boss.id,
        note="enjoy",
    )
    await db.refresh(balance)
    assert decided.status == "approved"
    assert decided.decided_by_user_id == boss.id
    assert float(balance.used_hours) == pytest.approx(8.0)
    assert float(balance.balance_hours) == pytest.approx(12.0)
    assert decided.coverage["decision_note"] == "enjoy"


async def test_approval_beyond_the_balance_is_refused(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "overbudget")
    boss = await manager(db, plat_scope)
    balance = await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=2.0
    )
    row = await _request(db, plat_scope, person)
    with pytest.raises(Conflict) as caught:
        await timeoff.decide(
            db,
            tenant_id=plat_scope.tenant.id,
            request_id=row.id,
            approved=True,
            decided_by_user_id=boss.id,
        )
    assert caught.value.detail["requested_hours"] == pytest.approx(8.0)
    await db.refresh(balance)
    assert float(balance.used_hours) == 0.0, "a refused approval must not move the balance"
    assert row.status == "requested"


async def test_an_explicit_exception_still_books_the_hours(
    db: AsyncSession, plat_scope: PlatScope
):
    person = await agent(db, plat_scope, "exception")
    boss = await manager(db, plat_scope)
    balance = await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=2.0
    )
    row = await _request(db, plat_scope, person)
    await timeoff.decide(
        db,
        tenant_id=plat_scope.tenant.id,
        request_id=row.id,
        approved=True,
        decided_by_user_id=boss.id,
        enforce_balance=False,
    )
    await db.refresh(balance)
    assert float(balance.used_hours) == pytest.approx(8.0), "granted and paid for"
    assert float(balance.balance_hours) == pytest.approx(-6.0)


async def test_sick_leave_may_go_negative(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "sick")
    boss = await manager(db, plat_scope)
    balance = await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="sick", hours=1.0
    )
    row = await _request(db, plat_scope, person, kind="sick")
    await timeoff.decide(
        db,
        tenant_id=plat_scope.tenant.id,
        request_id=row.id,
        approved=True,
        decided_by_user_id=boss.id,
    )
    await db.refresh(balance)
    assert float(balance.balance_hours) == pytest.approx(-7.0)


async def test_a_denial_leaves_the_balance_alone(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "denied")
    boss = await manager(db, plat_scope)
    balance = await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=20.0
    )
    row = await _request(db, plat_scope, person)
    decided = await timeoff.decide(
        db,
        tenant_id=plat_scope.tenant.id,
        request_id=row.id,
        approved=False,
        decided_by_user_id=boss.id,
        note="peak week",
    )
    await db.refresh(balance)
    assert decided.status == "denied"
    assert float(balance.balance_hours) == pytest.approx(20.0)
    assert decided.coverage["decision_note"] == "peak week"


async def test_a_request_cannot_be_decided_twice(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "twice")
    boss = await manager(db, plat_scope)
    balance = await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=20.0
    )
    row = await _request(db, plat_scope, person)
    await timeoff.decide(
        db,
        tenant_id=plat_scope.tenant.id,
        request_id=row.id,
        approved=True,
        decided_by_user_id=boss.id,
    )
    with pytest.raises(Conflict):
        await timeoff.decide(
            db,
            tenant_id=plat_scope.tenant.id,
            request_id=row.id,
            approved=False,
            decided_by_user_id=boss.id,
        )
    await db.refresh(balance)
    assert float(balance.used_hours) == pytest.approx(8.0), "moved exactly once"


async def test_a_request_from_another_tenant_is_not_found(
    db: AsyncSession, plat_scope: PlatScope, tenant_b
):
    person = await agent(db, plat_scope, "scoped")
    row = await _request(db, plat_scope, person)
    with pytest.raises(NotFound):
        await timeoff.decide(
            db,
            tenant_id=tenant_b.id,
            request_id=row.id,
            approved=True,
            decided_by_user_id=person.id,
        )


async def test_only_the_owner_can_cancel(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "owner")
    other = await agent(db, plat_scope, "stranger")
    row = await _request(db, plat_scope, person)
    with pytest.raises(NotFound):
        await timeoff.cancel(
            db, tenant_id=plat_scope.tenant.id, request_id=row.id, user_id=other.id
        )


async def test_cancelling_an_approved_request_returns_the_hours(
    db: AsyncSession, plat_scope: PlatScope
):
    person = await agent(db, plat_scope, "cancel")
    boss = await manager(db, plat_scope)
    balance = await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=20.0
    )
    row = await _request(db, plat_scope, person)
    await timeoff.decide(
        db,
        tenant_id=plat_scope.tenant.id,
        request_id=row.id,
        approved=True,
        decided_by_user_id=boss.id,
    )
    cancelled = await timeoff.cancel(
        db, tenant_id=plat_scope.tenant.id, request_id=row.id, user_id=person.id
    )
    await db.refresh(balance)
    assert cancelled.status == "cancelled"
    assert float(balance.balance_hours) == pytest.approx(20.0)
    assert float(balance.used_hours) == pytest.approx(0.0)


async def test_cancelling_a_pending_request_moves_no_hours(
    db: AsyncSession, plat_scope: PlatScope
):
    person = await agent(db, plat_scope, "cancelpending")
    balance = await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=20.0
    )
    row = await _request(db, plat_scope, person)
    await timeoff.cancel(db, tenant_id=plat_scope.tenant.id, request_id=row.id, user_id=person.id)
    await db.refresh(balance)
    assert float(balance.balance_hours) == pytest.approx(20.0)


# --- listing and the rota's view -----------------------------------------------


async def test_listing_filters_by_status(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "list")
    await _request(db, plat_scope, person)
    assert len(
        await timeoff.list_requests(
            db, tenant_id=plat_scope.tenant.id, statuses=("requested",)
        )
    ) == 1
    assert (
        await timeoff.list_requests(db, tenant_id=plat_scope.tenant.id, statuses=("approved",))
        == []
    )


async def test_listing_filters_by_person(db: AsyncSession, plat_scope: PlatScope):
    first = await agent(db, plat_scope, "list1")
    second = await agent(db, plat_scope, "list2")
    await _request(db, plat_scope, first)
    await _request(db, plat_scope, second)
    mine = await timeoff.list_requests(
        db, tenant_id=plat_scope.tenant.id, user_id=first.id
    )
    assert [row.user_id for row in mine] == [first.id]


async def test_only_approved_leave_blocks_the_rota(db: AsyncSession, plat_scope: PlatScope):
    person = await agent(db, plat_scope, "blocked")
    boss = await manager(db, plat_scope)
    await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=20.0
    )
    pending = await _request(db, plat_scope, person)
    window_start = local(MONDAY, 0)
    window_end = local(MONDAY + timedelta(days=7), 0)
    assert (
        await timeoff.blocked_windows_for_week(
            db, tenant_id=plat_scope.tenant.id, week_start=window_start, week_end=window_end
        )
        == []
    )
    await timeoff.decide(
        db,
        tenant_id=plat_scope.tenant.id,
        request_id=pending.id,
        approved=True,
        decided_by_user_id=boss.id,
    )
    blocked = await timeoff.blocked_windows_for_week(
        db, tenant_id=plat_scope.tenant.id, week_start=window_start, week_end=window_end
    )
    assert len(blocked) == 1
    assert blocked[0][0] == person.id


async def test_a_window_outside_the_week_is_not_returned(
    db: AsyncSession, plat_scope: PlatScope
):
    person = await agent(db, plat_scope, "outside")
    boss = await manager(db, plat_scope)
    await timeoff.accrue(
        db, tenant_id=plat_scope.tenant.id, user_id=person.id, kind="vacation", hours=20.0
    )
    row = await _request(db, plat_scope, person)
    await timeoff.decide(
        db,
        tenant_id=plat_scope.tenant.id,
        request_id=row.id,
        approved=True,
        decided_by_user_id=boss.id,
    )
    later = local(MONDAY + timedelta(days=30), 0)
    assert (
        await timeoff.blocked_windows_for_week(
            db,
            tenant_id=plat_scope.tenant.id,
            week_start=later,
            week_end=later + timedelta(days=7),
        )
        == []
    )


async def test_the_working_day_falls_back_to_the_default(db: AsyncSession, plat_scope: PlatScope):
    assert await timeoff.workday_minutes_from_templates(db, tenant_id=plat_scope.tenant.id) == (
        timeoff.DEFAULT_WORKDAY_MINUTES
    )


async def test_the_working_day_follows_the_shift_templates(
    db: AsyncSession, plat_scope: PlatScope
):
    await seed_templates(db, plat_scope, count=3)
    minutes = await timeoff.workday_minutes_from_templates(db, tenant_id=plat_scope.tenant.id)
    assert minutes == 480, "the median template length, not the default"
