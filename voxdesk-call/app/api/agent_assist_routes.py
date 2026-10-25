"""Live agent-assist sessions, grounded suggestions, alerts, and feedback."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.contact_center_v2.assist import (
    adherence_for_call,
    alerts_for_call,
    end_assist,
    generate_wrapup,
    ingest_transcript,
    list_compliance_scripts,
    record_feedback,
    start_assist,
    suggestions_for_call,
    turns_for_call,
    update_compliance_script,
    upsert_script,
)
from app.contact_center_v2.errors import NotFound, PermissionDenied
from app.contact_center_v2.http import audit, can_supervise, endpoint_errors
from app.contact_center_v2.repository import owned
from app.contact_center_v2.schemas import (
    AssistFeedbackBody,
    AssistStartBody,
    AssistTranscriptBody,
    ComplianceScriptBody,
    ComplianceScriptUpdateBody,
)
from app.db.contact_center_v2_models import AssistSession, AssistSuggestion, SoftphoneCall
from app.db.session import get_session

router = APIRouter(prefix="/api/cc2/assist", tags=["contact-center-v2-assist"])


async def _authorize_call(session: AsyncSession, ctx: TenantContext, call_id: uuid.UUID, *, require_softphone: bool = False) -> None:
    if can_supervise(ctx):
        return
    if require_softphone:
        owned_call = (
            await session.execute(
                select(SoftphoneCall.id).where(
                    SoftphoneCall.tenant_id == ctx.tenant_id,
                    SoftphoneCall.call_id == call_id,
                    SoftphoneCall.agent_user_id == ctx.user_id,
                )
            )
    ).first()
        if owned_call is None:
            raise PermissionDenied("Assist requires the agent's own softphone call")
        return
    assist = (
        await session.execute(
            select(AssistSession).where(
                AssistSession.tenant_id == ctx.tenant_id,
                AssistSession.call_id == call_id,
            )
        )
    ).scalar_one_or_none()
    if assist is None:
        raise NotFound("assist session")
    if assist.agent_user_id != ctx.user_id:
        raise PermissionDenied("Another agent owns this assist session")


@router.post("/calls/{call_id}/start")
@endpoint_errors
async def start(
    call_id: uuid.UUID,
    payload: AssistStartBody,
    ctx: TenantContext = Depends(require_permission(Permission.ASSIST_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    agent_id = payload.agent_user_id or ctx.user_id
    if agent_id != ctx.user_id and not can_supervise(ctx):
        raise PermissionDenied("Only a supervisor can start assist for another agent")
    await _authorize_call(session, ctx, call_id, require_softphone=True)
    row, created = await start_assist(session, tenant_id=ctx.tenant_id, call_id=call_id, agent_user_id=agent_id)
    await audit(session, ctx, event_type="cc2.assist.started", resource_type="assist_session", resource_id=row.id, detail={"created": created})
    await session.commit()
    return {"session": {"id": str(row.id), "call_id": str(row.call_id), "agent_user_id": str(row.agent_user_id), "status": row.status, "last_seq": row.last_seq, "started_at": row.started_at.isoformat()}, "created": created}


@router.post("/calls/{call_id}/stop")
@endpoint_errors
async def stop(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.ASSIST_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    await _authorize_call(session, ctx, call_id)
    assist = (
        await session.execute(
            select(AssistSession).where(
                AssistSession.tenant_id == ctx.tenant_id,
                AssistSession.call_id == call_id,
                AssistSession.status == "active",
            )
        )
    ).scalar_one_or_none()
    if assist is None:
        raise NotFound("active assist session")
    row = await end_assist(
        session,
        tenant_id=ctx.tenant_id,
        call_id=call_id,
        agent_user_id=assist.agent_user_id,
    )
    await audit(session, ctx, event_type="cc2.assist.stopped", resource_type="assist_session", resource_id=row.id)
    await session.commit()
    return {"session": {"id": str(row.id), "status": row.status, "ended_at": row.ended_at.isoformat() if row.ended_at else None}}


@router.post("/calls/{call_id}/turns", status_code=201)
@endpoint_errors
async def ingest_turn(
    call_id: uuid.UUID,
    payload: AssistTranscriptBody,
    ctx: TenantContext = Depends(require_permission(Permission.ASSIST_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    await _authorize_call(session, ctx, call_id)
    row = await ingest_transcript(session, call_id=call_id, speaker=payload.speaker, text=payload.text, seq=payload.seq, is_final=payload.is_final)
    await audit(session, ctx, event_type="cc2.assist.turn_ingested", resource_type="assist_session", resource_id=call_id, detail={"seq": payload.seq, "speaker": payload.speaker, "stored": row is not None})
    await session.commit()
    return {"turn": None if row is None else {"id": str(row.id), "seq": row.seq, "speaker": row.speaker, "text_redacted": row.text_redacted, "processing_status": row.processing_status}}


@router.get("/calls/{call_id}/suggestions")
@endpoint_errors
async def suggestions(
    call_id: uuid.UUID,
    after_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.ASSIST_READ)),
    session: AsyncSession = Depends(get_session),
):
    await _authorize_call(session, ctx, call_id)
    rows = await suggestions_for_call(session, tenant_id=ctx.tenant_id, call_id=call_id, after_id=after_id, limit=limit)
    await audit(session, ctx, event_type="cc2.assist.suggestions_read", resource_type="assist_session", resource_id=call_id, detail={"count": len(rows)})
    await session.commit()
    return {"suggestions": [row.as_dict() for row in rows]}


@router.get("/calls/{call_id}/turns")
@endpoint_errors
async def turns(
    call_id: uuid.UUID,
    limit: int = Query(default=100, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.ASSIST_READ)),
    session: AsyncSession = Depends(get_session),
):
    await _authorize_call(session, ctx, call_id)
    rows = await turns_for_call(session, tenant_id=ctx.tenant_id, call_id=call_id, limit=limit)
    await audit(session, ctx, event_type="cc2.assist.turns_read", resource_type="assist_session", resource_id=call_id, detail={"count": len(rows)})
    await session.commit()
    return {"turns": [{"id": str(row.id), "seq": row.seq, "speaker": row.speaker, "text_redacted": row.text_redacted, "processing_status": row.processing_status, "sentiment_score": str(row.sentiment_score) if row.sentiment_score is not None else None, "sentiment_label": row.sentiment_label, "sentiment_method": row.sentiment_method, "created_at": row.created_at.isoformat()} for row in rows]}


@router.get("/calls/{call_id}/alerts")
@endpoint_errors
async def alerts(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.ASSIST_READ)),
    session: AsyncSession = Depends(get_session),
):
    await _authorize_call(session, ctx, call_id)
    rows = await alerts_for_call(session, tenant_id=ctx.tenant_id, call_id=call_id)
    await audit(session, ctx, event_type="cc2.assist.alerts_read", resource_type="assist_session", resource_id=call_id, detail={"count": len(rows)})
    await session.commit()
    return {"alerts": [row.as_dict() for row in rows]}


@router.post("/calls/{call_id}/wrapup")
@endpoint_errors
async def create_wrapup(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.ASSIST_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    await _authorize_call(session, ctx, call_id)
    assist = (
        await session.execute(
            select(AssistSession).where(
                AssistSession.tenant_id == ctx.tenant_id,
                AssistSession.call_id == call_id,
            )
        )
    ).scalar_one_or_none()
    if assist is None:
        raise NotFound("assist session")
    row = await generate_wrapup(session, tenant_id=ctx.tenant_id, call_id=call_id, agent_user_id=assist.agent_user_id)
    await audit(session, ctx, event_type="cc2.assist.wrapup_generated", resource_type="wrapup_draft", resource_id=row.id, detail={"method": row.generation_method})
    await session.commit()
    return {"wrapup": {"id": str(row.id), "call_id": str(row.call_id), "status": row.status, "summary_redacted": row.summary_redacted, "generation_method": row.generation_method, "deadline_at": row.deadline_at.isoformat()}}


@router.get("/calls/{call_id}/adherence")
@endpoint_errors
async def adherence(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.ASSIST_READ)),
    session: AsyncSession = Depends(get_session),
):
    await _authorize_call(session, ctx, call_id)
    rows = await adherence_for_call(session, tenant_id=ctx.tenant_id, call_id=call_id)
    await audit(session, ctx, event_type="cc2.assist.adherence_read", resource_type="compliance_adherence", resource_id=call_id, detail={"count": len(rows)})
    await session.commit()
    return {"adherence": [{"id": str(row.id), "script_id": str(row.script_id), "status": row.status, "matched_phrases": list(row.matched_phrases or []), "missed_phrases": list(row.missed_phrases or []), "violated_phrases": list(row.violated_phrases or []), "updated_at": row.updated_at.isoformat()} for row in rows]}


def _script_payload(row) -> dict:
    return {
        "id": str(row.id), "environment_id": str(row.environment_id),
        "name": row.name, "version": row.version, "jurisdiction": row.jurisdiction,
        "script_text": row.script_text,
        "required_phrases": list(row.required_phrases or []),
        "forbidden_phrases": list(row.forbidden_phrases or []), "active": row.active,
    }


@router.get("/compliance-scripts")
@endpoint_errors
async def get_compliance_scripts(
    environment_id: uuid.UUID | None = Query(default=None),
    active: bool = Query(default=True),
    ctx: TenantContext = Depends(require_permission(Permission.ASSIST_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await list_compliance_scripts(session, tenant_id=ctx.tenant_id, environment_id=environment_id, active_only=active)
    await audit(session, ctx, event_type="cc2.assist.scripts_read", resource_type="compliance_script", detail={"count": len(rows), "active": active})
    await session.commit()
    return {"scripts": [_script_payload(row) for row in rows]}


@router.put("/compliance-scripts/{script_id}")
@endpoint_errors
async def put_compliance_script(
    script_id: uuid.UUID,
    payload: ComplianceScriptUpdateBody,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row, changed = await update_compliance_script(session, tenant_id=ctx.tenant_id, script_id=script_id, payload=payload.model_dump(exclude_unset=True))
    await audit(session, ctx, event_type="cc2.assist.script_updated", resource_type="compliance_script", resource_id=row.id, environment_id=row.environment_id, detail={"version": row.version, "changed": changed, "active": row.active})
    await session.commit()
    return {"script": _script_payload(row), "changed": changed}


@router.post("/scripts", status_code=201)
@endpoint_errors
async def create_script(
    payload: ComplianceScriptBody,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    row = await upsert_script(session, tenant_id=ctx.tenant_id, created_by=ctx.user_id, **payload.model_dump())
    await audit(session, ctx, event_type="cc2.assist.script_created", resource_type="compliance_script", resource_id=row.id, environment_id=row.environment_id, detail={"name": row.name, "version": row.version})
    await session.commit()
    return {"script": {"id": str(row.id), "name": row.name, "version": row.version, "jurisdiction": row.jurisdiction, "required_phrases": list(row.required_phrases or []), "forbidden_phrases": list(row.forbidden_phrases or []), "active": row.active}}


@router.post("/suggestions/{suggestion_id}/feedback")
@endpoint_errors
async def feedback(
    suggestion_id: uuid.UUID,
    payload: AssistFeedbackBody,
    ctx: TenantContext = Depends(require_permission(Permission.ASSIST_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    existing = await owned(session, AssistSuggestion, suggestion_id, ctx.tenant_id)
    await _authorize_call(session, ctx, existing.call_id)
    row = await record_feedback(session, tenant_id=ctx.tenant_id, suggestion_id=suggestion_id, actor_user_id=ctx.user_id, feedback=payload.feedback, note=payload.note)
    await audit(session, ctx, event_type="cc2.assist.feedback", resource_type="assist_suggestion", resource_id=row.id, detail={"feedback": row.feedback})
    await session.commit()
    return {"suggestion": row.as_dict()}


__all__ = ["router"]
