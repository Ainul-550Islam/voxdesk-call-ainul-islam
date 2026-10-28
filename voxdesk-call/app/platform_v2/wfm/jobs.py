"""The workforce jobs: roll up, forecast, score, sweep adherence.

These functions are what the scheduler ticks and the API both call, so there is
exactly one implementation of "rebuild history" and one of "generate the forecast
for next week". Each takes an explicit session and tenant, never a request, which
is what lets the same code run from a cron tick, an admin button and a test.

Every job returns a small report dict; none of them swallow an exception, because
a silently failing forecast job is worse than a loudly failing one — the rota is
built on the numbers it was supposed to produce.
"""

from __future__ import annotations

import uuid
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.platform_v2_models import (
    WfmForecast,
    WfmForecastAccuracy,
    WfmIntervalHistory,
)
from app.platform_v2.errors import NotFound
from app.platform_v2.settings import get_plat2_settings
from app.platform_v2.wfm import accuracy, adherence, forecast, history
from app.platform_v2.wfm.publish import shifts_for_version
from app.platform_v2.wfm.timeoff import workday_minutes_from_templates


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc) if value.tzinfo else value.replace(tzinfo=timezone.utc)


async def tenant_zone_name(session: AsyncSession, *, tenant_id: uuid.UUID) -> str:
    """The zone the interval grid is built in.

    ``environments`` stores no timezone of its own, so the deployment-level
    ``WFM_TIMEZONE`` is the single source. It is a function rather than a
    constant so that a per-tenant override can be added here — in one place —
    the day the schema grows one, without the callers changing.
    """
    _ = (session, tenant_id)  # kept in the signature so the override has a home
    return get_plat2_settings().wfm_timezone


async def rollup(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    days: int = 7,
    end_date: date | None = None,
    zone_name: str | None = None,
    interval_minutes: int | None = None,
) -> dict[str, Any]:
    """Rebuild interval history for the last ``days`` days, every queue + total."""
    cfg = get_plat2_settings()
    zone = zone_name or await tenant_zone_name(session, tenant_id=tenant_id)
    minutes = interval_minutes or cfg.wfm_interval_minutes
    last = end_date or datetime.now(timezone.utc).date()
    first = last - timedelta(days=max(1, days) - 1)
    results = await history.rollup_environment_history(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        start_date=first,
        end_date=last,
        zone_name=zone,
        interval_minutes=minutes,
    )
    return {
        "start": first.isoformat(),
        "end": last.isoformat(),
        "zone": zone,
        "interval_minutes": minutes,
        "queues": len(results),
        "written": sum(result.written for result in results),
        "updated": sum(result.updated for result in results),
        "gaps": sum(result.gaps for result in results),
    }


async def samples_for(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    queue_key: str,
    zone_name: str,
    interval_minutes: int,
    lookback_days: int,
) -> list[tuple[history.IntervalSlot, float]]:
    """Stored history as ``(slot, offered)`` pairs the forecaster accepts.

    Gaps are excluded here rather than inside the model: the forecaster must never
    see a bucket nobody covered, or it learns that quiet days are demand.
    """
    end = _now()
    start = end - timedelta(days=lookback_days)
    rows = await history.load_history(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        queue_key=queue_key,
        start=start,
        end=end,
        include_gaps=False,
    )
    grid = history.interval_grid(
        zone_name,
        start.astimezone(history.tenant_zone(zone_name)).date(),
        end.astimezone(history.tenant_zone(zone_name)).date(),
        interval_minutes,
    )
    if not grid:
        return []
    # History is stored at whatever interval the rollup ran with; a forecast at a
    # finer interval must not simply drop the rows that do not land on its grid —
    # that both loses half the volume and mislabels the rest. Each row's volume is
    # spread across the requested slots in proportion to how much of it each slot
    # covers, so a 30-minute row of 40 calls becomes 20 + 20 at 15 minutes.
    starts = [slot.start for slot in grid]
    totals = [0.0] * len(grid)
    for row in rows:
        row_start = _as_utc(row.interval_start)
        row_minutes = int(row.interval_minutes or interval_minutes)
        if row_minutes <= 0:
            continue
        row_end = row_start + timedelta(minutes=row_minutes)
        volume = float(row.offered or 0)
        if volume <= 0:
            continue
        index = bisect_left(starts, row_start)
        while index < len(grid) and grid[index].start < row_end:
            slot = grid[index]
            low = max(slot.start, row_start)
            high = min(slot.end, row_end)
            if high > low:
                totals[index] += volume * (high - low).total_seconds() / (row_minutes * 60.0)
            index += 1
    return [(slot, totals[index]) for index, slot in enumerate(grid)]


async def generate_forecast(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    queue_key: str = "",
    horizon_days: int | None = None,
    zone_name: str | None = None,
    interval_minutes: int | None = None,
    overrides: dict[str, Any] | None = None,
    lookback_days: int | None = None,
    created_by_user_id: uuid.UUID | None = None,
    start_date: date | None = None,
    commit: bool = True,
) -> WfmForecast:
    """Build and store a forecast from whatever history exists.

    Raises ``InsufficientHistory`` (through the model) when there is not enough
    data — the honest failure, and the one the console turns into "keep the
    integration running for two more weeks".
    """
    cfg = get_plat2_settings()
    zone = zone_name or await tenant_zone_name(session, tenant_id=tenant_id)
    minutes = interval_minutes or cfg.wfm_interval_minutes
    horizon = horizon_days or min(14, cfg.wfm_forecast_horizon_days)
    lookback = lookback_days or max(cfg.wfm_min_history_days * 2, 42)
    samples = await samples_for(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        queue_key=queue_key,
        zone_name=zone,
        interval_minutes=minutes,
        lookback_days=lookback,
    )
    first = start_date or (datetime.now(history.tenant_zone(zone)).date() + timedelta(days=1))
    result = forecast.forecast_intervals(
        samples,
        zone_name=zone,
        start_date=first,
        horizon_days=horizon,
        interval_minutes=minutes,
        overrides=overrides,
    )
    row = WfmForecast(
        tenant_id=tenant_id,
        environment_id=environment_id,
        queue_key=queue_key,
        timezone=zone,
        interval_minutes=minutes,
        generated_at=result.generated_at,
        horizon_days=horizon,
        history_days=result.history_days,
        method=result.method,
        model_version=result.model_version,
        points=result.payload(),
        overrides=result.overrides,
        created_by_user_id=created_by_user_id,
    )
    session.add(row)
    if commit:
        await session.commit()
    return row


async def score_due_forecasts(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    queue_key: str | None = None,
    limit: int = 20,
    commit: bool = True,
) -> dict[str, Any]:
    """Score every forecast whose horizon has partly elapsed and is unscored.

    A forecast is scored once: re-scoring would either duplicate the metric or
    overwrite history with a later view of the same data.
    """
    scored_ids = set(
        (
            await session.execute(
                select(WfmForecastAccuracy.forecast_id).where(
                    WfmForecastAccuracy.tenant_id == tenant_id,
                    WfmForecastAccuracy.environment_id == environment_id,
                )
            )
        )
        .scalars()
        .all()
    )
    stmt = (
        select(WfmForecast)
        .where(
            WfmForecast.tenant_id == tenant_id,
            WfmForecast.environment_id == environment_id,
        )
        .order_by(WfmForecast.generated_at.desc())
        .limit(max(1, min(200, limit)))
    )
    if queue_key is not None:
        stmt = stmt.where(WfmForecast.queue_key == queue_key)
    candidates = [row for row in (await session.execute(stmt)).scalars().all()]
    scored: list[dict[str, Any]] = []
    skipped: list[str] = []
    for row in candidates:
        if row.id in scored_ids:
            continue
        try:
            metric = await accuracy.evaluate_forecast(session, row, commit=False)
        except Exception as exc:  # only the "nothing has elapsed yet" case is expected
            skipped.append(f"{row.id}:{type(exc).__name__}")
            continue
        scored.append(
            {
                "forecast_id": str(row.id),
                "queue_key": row.queue_key,
                "wape": float(metric.wape),
                "sample_count": metric.sample_count,
            }
        )
    if commit:
        await session.commit()
    return {"scored": scored, "skipped": skipped}


async def adherence_sweep(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    day: date | None = None,
    zone_name: str | None = None,
    persist: bool = True,
    commit: bool = True,
) -> dict[str, Any]:
    """Evaluate yesterday's rota against the state ledger and store exceptions."""
    zone = zone_name or await tenant_zone_name(session, tenant_id=tenant_id)
    target_day = day or (datetime.now(history.tenant_zone(zone)).date() - timedelta(days=1))
    start, end = history.window_bounds(target_day, target_day, zone)
    shifts = await adherence.shifts_between(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        start=start,
        end=end,
    )
    states = await adherence.states_between(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        start=start,
        end=end,
    )
    report = adherence.build_report(shifts, states, start=start, end=end)
    created = 0
    if persist:
        state_by_user: dict[uuid.UUID, list[Any]] = {}
        for interval in states:
            state_by_user.setdefault(interval.user_id, []).append(interval)
        for shift in shifts:
            user_states = (
                state_by_user.get(shift.user_id, [])
                if shift.user_id is not None
                else []
            )
            detail = adherence.evaluate_shift(shift, user_states)
            detected = adherence.detect_exceptions(shift, detail)
            rows = await adherence.persist_exceptions(
                session,
                tenant_id=tenant_id,
                environment_id=environment_id,
                shift=shift,
                detected=detected,
                day=start,
                commit=False,
            )
            created += len(rows)
    if commit:
        await session.commit()
    summary = report.as_dict(include_shifts=False)
    summary.update({"day": target_day.isoformat(), "exceptions_created": created})
    return summary


async def publish_shift_notifications(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    version_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Per-person change list for a version, shaped for the mobile push job.

    Kept in the WFM package because it is about the rota, not about push: the
    mobile module decides how to deliver it.
    """
    from app.platform_v2.wfm.publish import version_by_id

    version = await version_by_id(session, tenant_id=tenant_id, version_id=version_id)
    shifts = await shifts_for_version(
        session, tenant_id=tenant_id, version_id=version_id
    )
    by_user: dict[str, int] = {}
    for shift in shifts:
        by_user[str(shift.user_id)] = by_user.get(str(shift.user_id), 0) + 1
    return [
        {
            "user_id": user_id,
            "shifts": count,
            "version_number": version.version_number,
            "week_start": version.created_at.date().isoformat() if version.created_at else None,
        }
        for user_id, count in sorted(by_user.items())
    ]


async def forecast_or_404(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    forecast_id: uuid.UUID,
) -> WfmForecast:
    row = (
        await session.execute(
            select(WfmForecast).where(
                WfmForecast.tenant_id == tenant_id,
                WfmForecast.environment_id == environment_id,
                WfmForecast.id == forecast_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("forecast not found")
    return row


async def history_counts(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    queue_key: str = "",
) -> dict[str, Any]:
    """How much history exists — the gate the console shows before forecasting."""
    rows = await history.load_history(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        queue_key=queue_key,
        include_gaps=False,
    )
    if not rows:
        return {"intervals": 0, "days": 0, "first": None, "last": None, "enough": False}
    cfg = get_plat2_settings()
    zone = rows[0].timezone or cfg.wfm_timezone
    days = len({_as_utc(row.interval_start).astimezone(history.tenant_zone(zone)).date() for row in rows})
    return {
        "intervals": len(rows),
        "days": days,
        "first": _as_utc(rows[0].interval_start).isoformat(),
        "last": _as_utc(rows[-1].interval_start).isoformat(),
        "enough": days >= cfg.wfm_min_history_days,
        "required_days": cfg.wfm_min_history_days,
        "workday_minutes": await workday_minutes_from_templates(
            session, tenant_id=tenant_id
        ),
    }


async def purge_stale_history(
    session: AsyncSession, *, tenant_id: uuid.UUID, older_than_days: int = 400, commit: bool = True
) -> int:
    """Delete interval history beyond the retention horizon.

    Interval history is the personal-free aggregate the forecast needs; keeping it
    for ever is a storage decision nobody revisits. The horizon is a parameter so
    an operator can set it to the tenant's own policy.
    """
    cutoff = _now() - timedelta(days=max(30, older_than_days))
    rows = (
        await session.execute(
            select(WfmIntervalHistory).where(
                WfmIntervalHistory.tenant_id == tenant_id,
                WfmIntervalHistory.interval_start < cutoff,
            )
        )
    ).scalars()
    removed = 0
    for row in rows:
        await session.delete(row)
        removed += 1
    if commit:
        await session.commit()
    return removed


__all__ = [
    "adherence_sweep",
    "forecast_or_404",
    "generate_forecast",
    "history_counts",
    "publish_shift_notifications",
    "purge_stale_history",
    "rollup",
    "samples_for",
    "score_due_forecasts",
    "tenant_zone_name",
]
