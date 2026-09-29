"""Authenticated governed forecasting API over supplied time series."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.forecast import FORECAST_METHODS, UsagePoint
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.errors import request_id_from
from app.db.session import get_session
from app.forecasting.service import ForecastingService
from app.governance.context import resolve_scope
from app.governance.hashing import sha256_hex
from app.review.service import create_case
from app.specialized_agents.service import SpecializedAgentService
from app.specialized_agents.executor import SpecializedExecutionRecord
from app.tenancy.isolation import BoundaryDenied, HierarchyError, NotFound, to_http

router = APIRouter(prefix="/api/forecast", tags=["forecasting"])


class ForecastRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: uuid.UUID
    model_version_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    series_reference: str | None = Field(default=None, max_length=500)
    series: list[dict[str, Any]] = Field(min_length=1, max_length=100_000)
    method: str = Field(default="linear", min_length=1, max_length=64)
    horizon: int = Field(ge=1, le=365)
    configuration: dict[str, Any] = Field(default_factory=dict)
    risk_tier: str = Field(default="high", min_length=1, max_length=24)
    agent_version: str = Field(default="1.0.0", min_length=1, max_length=100)


@router.post("", status_code=201)
async def forecast(payload: ForecastRequest, request: Request, ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)), session: AsyncSession = Depends(get_session)):
    try:
        scope = await resolve_scope(session, ctx, payload.environment_id)
        if scope.environment_id is None:
            raise BoundaryDenied()
        if payload.method not in FORECAST_METHODS:
            raise HierarchyError("Forecast method is unsupported", code="invalid_forecast_method", status_code=422)
        points = [UsagePoint(period=str(point["period"]), value=float(point["value"])) for point in payload.series]
        engine = ForecastingService()
        agent_service = SpecializedAgentService(session, scope)
        context = agent_service.context(actor_id=ctx.user_id, request_id=request_id_from(request), trace_id=request.headers.get("X-Trace-ID") or uuid.uuid4().hex, agent_type="forecasting", agent_version=payload.agent_version, model_version_id=payload.model_version_id, risk_tier=payload.risk_tier)
        outcome = await agent_service.execute(context, idempotency_key=payload.idempotency_key, payload={"series_fingerprint": sha256_hex(payload.series), "series_reference": payload.series_reference, "method": payload.method, "horizon": payload.horizon, "configuration": payload.configuration}, handler=lambda _context, _data, _sources: engine.analyze(points=points, method=payload.method, horizon=payload.horizon, window=int(payload.configuration.get("window", 3)), alpha=float(payload.configuration.get("alpha", 0.5)), series_reference=payload.series_reference), policy_context={"forecast_method": payload.method, "uncertainty_disclosed": True})
        case = None
        if outcome.record.review_required:
            case = await create_case(session, scope, actor_user_id=ctx.user_id, case_type="forecast", execution_id=outcome.record.id, reason="Forecast has insufficient history for the requested method", requested_controls=["human_review_of_forecast_quality"])
        await session.commit()
        return {"execution_id": str(outcome.record.id), "status": outcome.record.status, "method": payload.method, "projections": outcome.output.get("projections", []), "data_quality": outcome.output.get("data_quality"), "residual_error": outcome.output.get("residual_error"), "method_metadata": outcome.output.get("method_metadata"), "series_reference": outcome.output.get("series_reference"), "horizon": payload.horizon, "review_required": outcome.record.review_required, "review_state": outcome.record.review_state, "review_case_id": str(case.id) if case else None, "lineage_root_id": str(outcome.record.lineage_root_id) if outcome.record.lineage_root_id else None, "evidence_root_hash": outcome.record.evidence_root_hash, "disclaimer": outcome.output.get("disclaimer")}
    except (KeyError, TypeError, ValueError) as exc:
        raise HierarchyError("Forecast series/configuration is invalid", code="invalid_forecast_input", status_code=422) from exc
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/{execution_id}")
async def get_forecast(execution_id: uuid.UUID, environment_id: uuid.UUID, ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)), session: AsyncSession = Depends(get_session)):
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        row = await session.scalar(select(SpecializedExecutionRecord).where(SpecializedExecutionRecord.id == execution_id, SpecializedExecutionRecord.tenant_id == scope.tenant_id, SpecializedExecutionRecord.organization_id == scope.organization_id, SpecializedExecutionRecord.environment_id == scope.environment_id, SpecializedExecutionRecord.agent_type == "forecasting"))
        if row is None:
            raise NotFound()
        result = row.result or {}
        return {"execution_id": str(row.id), "status": row.status, "method": result.get("method"), "projections": result.get("projections", []), "data_quality": result.get("data_quality"), "residual_error": result.get("residual_error"), "method_metadata": result.get("method_metadata"), "review_required": row.review_required, "review_state": row.review_state, "lineage_root_id": str(row.lineage_root_id) if row.lineage_root_id else None, "evidence_root_hash": row.evidence_root_hash, "disclaimer": result.get("disclaimer")}
    except HierarchyError as exc:
        raise to_http(exc) from None
