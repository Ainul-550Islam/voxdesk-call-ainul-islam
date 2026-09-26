"""Conversation intelligence reads.

Call ids are tenant-scoped. Responses reference evidence. They do not return
a recording URL or a copied transcript body.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.qa import sentiment, topics
from app.qa.evidence import load_call
from app.qa.exceptions import QaError
from app.qa.repository import (
    coaching_for_call,
    compliance_for_call,
    evidence_for_call,
)
from app.tenancy.context import reject_client_override
from app.tenancy.isolation import HierarchyError, to_http

router = APIRouter(tags=["conversation-intelligence"])


class SentimentBody(BaseModel):
    label: str
    confidence: int = Field(ge=0, le=100)
    scope: str = "conversation"
    turn_id: uuid.UUID | None = None
    provider: str = ""
    model: str = ""
    prompt_version: str = ""
    tenant_id: uuid.UUID | None = None


class TopicBody(BaseModel):
    label: str
    confidence: int = Field(ge=0, le=100)
    taxonomy_version: str
    rank: str = "secondary"
    turn_ids: list[uuid.UUID] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    tenant_id: uuid.UUID | None = None


def _qa(exc: QaError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.as_dict())


@router.get("/api/conversations/{call_id}/sentiment")
async def get_sentiment(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        body = await sentiment.summary(session, ctx.tenant_id, call_id)
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return body


@router.post("/api/conversations/{call_id}/sentiment", status_code=201)
async def post_sentiment(
    call_id: uuid.UUID,
    payload: SentimentBody,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        reject_client_override(ctx, payload.tenant_id)
        row = await sentiment.record(
            session,
            tenant_id=ctx.tenant_id,
            call_id=call_id,
            label=payload.label,
            confidence=payload.confidence,
            scope=payload.scope,
            turn_id=payload.turn_id,
            provider=payload.provider,
            model=payload.model,
            prompt_version=payload.prompt_version,
        )
        await session.commit()
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.get("/api/conversations/{call_id}/topics")
async def get_topics(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        rows = await topics.for_call(session, ctx.tenant_id, call_id)
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"topics": [row.as_dict() for row in rows]}


@router.post("/api/conversations/{call_id}/topics", status_code=201)
async def post_topic(
    call_id: uuid.UUID,
    payload: TopicBody,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        reject_client_override(ctx, payload.tenant_id)
        row = await topics.record(
            session,
            tenant_id=ctx.tenant_id,
            call_id=call_id,
            label=payload.label,
            confidence=payload.confidence,
            taxonomy_version=payload.taxonomy_version,
            rank=payload.rank,
            turn_ids=payload.turn_ids,
            provider=payload.provider,
            model=payload.model,
        )
        await session.commit()
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.get("/api/conversations/{call_id}/compliance")
async def get_compliance(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        await load_call(session, ctx.tenant_id, call_id)
        rows = await compliance_for_call(session, ctx.tenant_id, call_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"findings": [row.as_dict() for row in rows]}


@router.get("/api/conversations/{call_id}/evidence")
async def get_evidence(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        await load_call(session, ctx.tenant_id, call_id)
        rows = await evidence_for_call(session, ctx.tenant_id, call_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"evidence": [row.as_dict() for row in rows]}


@router.get("/api/conversations/{call_id}/coaching")
async def get_coaching(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        await load_call(session, ctx.tenant_id, call_id)
        rows = await coaching_for_call(session, ctx.tenant_id, call_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"coaching": [row.as_dict() for row in rows]}


@router.get("/api/conversations/{call_id}/intelligence")
async def get_summary(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        call = await load_call(session, ctx.tenant_id, call_id)
        mood = await sentiment.summary(session, ctx.tenant_id, call_id)
        topic_rows = await topics.for_call(session, ctx.tenant_id, call_id)
        findings = await compliance_for_call(session, ctx.tenant_id, call_id)
        signals = await coaching_for_call(session, ctx.tenant_id, call_id)
        evidence_rows = await evidence_for_call(session, ctx.tenant_id, call_id)
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {
        "call_id": str(call.id),
        "recording_url": None,
        "transcript_copied": False,
        "sentiment": {key: mood[key] for key in ("label", "confidence", "source", "certainty")},
        "topics": [row.as_dict() for row in topic_rows],
        "compliance": [row.as_dict() for row in findings],
        "coaching": [row.as_dict() for row in signals],
        "evidence": [row.as_dict() for row in evidence_rows],
    }
