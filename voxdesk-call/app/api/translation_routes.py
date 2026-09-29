"""Tenant-scoped governed translation API."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.core.errors import request_id_from
from app.db.session import get_session
from app.specialized_agents.executor import SpecializedExecutionRecord
from app.specialized_agents.service import SpecializedAgentService
from app.translation.engine import TranslationEngine
from app.translation.glossary import Glossary
from app.translation.schemas import GlossaryEntryInput, TranslationJobRequest
from app.translation.job_service import get_or_create_glossary, record_translation_result
from app.review.service import create_case
from app.api.specialized_agent_routes import _scope, response_for
from app.tenancy.isolation import HierarchyError, NotFound, to_http

router = APIRouter(prefix="/api/translation", tags=["translation-agent"])


@router.post("/jobs", response_model=dict, status_code=201)
async def create_translation_job(
    payload: TranslationJobRequest,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.ANALYTICS_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        scope = await _scope(session, ctx, payload.environment_id)
        service = SpecializedAgentService(session, scope)
        engine = TranslationEngine(session=session)
        glossary_inputs = [entry.model_dump(mode="json") for entry in payload.glossary]
        glossary_row = await get_or_create_glossary(
            session, scope, version=payload.glossary_version, entries=glossary_inputs
        )
        glossary = Glossary.from_inputs(
            payload.glossary_version,
            [GlossaryEntryInput.model_validate(entry) for entry in glossary_row.entries],
        )
        context = service.context(
            actor_id=ctx.user_id,
            request_id=request_id_from(request),
            trace_id=request.headers.get("X-Trace-ID") or uuid.uuid4().hex,
            agent_type="translation",
            agent_version=payload.agent_version,
            model_version_id=payload.model_version_id,
            risk_tier=payload.risk_tier,
            language=payload.target_language,
        )
        outcome = await service.execute(
            context,
            idempotency_key=payload.idempotency_key,
            payload={
                "source_language": payload.source_language,
                "target_language": payload.target_language,
                "glossary_version": payload.glossary_version,
                "segments": [segment.model_dump(mode="json", exclude={"translated_text"}) for segment in payload.segments],
            },
            source_references=[
                {
                    **reference.model_dump(mode="json"),
                    "content_fingerprint": reference.content_fingerprint.lower(),
                }
                for reference in payload.source_references
            ],
            handler=lambda _context, _payload, _sources: engine.translate(
                source_language=payload.source_language,
                target_language=payload.target_language,
                segments=payload.segments,
                glossary=glossary,
                context=_context,
            ),
        )
        await record_translation_result(
            session, scope, execution_id=outcome.record.id, output=outcome.output,
            idempotency_key=payload.idempotency_key, source_language=payload.source_language,
            target_language=payload.target_language, glossary_version=payload.glossary_version,
            glossary_entries=glossary_inputs,
            source_lengths={segment.segment_id: len(segment.source_text) for segment in payload.segments},
        )
        review_case = None
        if outcome.record.review_required:
            review_case = await create_case(
                session, scope, actor_user_id=ctx.user_id, case_type="translation",
                execution_id=outcome.record.id, reason="Translation quality checks require human review",
                requested_controls=["translation_quality_review"],
            )
        await session.commit()
        response = response_for(outcome.record, outcome.output).model_dump(mode="json")
        response["review_case_id"] = str(review_case.id) if review_case else None
        return response
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/jobs/{execution_id}", response_model=dict)
async def get_translation_job(
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
                SpecializedExecutionRecord.agent_type == "translation",
            )
        )
        if row is None:
            raise NotFound()
        return response_for(row, row.result or {}).model_dump(mode="json")
    except HierarchyError as exc:
        raise to_http(exc) from None
