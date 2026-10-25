"""Authenticated live supervisor wallboard stream."""
from __future__ import annotations

import asyncio
import hashlib
import json

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.auth.service import record_audit
from app.contact_center_v2.errors import Validation
from app.contact_center_v2.http import audit, endpoint_errors
from app.contact_center_v2.settings import settings
from app.contact_center_v2.realtime import (
    active_realtime_user,
    issue_realtime_ticket,
    supervisor_snapshot,
    verify_realtime_ticket,
    without_stream_clock,
)
from app.db.models import AuditAction
from app.db.session import get_session, get_sessionmaker

router = APIRouter(tags=["contact-center-v2-supervisor-realtime"])


@router.get("/api/cc2/supervisor/stream-ticket")
@endpoint_errors
async def supervisor_stream_ticket(
    ctx: TenantContext = Depends(require_permission(Permission.SUPERVISOR_READ)),
    session: AsyncSession = Depends(get_session),
):
    if ctx.environment_id is None:
        raise Validation("An active environment is required")
    ticket = issue_realtime_ticket(
        tenant_id=ctx.tenant_id,
        environment_id=ctx.environment_id,
        user_id=ctx.user_id,
        token_version=ctx.user.token_version,
        channel="supervisor",
        supervisor=ctx.can(Permission.SUPERVISOR_READ),
    )
    await audit(
        session, ctx, event_type="cc2.supervisor.stream_ticket",
        resource_type="supervisor_wallboard", environment_id=ctx.environment_id,
    )
    await session.commit()
    return {
        "ticket": ticket,
        "expires_in": 300,
        "websocket_url": "/ws/cc2/supervisor",
    }


@router.websocket("/ws/cc2/supervisor")
async def supervisor_stream(websocket: WebSocket):
    environment_id = tenant_id = user_id = None
    try:
        principal = verify_realtime_ticket(
            websocket.query_params.get("ticket"), channel="supervisor"
        )
        async with get_sessionmaker()() as db:
            await active_realtime_user(db, principal)
            tenant_id, environment_id, user_id = (
                principal.tenant_id, principal.environment_id, principal.user_id,
            )
            await record_audit(
                db, action=AuditAction.GOVERNANCE_EVENT,
                event_type="cc2.supervisor.stream_connected", tenant_id=tenant_id,
                environment_id=environment_id, actor_user_id=user_id, actor_type="human",
                resource_type="supervisor_wallboard", detail={}, commit=False,
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
                    await active_realtime_user(db, principal)
                    snapshot = await supervisor_snapshot(db, tenant_id=tenant_id)
                comparable = without_stream_clock(snapshot)
                digest = hashlib.sha256(
                    json.dumps(comparable, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                if digest != previous:
                    previous = digest
                    await websocket.send_json({"type": "wallboard.snapshot", "payload": snapshot})
    except WebSocketDisconnect:
        return
    finally:
        async with get_sessionmaker()() as db:
            await record_audit(
                db, action=AuditAction.GOVERNANCE_EVENT,
                event_type="cc2.supervisor.stream_disconnected", tenant_id=tenant_id,
                environment_id=environment_id, actor_user_id=user_id, actor_type="human",
                resource_type="supervisor_wallboard", detail={}, commit=False,
            )
            await db.commit()


__all__ = ["router", "supervisor_stream", "supervisor_stream_ticket"]
