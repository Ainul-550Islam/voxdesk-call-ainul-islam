"""Authenticated tenant-scoped insight analysis and execution retrieval."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.errors import request_id_from
from app.db.session import get_session
from app.governance.context import resolve_scope
from app.insight.service import InsightService
from app.review.service import create_case
from app.specialized_agents.executor import SpecializedExecutionRecord
from app.specialized_agents.service import SpecializedAgentService
from app.tenancy.isolation import BoundaryDenied, HierarchyError, NotFound, to_http

router = APIRouter(prefix="/api/insight", tags=["insight"])


class InsightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: uuid.UUID
    model_version_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    metrics: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    source_references: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    knowledge_document_ids: list[str] = Field(default_factory=list, max_length=100)
    question: str = Field(min_length=1, max_length=4000)
    time_range: dict[str, str] = Field(default_factory=dict)
    analysis_type: str = Field(default="summary", min_length=1, max_length=64)
    risk_tier: str = Field(default="high", min_length=1, max_length=24)
    agent_version: str = Field(default="1.0.0", min_length=1, max_length=100)


@router.post("/analyze", status_code=201)
async def analyze_insight(payload: InsightRequest, request: Request, ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)), session: AsyncSession = Depends(get_session)):
    try:
        scope = await resolve_scope(session, ctx, payload.environment_id)
        if scope.environment_id is None:
            raise BoundaryDenied()
        service = SpecializedAgentService(session, scope)
        context = service.context(actor_id=ctx.user_id, request_id=request_id_from(request), trace_id=request.headers.get("X-Trace-ID") or uuid.uuid4().hex, agent_type="insight", agent_version=payload.agent_version, model_version_id=payload.model_version_id, risk_tier=payload.risk_tier)
        insight = InsightService()
        request_data = payload.model_dump(mode="json", exclude={"environment_id", "model_version_id", "idempotency_key", "knowledge_document_ids"})
        resolved_chunks: list[Any] = []

        async def resolve_sources(_execution_context, _data):
            nonlocal resolved_chunks
            resolved, resolved_chunks = await insight.resolve_sources(
                session=session, scope=scope, question=payload.question,
                document_ids=payload.knowledge_document_ids or None,
                supplied_references=payload.source_references,
            )
            return resolved.references

        async def execute_insight(execution_context, _data, admitted_sources):
            return await insight.analyze(
                session=session, scope=scope, context=execution_context,
                model_version_id=payload.model_version_id, metrics=payload.metrics,
                question=payload.question, sources=admitted_sources,
                retrieved_chunks=resolved_chunks, time_range=payload.time_range,
                analysis_type=payload.analysis_type,
            )

        outcome = await service.execute(
            context, idempotency_key=payload.idempotency_key,
            payload={"metrics": payload.metrics, "source_references": payload.source_references,
                     "knowledge_document_ids": payload.knowledge_document_ids,
                     "question_fingerprint": __import__("app.governance.hashing", fromlist=["sha256_hex"]).sha256_hex(payload.question),
                     "analysis_type": payload.analysis_type},
            handler=execute_insight, source_resolver=resolve_sources,
            policy_context={"source_request_count": len(payload.source_references) + len(payload.knowledge_document_ids), "knowledge_backed": bool(payload.knowledge_document_ids)},
        )
        case = None
        if outcome.record.review_required:
            case = await create_case(session, scope, actor_user_id=ctx.user_id, case_type="insight", execution_id=outcome.record.id, reason="Insight interpretation or recommendation requires human review", requested_controls=["human_review_of_model_interpretation"])
        await session.commit()
        return {"execution_id": str(outcome.record.id), "status": outcome.record.status, "review_required": outcome.record.review_required, "review_state": outcome.record.review_state, "review_case_id": str(case.id) if case else None, "result": outcome.output, "lineage_root_id": str(outcome.record.lineage_root_id) if outcome.record.lineage_root_id else None, "evidence_root_hash": outcome.record.evidence_root_hash, "request": {"analysis_type": request_data["analysis_type"]}}
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/{execution_id}")
async def get_insight(execution_id: uuid.UUID, environment_id: uuid.UUID, ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)), session: AsyncSession = Depends(get_session)):
    try:
        scope = await resolve_scope(session, ctx, environment_id)
        row = await session.scalar(select(SpecializedExecutionRecord).where(SpecializedExecutionRecord.id == execution_id, SpecializedExecutionRecord.tenant_id == scope.tenant_id, SpecializedExecutionRecord.organization_id == scope.organization_id, SpecializedExecutionRecord.environment_id == scope.environment_id, SpecializedExecutionRecord.agent_type == "insight"))
        if row is None:
            raise NotFound()
        return {"execution_id": str(row.id), "status": row.status, "review_required": row.review_required, "review_state": row.review_state, "result": row.result or {}, "lineage_root_id": str(row.lineage_root_id) if row.lineage_root_id else None, "evidence_root_hash": row.evidence_root_hash}
    except HierarchyError as exc:
        raise to_http(exc) from None
