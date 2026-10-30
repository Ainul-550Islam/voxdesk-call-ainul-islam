"""Authenticated governed forecasting API over supplied time series."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.forecast import FORECAST_METHODS, UsagePoint
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.errors import request_id_from
from app.db.session import get_session
from app.forecasting.service import ForecastingService
from app.governance.context import resolve_scope
from app.governance.hashing import sha256_hex
from app.specialized_agents.executor import ExecutionOutcome, SpecializedExecutionRecord
from app.specialized_agents.service import SpecializedAgentService
from app.tenancy.isolation import BoundaryDenied, HierarchyError, NotFound, to_http

router = APIRouter(prefix="/api/forecast", tags=["forecasting"])


class SeriesExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: uuid.UUID
    model_version_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    series_reference: str | None = Field(default=None, max_length=500)
    series: list[dict[str, Any]] = Field(min_length=1, max_length=100_000)
    method: str = Field(default="linear", min_length=1, max_length=64)
    configuration: dict[str, Any] = Field(default_factory=dict)
    risk_tier: str = Field(default="high", min_length=1, max_length=24)
    agent_version: str = Field(default="1.0.0", min_length=1, max_length=100)


class ForecastRequest(SeriesExecutionRequest):
    horizon: int = Field(ge=1, le=365)


class BacktestRequest(SeriesExecutionRequest):
    test_size: int = Field(default=2, ge=1, le=99_998)


def _points(series: list[dict[str, Any]]) -> list[UsagePoint]:
    return [
        UsagePoint(period=str(point["period"]), value=float(point["value"]))
        for point in series
    ]


def _response_body(
    row: SpecializedExecutionRecord,
    result: dict[str, Any],
) -> dict[str, Any]:
    stored = row.result if isinstance(row.result, dict) else {}
    return {
        **result,
        "execution_id": str(row.id),
        "status": row.status,
        "review_required": row.review_required,
        "review_state": row.review_state,
        "review_case_id": stored.get("review_case_id"),
        "lineage_root_id": str(row.lineage_root_id) if row.lineage_root_id else None,
        "evidence_root_hash": row.evidence_root_hash,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


async def _execute(
    payload: SeriesExecutionRequest,
    request: Request,
    ctx: TenantContext,
    session: AsyncSession,
    *,
    operation: str,
    execution_payload: dict[str, Any],
    handler,
) -> ExecutionOutcome:
    scope = await resolve_scope(session, ctx, payload.environment_id)
    if scope.environment_id is None:
        raise BoundaryDenied()
    if payload.method not in FORECAST_METHODS:
        raise HierarchyError(
            "Forecast method is unsupported",
            code="invalid_forecast_method",
            status_code=422,
        )
    service = SpecializedAgentService(session, scope)
    context = service.context(
        actor_id=ctx.user_id,
        request_id=request_id_from(request),
        trace_id=request.headers.get("X-Trace-ID") or uuid.uuid4().hex,
        agent_type="forecasting",
        agent_version=payload.agent_version,
        model_version_id=payload.model_version_id,
        risk_tier=payload.risk_tier,
    )
    return await service.execute(
        context,
        idempotency_key=payload.idempotency_key,
        payload={
            "operation": operation,
            "series_fingerprint": sha256_hex(payload.series),
            "series_reference": payload.series_reference,
            "method": payload.method,
            "configuration": payload.configuration,
            **execution_payload,
        },
        handler=handler,
        policy_context={
            "forecast_method": payload.method,
            "uncertainty_disclosed": True,
        },
    )


@router.post("", status_code=201)
async def forecast(
    payload: ForecastRequest,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        points = _points(payload.series)
        engine = ForecastingService()
        outcome = await _execute(
            payload,
            request,
            ctx,
            session,
            operation="forecast",
            execution_payload={"horizon": payload.horizon},
            handler=lambda _context, _data, _sources: engine.analyze(
                points=points,
                method=payload.method,
                horizon=payload.horizon,
                window=int(payload.configuration.get("window", 3)),
                alpha=float(payload.configuration.get("alpha", 0.5)),
                series_reference=payload.series_reference,
            ),
        )
        return _response_body(outcome.record, outcome.output)
    except (KeyError, TypeError, ValueError) as exc:
        raise to_http(
            HierarchyError(
                "Forecast series/configuration is invalid",
                code="invalid_forecast_input",
                status_code=422,
            )
        ) from exc
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("")
async def list_forecasts(
    environment_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
):
    """List persisted forecast/backtest executions for scenario comparison."""
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        filters = (
            SpecializedExecutionRecord.tenant_id == scope.tenant_id,
            SpecializedExecutionRecord.organization_id == scope.organization_id,
            SpecializedExecutionRecord.environment_id == scope.environment_id,
            SpecializedExecutionRecord.agent_type == "forecasting",
        )
        total = await session.scalar(
            select(func.count()).select_from(SpecializedExecutionRecord).where(*filters)
        )
        rows = (
            await session.execute(
                select(SpecializedExecutionRecord)
                .where(*filters)
                .order_by(SpecializedExecutionRecord.created_at.desc())
                .offset(offset)
                .limit(limit)
            )
        ).scalars().all()
        return {
            "items": [_response_body(row, row.result or {}) for row in rows],
            "total": int(total or 0),
            "limit": limit,
            "offset": offset,
        }
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/backtest", status_code=201)
async def backtest(
    payload: BacktestRequest,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        points = _points(payload.series)
        engine = ForecastingService()
        outcome = await _execute(
            payload,
            request,
            ctx,
            session,
            operation="backtest",
            execution_payload={"test_size": payload.test_size},
            handler=lambda _context, _data, _sources: engine.backtest(
                points=points,
                method=payload.method,
                test_size=payload.test_size,
                window=int(payload.configuration.get("window", 3)),
                alpha=float(payload.configuration.get("alpha", 0.5)),
                series_reference=payload.series_reference,
            ),
        )
        return _response_body(outcome.record, outcome.output)
    except (KeyError, TypeError, ValueError) as exc:
        raise to_http(
            HierarchyError(
                "Backtest series/configuration is invalid",
                code="invalid_forecast_input",
                status_code=422,
            )
        ) from exc
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/{execution_id}")
async def get_forecast(
    execution_id: uuid.UUID,
    environment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        row = await session.scalar(
            select(SpecializedExecutionRecord).where(
                SpecializedExecutionRecord.id == execution_id,
                SpecializedExecutionRecord.tenant_id == scope.tenant_id,
                SpecializedExecutionRecord.organization_id == scope.organization_id,
                SpecializedExecutionRecord.environment_id == scope.environment_id,
                SpecializedExecutionRecord.agent_type == "forecasting",
            )
        )
        if row is None:
            raise NotFound()
        return _response_body(row, row.result or {})
    except HierarchyError as exc:
        raise to_http(exc) from None
