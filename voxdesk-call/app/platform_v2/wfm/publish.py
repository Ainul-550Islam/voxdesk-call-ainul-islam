"""Publishing: turning a solved rota into the document people are held to.

The rules here exist because a published rota is a promise:

* **Versions are immutable.** Once a version is published it is never edited; a
  change is a new version, and the previous one is marked ``superseded``. That is
  what makes "you were scheduled for Saturday" answerable a month later.
* **Publishing moves pointers, it does not rewrite history.** ``wfm_schedules``
  records which version is current; the versions themselves keep their own
  ``published_at`` and author.
* **A version that cannot cover the week says so before it is published.** The
  coverage report from the solver is stored with the version, and publishing
  refuses a version whose report is missing — an unmeasured rota is not a plan.
* **Notification is a byproduct, not a side channel.** ``publish_version``
  returns the per-person change set so the mobile push job can tell people what
  changed, instead of every employee discovering it on Monday.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.platform_v2_models import (
    WfmSchedule,
    WfmScheduleVersion,
    WfmShift,
)
from app.platform_v2.errors import Conflict, NotFound, ValidationFailed
from app.platform_v2.wfm.scheduler import SolveResult, validate_week_start


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class VersionDiff:
    added: list[dict[str, Any]]
    removed: list[dict[str, Any]]
    unchanged: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "removed": self.removed,
            "unchanged": self.unchanged,
            "changed_assignments": len(self.added) + len(self.removed),
        }


def assignment_key(assignment: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(assignment.get("user_id")),
        str(assignment.get("starts_at")),
        str(assignment.get("template_id")),
    )


def diff_assignments(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> VersionDiff:
    """What changed between two versions, by (person, start, template)."""
    before_keys = {assignment_key(item): item for item in before}
    after_keys = {assignment_key(item): item for item in after}
    added = [after_keys[key] for key in sorted(after_keys.keys() - before_keys.keys())]
    removed = [before_keys[key] for key in sorted(before_keys.keys() - after_keys.keys())]
    unchanged = len(before_keys.keys() & after_keys.keys())
    return VersionDiff(added=added, removed=removed, unchanged=unchanged)


async def create_schedule(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    name: str,
    week_start: Any,
    timezone_name: str,
    created_by_user_id: uuid.UUID,
    commit: bool = True,
) -> WfmSchedule:
    """Create the container for one week's rota."""
    validate_week_start(week_start)
    existing = (
        await session.execute(
            select(WfmSchedule).where(
                WfmSchedule.tenant_id == tenant_id,
                WfmSchedule.environment_id == environment_id,
                WfmSchedule.week_start == week_start,
                WfmSchedule.status != "archived",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict(
            "a schedule already exists for that week",
            detail={"schedule_id": str(existing.id)},
        )
    # The table is unique on (tenant, environment, week_start, name), so a reused name
    # would surface as a 500 from the driver instead of a sentence the caller can act on.
    same_name = (
        await session.execute(
            select(WfmSchedule).where(
                WfmSchedule.tenant_id == tenant_id,
                WfmSchedule.environment_id == environment_id,
                WfmSchedule.week_start == week_start,
                WfmSchedule.name == name[:160],
            )
        )
    ).scalar_one_or_none()
    if same_name is not None:
        raise Conflict(
            "a schedule with that name already exists for that week",
            detail={"schedule_id": str(same_name.id), "name": name[:160]},
        )
    row = WfmSchedule(
        tenant_id=tenant_id,
        environment_id=environment_id,
        name=name[:160],
        week_start=week_start,
        timezone=timezone_name,
        status="draft",
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    if commit:
        await session.commit()
    return row


async def next_version_number(
    session: AsyncSession, *, tenant_id: uuid.UUID, schedule_id: uuid.UUID
) -> int:
    numbers = list(
        (
            await session.execute(
                select(WfmScheduleVersion.version_number).where(
                    WfmScheduleVersion.tenant_id == tenant_id,
                    WfmScheduleVersion.schedule_id == schedule_id,
                )
            )
        )
        .scalars()
        .all()
    )
    return (max(numbers) + 1) if numbers else 1


async def record_version(
    session: AsyncSession,
    *,
    schedule: WfmSchedule,
    result: SolveResult,
    created_by_user_id: uuid.UUID,
    note: str = "",
    commit: bool = True,
) -> WfmScheduleVersion:
    """Store a solved rota as a draft version and materialise its shifts.

    Draft shifts are written immediately so that swaps, time-off conflicts and
    the intraday board can all reason about the plan before anybody publishes it.
    Re-recording replaces the previous *draft* version's shifts; published
    versions are left alone, because they are the record.
    """
    if not result.solved:
        raise ValidationFailed(f"cannot record a rota with solver status {result.status}")
    number = await next_version_number(
        session, tenant_id=schedule.tenant_id, schedule_id=schedule.id
    )
    assignments = [assignment.as_dict() for assignment in result.assignments]
    report = result.as_report()
    version = WfmScheduleVersion(
        tenant_id=schedule.tenant_id,
        environment_id=schedule.environment_id,
        schedule_id=schedule.id,
        version_number=number,
        status="draft",
        assignments=assignments,
        coverage={
            "coverage_ratio": report["coverage_ratio"],
            "shortfall_agent_intervals": report["shortfall_agent_intervals"],
            "overcoverage_agent_intervals": report["overcoverage_agent_intervals"],
            "intervals": report["intervals"],
        },
        solver_report=report,
        note=note[:400],
        created_by_user_id=created_by_user_id,
    )
    session.add(version)
    await session.flush()

    for assignment in result.assignments:
        session.add(
            WfmShift(
                tenant_id=schedule.tenant_id,
                environment_id=schedule.environment_id,
                schedule_version_id=version.id,
                user_id=assignment.user_id,
                template_id=assignment.template_id,
                starts_at=assignment.starts_at,
                ends_at=assignment.ends_at,
                break_minutes=assignment.break_minutes,
                status="scheduled",
                skill_key=assignment.skill_key,
            )
        )
    if commit:
        await session.commit()
    return version


async def current_version(
    session: AsyncSession, *, schedule: WfmSchedule
) -> WfmScheduleVersion | None:
    if schedule.current_version_id is None:
        return None
    return (
        await session.execute(
            select(WfmScheduleVersion).where(
                WfmScheduleVersion.tenant_id == schedule.tenant_id,
                WfmScheduleVersion.id == schedule.current_version_id,
            )
        )
    ).scalar_one_or_none()


async def version_by_id(
    session: AsyncSession, *, tenant_id: uuid.UUID, version_id: uuid.UUID
) -> WfmScheduleVersion:
    row = (
        await session.execute(
            select(WfmScheduleVersion).where(
                WfmScheduleVersion.tenant_id == tenant_id,
                WfmScheduleVersion.id == version_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("schedule version not found")
    return row


async def publish_version(
    session: AsyncSession,
    *,
    schedule: WfmSchedule,
    version: WfmScheduleVersion,
    published_by_user_id: uuid.UUID,
    min_coverage_ratio: float = 0.0,
    commit: bool = True,
) -> dict[str, Any]:
    """Promote a version to current, superseding whatever was current.

    Returns the change set per person so the push job can notify only the people
    whose week actually moved — publishing an identical rota should not buzz
    four hundred phones.
    """
    if version.schedule_id != schedule.id:
        raise NotFound("schedule version not found")
    if version.tenant_id != schedule.tenant_id:
        raise NotFound("schedule version not found")
    if version.status == "published" and schedule.current_version_id == version.id:
        raise Conflict("this version is already the current one")
    report = version.solver_report or {}
    if not report:
        raise Conflict(
            "version has no solver report; re-run the solver before publishing",
            detail={"version_id": str(version.id)},
        )
    coverage = float((version.coverage or {}).get("coverage_ratio", 0.0))
    if coverage < min_coverage_ratio - 1e-9:
        raise Conflict(
            "version does not meet the minimum coverage ratio",
            detail={
                "coverage_ratio": coverage,
                "required": min_coverage_ratio,
                "shortfall_agent_intervals": (version.coverage or {}).get(
                    "shortfall_agent_intervals"
                ),
            },
        )

    previous = await current_version(session, schedule=schedule)
    previous_assignments = list(previous.assignments or []) if previous else []
    diff = diff_assignments(previous_assignments, list(version.assignments or []))

    if previous is not None and previous.id != version.id:
        previous.status = "superseded"
        await _set_shift_status(
            session,
            tenant_id=schedule.tenant_id,
            version_id=previous.id,
            status="dropped",
        )
    version.status = "published"
    version.published_at = _now()
    version.published_by_user_id = published_by_user_id
    schedule.status = "published"
    schedule.current_version_id = version.id
    schedule.updated_at = _now()
    await _set_shift_status(
        session,
        tenant_id=schedule.tenant_id,
        version_id=version.id,
        status="published",
    )
    if commit:
        await session.commit()
    return {
        "schedule_id": str(schedule.id),
        "version_id": str(version.id),
        "version_number": version.version_number,
        "published_at": version.published_at.isoformat(),
        "diff": diff.as_dict(),
        "notifications": _notification_payloads(diff),
    }


def _notification_payloads(diff: VersionDiff) -> list[dict[str, Any]]:
    per_user: dict[str, list[dict[str, Any]]] = {}
    for item in diff.added:
        per_user.setdefault(str(item.get("user_id")), []).append({"change": "added", **item})
    for item in diff.removed:
        per_user.setdefault(str(item.get("user_id")), []).append({"change": "removed", **item})
    return [
        {"user_id": user_id, "changes": len(changes), "items": changes}
        for user_id, changes in sorted(per_user.items())
    ]


async def _set_shift_status(
    session: AsyncSession, *, tenant_id: uuid.UUID, version_id: uuid.UUID, status: str
) -> None:
    rows = (
        await session.execute(
            select(WfmShift).where(
                WfmShift.tenant_id == tenant_id,
                WfmShift.schedule_version_id == version_id,
            )
        )
    ).scalars()
    for row in rows:
        if row.status in ("swapped", "open"):
            # A swap or an open bid survives a re-publish; it is a human decision
            # about a specific shift, not a property of the version.
            continue
        row.status = status


async def archive_schedule(
    session: AsyncSession,
    *,
    schedule: WfmSchedule,
    commit: bool = True,
) -> WfmSchedule:
    if schedule.status == "archived":
        raise Conflict("schedule is already archived")
    schedule.status = "archived"
    schedule.updated_at = _now()
    if commit:
        await session.commit()
    return schedule


async def shifts_for_version(
    session: AsyncSession, *, tenant_id: uuid.UUID, version_id: uuid.UUID
) -> list[WfmShift]:
    return list(
        (
            await session.execute(
                select(WfmShift)
                .where(
                    WfmShift.tenant_id == tenant_id,
                    WfmShift.schedule_version_id == version_id,
                )
                .order_by(WfmShift.starts_at)
            )
        )
        .scalars()
        .all()
    )


async def diff_versions(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    before_version_id: uuid.UUID,
    after_version_id: uuid.UUID,
) -> VersionDiff:
    before = await version_by_id(session, tenant_id=tenant_id, version_id=before_version_id)
    after = await version_by_id(session, tenant_id=tenant_id, version_id=after_version_id)
    return diff_assignments(list(before.assignments or []), list(after.assignments or []))


def export_rows(shifts: list[WfmShift], *, timezone_name: str) -> list[dict[str, Any]]:
    """CSV/payroll-shaped rows, in the rota's own zone.

    Exported in the tenant's local time with the UTC instant alongside, because a
    payroll clerk needs the former and a dispute needs the latter.
    """
    from app.platform_v2.wfm.history import tenant_zone

    zone = tenant_zone(timezone_name)
    rows: list[dict[str, Any]] = []
    for shift in shifts:
        starts = _as_utc(shift.starts_at)
        ends = _as_utc(shift.ends_at)
        rows.append(
            {
                "shift_id": str(shift.id),
                "user_id": str(shift.user_id),
                # A manual shift has no template; "None" in a payroll CSV reads as a
                # template called None, so the absence stays an absence.
                "template_id": "" if shift.template_id is None else str(shift.template_id),
                "starts_at_utc": starts.isoformat(),
                "ends_at_utc": ends.isoformat(),
                "starts_at_local": starts.astimezone(zone).isoformat(),
                "ends_at_local": ends.astimezone(zone).isoformat(),
                "break_minutes": int(shift.break_minutes or 0),
                "paid_hours": round(
                    max(
                        0.0,
                        (ends - starts).total_seconds() / 3600.0
                        - float(shift.break_minutes or 0) / 60.0,
                    ),
                    2,
                ),
                "skill_key": shift.skill_key,
                "status": shift.status,
            }
        )
    return rows


def summarise_version(version: WfmScheduleVersion) -> dict[str, Any]:
    return {
        "version_id": str(version.id),
        "version_number": version.version_number,
        "status": version.status,
        "assignments": len(version.assignments or []),
        "coverage": version.coverage or {},
        "solver": {
            key: (version.solver_report or {}).get(key)
            for key in ("status", "objective", "wall_time_seconds", "coverage_ratio")
        },
        "created_at": version.created_at.isoformat() if version.created_at else None,
        "published_at": version.published_at.isoformat() if version.published_at else None,
    }


__all__ = [
    "VersionDiff",
    "archive_schedule",
    "assignment_key",
    "create_schedule",
    "current_version",
    "diff_assignments",
    "diff_versions",
    "export_rows",
    "next_version_number",
    "publish_version",
    "record_version",
    "shifts_for_version",
    "summarise_version",
    "version_by_id",
]
