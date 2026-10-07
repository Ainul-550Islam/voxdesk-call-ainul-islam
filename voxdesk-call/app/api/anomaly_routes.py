"""Tenant-scoped deterministic anomaly analysis API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.schemas import AnomalyAnalysisRequest
from app.anomaly.service import AnomalyService
from app.anomaly.persistence import persist_anomaly
from app.anomaly.alerting import publish_alerts
from app.review.service import create_case
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.errors import request_id_from
from app.db.session import get_session
from app.specialized_agents.executor import SpecializedExecutionRecord
from app.specialized_agents.service import SpecializedAgentService
from app.api.specialized_agent_routes import _scope, response_for
from app.tenancy.isolation import HierarchyError, NotFound, to_http

router = APIRouter(prefix="/api/anomaly", tags=["anomaly-agent"])


@router.post("/analyze", response_model=dict, status_code=201)
async def analyze(
    payload: AnomalyAnalysisRequest,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, payload.environment_id)
        service = SpecializedAgentService(session, scope)
        analyzer = AnomalyService()
        context = service.context(
            actor_id=ctx.user_id,
            request_id=request_id_from(request),
            trace_id=request.headers.get("X-Trace-ID") or uuid.uuid4().hex,
            agent_type="anomaly",
            agent_version=payload.agent_version,
            model_version_id=payload.model_version_id,
            risk_tier=payload.risk_tier,
        )
        sources = analyzer.source_references(payload.metric or payload.observations[0].metric, payload.observations)
        metric = payload.metric or payload.observations[0].metric
        outcome = await service.execute(
            context,
            idempotency_key=payload.idempotency_key,
            payload={
                "metric": metric,
                "observations": [observation.model_dump(mode="json") for observation in payload.observations],
                "configuration": payload.configuration.model_dump(mode="json"),
            },
            source_references=sources,
            handler=lambda _context, _payload, _sources: analyzer.analyze(
                metric=metric,
                observations=payload.observations,
                configuration=payload.configuration,
            ),
        )
        await persist_anomaly(session, scope, execution_id=outcome.record.id, output=outcome.output)
        alerts = await publish_alerts(session, scope, execution_id=outcome.record.id, results=outcome.output.get("results", []))
        review_case = None
        if outcome.record.review_required:
            review_case = await create_case(
                session, scope, actor_user_id=ctx.user_id, case_type="anomaly",
                execution_id=outcome.record.id, reason="Anomaly results require human review",
                requested_controls=["anomaly_result_review"],
            )
        await session.commit()
        response = response_for(outcome.record, outcome.output).model_dump(mode="json")
        response["review_case_id"] = str(review_case.id) if review_case else None
        response["alert_ids"] = [str(alert.id) for alert in alerts]
        return response
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/{execution_id}", response_model=dict)
async def get_anomaly(
    execution_id: uuid.UUID,
    environment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, environment_id)
        row = await session.scalar(
            select(SpecializedExecutionRecord).where(
                SpecializedExecutionRecord.id == execution_id,
                SpecializedExecutionRecord.tenant_id == scope.tenant_id,
                SpecializedExecutionRecord.organization_id == scope.organization_id,
                SpecializedExecutionRecord.environment_id == scope.environment_id,
                SpecializedExecutionRecord.agent_type == "anomaly",
            )
        )
        if row is None:
            raise NotFound()
        return response_for(row, row.result or {}).model_dump(mode="json")
    except HierarchyError as exc:
        raise to_http(exc) from None
