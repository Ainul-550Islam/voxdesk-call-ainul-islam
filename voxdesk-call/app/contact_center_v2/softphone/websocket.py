"""Authenticated human-agent softphone signaling WebSocket."""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

router = APIRouter(prefix="/api/cc2/softphone", tags=["contact-center-v2-softphone"])


@router.websocket("/signaling")
async def signaling(websocket: WebSocket):
    """One-ticket signaling with validated media relay and durable controls."""
    from app.auth.service import record_audit
    from app.contact_center_v2.errors import ContactCenterV2Error
    from app.contact_center_v2.softphone.signaling import (
        SignalingPeer,
        consume_websocket_ticket,
        event_envelope,
        handle_signaling_message,
        signaling_hub,
    )
    from app.db.contact_center_v2_models import SoftphoneCall, SoftphoneSession
    from app.db.models import AuditAction
    from app.db.session import get_sessionmaker

    ticket = websocket.query_params.get("ticket", "")
    try:
        async with get_sessionmaker()() as db:
            registration = await consume_websocket_ticket(db, ticket)
            tenant_id, user_id, session_id = registration.tenant_id, registration.user_id, registration.id
            environment_id = registration.environment_id
            await record_audit(
                db, action=AuditAction.GOVERNANCE_EVENT,
                event_type="cc2.softphone.signaling_connected", tenant_id=tenant_id,
                environment_id=environment_id, actor_user_id=user_id, actor_type="human",
                resource_type="softphone_session", resource_id=session_id,
                detail={"session_id": str(session_id)}, commit=False,
            )
            await db.commit()
    except Exception:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    peer = SignalingPeer(
        tenant_id=tenant_id,
        environment_id=environment_id,
        user_id=user_id,
        session_id=session_id,
        send=websocket.send_json,
    )
    await websocket.send_json(
        event_envelope(event_type="session.heartbeat", payload={"session_id": str(session_id)})
    )
    versions: dict[uuid.UUID, str] = {}
    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                if len(raw) > 65_536:
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
                try:
                    async with get_sessionmaker()() as db:
                        registration = await db.get(SoftphoneSession, session_id)
                        if (
                            registration is None
                            or registration.tenant_id != tenant_id
                            or registration.user_id != user_id
                            or registration.status != "active"
                        ):
                            await websocket.close(code=1008)
                            return
                        response = await handle_signaling_message(
                            db, signaling_hub, peer, registration, message
                        )
                        await db.commit()
                    if response is not None:
                        await websocket.send_json(response)
                except ContactCenterV2Error as exc:
                    await websocket.send_json(
                        event_envelope(
                            event_type="error",
                            call_id=message.get("call_id") if isinstance(message.get("call_id"), uuid.UUID) else None,
                            payload={"code": exc.code, "message": exc.message},
                        )
                    )
            except asyncio.TimeoutError:
                async with get_sessionmaker()() as db:
                    rows = (
                        await db.execute(
                            select(SoftphoneCall).where(
                                SoftphoneCall.tenant_id == tenant_id,
                                SoftphoneCall.agent_user_id == user_id,
                            ).order_by(SoftphoneCall.updated_at.desc()).limit(50)
                        )
                    ).scalars().all()
                    for row in reversed(rows):
                        version = row.updated_at.isoformat()
                        if versions.get(row.id) == version:
                            continue
                        versions[row.id] = version
                        await websocket.send_json(event_envelope(
                            event_type="call.updated", call_id=row.id,
                            payload={"call": row.as_dict()},
                        ))
    except WebSocketDisconnect:
        return
    finally:
        signaling_hub.disconnect(peer)
        async with get_sessionmaker()() as db:
            await record_audit(
                db, action=AuditAction.GOVERNANCE_EVENT,
                event_type="cc2.softphone.signaling_disconnected", tenant_id=tenant_id,
                environment_id=environment_id, actor_user_id=user_id, actor_type="human",
                resource_type="softphone_session", resource_id=session_id,
                detail={"session_id": str(session_id)}, commit=False,
            )
            await db.commit()


__all__ = ["router", "signaling"]
