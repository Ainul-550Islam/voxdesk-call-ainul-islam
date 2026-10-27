"""Swaps, open shifts and bids.

The rule engine is pure, so it is tested exhaustively without a database; the
workflow around it is tested against real rows because the interesting failures
are about state: an offer that is recorded even though it violates a rule, an
acceptance that re-checks because the rota moved, an approval that is the only
thing allowed to move the shift.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import UserRole
from app.db.platform_v2_models import WfmShift
from app.platform_v2.errors import Conflict, NotFound, ValidationFailed
from app.platform_v2.wfm import swaps
from app.platform_v2.wfm.scheduler import LaborRules
from tests.conftest import make_user
from tests.platform_v2.conftest import PlatScope, seed_shift

BASE = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


def window(
    *,
    starts_at: datetime = BASE,
    hours: float = 8,
    break_minutes: int = 0,
    skill: str = "",
    shift_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> swaps.ShiftWindow:
    return swaps.ShiftWindow(
        id=shift_id or uuid.uuid4(),
        user_id=user_id or uuid.uuid4(),
        starts_at=starts_at,
        ends_at=starts_at + timedelta(hours=hours),
        skill_key=skill,
        break_minutes=break_minutes,
    )


def rules(**overrides) -> LaborRules:
    payload = {
        "max_hours_per_week": 48.0,
        "min_rest_hours": 10.0,
        "max_consecutive_days": 6,
        "max_shift_hours": 12.0,
        "min_split_gap_minutes": 0,
        "max_shifts_per_day": 1,
    }
    payload.update(overrides)
    return LaborRules(**payload)


def flagged(check: swaps.RuleCheck) -> list[str]:
    return [item["rule"] for item in check.violations]


async def agent(db: AsyncSession, scope: PlatScope, tag: str = "x"):
    return await make_user(
        db, scope.tenant, UserRole.AGENT, email=f"swap-{tag}-{uuid.uuid4().hex[:6]}@example.com"
    )


async def manager(db: AsyncSession, scope: PlatScope):
    return await make_user(
        db, scope.tenant, UserRole.MANAGER, email=f"swap-mgr-{uuid.uuid4().hex[:6]}@example.com"
    )


# --- the rule engine -----------------------------------------------------------


def test_a_clean_trade_has_no_violations():
    check = swaps.rule_violations(incoming=window(), target_shifts=[], rules=rules())
    assert check.allowed is True
    assert check.violations == []
    assert check.hours_after == pytest.approx(8.0)


def test_the_incoming_shift_is_not_counted_against_itself():
    incoming = window()
    check = swaps.rule_violations(incoming=incoming, target_shifts=[incoming], rules=rules())
    assert check.allowed is True
    assert check.hours_after == pytest.approx(8.0)


def test_an_overlapping_shift_is_a_double_booking():
    incoming = window()
    other = window(
        starts_at=BASE + timedelta(hours=2), shift_id=uuid.uuid4(), user_id=incoming.user_id
    )
    check = swaps.rule_violations(incoming=incoming, target_shifts=[other], rules=rules())
    # A same-day overlap is also two shifts in one day and a zero turnaround; all
    # three are reported so the manager fixes them in one pass.
    assert {"double_booking", "max_shifts_per_day", "min_rest_hours"} == set(flagged(check))
    double = next(item for item in check.violations if item["rule"] == "double_booking")
    assert double["shift_id"] == str(other.id)


def test_the_weekly_hours_ceiling_counts_the_targets_existing_week():
    incoming = window()
    day = BASE
    existing = [
        window(starts_at=day + timedelta(days=offset), hours=8, user_id=incoming.user_id)
        for offset in range(1, 6)
    ]
    check = swaps.rule_violations(
        incoming=incoming, target_shifts=existing, rules=rules(max_hours_per_week=40.0)
    )
    assert "max_hours_per_week" in flagged(check)
    assert check.hours_after == pytest.approx(48.0)


def test_a_shift_longer_than_the_maximum_is_flagged():
    check = swaps.rule_violations(
        incoming=window(hours=13), target_shifts=[], rules=rules(max_shift_hours=12.0)
    )
    assert flagged(check) == ["max_shift_hours"]


def test_paid_hours_exclude_the_unpaid_break():
    assert window(hours=8, break_minutes=45).paid_hours == pytest.approx(7.25)
    check = swaps.rule_violations(
        incoming=window(hours=8, break_minutes=45), target_shifts=[], rules=rules()
    )
    assert check.hours_after == pytest.approx(7.25)


def test_two_shifts_on_one_day_break_the_daily_limit():
    incoming = window()
    other = window(starts_at=BASE + timedelta(hours=12), user_id=incoming.user_id)
    check = swaps.rule_violations(incoming=incoming, target_shifts=[other], rules=rules())
    assert "max_shifts_per_day" in flagged(check)


def test_a_split_shift_too_close_together_is_flagged():
    incoming = window(starts_at=BASE, hours=4)
    other = window(starts_at=BASE + timedelta(hours=5), hours=4, user_id=incoming.user_id)
    check = swaps.rule_violations(
        incoming=incoming, target_shifts=[other], rules=rules(min_split_gap_minutes=120)
    )
    assert "min_split_gap" in flagged(check)


def test_the_turnaround_is_measured_in_both_directions():
    incoming = window(starts_at=BASE, hours=8)
    after = window(starts_at=BASE + timedelta(hours=10), hours=8, user_id=incoming.user_id)
    check = swaps.rule_violations(incoming=incoming, target_shifts=[after], rules=rules())
    assert "min_rest_hours" in flagged(check)
    assert check.rest_hours == pytest.approx(2.0)


def test_a_generous_turnaround_is_allowed():
    incoming = window(starts_at=BASE, hours=8)
    later = window(starts_at=BASE + timedelta(hours=22), hours=8, user_id=incoming.user_id)
    check = swaps.rule_violations(incoming=incoming, target_shifts=[later], rules=rules())
    assert "min_rest_hours" not in flagged(check)
    assert check.rest_hours == pytest.approx(14.0)


def test_a_missing_skill_blocks_the_trade():
    check = swaps.rule_violations(
        incoming=window(skill="billing"),
        target_shifts=[],
        rules=rules(),
        target_skills=frozenset({"support"}),
    )
    assert flagged(check) == ["skill_mismatch"]


def test_a_wildcard_skill_covers_everything():
    check = swaps.rule_violations(
        incoming=window(skill="billing"),
        target_shifts=[],
        rules=rules(),
        target_skills=frozenset({"*"}),
    )
    assert check.allowed is True


def test_an_unspecified_skill_needs_no_skills():
    check = swaps.rule_violations(
        incoming=window(skill=""),
        target_shifts=[],
        rules=rules(),
        target_skills=frozenset(),
    )
    assert check.allowed is True


def test_approved_leave_blocks_the_trade():
    incoming = window()
    check = swaps.rule_violations(
        incoming=incoming,
        target_shifts=[],
        rules=rules(),
        leave=[(BASE + timedelta(hours=1), BASE + timedelta(hours=3))],
    )
    assert flagged(check) == ["approved_leave"]


def test_leave_on_another_day_does_not_block():
    incoming = window()
    check = swaps.rule_violations(
        incoming=incoming,
        target_shifts=[],
        rules=rules(),
        leave=[(BASE + timedelta(days=1), BASE + timedelta(days=2))],
    )
    assert check.allowed is True


def test_too_many_consecutive_days_are_flagged():
    incoming = window(starts_at=BASE + timedelta(days=6))
    existing = [
        window(starts_at=BASE + timedelta(days=offset), user_id=incoming.user_id)
        for offset in range(0, 6)
    ]
    check = swaps.rule_violations(
        incoming=incoming, target_shifts=existing, rules=rules(max_consecutive_days=6)
    )
    assert "max_consecutive_days" in flagged(check)


def test_every_violation_is_reported_not_just_the_first():
    incoming = window(hours=13, skill="billing")
    existing = [
        window(starts_at=BASE + timedelta(days=offset), hours=12, user_id=incoming.user_id)
        for offset in range(5)
    ]
    check = swaps.rule_violations(
        incoming=incoming,
        target_shifts=existing,
        rules=rules(max_shift_hours=8.0),
        target_skills=frozenset({"support"}),
    )
    found = set(flagged(check))
    assert {"max_hours_per_week", "max_shift_hours", "skill_mismatch"} <= found


def test_the_week_bounds_are_iso_weeks():
    start, end = swaps.week_bounds(BASE)
    assert start == datetime(2026, 11, 2, 0, 0, tzinfo=UTC), "2026-11-02 is a Monday"
    assert end - start == timedelta(days=7)
    sunday, _ = swaps.week_bounds(BASE + timedelta(days=6))
    assert sunday == start


def test_bid_score_prefers_the_person_with_fewer_hours():
    light = swaps.bid_score(hours_already=10, hours_incoming=8)
    heavy = swaps.bid_score(hours_already=40, hours_incoming=8)
    assert light < heavy
    assert heavy - light == pytest.approx(30.0)


def test_a_preference_can_only_tip_a_close_race():
    plain = swaps.bid_score(hours_already=20, hours_incoming=8, preference=0)
    tipped = swaps.bid_score(hours_already=20, hours_incoming=8, preference=20)
    assert tipped < plain
    assert plain - tipped == pytest.approx(1.0), "20 preference points are worth one hour"
    assert (
        swaps.bid_score(hours_already=29, hours_incoming=8, preference=20) > plain
    ), "a preference must never beat four hours of imbalance"


# --- offering, accepting, deciding ---------------------------------------------


async def test_an_offer_records_the_rule_check(db: AsyncSession, plat_scope: PlatScope):
    owner = await agent(db, plat_scope, "offer")
    other = await agent(db, plat_scope, "target")
    shift = await seed_shift(db, plat_scope, owner, starts_at=BASE, hours=8)
    swap = await swaps.offer_swap(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        shift_id=shift.id,
        offering_user_id=owner.id,
        target_user_id=other.id,
    )
    assert swap.status == "offered"
    assert swap.rule_check["allowed"] is True
    assert swap.rule_check["hours_after"] == pytest.approx(7.25)


async def test_a_violating_offer_is_recorded_with_the_violations(
    db: AsyncSession, plat_scope: PlatScope
):
    owner = await agent(db, plat_scope, "bad")
    other = await agent(db, plat_scope, "bad-target")
    shift = await seed_shift(db, plat_scope, owner, starts_at=BASE, hours=8)
    await seed_shift(db, plat_scope, other, starts_at=BASE + timedelta(hours=4), hours=8)
    swap = await swaps.offer_swap(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        shift_id=shift.id,
        offering_user_id=owner.id,
        target_user_id=other.id,
    )
    assert swap.status == "offered", "the attempt is information, not an error"
    assert swap.rule_check["allowed"] is False
    assert swap.rule_check["violations"]


async def test_you_cannot_swap_with_yourself(db: AsyncSession, plat_scope: PlatScope):
    owner = await agent(db, plat_scope, "self")
    shift = await seed_shift(db, plat_scope, owner, starts_at=BASE, hours=8)
    with pytest.raises(ValidationFailed):
        await swaps.offer_swap(
            db,
            tenant_id=plat_scope.tenant.id,
            environment_id=plat_scope.environment.id,
            shift_id=shift.id,
            offering_user_id=owner.id,
            target_user_id=owner.id,
        )


async def test_someone_elses_shift_is_not_found(db: AsyncSession, plat_scope: PlatScope):
    owner = await agent(db, plat_scope, "owner")
    stranger = await agent(db, plat_scope, "stranger")
    other = await agent(db, plat_scope, "third")
    shift = await seed_shift(db, plat_scope, owner, starts_at=BASE, hours=8)
    with pytest.raises(NotFound):
        await swaps.offer_swap(
            db,
            tenant_id=plat_scope.tenant.id,
            environment_id=plat_scope.environment.id,
            shift_id=shift.id,
            offering_user_id=stranger.id,
            target_user_id=other.id,
        )


async def test_a_started_shift_cannot_be_offered(db: AsyncSession, plat_scope: PlatScope):
    owner = await agent(db, plat_scope, "started")
    other = await agent(db, plat_scope, "started-target")
    shift = await seed_shift(
        db, plat_scope, owner, starts_at=datetime.now(tz=UTC) - timedelta(hours=2), hours=8
    )
    with pytest.raises(Conflict):
        await swaps.offer_swap(
            db,
            tenant_id=plat_scope.tenant.id,
            environment_id=plat_scope.environment.id,
            shift_id=shift.id,
            offering_user_id=owner.id,
            target_user_id=other.id,
        )


async def _offered(db: AsyncSession, scope: PlatScope, tag: str):
    owner = await agent(db, scope, f"{tag}-owner")
    target = await agent(db, scope, f"{tag}-target")
    shift = await seed_shift(db, scope, owner, starts_at=BASE + timedelta(days=30), hours=8)
    swap = await swaps.offer_swap(
        db,
        tenant_id=scope.tenant.id,
        environment_id=scope.environment.id,
        shift_id=shift.id,
        offering_user_id=owner.id,
        target_user_id=target.id,
    )
    return swap, shift, owner, target


async def test_only_the_target_can_accept(db: AsyncSession, plat_scope: PlatScope):
    swap, shift, owner, _target = await _offered(db, plat_scope, "accept")
    with pytest.raises(NotFound):
        await swaps.accept_swap(
            db, tenant_id=plat_scope.tenant.id, swap_id=swap.id, acting_user_id=owner.id
        )


async def test_accepting_moves_nothing_until_a_manager_approves(
    db: AsyncSession, plat_scope: PlatScope
):
    swap, shift, _owner, target = await _offered(db, plat_scope, "approve")
    accepted = await swaps.accept_swap(
        db, tenant_id=plat_scope.tenant.id, swap_id=swap.id, acting_user_id=target.id
    )
    assert accepted.status == "accepted"
    fresh = await db.get(WfmShift, shift.id)
    assert fresh is not None and fresh.user_id != target.id, "the rota has not moved yet"


async def test_approval_transfers_the_shift(db: AsyncSession, plat_scope: PlatScope):
    swap, shift, _owner, target = await _offered(db, plat_scope, "transfer")
    boss = await manager(db, plat_scope)
    await swaps.accept_swap(
        db, tenant_id=plat_scope.tenant.id, swap_id=swap.id, acting_user_id=target.id
    )
    approved = await swaps.decide_swap(
        db,
        tenant_id=plat_scope.tenant.id,
        swap_id=swap.id,
        approved=True,
        decided_by_user_id=boss.id,
    )
    assert approved.status == "approved"
    fresh = await db.get(WfmShift, shift.id)
    assert fresh is not None
    assert fresh.user_id == target.id
    assert fresh.status == "swapped"


async def test_a_violating_swap_cannot_be_approved(db: AsyncSession, plat_scope: PlatScope):
    owner = await agent(db, plat_scope, "vowner")
    target = await agent(db, plat_scope, "vtarget")
    boss = await manager(db, plat_scope)
    shift = await seed_shift(db, plat_scope, owner, starts_at=BASE + timedelta(days=60), hours=8)
    await seed_shift(db, plat_scope, target, starts_at=BASE + timedelta(days=60), hours=8)
    swap = await swaps.offer_swap(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        shift_id=shift.id,
        offering_user_id=owner.id,
        target_user_id=target.id,
    )
    with pytest.raises(Conflict) as caught:
        await swaps.decide_swap(
            db,
            tenant_id=plat_scope.tenant.id,
            swap_id=swap.id,
            approved=True,
            decided_by_user_id=boss.id,
        )
    assert "rule_check" in caught.value.detail


async def test_a_refusal_records_the_note_and_leaves_the_rota_alone(
    db: AsyncSession, plat_scope: PlatScope
):
    swap, shift, owner, target = await _offered(db, plat_scope, "refuse")
    boss = await manager(db, plat_scope)
    refused = await swaps.decide_swap(
        db,
        tenant_id=plat_scope.tenant.id,
        swap_id=swap.id,
        approved=False,
        decided_by_user_id=boss.id,
        note="peak week",
    )
    assert refused.status == "rejected"
    assert refused.note == "peak week"
    fresh = await db.get(WfmShift, shift.id)
    assert fresh is not None and fresh.user_id == owner.id


async def test_a_swap_cannot_be_decided_twice(db: AsyncSession, plat_scope: PlatScope):
    swap, _shift, _owner, _target = await _offered(db, plat_scope, "twice")
    boss = await manager(db, plat_scope)
    await swaps.decide_swap(
        db,
        tenant_id=plat_scope.tenant.id,
        swap_id=swap.id,
        approved=False,
        decided_by_user_id=boss.id,
    )
    with pytest.raises(Conflict):
        await swaps.decide_swap(
            db,
            tenant_id=plat_scope.tenant.id,
            swap_id=swap.id,
            approved=True,
            decided_by_user_id=boss.id,
        )


async def test_either_party_can_cancel(db: AsyncSession, plat_scope: PlatScope):
    swap, _shift, owner, target = await _offered(db, plat_scope, "cancel")
    by_target = await swaps.cancel_swap(
        db, tenant_id=plat_scope.tenant.id, swap_id=swap.id, acting_user_id=target.id
    )
    assert by_target.status == "cancelled"
    second, _shift2, owner2, _target2 = await _offered(db, plat_scope, "cancel2")
    by_owner = await swaps.cancel_swap(
        db, tenant_id=plat_scope.tenant.id, swap_id=second.id, acting_user_id=owner2.id
    )
    assert by_owner.status == "cancelled"


async def test_an_approved_swap_cannot_be_cancelled(db: AsyncSession, plat_scope: PlatScope):
    swap, _shift, owner, target = await _offered(db, plat_scope, "nocancel")
    boss = await manager(db, plat_scope)
    await swaps.accept_swap(
        db, tenant_id=plat_scope.tenant.id, swap_id=swap.id, acting_user_id=target.id
    )
    await swaps.decide_swap(
        db,
        tenant_id=plat_scope.tenant.id,
        swap_id=swap.id,
        approved=True,
        decided_by_user_id=boss.id,
    )
    with pytest.raises(Conflict):
        await swaps.cancel_swap(
            db, tenant_id=plat_scope.tenant.id, swap_id=swap.id, acting_user_id=owner.id
        )


async def test_a_third_party_cannot_cancel(db: AsyncSession, plat_scope: PlatScope):
    swap, _shift, _owner, _target = await _offered(db, plat_scope, "thirdparty")
    outsider = await agent(db, plat_scope, "outsider")
    with pytest.raises(NotFound):
        await swaps.cancel_swap(
            db, tenant_id=plat_scope.tenant.id, swap_id=swap.id, acting_user_id=outsider.id
        )


async def test_a_swap_from_another_tenant_is_not_found(
    db: AsyncSession, plat_scope: PlatScope, tenant_b
):
    swap, _shift, owner, _target = await _offered(db, plat_scope, "scoped")
    with pytest.raises(NotFound):
        await swaps.cancel_swap(
            db, tenant_id=tenant_b.id, swap_id=swap.id, acting_user_id=owner.id
        )


async def test_listing_is_scoped_to_the_person(db: AsyncSession, plat_scope: PlatScope):
    swap, _shift, owner, target = await _offered(db, plat_scope, "listing")
    for person in (owner, target):
        mine = await swaps.list_swaps(
            db, tenant_id=plat_scope.tenant.id, user_id=person.id
        )
        assert [row.id for row in mine] == [swap.id]
    outsider = await agent(db, plat_scope, "listing-outsider")
    assert (
        await swaps.list_swaps(db, tenant_id=plat_scope.tenant.id, user_id=outsider.id) == []
    )


# --- open shifts and bids ------------------------------------------------------


async def test_a_shift_must_be_open_before_it_can_be_bid_on(
    db: AsyncSession, plat_scope: PlatScope
):
    owner = await agent(db, plat_scope, "closed")
    bidder = await agent(db, plat_scope, "closed-bidder")
    shift = await seed_shift(db, plat_scope, owner, starts_at=BASE + timedelta(days=30), hours=8)
    with pytest.raises(Conflict):
        await swaps.place_bid(
            db,
            tenant_id=plat_scope.tenant.id,
            environment_id=plat_scope.environment.id,
            shift_id=shift.id,
            user_id=bidder.id,
        )


async def test_opening_a_shift_marks_it_open(db: AsyncSession, plat_scope: PlatScope):
    owner = await agent(db, plat_scope, "open")
    shift = await seed_shift(db, plat_scope, owner, starts_at=BASE + timedelta(days=30), hours=8)
    opened = await swaps.open_shift(db, tenant_id=plat_scope.tenant.id, shift_id=shift.id)
    assert opened.status == "open"


async def test_an_ended_shift_cannot_be_opened(db: AsyncSession, plat_scope: PlatScope):
    owner = await agent(db, plat_scope, "ended")
    shift = await seed_shift(
        db, plat_scope, owner, starts_at=datetime.now(tz=UTC) - timedelta(days=2), hours=8
    )
    with pytest.raises(Conflict):
        await swaps.open_shift(db, tenant_id=plat_scope.tenant.id, shift_id=shift.id)


async def test_a_bid_is_scored_by_hours_and_preference(
    db: AsyncSession, plat_scope: PlatScope
):
    owner = await agent(db, plat_scope, "score")
    bidder = await agent(db, plat_scope, "score-bidder")
    shift = await seed_shift(db, plat_scope, owner, starts_at=BASE + timedelta(days=30), hours=8)
    await swaps.open_shift(db, tenant_id=plat_scope.tenant.id, shift_id=shift.id)
    bid = await swaps.place_bid(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        shift_id=shift.id,
        user_id=bidder.id,
        preference=10,
    )
    assert bid.status == "placed"
    assert float(bid.score) == pytest.approx(7.25 - 0.5)


async def test_the_same_person_cannot_bid_twice(db: AsyncSession, plat_scope: PlatScope):
    owner = await agent(db, plat_scope, "twicebid")
    bidder = await agent(db, plat_scope, "twicebid-bidder")
    shift = await seed_shift(db, plat_scope, owner, starts_at=BASE + timedelta(days=30), hours=8)
    await swaps.open_shift(db, tenant_id=plat_scope.tenant.id, shift_id=shift.id)
    await swaps.place_bid(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        shift_id=shift.id,
        user_id=bidder.id,
    )
    with pytest.raises(Conflict):
        await swaps.place_bid(
            db,
            tenant_id=plat_scope.tenant.id,
            environment_id=plat_scope.environment.id,
            shift_id=shift.id,
            user_id=bidder.id,
        )


async def test_the_lowest_score_wins_and_the_rest_are_rejected(
    db: AsyncSession, plat_scope: PlatScope
):
    owner = await agent(db, plat_scope, "award")
    boss = await manager(db, plat_scope)
    light = await agent(db, plat_scope, "award-light")
    heavy = await agent(db, plat_scope, "award-heavy")
    shift = await seed_shift(db, plat_scope, owner, starts_at=BASE + timedelta(days=30), hours=8)
    # The heavy bidder already carries a full week, so the shift would overload them.
    for offset in range(1, 6):
        await seed_shift(
            db, plat_scope, heavy, starts_at=BASE + timedelta(days=30 + offset), hours=8
        )
    await swaps.open_shift(db, tenant_id=plat_scope.tenant.id, shift_id=shift.id)
    heavy_bid = await swaps.place_bid(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        shift_id=shift.id,
        user_id=heavy.id,
    )
    light_bid = await swaps.place_bid(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        shift_id=shift.id,
        user_id=light.id,
    )
    assert float(light_bid.score) < float(heavy_bid.score)
    awarded_shift, winner = await swaps.award_shift(
        db, tenant_id=plat_scope.tenant.id, shift_id=shift.id, decided_by_user_id=boss.id
    )
    assert winner.id == light_bid.id
    assert awarded_shift.user_id == light.id
    assert awarded_shift.status == "scheduled"
    bids = await swaps.list_bids(db, tenant_id=plat_scope.tenant.id, shift_id=shift.id)
    statuses = {row.id: row.status for row in bids}
    assert statuses[light_bid.id] == "awarded"
    assert statuses[heavy_bid.id] == "rejected"


async def test_awarding_without_bids_is_a_conflict(db: AsyncSession, plat_scope: PlatScope):
    owner = await agent(db, plat_scope, "nobids")
    boss = await manager(db, plat_scope)
    shift = await seed_shift(db, plat_scope, owner, starts_at=BASE + timedelta(days=30), hours=8)
    await swaps.open_shift(db, tenant_id=plat_scope.tenant.id, shift_id=shift.id)
    with pytest.raises(Conflict):
        await swaps.award_shift(
            db, tenant_id=plat_scope.tenant.id, shift_id=shift.id, decided_by_user_id=boss.id
        )


async def test_awarding_twice_is_a_conflict(db: AsyncSession, plat_scope: PlatScope):
    owner = await agent(db, plat_scope, "awtwice")
    bidder = await agent(db, plat_scope, "awtwice-bidder")
    boss = await manager(db, plat_scope)
    shift = await seed_shift(db, plat_scope, owner, starts_at=BASE + timedelta(days=30), hours=8)
    await swaps.open_shift(db, tenant_id=plat_scope.tenant.id, shift_id=shift.id)
    await swaps.place_bid(
        db,
        tenant_id=plat_scope.tenant.id,
        environment_id=plat_scope.environment.id,
        shift_id=shift.id,
        user_id=bidder.id,
    )
    await swaps.award_shift(
        db, tenant_id=plat_scope.tenant.id, shift_id=shift.id, decided_by_user_id=boss.id
    )
    with pytest.raises(Conflict):
        await swaps.award_shift(
            db, tenant_id=plat_scope.tenant.id, shift_id=shift.id, decided_by_user_id=boss.id
        )
