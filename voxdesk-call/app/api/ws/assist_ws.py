"""Authenticated live redacted transcript, suggestion, and alert stream."""
from __future__ import annotations

import asyncio
import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.auth.service import record_audit
from app.contact_center_v2.errors import NotFound, Validation
from app.contact_center_v2.http import audit, can_supervise, endpoint_errors
from app.contact_center_v2.settings import settings
from app.contact_center_v2.realtime import (
    active_realtime_user,
    issue_realtime_ticket,
    verify_realtime_ticket,
)
from app.db.contact_center_v2_models import (
    AssistAlert,
    AssistSession,
    AssistSuggestion,
    AssistTurn,
)
from app.db.models import AuditAction
from app.db.session import get_session, get_sessionmaker

router = APIRouter(tags=["contact-center-v2-assist-realtime"])


async def _assist_for_call(
    session: AsyncSession, *, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> AssistSession:
    row = (
        await session.execute(
            select(AssistSession)
            .where(AssistSession.tenant_id == tenant_id, AssistSession.call_id == call_id)
            .order_by(AssistSession.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        raise NotFound("assist session")
    return row


@router.get("/api/cc2/assist/calls/{call_id}/stream-ticket")
@endpoint_errors
async def assist_stream_ticket(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.ASSIST_READ)),
    session: AsyncSession = Depends(get_session),
):
    row = await _assist_for_call(session, tenant_id=ctx.tenant_id, call_id=call_id)
    if ctx.environment_id is None:
        raise Validation("An active environment is required")
    if row.environment_id != ctx.environment_id or (
        row.agent_user_id != ctx.user_id and not can_supervise(ctx)
    ):
        raise NotFound("assist session")
    ticket = issue_realtime_ticket(
        tenant_id=ctx.tenant_id,
        environment_id=row.environment_id,
        user_id=ctx.user_id,
        token_version=ctx.user.token_version,
        channel="assist",
        call_id=call_id,
        supervisor=can_supervise(ctx),
    )
    await audit(
        session, ctx, event_type="cc2.assist.stream_ticket", resource_type="assist_session",
        resource_id=row.id, environment_id=row.environment_id,
    )
    await session.commit()
    return {
        "ticket": ticket,
        "expires_in": 300,
        "websocket_url": f"/ws/cc2/assist/{call_id}",
    }


async def _snapshot(
    session: AsyncSession, *, tenant_id: uuid.UUID, call_id: uuid.UUID
) -> dict:
    turns = (
        (
            await session.execute(
                select(AssistTurn)
                .where(AssistTurn.tenant_id == tenant_id, AssistTurn.call_id == call_id)
                .order_by(AssistTurn.seq, AssistTurn.speaker)
                .limit(200)
            )
        )
        .scalars()
        .all()
    )
    suggestions = (
        (
            await session.execute(
                select(AssistSuggestion)
                .where(AssistSuggestion.tenant_id == tenant_id, AssistSuggestion.call_id == call_id)
                .order_by(AssistSuggestion.created_at)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    alerts = (
        (
            await session.execute(
                select(AssistAlert)
                .where(AssistAlert.tenant_id == tenant_id, AssistAlert.call_id == call_id)
                .order_by(AssistAlert.created_at)
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return {
        "turns": [
            {
                "id": str(row.id), "seq": row.seq, "speaker": row.speaker,
                "text_redacted": row.text_redacted, "processing_status": row.processing_status,
                "sentiment_label": row.sentiment_label,
                "sentiment_method": row.sentiment_method,
                "created_at": row.created_at.isoformat(),
            }
            for row in turns
        ],
        "suggestions": [row.as_dict() for row in suggestions],
        "alerts": [row.as_dict() for row in alerts],
    }


@router.websocket("/ws/cc2/assist/{call_id}")
async def assist_stream(websocket: WebSocket, call_id: uuid.UUID):
    environment_id = tenant_id = user_id = session_id = None
    try:
        principal = verify_realtime_ticket(
            websocket.query_params.get("ticket"), channel="assist", call_id=call_id
        )
        async with get_sessionmaker()() as db:
            _, supervisor = await active_realtime_user(db, principal)
            row = await _assist_for_call(db, tenant_id=principal.tenant_id, call_id=call_id)
            if row.environment_id != principal.environment_id or (
                row.agent_user_id != principal.user_id and not supervisor
            ):
                raise NotFound("assist session")
            tenant_id, environment_id, user_id, session_id = (
                principal.tenant_id, principal.environment_id, principal.user_id, row.id,
            )
            await record_audit(
                db, action=AuditAction.GOVERNANCE_EVENT,
                event_type="cc2.assist.stream_connected", tenant_id=tenant_id,
                environment_id=environment_id, actor_user_id=user_id, actor_type="human",
                resource_type="assist_session", resource_id=session_id,
                detail={"call_id": str(call_id)}, commit=False,
            )
            await db.commit()
    except Exception:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    previous = ""
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=settings.realtime_snapshot_interval_ms / 1000)
                if len(raw) > 4096:
                    await websocket.close(code=1009)
                    return
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.close(code=1007)
                    return
                if not isinstance(message, dict):
                    await websocket.close(code=1007)
                    return
                await websocket.send_json(
                    {"type": "pong" if message.get("type") in {"ping", "heartbeat"} else "error"}
                )
            except asyncio.TimeoutError:
                async with get_sessionmaker()() as db:
                    snapshot = await _snapshot(db, tenant_id=tenant_id, call_id=call_id)
                digest = hashlib.sha256(
                    json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                if digest != previous:
                    previous = digest
                    await websocket.send_json({
                        "type": "assist.snapshot", "call_id": str(call_id), "payload": snapshot,
                    })
    except WebSocketDisconnect:
        return
    finally:
        async with get_sessionmaker()() as db:
            await record_audit(
                db, action=AuditAction.GOVERNANCE_EVENT,
                event_type="cc2.assist.stream_disconnected", tenant_id=tenant_id,
                environment_id=environment_id, actor_user_id=user_id, actor_type="human",
                resource_type="assist_session", resource_id=session_id,
                detail={"call_id": str(call_id)}, commit=False,
            )
            await db.commit()


__all__ = ["assist_stream", "assist_stream_ticket", "router"]
