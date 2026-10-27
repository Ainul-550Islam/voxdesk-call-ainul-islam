"""Shift swaps and open-shift bidding.

Both features move a shift between people, so both can break the labour rules
that ``wfm.scheduler`` spent time satisfying. This module therefore answers three
questions separately, and never merges them:

1. **Is this trade legal?** ``rule_violations`` checks the target's weekly hours,
   rest, consecutive days, skills and approved leave. A trade that would give
   somebody 56 hours or a five-hour turnaround is refused with the specific rule.
2. **Is it agreed?** A swap needs the offered side, the receiving side and a
   manager. The status trail records all three so the audit log can attribute it.
3. **Who gets it?** An open shift is awarded by a deterministic score — the person
   with the fewest scheduled hours first, earliest bid as the tie-break — because
   "first come first served" quietly rewards whoever is not on a call.

Nothing here mutates a published rota silently: an approved swap rewrites the
shift's owner and marks it ``swapped``, which is exactly what the adherence
report and the payroll export need to see.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.platform_v2_models import (
    LaborRule,
    OpenShiftBid,
    ShiftSwap,
    WfmShift,
    TimeOffRequest,
)
from app.platform_v2.errors import Conflict, NotFound, ValidationFailed
from app.platform_v2.wfm.scheduler import LaborRules, rules_from_row

SWAP_STATUSES = ("offered", "accepted", "approved", "rejected", "cancelled")
BID_STATUSES = ("placed", "awarded", "rejected", "withdrawn")
#: A shift further in the past than this is history, not a bargaining chip.
MAX_PAST_GRACE = timedelta(minutes=1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ShiftWindow:
    id: uuid.UUID
    user_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    skill_key: str = ""
    break_minutes: int = 0

    @property
    def paid_hours(self) -> float:
        return max(
            0.0,
            (self.ends_at - self.starts_at).total_seconds() / 3600.0
            - self.break_minutes / 60.0,
        )


def shift_window(shift: WfmShift) -> ShiftWindow:
    return ShiftWindow(
        id=shift.id,
        user_id=shift.user_id,
        starts_at=_as_utc(shift.starts_at),
        ends_at=_as_utc(shift.ends_at),
        skill_key=str(shift.skill_key or ""),
        break_minutes=int(shift.break_minutes or 0),
    )


@dataclass
class RuleCheck:
    """The outcome of checking one trade against every rule that applies."""

    violations: list[dict[str, Any]] = field(default_factory=list)
    hours_after: float = 0.0
    rest_hours: float | None = None

    @property
    def allowed(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "violations": list(self.violations),
            "hours_after": round(self.hours_after, 2),
            "rest_hours": None if self.rest_hours is None else round(self.rest_hours, 2),
        }


def week_bounds(shift_start: datetime) -> tuple[datetime, datetime]:
    """UTC bounds of the ISO week containing ``shift_start``."""
    local = _as_utc(shift_start)
    start = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return start, start + timedelta(days=7)


def rule_violations(
    *,
    incoming: ShiftWindow,
    target_shifts: list[ShiftWindow],
    rules: LaborRules,
    target_skills: frozenset[str] | None = None,
    leave: list[tuple[datetime, datetime]] | None = None,
) -> RuleCheck:
    """Pure rule evaluation — no I/O, so it is cheap and directly testable.

    ``target_shifts`` is the target employee's *other* shifts for the week; the
    incoming shift is added on top. The check reports every violation it finds,
    not just the first, because a manager fixing one problem at a time is how a
    swap turns into three round trips.
    """
    violations: list[dict[str, Any]] = []
    others = [shift for shift in target_shifts if shift.id != incoming.id]
    overlaps = [
        shift
        for shift in others
        if shift.starts_at < incoming.ends_at and shift.ends_at > incoming.starts_at
    ]
    if overlaps:
        violations.append(
            {
                "rule": "double_booking",
                "detail": "the target already works an overlapping shift",
                "shift_id": str(overlaps[0].id),
            }
        )

    hours = sum(shift.paid_hours for shift in others) + incoming.paid_hours
    if hours > rules.max_hours_per_week + 1e-9:
        violations.append(
            {
                "rule": "max_hours_per_week",
                "detail": f"{round(hours, 2)}h would exceed {rules.max_hours_per_week}h",
                "hours_after": round(hours, 2),
            }
        )

    if incoming.paid_hours > rules.max_shift_hours + 1e-9:
        violations.append(
            {
                "rule": "max_shift_hours",
                "detail": f"{round(incoming.paid_hours, 2)}h exceeds {rules.max_shift_hours}h",
                "hours": round(incoming.paid_hours, 2),
            }
        )

    same_day = [shift for shift in others if shift.starts_at.date() == incoming.starts_at.date()]
    if len(same_day) + 1 > rules.max_shifts_per_day:
        violations.append(
            {
                "rule": "max_shifts_per_day",
                "detail": f"{len(same_day) + 1} shifts on {incoming.starts_at.date().isoformat()}",
            }
        )
    if rules.min_split_gap_minutes > 0:
        for shift in same_day:
            if shift.ends_at <= incoming.starts_at:
                gap_minutes = (incoming.starts_at - shift.ends_at).total_seconds() / 60.0
            else:
                gap_minutes = (shift.starts_at - incoming.ends_at).total_seconds() / 60.0
            if gap_minutes < rules.min_split_gap_minutes - 1e-9:
                violations.append(
                    {
                        "rule": "min_split_gap",
                        "detail": f"only {round(gap_minutes)}min between split shifts",
                        "shift_id": str(shift.id),
                    }
                )

    rest: float | None = None
    for shift in others:
        # Only the *separation* counts. Taking the minimum of "gap before" and
        # "gap after" would let the negative side of an unrelated direction hide
        # a genuine five-hour turnaround.
        if shift.ends_at <= incoming.starts_at:
            gap = (incoming.starts_at - shift.ends_at).total_seconds() / 3600.0
        elif shift.starts_at >= incoming.ends_at:
            gap = (shift.starts_at - incoming.ends_at).total_seconds() / 3600.0
        else:
            gap = 0.0
        rest = gap if rest is None else min(rest, gap)
        if gap < rules.min_rest_hours - 1e-9:
            violations.append(
                {
                    "rule": "min_rest_hours",
                    "detail": f"only {round(gap, 2)}h between shifts",
                    "shift_id": str(shift.id),
                }
            )

    if incoming.skill_key and target_skills is not None:
        if incoming.skill_key not in target_skills and "*" not in target_skills:
            violations.append(
                {
                    "rule": "skill_mismatch",
                    "detail": f"target lacks skill {incoming.skill_key!r}",
                }
            )

    for low, high in leave or []:
        if low < incoming.ends_at and high > incoming.starts_at:
            violations.append(
                {
                    "rule": "approved_leave",
                    "detail": "the target has approved leave in that window",
                }
            )
            break

    consecutive = _consecutive_days([*others, incoming])
    if consecutive > rules.max_consecutive_days:
        violations.append(
            {
                "rule": "max_consecutive_days",
                "detail": f"{consecutive} consecutive days would exceed "
                f"{rules.max_consecutive_days}",
            }
        )
    return RuleCheck(violations=violations, hours_after=hours, rest_hours=rest)


def _consecutive_days(shifts: list[ShiftWindow]) -> int:
    days = sorted({shift.starts_at.date() for shift in shifts})
    if not days:
        return 0
    longest = current = 1
    for previous, following in zip(days, days[1:]):
        current = current + 1 if (following - previous).days == 1 else 1
        longest = max(longest, current)
    return longest


async def _shift(
    session: AsyncSession, *, tenant_id: uuid.UUID, shift_id: uuid.UUID
) -> WfmShift:
    shift = (
        await session.execute(
            select(WfmShift).where(WfmShift.tenant_id == tenant_id, WfmShift.id == shift_id)
        )
    ).scalar_one_or_none()
    if shift is None:
        raise NotFound("shift not found")
    return shift


async def _week_shifts(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID, moment: datetime
) -> list[ShiftWindow]:
    start, end = week_bounds(moment)
    rows = (
        await session.execute(
            select(WfmShift).where(
                WfmShift.tenant_id == tenant_id,
                WfmShift.user_id == user_id,
                WfmShift.starts_at >= start,
                WfmShift.starts_at < end,
            )
        )
    ).scalars()
    return [shift_window(row) for row in rows]


async def _rules(session: AsyncSession, *, tenant_id: uuid.UUID) -> LaborRules:
    row = (
        await session.execute(
            select(LaborRule).where(
                LaborRule.tenant_id == tenant_id, LaborRule.is_active.is_(True)
            )
        )
    ).scalars().first()
    return rules_from_row(row)


async def _leave(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> list[tuple[datetime, datetime]]:
    rows = (
        await session.execute(
            select(TimeOffRequest).where(
                TimeOffRequest.tenant_id == tenant_id,
                TimeOffRequest.user_id == user_id,
                TimeOffRequest.status == "approved",
            )
        )
    ).scalars()
    return [(_as_utc(row.starts_at), _as_utc(row.ends_at)) for row in rows]


async def offer_swap(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    shift_id: uuid.UUID,
    offering_user_id: uuid.UUID,
    target_user_id: uuid.UUID,
    note: str = "",
    target_skills: frozenset[str] | None = None,
    commit: bool = True,
) -> ShiftSwap:
    """Offer a shift to a colleague, pre-checked so the manager sees the truth.

    The swap is *recorded even when the rules fail* — with the violations in
    ``rule_check`` — because "I tried to swap and it said no" is information the
    manager needs. Approval is what refuses.
    """
    if offering_user_id == target_user_id:
        raise ValidationFailed("a swap needs two different people")
    shift = await _shift(session, tenant_id=tenant_id, shift_id=shift_id)
    if shift.user_id != offering_user_id:
        raise NotFound("shift not found")
    # A shift that has *started* can no longer be handed over: the person is working
    # it, and a trade mid-shift leaves the queue covered by nobody. The old check
    # compared the end, which let exactly that happen.
    if _as_utc(shift.starts_at) <= _now() - MAX_PAST_GRACE:
        raise Conflict("this shift has already started")

    incoming = shift_window(shift)
    check = rule_violations(
        incoming=incoming,
        target_shifts=await _week_shifts(
            session, tenant_id=tenant_id, user_id=target_user_id, moment=incoming.starts_at
        ),
        rules=await _rules(session, tenant_id=tenant_id),
        target_skills=target_skills,
        leave=await _leave(session, tenant_id=tenant_id, user_id=target_user_id),
    )
    swap = ShiftSwap(
        tenant_id=tenant_id,
        environment_id=environment_id,
        shift_id=shift.id,
        offering_user_id=offering_user_id,
        target_user_id=target_user_id,
        status="offered",
        rule_check=check.as_dict(),
        note=note[:400],
    )
    session.add(swap)
    if commit:
        await session.commit()
    return swap


async def accept_swap(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    swap_id: uuid.UUID,
    acting_user_id: uuid.UUID,
    target_skills: frozenset[str] | None = None,
    commit: bool = True,
) -> ShiftSwap:
    """The receiving employee agrees; the swap still needs a manager."""
    swap = await _swap(session, tenant_id=tenant_id, swap_id=swap_id)
    if swap.target_user_id != acting_user_id:
        raise NotFound("swap not found")
    if swap.status != "offered":
        raise Conflict(f"swap is already {swap.status}")
    shift = await _shift(session, tenant_id=tenant_id, shift_id=swap.shift_id)
    check = rule_violations(
        incoming=shift_window(shift),
        target_shifts=await _week_shifts(
            session, tenant_id=tenant_id, user_id=swap.target_user_id, moment=shift.starts_at
        ),
        rules=await _rules(session, tenant_id=tenant_id),
        target_skills=target_skills,
        leave=await _leave(session, tenant_id=tenant_id, user_id=swap.target_user_id),
    )
    # Re-checked at acceptance: the rota may have moved since the offer.
    swap.rule_check = check.as_dict()
    if not check.allowed:
        swap.status = "rejected"
        if commit:
            await session.commit()
        return swap
    swap.status = "accepted"
    if commit:
        await session.commit()
    return swap


async def decide_swap(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    swap_id: uuid.UUID,
    approved: bool,
    decided_by_user_id: uuid.UUID,
    note: str = "",
    commit: bool = True,
) -> ShiftSwap:
    """Manager decision. Approval transfers the shift; refusal touches nothing."""
    swap = await _swap(session, tenant_id=tenant_id, swap_id=swap_id)
    if swap.status in ("approved", "rejected", "cancelled"):
        raise Conflict(f"swap is already {swap.status}")
    if approved and not (swap.rule_check or {}).get("allowed", False):
        raise Conflict(
            "swap violates labour rules and cannot be approved",
            detail={"rule_check": swap.rule_check or {}},
        )
    shift = await _shift(session, tenant_id=tenant_id, shift_id=swap.shift_id)
    if approved:
        shift.user_id = swap.target_user_id
        shift.status = "swapped"
        swap.status = "approved"
    else:
        swap.status = "rejected"
    swap.decided_by_user_id = decided_by_user_id
    swap.decided_at = _now()
    if note:
        swap.note = note[:400]
    if commit:
        await session.commit()
    return swap


async def cancel_swap(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    swap_id: uuid.UUID,
    acting_user_id: uuid.UUID,
    commit: bool = True,
) -> ShiftSwap:
    swap = await _swap(session, tenant_id=tenant_id, swap_id=swap_id)
    if acting_user_id not in (swap.offering_user_id, swap.target_user_id):
        raise NotFound("swap not found")
    if swap.status == "approved":
        raise Conflict("an approved swap cannot be cancelled; ask a manager to reassign")
    swap.status = "cancelled"
    if commit:
        await session.commit()
    return swap


async def _swap(session: AsyncSession, *, tenant_id: uuid.UUID, swap_id: uuid.UUID) -> ShiftSwap:
    row = (
        await session.execute(
            select(ShiftSwap).where(
                ShiftSwap.tenant_id == tenant_id, ShiftSwap.id == swap_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("swap not found")
    return row


async def open_shift(
    session: AsyncSession, *, tenant_id: uuid.UUID, shift_id: uuid.UUID, commit: bool = True
) -> WfmShift:
    """Release a shift for bidding, removing it from its current owner."""
    shift = await _shift(session, tenant_id=tenant_id, shift_id=shift_id)
    if _as_utc(shift.ends_at) <= _now():
        raise Conflict("this shift has already ended")
    shift.status = "open"
    if commit:
        await session.commit()
    return shift


def bid_score(*, hours_already: float, hours_incoming: float, preference: int = 0) -> float:
    """Lower is better: total hours after taking the shift, minus preference.

    Hours dominate because fairness is the reason open shifts exist. ``preference``
    is a small integer the employee can carry (a shift they asked for) and is
    bounded so it can never outweigh eleven hours of imbalance.
    """
    bounded = min(20, max(0, int(preference)))
    return round(hours_already + hours_incoming - bounded * 0.05, 4)


async def place_bid(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    shift_id: uuid.UUID,
    user_id: uuid.UUID,
    preference: int = 0,
    commit: bool = True,
) -> OpenShiftBid:
    shift = await _shift(session, tenant_id=tenant_id, shift_id=shift_id)
    if shift.status != "open":
        raise Conflict("this shift is not open for bidding")
    existing = (
        await session.execute(
            select(OpenShiftBid).where(
                OpenShiftBid.tenant_id == tenant_id,
                OpenShiftBid.shift_id == shift_id,
                OpenShiftBid.user_id == user_id,
                OpenShiftBid.status == "placed",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict("you have already bid for this shift")
    window = shift_window(shift)
    hours = sum(
        item.paid_hours
        for item in await _week_shifts(
            session, tenant_id=tenant_id, user_id=user_id, moment=window.starts_at
        )
    )
    bid = OpenShiftBid(
        tenant_id=tenant_id,
        environment_id=environment_id,
        shift_id=shift_id,
        user_id=user_id,
        status="placed",
        score=bid_score(
            hours_already=hours, hours_incoming=window.paid_hours, preference=preference
        ),
    )
    session.add(bid)
    if commit:
        await session.commit()
    return bid


async def award_shift(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    shift_id: uuid.UUID,
    decided_by_user_id: uuid.UUID,
    commit: bool = True,
) -> tuple[WfmShift, OpenShiftBid]:
    """Give an open shift to the lowest-scoring bid (ties: earliest bid)."""
    shift = await _shift(session, tenant_id=tenant_id, shift_id=shift_id)
    if shift.status != "open":
        raise Conflict("this shift is not open for bidding")
    bids = list(
        (
            await session.execute(
                select(OpenShiftBid)
                .where(
                    OpenShiftBid.tenant_id == tenant_id,
                    OpenShiftBid.shift_id == shift_id,
                    OpenShiftBid.status == "placed",
                )
                .order_by(OpenShiftBid.score, OpenShiftBid.created_at)
            )
        )
        .scalars()
        .all()
    )
    if not bids:
        raise Conflict("no bids have been placed for this shift")
    winner = bids[0]
    shift.user_id = winner.user_id
    shift.status = "scheduled"
    winner.status = "awarded"
    winner.score = float(winner.score)
    for loser in bids[1:]:
        loser.status = "rejected"
    if commit:
        await session.commit()
    return shift, winner


async def list_swaps(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    statuses: tuple[str, ...] = ("offered", "accepted"),
    user_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[ShiftSwap]:
    stmt = select(ShiftSwap).where(
        ShiftSwap.tenant_id == tenant_id, ShiftSwap.status.in_(statuses)
    )
    if user_id is not None:
        stmt = stmt.where(
            (ShiftSwap.offering_user_id == user_id) | (ShiftSwap.target_user_id == user_id)
        )
    stmt = stmt.order_by(ShiftSwap.created_at.desc()).limit(max(1, min(500, limit)))
    return list((await session.execute(stmt)).scalars().all())


async def list_bids(
    session: AsyncSession, *, tenant_id: uuid.UUID, shift_id: uuid.UUID
) -> list[OpenShiftBid]:
    return list(
        (
            await session.execute(
                select(OpenShiftBid)
                .where(
                    OpenShiftBid.tenant_id == tenant_id, OpenShiftBid.shift_id == shift_id
                )
                .order_by(OpenShiftBid.score, OpenShiftBid.created_at)
            )
        )
        .scalars()
        .all()
    )


__all__ = [
    "BID_STATUSES",
    "RuleCheck",
    "SWAP_STATUSES",
    "ShiftWindow",
    "accept_swap",
    "award_shift",
    "bid_score",
    "cancel_swap",
    "decide_swap",
    "list_bids",
    "list_swaps",
    "offer_swap",
    "open_shift",
    "place_bid",
    "rule_violations",
    "shift_window",
    "week_bounds",
]
