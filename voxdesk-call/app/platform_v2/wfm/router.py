"""HTTP surface for workforce management (Batch 07, block C).

Route order follows the work: measure history, forecast, see what the forecast
needs, build a rota, adjust it with time off and swaps, publish it, then hold
people to the published plan. Prefix is ``/api/wfm``.

Deliberate choices:

* **Every mutation carries an idempotency key** when the client sends one, and
  writes its audit row in the same transaction. Publishing twice is the classic
  way a team gets two "your rota changed" notifications per person.
* **Environment-scoped reads require a resolvable environment.** They 409 with
  ``environment_required`` rather than returning an empty list that looks like
  "no history".
* **Forecasts are never returned with a hard-coded accuracy.** ``/accuracy``
  scores stored points against stored actuals at request time; when nothing has
  elapsed yet it says so (``scored: false``) instead of inventing a percentage.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.platform_v2_models import (
    LaborRule,
    ShiftTemplate,
    TimeOffBalance,
    TimeOffRequest,
    WfmSchedule,
    WfmScheduleVersion,
    WfmShift,
)
from app.db.session import get_session
from app.platform_v2 import http as api
from app.platform_v2.errors import Conflict, NotFound, ValidationFailed
from app.platform_v2.settings import get_plat2_settings
from app.platform_v2.wfm import adherence, forecast, intraday, jobs, publish, scheduler, staffing, timeoff
from app.platform_v2.wfm import history as wfm_history
from app.platform_v2.wfm import swaps as swaps_mod

router = APIRouter(prefix="/api/wfm", tags=["workforce-management"])


# ------------------------------------------------------------------ schemas ---
class RollupRequest(BaseModel):
    days: int = Field(default=7, ge=1, le=90)
    end_date: date | None = None
    interval_minutes: int | None = None
    zone: str | None = None


class ForecastRequest(BaseModel):
    queue_key: str = Field(default="", max_length=36)
    horizon_days: int = Field(default=14, ge=1, le=365)
    interval_minutes: int | None = None
    zone: str | None = None
    start_date: date | None = None
    overrides: dict[str, Any] | None = None


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    start_minute: int = Field(ge=0, le=1439)
    length_minutes: int = Field(ge=15, le=1440)
    paid_break_minutes: int = Field(default=0, ge=0, le=600)
    unpaid_break_minutes: int = Field(default=0, ge=0, le=600)
    required_skill_key: str = Field(default="", max_length=64)
    is_active: bool = True


class LaborRuleIn(BaseModel):
    name: str = Field(default="Default", max_length=120)
    max_hours_per_week: float = Field(default=48.0, gt=0, le=168)
    min_rest_hours: float = Field(default=10.0, ge=0, le=48)
    max_consecutive_days: int = Field(default=6, ge=1, le=14)
    max_shift_hours: float = Field(default=12.0, gt=0, le=24)
    min_split_gap_minutes: int = Field(default=0, ge=0, le=720)
    is_active: bool = True


class ScheduleIn(BaseModel):
    name: str = Field(default="Weekly rota", max_length=160)
    week_start: date
    timezone: str | None = None
    queue_key: str = Field(default="", max_length=36)


class SolveIn(BaseModel):
    schedule_id: uuid.UUID
    employee_ids: list[uuid.UUID] = Field(default_factory=list)
    template_ids: list[uuid.UUID] = Field(default_factory=list)
    auto_record: bool = True
    allow_shortfall: bool = False
    note: str = Field(default="", max_length=400)


class PublishIn(BaseModel):
    version_id: uuid.UUID
    min_coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)


class TimeOffIn(BaseModel):
    kind: str = "vacation"
    starts_at: datetime
    ends_at: datetime
    reason: str = Field(default="", max_length=400)
    user_id: uuid.UUID | None = None


class DecideIn(BaseModel):
    approved: bool
    note: str = Field(default="", max_length=400)


class SwapOfferIn(BaseModel):
    shift_id: uuid.UUID
    target_user_id: uuid.UUID
    note: str = Field(default="", max_length=400)


class BidIn(BaseModel):
    preference: int = Field(default=0, ge=0, le=20)


class AccrualIn(BaseModel):
    user_id: uuid.UUID
    kind: str = "vacation"
    hours: float = Field(gt=0, le=1000)


# ------------------------------------------------------------------ helpers ---
def _dt(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _template(row: ShiftTemplate) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "start_minute": row.start_minute,
        "length_minutes": row.length_minutes,
        "paid_break_minutes": row.paid_break_minutes,
        "unpaid_break_minutes": row.unpaid_break_minutes,
        "required_skill_key": row.required_skill_key,
        "is_active": row.is_active,
    }


def _rule(row: LaborRule) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "max_hours_per_week": float(row.max_hours_per_week),
        "min_rest_hours": float(row.min_rest_hours),
        "max_consecutive_days": row.max_consecutive_days,
        "max_shift_hours": float(row.max_shift_hours),
        "min_split_gap_minutes": row.min_split_gap_minutes,
        "is_active": row.is_active,
    }


def _schedule(row: WfmSchedule) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "name": row.name,
        "week_start": row.week_start.isoformat(),
        "timezone": row.timezone,
        "status": row.status,
        "current_version_id": str(row.current_version_id) if row.current_version_id else None,
    }


def _time_off(row: TimeOffRequest) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "user_id": str(row.user_id),
        "kind": row.kind,
        "starts_at": _dt(row.starts_at),
        "ends_at": _dt(row.ends_at),
        "hours": float(row.hours or 0),
        "status": row.status,
        "reason": row.reason,
        "coverage": row.coverage or {},
        "decided_at": _dt(row.decided_at) if row.decided_at else None,
    }


def _swap(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "shift_id": str(row.shift_id),
        "offering_user_id": str(row.offering_user_id),
        "target_user_id": str(row.target_user_id),
        "status": row.status,
        "rule_check": row.rule_check or {},
        "note": row.note,
    }


async def _schedule_or_404(
    session: AsyncSession, *, tenant_id: uuid.UUID, schedule_id: uuid.UUID
) -> WfmSchedule:
    row = (
        await session.execute(
            select(WfmSchedule).where(
                WfmSchedule.tenant_id == tenant_id, WfmSchedule.id == schedule_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("schedule not found")
    return row


# ------------------------------------------------------------------ history ---
@router.post("/history/rebuild")
@api.endpoint_errors
async def rebuild_history(
    body: RollupRequest,
    request: Request,
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Recompute interval history from telephony rows (idempotent)."""
    env = await api.require_environment(session, ctx, environment_id)
    claim = await api.claim(
        session, ctx, request, operation="wfm.history.rebuild", payload=body.model_dump()
    )
    replay = api.replay_response(claim, resource_type="wfm_interval_history")
    if replay is not None:
        return replay
    result = await jobs.rollup(
        session,
        tenant_id=ctx.tenant_id,
        environment_id=env,
        days=body.days,
        end_date=body.end_date,
        zone_name=body.zone,
        interval_minutes=body.interval_minutes,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.history.rebuilt",
        resource_type="wfm_interval_history",
        detail=result,
    )
    await api.complete_claim(
        session, claim, resource_type="wfm_interval_history", resource_id=env
    )
    await session.commit()
    return result


@router.get("/history")
@api.endpoint_errors
async def read_history(
    queue_key: str = Query(default="", max_length=36),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    limit: int | None = Query(default=None),
    offset: int | None = Query(default=None),
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    env = await api.require_environment(session, ctx, environment_id)
    size, skip = api.page(limit, offset)
    rows = await wfm_history.load_history(
        session,
        tenant_id=ctx.tenant_id,
        environment_id=env,
        queue_key=queue_key,
        start=start,
        end=end,
    )
    summary = wfm_history.summarise(rows)
    window = rows[skip : skip + size]
    return {
        "queue_key": queue_key,
        "summary": summary.as_dict(),
        "count": len(rows),
        "limit": size,
        "offset": skip,
        "intervals": [
            {
                "interval_start": _dt(row.interval_start),
                "interval_minutes": row.interval_minutes,
                "offered": row.offered,
                "handled": row.handled,
                "abandoned": row.abandoned,
                "agents_available": row.agents_available,
                "is_gap": row.is_gap,
                "total_handle_seconds": float(row.total_handle_seconds or 0),
            }
            for row in window
        ],
    }


@router.get("/history/coverage")
@api.endpoint_errors
async def history_coverage(
    start: datetime = Query(),
    end: datetime = Query(),
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Where agents were logged in, and in which states — the shrinkage ledger."""
    env = await api.require_environment(session, ctx, environment_id)
    minutes = await wfm_history.state_minutes(
        session, tenant_id=ctx.tenant_id, environment_id=env, start=start, end=end
    )
    spans = await wfm_history.coverage_windows(
        session, tenant_id=ctx.tenant_id, environment_id=env, start=start, end=end
    )
    return {
        "state_minutes": minutes,
        "covered_seconds": round(
            sum((high - low).total_seconds() for low, high in spans), 2
        ),
        "windows": [{"start": _dt(low), "end": _dt(high)} for low, high in spans],
    }


@router.get("/history/counts")
@api.endpoint_errors
async def history_counts(
    queue_key: str = Query(default="", max_length=36),
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    env = await api.require_environment(session, ctx, environment_id)
    return await jobs.history_counts(
        session, tenant_id=ctx.tenant_id, environment_id=env, queue_key=queue_key
    )


# ---------------------------------------------------------------- forecasts ---
@router.post("/forecasts", status_code=status.HTTP_201_CREATED)
@api.endpoint_errors
async def create_forecast(
    body: ForecastRequest,
    request: Request,
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_SCHEDULE)),
    session: AsyncSession = Depends(get_session),
):
    """Generate a forecast from stored history. 422 when there is too little."""
    env = await api.require_environment(session, ctx, environment_id)
    claim = await api.claim(
        session, ctx, request, operation="wfm.forecast.create", payload=body.model_dump()
    )
    # A completed retry returns the stored forecast in full, not just its id: the
    # caller asked for a forecast and a forecast is what it should receive.
    replay = api.replay_id(claim, resource_type="wfm_forecast")
    if replay:
        row = await jobs.forecast_or_404(
            session, tenant_id=ctx.tenant_id, environment_id=env, forecast_id=uuid.UUID(replay)
        )
        return summarize_forecast(row)
    row = await jobs.generate_forecast(
        session,
        tenant_id=ctx.tenant_id,
        environment_id=env,
        queue_key=body.queue_key,
        horizon_days=body.horizon_days,
        zone_name=body.zone,
        interval_minutes=body.interval_minutes,
        overrides=body.overrides,
        created_by_user_id=ctx.user_id,
        start_date=body.start_date,
        commit=False,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.forecast.generated",
        resource_type="wfm_forecast",
        resource_id=row.id,
        detail={
            "queue_key": row.queue_key,
            "horizon_days": row.horizon_days,
            "history_days": row.history_days,
            "method": row.method,
        },
    )
    await api.complete_claim(session, claim, resource_type="wfm_forecast", resource_id=row.id)
    await session.commit()
    return summarize_forecast(row)


def summarize_forecast(row: Any) -> dict[str, Any]:
    # ``points`` is the stored point list (a JSON array), not a wrapper object:
    # the totals below are derived from it so they cannot disagree with what the
    # console charts.
    payload = row.points or []
    points = forecast.points_from_payload(payload)
    totals = _daily_totals(points)
    return {
        "id": str(row.id),
        "queue_key": row.queue_key,
        "timezone": row.timezone,
        "interval_minutes": row.interval_minutes,
        "generated_at": _dt(row.generated_at),
        "horizon_days": row.horizon_days,
        "history_days": row.history_days,
        "method": row.method,
        "model_version": row.model_version,
        "points": len(points),
        "total_expected": round(sum(point.expected for point in points), 3),
        "peak": round(max((point.expected for point in points), default=0.0), 3),
        "overrides": row.overrides or {},
        "daily_totals": totals,
    }


def _daily_totals(points: list[forecast.ForecastPoint]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for point in points:
        key = point.interval_start.date().isoformat()
        totals[key] = round(totals.get(key, 0.0) + point.expected, 3)
    return dict(sorted(totals.items()))


@router.get("/forecasts")
@api.endpoint_errors
async def list_forecasts(
    queue_key: str | None = Query(default=None, max_length=36),
    limit: int | None = Query(default=None),
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    from app.db.platform_v2_models import WfmForecast

    env = await api.require_environment(session, ctx, environment_id)
    size, _ = api.page(limit, 0)
    stmt = (
        select(WfmForecast)
        .where(
            WfmForecast.tenant_id == ctx.tenant_id,
            WfmForecast.environment_id == env,
        )
        .order_by(WfmForecast.generated_at.desc())
        .limit(size)
    )
    if queue_key is not None:
        stmt = stmt.where(WfmForecast.queue_key == queue_key)
    rows = (await session.execute(stmt)).scalars().all()
    return {"forecasts": [summarize_forecast(row) for row in rows]}


@router.get("/forecasts/{forecast_id}")
@api.endpoint_errors
async def read_forecast(
    forecast_id: uuid.UUID,
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    env = await api.require_environment(session, ctx, environment_id)
    row = await jobs.forecast_or_404(
        session, tenant_id=ctx.tenant_id, environment_id=env, forecast_id=forecast_id
    )
    body = summarize_forecast(row)
    # ``points`` is stored as the point array itself; the curve is that array.
    body["curve"] = list(row.points or [])
    return body


@router.post("/forecasts/{forecast_id}/score")
@api.endpoint_errors
async def score_forecast(
    forecast_id: uuid.UUID,
    request: Request,
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_SCHEDULE)),
    session: AsyncSession = Depends(get_session),
):
    """Score the elapsed part of a forecast against stored actuals.

    Refuses when nothing has elapsed: an accuracy number computed from the future
    is a fabrication, not a measurement.
    """
    from app.platform_v2.wfm import accuracy as accuracy_mod

    env = await api.require_environment(session, ctx, environment_id)
    row = await jobs.forecast_or_404(
        session, tenant_id=ctx.tenant_id, environment_id=env, forecast_id=forecast_id
    )
    metric = await accuracy_mod.evaluate_forecast(session, row, commit=False)
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.forecast.scored",
        resource_type="wfm_forecast",
        resource_id=row.id,
        detail={"wape": float(metric.wape), "samples": metric.sample_count},
    )
    await session.commit()
    return {
        "forecast_id": str(row.id),
        "queue_key": row.queue_key,
        "evaluated_from": _dt(metric.evaluated_from),
        "evaluated_to": _dt(metric.evaluated_to),
        **metric.as_dict(include_intervals=True)["metrics"],
        "intervals": metric.as_dict(include_intervals=True)["per_interval"],
    }


@router.get("/accuracy")
@api.endpoint_errors
async def read_accuracy(
    queue_key: str = Query(default="", max_length=36),
    limit: int = Query(default=10, ge=1, le=100),
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Stored, previously computed accuracy for this queue — plus its trend."""
    from app.platform_v2.wfm import accuracy as accuracy_mod

    env = await api.require_environment(session, ctx, environment_id)
    rows = await accuracy_mod.latest_accuracy(
        session,
        tenant_id=ctx.tenant_id,
        environment_id=env,
        queue_key=queue_key,
        limit=limit,
    )
    return {
        "queue_key": queue_key,
        "count": len(rows),
        "trend": accuracy_mod.trend(rows).as_dict(),
        "evaluations": [
            {
                "forecast_id": str(row.forecast_id),
                "evaluated_from": _dt(row.evaluated_from),
                "evaluated_to": _dt(row.evaluated_to),
                "wape": float(row.wape),
                "mape": float(row.mape),
                "bias": float(row.bias),
                "mae": float(row.mae),
                "sample_count": row.sample_count,
            }
            for row in rows
        ],
    }


# ----------------------------------------------------------------- staffing ---
@router.get("/staffing")
@api.endpoint_errors
async def read_staffing(
    forecast_id: uuid.UUID = Query(),
    service_level: float = Query(default=0.8, gt=0, le=1),
    answer_seconds: float = Query(default=20.0, ge=0, le=600),
    max_occupancy: float = Query(default=0.85, gt=0, le=1),
    shrinkage: float = Query(default=0.0, ge=0, lt=1),
    average_handle_seconds: float = Query(default=240.0, gt=0, le=14400),
    band: str = Query(default="expected", pattern="^(low|expected|high)$"),
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Agent requirement per interval for a stored forecast."""
    env = await api.require_environment(session, ctx, environment_id)
    row = await jobs.forecast_or_404(
        session, tenant_id=ctx.tenant_id, environment_id=env, forecast_id=forecast_id
    )
    points = forecast.points_from_payload(row.points)
    target = staffing.StaffingTarget(
        service_level=service_level,
        answer_seconds=answer_seconds,
        max_occupancy=max_occupancy,
        shrinkage=shrinkage,
        average_handle_seconds=average_handle_seconds,
    )
    target.validate()
    plan = staffing.StaffingPlan(
        requirements=[
            staffing.requirement_for_point(
                point,
                target,
                volume={"low": point.low, "expected": point.expected, "high": point.high}[band],
            )
            for point in points
        ],
        target=target,
        timezone=row.timezone,
        band=band,
        queue_key=row.queue_key,
    )
    return {**plan.as_dict(include_intervals=True), "distribution": staffing.distribution(plan.requirements)}


# ---------------------------------------------------------------- templates ---
@router.get("/templates")
@api.endpoint_errors
async def list_templates(
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(ShiftTemplate)
            .where(ShiftTemplate.tenant_id == ctx.tenant_id)
            .order_by(ShiftTemplate.start_minute)
        )
    ).scalars()
    return {"templates": [_template(row) for row in rows]}


@router.post("/templates", status_code=status.HTTP_201_CREATED)
@api.endpoint_errors
async def create_template(
    body: TemplateIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    if body.paid_break_minutes + body.unpaid_break_minutes >= body.length_minutes:
        raise ValidationFailed("breaks cannot consume the whole shift")
    claim = await api.claim(
        session, ctx, request, operation="wfm.template.create", payload=body.model_dump()
    )
    replay = api.replay_response(claim, resource_type="wfm_shift_template")
    if replay is not None:
        return replay
    row = ShiftTemplate(
        tenant_id=ctx.tenant_id,
        name=body.name,
        start_minute=body.start_minute,
        length_minutes=body.length_minutes,
        paid_break_minutes=body.paid_break_minutes,
        unpaid_break_minutes=body.unpaid_break_minutes,
        required_skill_key=body.required_skill_key,
        is_active=body.is_active,
        break_rules={},
    )
    session.add(row)
    await session.flush()
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.template.created",
        resource_type="wfm_shift_template",
        resource_id=row.id,
        detail={"name": row.name, "length_minutes": row.length_minutes},
    )
    await api.complete_claim(session, claim, resource_type="wfm_shift_template", resource_id=row.id)
    await session.commit()
    return _template(row)


@router.delete("/templates/{template_id}", status_code=status.HTTP_200_OK)
@api.endpoint_errors
async def deactivate_template(
    template_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Deactivate rather than delete: published rotas reference this row."""
    row = (
        await session.execute(
            select(ShiftTemplate).where(
                ShiftTemplate.tenant_id == ctx.tenant_id,
                ShiftTemplate.id == template_id,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("template not found")
    row.is_active = False
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.template.deactivated",
        resource_type="wfm_shift_template",
        resource_id=row.id,
    )
    await session.commit()
    return _template(row)


# -------------------------------------------------------------- labor rules ---
@router.get("/labor-rules")
@api.endpoint_errors
async def list_labor_rules(
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        await session.execute(
            select(LaborRule)
            .where(LaborRule.tenant_id == ctx.tenant_id)
            .order_by(LaborRule.created_at)
        )
    ).scalars()
    return {"rules": [_rule(row) for row in rows]}


@router.put("/labor-rules")
@api.endpoint_errors
async def upsert_labor_rules(
    body: LaborRuleIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """There is one active rule set per tenant; a new one supersedes the old."""
    scheduler.LaborRules(
        max_hours_per_week=body.max_hours_per_week,
        min_rest_hours=body.min_rest_hours,
        max_consecutive_days=body.max_consecutive_days,
        max_shift_hours=body.max_shift_hours,
        min_split_gap_minutes=body.min_split_gap_minutes,
    ).validate()
    existing = (
        await session.execute(
            select(LaborRule).where(
                LaborRule.tenant_id == ctx.tenant_id, LaborRule.is_active.is_(True)
            )
        )
    ).scalars().all()
    for row in existing:
        row.is_active = False
    row = LaborRule(
        tenant_id=ctx.tenant_id,
        name=body.name,
        max_hours_per_week=body.max_hours_per_week,
        min_rest_hours=body.min_rest_hours,
        max_consecutive_days=body.max_consecutive_days,
        max_shift_hours=body.max_shift_hours,
        min_split_gap_minutes=body.min_split_gap_minutes,
        is_active=True,
    )
    session.add(row)
    await session.flush()
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.labor_rules.updated",
        resource_type="wfm_labor_rule",
        resource_id=row.id,
        detail={"superseded": len(existing), "max_hours_per_week": body.max_hours_per_week},
    )
    await session.commit()
    return _rule(row)


# ---------------------------------------------------------------- schedules ---
@router.get("/schedules")
@api.endpoint_errors
async def list_schedules(
    limit: int | None = Query(default=None),
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    env = await api.require_environment(session, ctx, environment_id)
    size, _ = api.page(limit, 0)
    rows = (
        await session.execute(
            select(WfmSchedule)
            .where(
                WfmSchedule.tenant_id == ctx.tenant_id,
                WfmSchedule.environment_id == env,
            )
            .order_by(WfmSchedule.week_start.desc())
            .limit(size)
        )
    ).scalars()
    return {"schedules": [_schedule(row) for row in rows]}


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
@api.endpoint_errors
async def create_schedule(
    body: ScheduleIn,
    request: Request,
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_SCHEDULE)),
    session: AsyncSession = Depends(get_session),
):
    env = await api.require_environment(session, ctx, environment_id)
    claim = await api.claim(
        session, ctx, request, operation="wfm.schedule.create", payload=body.model_dump(mode="json")
    )
    replay = api.replay_response(claim, resource_type="wfm_schedule")
    if replay is not None:
        return replay
    row = await publish.create_schedule(
        session,
        tenant_id=ctx.tenant_id,
        environment_id=env,
        name=body.name,
        week_start=body.week_start,
        timezone_name=body.timezone or get_plat2_settings().wfm_timezone,
        created_by_user_id=ctx.user_id,
        commit=False,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.schedule.created",
        resource_type="wfm_schedule",
        resource_id=row.id,
        detail={"week_start": row.week_start.isoformat(), "timezone": row.timezone},
    )
    await api.complete_claim(session, claim, resource_type="wfm_schedule", resource_id=row.id)
    await session.commit()
    return _schedule(row)


@router.get("/schedules/{schedule_id}")
@api.endpoint_errors
async def read_schedule(
    schedule_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await _schedule_or_404(session, tenant_id=ctx.tenant_id, schedule_id=schedule_id)
    versions = (
        await session.execute(
            select(WfmScheduleVersion)
            .where(
                WfmScheduleVersion.tenant_id == ctx.tenant_id,
                WfmScheduleVersion.schedule_id == row.id,
            )
            .order_by(WfmScheduleVersion.version_number.desc())
        )
    ).scalars().all()
    current = await publish.current_version(session, schedule=row)
    # Before the first publish there is no current version, but the solver has
    # already written the draft shifts — and that draft is exactly what a manager
    # has to review. Show the current version, else the newest one.
    shown = current or (versions[0] if versions else None)
    shift_rows = (
        await publish.shifts_for_version(session, tenant_id=ctx.tenant_id, version_id=shown.id)
        if shown is not None
        else []
    )
    return {
        **_schedule(row),
        "versions": [publish.summarise_version(version) for version in versions],
        # Which version the ``shifts`` belong to, so the console never presents a
        # draft grid as if it were the published rota.
        "shifts_version_id": str(shown.id) if shown is not None else None,
        "shifts_published": bool(current is not None and shown is not None and shown.id == current.id),
        "shifts": publish.export_rows(shift_rows, timezone_name=row.timezone),
    }


@router.post("/schedules/{schedule_id}/solve")
@api.endpoint_errors
async def solve_schedule(
    schedule_id: uuid.UUID,
    body: SolveIn,
    request: Request,
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_SCHEDULE)),
    session: AsyncSession = Depends(get_session),
):
    """Build a rota for this week from the latest forecast and the labour rules.

    Employees default to every active user with queue access in the tenant; the
    caller can narrow the list. A solve that cannot satisfy the rules raises
    ``schedule_infeasible`` with the arithmetic that explains it.
    """
    from app.db.models import User, UserRole

    env = await api.require_environment(session, ctx, environment_id)
    schedule = await _schedule_or_404(
        session, tenant_id=ctx.tenant_id, schedule_id=schedule_id
    )
    if body.schedule_id != schedule.id:
        raise ValidationFailed("schedule_id in the body must match the path")
    cfg = get_plat2_settings()
    days = scheduler.week_days(schedule.week_start)

    template_rows = (
        await session.execute(
            select(ShiftTemplate).where(
                ShiftTemplate.tenant_id == ctx.tenant_id,
                ShiftTemplate.is_active.is_(True),
                *(
                    [ShiftTemplate.id.in_(body.template_ids)]
                    if body.template_ids
                    else []
                ),
            )
        )
    ).scalars().all()
    specs = scheduler.templates_from_rows(list(template_rows))
    if not specs:
        raise Conflict("no active shift templates; create one before solving")

    if body.employee_ids:
        user_rows = (
            await session.execute(
                select(User).where(
                    User.tenant_id == ctx.tenant_id,
                    User.id.in_(body.employee_ids),
                    User.is_active.is_(True),
                )
            )
        ).scalars().all()
    else:
        user_rows = (
            await session.execute(
                select(User).where(
                    User.tenant_id == ctx.tenant_id,
                    User.is_active.is_(True),
                    User.role.in_((UserRole.OWNER, UserRole.ADMIN, UserRole.MANAGER, UserRole.AGENT)),
                )
            )
        ).scalars().all()
    employees = tuple(
        scheduler.EmployeeSpec(user_id=user.id, skills=frozenset({"*"})) for user in user_rows
    )

    rule_row = (
        await session.execute(
            select(LaborRule).where(
                LaborRule.tenant_id == ctx.tenant_id, LaborRule.is_active.is_(True)
            )
        )
    ).scalars().first()
    rules = scheduler.rules_from_row(rule_row)

    from app.db.platform_v2_models import WfmForecast

    # The newest forecast in this environment whose horizon reaches the week being
    # staffed. Reading the newest one outright would staff next week from a
    # forecast that only covered last week.
    week_start_utc, week_end_utc = wfm_history.window_bounds(
        days[0], days[-1], schedule.timezone
    )
    forecast_row = None
    for candidate in (
        await session.execute(
            select(WfmForecast)
            .where(
                WfmForecast.tenant_id == ctx.tenant_id,
                WfmForecast.environment_id == env,
            )
            .order_by(WfmForecast.generated_at.desc())
            .limit(20)
        )
    ).scalars().all():
        candidate_points = forecast.points_from_payload(candidate.points)
        if not candidate_points:
            continue
        first = min(point.interval_start for point in candidate_points)
        # The last point is a *start*: a forecast whose final interval begins at
        # 23:45 covers the week until midnight, and comparing starts would declare
        # every complete week uncovered.
        last = max(
            point.interval_start + timedelta(minutes=point.interval_minutes)
            for point in candidate_points
        )
        if first <= week_start_utc and last >= week_end_utc:
            forecast_row = candidate
            break
    if forecast_row is None:
        raise Conflict(
            "no forecast covers this week; generate one before solving",
            detail={"week_start": days[0].isoformat(), "week_end": days[-1].isoformat()},
        )
    points = forecast.points_from_payload(forecast_row.points)
    target = staffing.default_target()
    plan = staffing.StaffingPlan(
        requirements=[staffing.requirement_for_point(point, target) for point in points],
        target=target,
        timezone=schedule.timezone,
        # The rota is built from expected demand; ``band`` is not optional because
        # the plan reports which band it used.
        band="expected",
    )
    requirement = scheduler.curve_from_requirements(
        plan.requirements,
        days=days,
        interval_minutes=cfg.wfm_interval_minutes,
        zone_name=schedule.timezone,
    )
    blocked = tuple(
        await timeoff.blocked_windows_for_week(
            session,
            tenant_id=ctx.tenant_id,
            week_start=week_start_utc,
            week_end=week_end_utc,
        )
    )
    result = scheduler.solve(
        scheduler.ScheduleRequest(
            week_start=schedule.week_start,
            timezone_name=schedule.timezone,
            interval_minutes=cfg.wfm_interval_minutes,
            employees=employees,
            templates=tuple(specs),
            rules=rules,
            requirement=requirement,
            days=days,
            blocked=blocked,
            allow_shortfall=body.allow_shortfall,
        )
    )
    version = None
    if body.auto_record:
        version = await publish.record_version(
            session,
            schedule=schedule,
            result=result,
            created_by_user_id=ctx.user_id,
            note=body.note,
            commit=False,
        )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.schedule.solved",
        resource_type="wfm_schedule",
        resource_id=schedule.id,
        detail={
            "status": result.status,
            "assignments": len(result.assignments),
            "shortfall": result.shortfall,
            "version_id": str(version.id) if version else None,
        },
    )
    await session.commit()
    body_report = result.as_report()
    if version is not None:
        body_report["version"] = publish.summarise_version(version)
    return body_report


@router.post("/schedules/{schedule_id}/publish")
@api.endpoint_errors
async def publish_schedule(
    schedule_id: uuid.UUID,
    body: PublishIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_PUBLISH)),
    session: AsyncSession = Depends(get_session),
):
    schedule = await _schedule_or_404(
        session, tenant_id=ctx.tenant_id, schedule_id=schedule_id
    )
    version = await publish.version_by_id(
        session, tenant_id=ctx.tenant_id, version_id=body.version_id
    )
    claim = await api.claim(
        session,
        ctx,
        request,
        operation="wfm.schedule.publish",
        payload=body.model_dump(mode="json"),
    )
    replay = api.replay_response(claim, resource_type="wfm_schedule_version")
    if replay is not None:
        return replay
    result = await publish.publish_version(
        session,
        schedule=schedule,
        version=version,
        published_by_user_id=ctx.user_id,
        min_coverage_ratio=body.min_coverage_ratio,
        commit=False,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.schedule.published",
        resource_type="wfm_schedule_version",
        resource_id=version.id,
        detail={
            "schedule_id": str(schedule.id),
            "version_number": version.version_number,
            "changed_assignments": result["diff"]["changed_assignments"],
        },
    )
    await api.complete_claim(
        session, claim, resource_type="wfm_schedule_version", resource_id=version.id
    )
    await session.commit()
    return result


@router.get("/schedules/{schedule_id}/diff")
@api.endpoint_errors
async def diff_schedule_versions(
    schedule_id: uuid.UUID,
    before_version_id: uuid.UUID = Query(),
    after_version_id: uuid.UUID = Query(),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    await _schedule_or_404(session, tenant_id=ctx.tenant_id, schedule_id=schedule_id)
    diff = await publish.diff_versions(
        session,
        tenant_id=ctx.tenant_id,
        before_version_id=before_version_id,
        after_version_id=after_version_id,
    )
    return diff.as_dict()


@router.post("/schedules/{schedule_id}/archive")
@api.endpoint_errors
async def archive_schedule(
    schedule_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_PUBLISH)),
    session: AsyncSession = Depends(get_session),
):
    schedule = await _schedule_or_404(
        session, tenant_id=ctx.tenant_id, schedule_id=schedule_id
    )
    await publish.archive_schedule(session, schedule=schedule, commit=False)
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.schedule.archived",
        resource_type="wfm_schedule",
        resource_id=schedule.id,
    )
    await session.commit()
    return _schedule(schedule)


@router.get("/my-shifts")
@api.endpoint_errors
async def my_shifts(
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    """The caller's own rota — the endpoint the mobile app reads."""
    window_start = start or (datetime.now(timezone.utc) - timedelta(days=1))
    window_end = end or (window_start + timedelta(days=21))
    rows = (
        await session.execute(
            select(WfmShift)
            .where(
                WfmShift.tenant_id == ctx.tenant_id,
                WfmShift.user_id == ctx.user_id,
                WfmShift.starts_at < window_end,
                WfmShift.ends_at > window_start,
            )
            .order_by(WfmShift.starts_at)
        )
    ).scalars()
    exported = publish.export_rows(
        list(rows), timezone_name=get_plat2_settings().wfm_timezone
    )
    return {
        "user_id": str(ctx.user_id),
        "shifts": exported,
        "total_paid_hours": round(sum(row["paid_hours"] for row in exported), 2),
    }


# ----------------------------------------------------------------- time off ---
@router.get("/time-off")
@api.endpoint_errors
async def list_time_off(
    statuses: str = Query(default="requested", max_length=120),
    user_id: uuid.UUID | None = Query(default=None),
    limit: int | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    wanted = tuple(item.strip() for item in statuses.split(",") if item.strip())
    for status_name in wanted:
        if status_name not in ("requested", "approved", "denied", "cancelled"):
            raise ValidationFailed(f"unknown status {status_name!r}")
    size, _ = api.page(limit, 0)
    # An agent sees only their own requests unless they hold the approver role.
    from app.auth.rbac import has_permission
    from app.db.models import UserRole

    scoped_user = user_id
    if not has_permission(ctx.user.role, Permission.WFM_APPROVE):
        scoped_user = ctx.user_id
    rows = await timeoff.list_requests(
        session,
        tenant_id=ctx.tenant_id,
        user_id=scoped_user,
        statuses=wanted or ("requested",),
        limit=size,
    )
    return {"requests": [_time_off(row) for row in rows]}


@router.post("/time-off", status_code=status.HTTP_201_CREATED)
@api.endpoint_errors
async def create_time_off(
    body: TimeOffIn,
    request: Request,
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Any employee may request their own leave; an approver may file for others."""
    from app.auth.rbac import has_permission

    target_user = body.user_id or ctx.user_id
    if target_user != ctx.user_id and not has_permission(
        ctx.user.role, Permission.WFM_APPROVE
    ):
        raise NotFound("user not found")
    env = await api.require_environment(session, ctx, environment_id)
    claim = await api.claim(
        session,
        ctx,
        request,
        operation="wfm.time_off.create",
        payload={**body.model_dump(mode="json"), "user_id": str(target_user)},
    )
    replay = api.replay_response(claim, resource_type="wfm_time_off_request")
    if replay is not None:
        return replay
    zone = get_plat2_settings().wfm_timezone
    row = await timeoff.create_request(
        session,
        tenant_id=ctx.tenant_id,
        user_id=target_user,
        kind=body.kind,
        starts_at=body.starts_at,
        ends_at=body.ends_at,
        reason=body.reason,
        zone_name=zone,
        environment_id=env,
        commit=False,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.time_off.requested",
        resource_type="wfm_time_off_request",
        resource_id=row.id,
        detail={
            "user_id": str(row.user_id),
            "kind": row.kind,
            "hours": float(row.hours or 0),
            "starts_at": _dt(row.starts_at),
        },
    )
    await api.complete_claim(
        session, claim, resource_type="wfm_time_off_request", resource_id=row.id
    )
    await session.commit()
    return _time_off(row)


@router.post("/time-off/{request_id}/decide")
@api.endpoint_errors
async def decide_time_off(
    request_id: uuid.UUID,
    body: DecideIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    row = await timeoff.decide(
        session,
        tenant_id=ctx.tenant_id,
        request_id=request_id,
        approved=body.approved,
        decided_by_user_id=ctx.user_id,
        note=body.note,
        commit=False,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.time_off.decided",
        resource_type="wfm_time_off_request",
        resource_id=row.id,
        detail={"approved": body.approved, "hours": float(row.hours or 0)},
    )
    await session.commit()
    return _time_off(row)


@router.post("/time-off/{request_id}/cancel")
@api.endpoint_errors
async def cancel_time_off(
    request_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await timeoff.cancel(
        session,
        tenant_id=ctx.tenant_id,
        request_id=request_id,
        user_id=ctx.user_id,
        commit=False,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.time_off.cancelled",
        resource_type="wfm_time_off_request",
        resource_id=row.id,
    )
    await session.commit()
    return _time_off(row)


@router.get("/time-off/balances")
@api.endpoint_errors
async def time_off_balances(
    user_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    from app.auth.rbac import has_permission

    target = user_id or ctx.user_id
    if target != ctx.user_id and not has_permission(ctx.user.role, Permission.WFM_APPROVE):
        raise NotFound("user not found")
    rows = (
        await session.execute(
            select(TimeOffBalance).where(
                TimeOffBalance.tenant_id == ctx.tenant_id,
                TimeOffBalance.user_id == target,
            )
        )
    ).scalars()
    return {
        "user_id": str(target),
        "balances": [timeoff.summarise_balance(row) for row in rows],
    }


@router.post("/time-off/accruals")
@api.endpoint_errors
async def accrue_time_off(
    body: AccrualIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    if body.kind not in timeoff.KINDS:
        raise ValidationFailed(f"kind must be one of {timeoff.KINDS}")
    row = await timeoff.accrue(
        session,
        tenant_id=ctx.tenant_id,
        user_id=body.user_id,
        kind=body.kind,
        hours=body.hours,
        commit=False,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.time_off.accrued",
        resource_type="wfm_time_off_balance",
        resource_id=row.id,
        detail={"hours": body.hours, "kind": body.kind, "user_id": str(body.user_id)},
    )
    await session.commit()
    return timeoff.summarise_balance(row)


# -------------------------------------------------------------------- swaps ---
@router.post("/swaps", status_code=status.HTTP_201_CREATED)
@api.endpoint_errors
async def offer_swap(
    body: SwapOfferIn,
    request: Request,
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    env = await api.require_environment(session, ctx, environment_id)
    claim = await api.claim(
        session, ctx, request, operation="wfm.swap.offer", payload=body.model_dump(mode="json")
    )
    replay = api.replay_response(claim, resource_type="wfm_shift_swap")
    if replay is not None:
        return replay
    row = await swaps_mod.offer_swap(
        session,
        tenant_id=ctx.tenant_id,
        environment_id=env,
        shift_id=body.shift_id,
        offering_user_id=ctx.user_id,
        target_user_id=body.target_user_id,
        note=body.note,
        commit=False,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.swap.offered",
        resource_type="wfm_shift_swap",
        resource_id=row.id,
        detail={
            "shift_id": str(row.shift_id),
            "target_user_id": str(row.target_user_id),
            "allowed": (row.rule_check or {}).get("allowed"),
        },
    )
    await api.complete_claim(session, claim, resource_type="wfm_shift_swap", resource_id=row.id)
    await session.commit()
    return _swap(row)


@router.get("/swaps")
@api.endpoint_errors
async def list_swaps(
    statuses: str = Query(default="offered,accepted", max_length=120),
    all_users: bool = Query(default=False),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    from app.auth.rbac import has_permission

    wanted = tuple(item.strip() for item in statuses.split(",") if item.strip())
    for status_name in wanted:
        if status_name not in swaps_mod.SWAP_STATUSES:
            raise ValidationFailed(f"unknown status {status_name!r}")
    scope_user = None if all_users and has_permission(ctx.user.role, Permission.WFM_APPROVE) else ctx.user_id
    rows = await swaps_mod.list_swaps(
        session, tenant_id=ctx.tenant_id, statuses=wanted or ("offered",), user_id=scope_user
    )
    return {"swaps": [_swap(row) for row in rows]}


@router.post("/swaps/{swap_id}/accept")
@api.endpoint_errors
async def accept_swap(
    swap_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await swaps_mod.accept_swap(
        session, tenant_id=ctx.tenant_id, swap_id=swap_id, acting_user_id=ctx.user_id, commit=False
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.swap.accepted",
        resource_type="wfm_shift_swap",
        resource_id=row.id,
        detail={"status": row.status, "allowed": (row.rule_check or {}).get("allowed")},
        result="success" if row.status == "accepted" else "failure",
    )
    await session.commit()
    return _swap(row)


@router.post("/swaps/{swap_id}/decide")
@api.endpoint_errors
async def decide_swap(
    swap_id: uuid.UUID,
    body: DecideIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    row = await swaps_mod.decide_swap(
        session,
        tenant_id=ctx.tenant_id,
        swap_id=swap_id,
        approved=body.approved,
        decided_by_user_id=ctx.user_id,
        note=body.note,
        commit=False,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.swap.decided",
        resource_type="wfm_shift_swap",
        resource_id=row.id,
        detail={"approved": body.approved, "status": row.status},
    )
    await session.commit()
    return _swap(row)


@router.post("/shifts/{shift_id}/open")
@api.endpoint_errors
async def open_shift(
    shift_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    row = await swaps_mod.open_shift(session, tenant_id=ctx.tenant_id, shift_id=shift_id, commit=False)
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.shift.opened",
        resource_type="wfm_shift",
        resource_id=row.id,
    )
    await session.commit()
    return {"shift_id": str(row.id), "status": row.status}


@router.post("/shifts/{shift_id}/bids", status_code=status.HTTP_201_CREATED)
@api.endpoint_errors
async def place_bid(
    shift_id: uuid.UUID,
    body: BidIn,
    request: Request,
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    env = await api.require_environment(session, ctx, environment_id)
    claim = await api.claim(
        session,
        ctx,
        request,
        operation="wfm.bid.place",
        payload={"shift_id": str(shift_id), **body.model_dump()},
    )
    replay = api.replay_response(claim, resource_type="wfm_open_shift_bid")
    if replay is not None:
        return replay
    row = await swaps_mod.place_bid(
        session,
        tenant_id=ctx.tenant_id,
        environment_id=env,
        shift_id=shift_id,
        user_id=ctx.user_id,
        preference=body.preference,
        commit=False,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.bid.placed",
        resource_type="wfm_open_shift_bid",
        resource_id=row.id,
        detail={"shift_id": str(shift_id), "score": row.score},
    )
    await api.complete_claim(session, claim, resource_type="wfm_open_shift_bid", resource_id=row.id)
    await session.commit()
    return {
        "id": str(row.id),
        "shift_id": str(row.shift_id),
        "user_id": str(row.user_id),
        "status": row.status,
        "score": float(row.score),
    }


@router.get("/shifts/{shift_id}/bids")
@api.endpoint_errors
async def list_bids(
    shift_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    rows = await swaps_mod.list_bids(session, tenant_id=ctx.tenant_id, shift_id=shift_id)
    return {
        "shift_id": str(shift_id),
        "bids": [
            {
                "id": str(row.id),
                "user_id": str(row.user_id),
                "status": row.status,
                "score": float(row.score),
            }
            for row in rows
        ],
    }


@router.post("/shifts/{shift_id}/award")
@api.endpoint_errors
async def award_shift(
    shift_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    shift, bid = await swaps_mod.award_shift(
        session, tenant_id=ctx.tenant_id, shift_id=shift_id, decided_by_user_id=ctx.user_id, commit=False
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.shift.awarded",
        resource_type="wfm_shift",
        resource_id=shift.id,
        detail={"bid_id": str(bid.id), "user_id": str(bid.user_id)},
    )
    await session.commit()
    return {
        "shift_id": str(shift.id),
        "user_id": str(shift.user_id),
        "bid_id": str(bid.id),
        "status": shift.status,
    }


# ----------------------------------------------------------------- intraday ---
@router.get("/intraday")
@api.endpoint_errors
async def read_intraday(
    queue_key: str = Query(default="", max_length=36),
    as_of: datetime | None = Query(default=None),
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    env = await api.require_environment(session, ctx, environment_id)
    zone = get_plat2_settings().wfm_timezone
    snapshot = await intraday.snapshot(
        session,
        tenant_id=ctx.tenant_id,
        environment_id=env,
        queue_key=queue_key,
        as_of=as_of,
        zone_name=zone,
    )
    return {**snapshot.as_dict(), "alert": intraday.alert_level(snapshot)}


# --------------------------------------------------------------- adherence ---
@router.get("/adherence")
@api.endpoint_errors
async def read_adherence(
    day: date | None = Query(default=None),
    user_id: uuid.UUID | None = Query(default=None),
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    from app.auth.rbac import has_permission

    env = await api.require_environment(session, ctx, environment_id)
    zone = get_plat2_settings().wfm_timezone
    target = day or (datetime.now(wfm_history.tenant_zone(zone)).date() - timedelta(days=1))
    scoped_user = user_id
    if not has_permission(ctx.user.role, Permission.WFM_APPROVE):
        scoped_user = ctx.user_id
    start, end = wfm_history.window_bounds(target, target, zone)
    shifts = await adherence.shifts_between(
        session,
        tenant_id=ctx.tenant_id,
        environment_id=env,
        start=start,
        end=end,
        user_id=scoped_user,
    )
    states = await adherence.states_between(
        session, tenant_id=ctx.tenant_id, environment_id=env, start=start, end=end
    )
    report = adherence.build_report(shifts, states, start=start, end=end)
    return {"day": target.isoformat(), **report.as_dict(include_shifts=True)}


@router.post("/adherence/sweep")
@api.endpoint_errors
async def sweep_adherence(
    request: Request,
    day: date | None = Query(default=None),
    environment_id: uuid.UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    env = await api.require_environment(session, ctx, environment_id)
    result = await jobs.adherence_sweep(
        session,
        tenant_id=ctx.tenant_id,
        environment_id=env,
        day=day,
        persist=True,
        commit=False,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.adherence.swept",
        resource_type="wfm_adherence_exception",
        detail={"day": result.get("day"), "created": result.get("exceptions_created")},
    )
    await session.commit()
    return result


@router.get("/adherence/exceptions")
@api.endpoint_errors
async def list_adherence_exceptions(
    statuses: str = Query(default="open", max_length=120),
    user_id: uuid.UUID | None = Query(default=None),
    limit: int | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.WFM_READ)),
    session: AsyncSession = Depends(get_session),
):
    from app.auth.rbac import has_permission

    wanted = tuple(item.strip() for item in statuses.split(",") if item.strip())
    for status_name in wanted:
        if status_name not in ("open", "approved", "denied"):
            raise ValidationFailed(f"unknown status {status_name!r}")
    size, _ = api.page(limit, 0)
    scoped_user = user_id
    if not has_permission(ctx.user.role, Permission.WFM_APPROVE):
        scoped_user = ctx.user_id
    rows = await adherence.list_exceptions(
        session, tenant_id=ctx.tenant_id, statuses=wanted or ("open",), user_id=scoped_user, limit=size
    )
    return {
        "exceptions": [
            {
                "id": str(row.id),
                "user_id": str(row.user_id),
                "shift_id": str(row.shift_id) if row.shift_id else None,
                "day": row.day.date().isoformat(),
                "kind": row.kind,
                "minutes": float(row.minutes or 0),
                "status": row.status,
                "detail": row.detail or {},
            }
            for row in rows
        ]
    }


@router.post("/adherence/exceptions/{exception_id}/decide")
@api.endpoint_errors
async def decide_adherence_exception(
    exception_id: uuid.UUID,
    body: DecideIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.WFM_APPROVE)),
    session: AsyncSession = Depends(get_session),
):
    row = await adherence.decide_exception(
        session,
        tenant_id=ctx.tenant_id,
        exception_id=exception_id,
        approved=body.approved,
        decided_by_user_id=ctx.user_id,
        note=body.note,
        commit=False,
    )
    await api.audit(
        session,
        ctx,
        request,
        event_type="wfm.adherence.decided",
        resource_type="wfm_adherence_exception",
        resource_id=row.id,
        detail={"approved": body.approved, "kind": row.kind},
    )
    await session.commit()
    return {"id": str(row.id), "status": row.status, "kind": row.kind}


__all__ = ["router"]
