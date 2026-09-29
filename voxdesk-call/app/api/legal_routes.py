"""Tenant-scoped legal review API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.errors import request_id_from
from app.db.session import get_session
from app.legal.review_engine import ReviewEngine
from app.legal.review_service import persist_and_open_review
from app.legal.schemas import LegalReviewRequest
from app.specialized_agents.service import SpecializedAgentService
from app.specialized_agents.executor import SpecializedExecutionRecord
from app.api.specialized_agent_routes import _scope, response_for
from app.tenancy.isolation import HierarchyError, NotFound, to_http

router = APIRouter(prefix="/api/legal", tags=["legal-agent"])


@router.post("/reviews", response_model=dict, status_code=201)
async def create_review(
    payload: LegalReviewRequest,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, payload.environment_id)
        service = SpecializedAgentService(session, scope)
        engine = ReviewEngine()
        context = service.context(
            actor_id=ctx.user_id,
            request_id=request_id_from(request),
            trace_id=request.headers.get("X-Trace-ID") or uuid.uuid4().hex,
            agent_type="legal",
            agent_version=payload.agent_version,
            model_version_id=payload.model_version_id,
            risk_tier=payload.risk_tier,
            source_references=tuple(chunk.source_reference().content_fingerprint for chunk in payload.chunks),
        )
        chunks = list(payload.chunks)
        sources = engine.source_references(chunks)
        outcome = await service.execute(
            context,
            idempotency_key=payload.idempotency_key,
            payload={
                "document_id": payload.document_id,
                "chunks": [chunk.model_dump(mode="json", exclude={"text"}) for chunk in chunks],
            },
            source_references=sources,
            handler=lambda _context, _payload, _sources: engine.review(
                document_id=payload.document_id, chunks=chunks
            ),
        )
        review_case = await persist_and_open_review(
            session, scope, execution=outcome.record, output=outcome.output, actor_user_id=ctx.user_id
        )
        await session.commit()
        response = response_for(outcome.record, outcome.output).model_dump(mode="json")
        response["review_case_id"] = str(review_case.id) if review_case else None
        return response
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/reviews/{execution_id}", response_model=dict)
async def get_review(
    execution_id: uuid.UUID,
    environment_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_READ)),
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
                SpecializedExecutionRecord.agent_type == "legal",
            )
        )
        if row is None:
            raise NotFound()
        return response_for(row, row.result or {}).model_dump(mode="json")
    except HierarchyError as exc:
        raise to_http(exc) from None
