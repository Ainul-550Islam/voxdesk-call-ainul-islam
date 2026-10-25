"""Human-agent WebRTC softphone APIs; provider webhooks remain under /telephony."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.contact_center_v2.http import audit, can_supervise, endpoint_errors
from app.contact_center_v2.occupancy import active_softphone_session
from app.contact_center_v2.schemas import (
    SoftphoneCallBody,
    SoftphoneControlBody,
    SoftphoneRegisterBody,
    TransferBody,
)
from app.contact_center_v2.softphone import (
    call_payload,
    cancel_consultation,
    complete_warm_transfer,
    control_call,
    create_outbound_call,
    end_call,
    get_call,
    heartbeat_session,
    list_calls,
    list_devices,
    register_session,
    revoke_device,
    revoke_session,
    session_credentials,
    session_payload,
    set_recording_pause,
    transfer_call,
)
from app.db.session import get_session

router = APIRouter(prefix="/api/cc2/softphone", tags=["contact-center-v2-softphone"])


@router.post("/sessions")
@endpoint_errors
async def register(
    payload: SoftphoneRegisterBody,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=256),
    ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)),
    session: AsyncSession = Depends(get_session),
):
    row = await register_session(
        session, ctx, environment_id=payload.environment_id,
        device_label=payload.device_label, idempotency_key=idempotency_key,
        device_fingerprint=payload.device_fingerprint, device_kind=payload.device_kind,
    )
    from app.contact_center_v2.softphone.signaling import issue_websocket_ticket

    await issue_websocket_ticket(session, row)
    await audit(session, ctx, event_type="cc2.softphone.registered", resource_type="softphone_session", resource_id=row.id, environment_id=row.environment_id)
    await session.commit()
    return {"session": session_payload(row)}


@router.get("/sessions/current")
@endpoint_errors
async def current(
    ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)),
    session: AsyncSession = Depends(get_session),
):
    row = await active_softphone_session(session, ctx.tenant_id, ctx.user_id)
    if row is None:
        await audit(session, ctx, event_type="cc2.softphone.current_absent", resource_type="softphone_session")
        await session.commit()
        return {"session": None}
    await audit(session, ctx, event_type="cc2.softphone.current_read", resource_type="softphone_session", resource_id=row.id)
    await session.commit()
    return {"session": session_payload(row)}


@router.post("/sessions/{session_id}/heartbeat")
@endpoint_errors
async def heartbeat(
    session_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)),
    session: AsyncSession = Depends(get_session),
):
    row = await heartbeat_session(session, ctx, session_id)
    await audit(session, ctx, event_type="cc2.softphone.heartbeat", resource_type="softphone_session", resource_id=row.id)
    await session.commit()
    return {"session": session_payload(row)}


@router.delete("/sessions/{session_id}")
@endpoint_errors
async def revoke(
    session_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)),
    session: AsyncSession = Depends(get_session),
):
    row = await revoke_session(session, ctx, session_id)
    await audit(session, ctx, event_type="cc2.softphone.revoked", resource_type="softphone_session", resource_id=row.id)
    await session.commit()
    return {"session": row.as_dict()}


@router.post("/sessions/{session_id}/token")
@endpoint_errors
async def refresh_token(
    session_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)),
    session: AsyncSession = Depends(get_session),
):
    from app.contact_center_v2.softphone.service import _owned_session
    from app.contact_center_v2.softphone.signaling import issue_websocket_ticket

    row = await _owned_session(session, ctx, session_id)
    await issue_websocket_ticket(session, row)
    await audit(session, ctx, event_type="cc2.softphone.token_refreshed", resource_type="softphone_session", resource_id=row.id)
    await session.commit()
    payload = session_credentials(row)
    ticket = getattr(row, "_cc2_websocket_ticket", None)
    if ticket:
        payload["websocket_ticket"] = ticket
        payload["signaling_url"] = "/api/cc2/softphone/signaling"
    return payload


@router.get("/devices")
@endpoint_errors
async def devices(
    ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)),
    session: AsyncSession = Depends(get_session),
):
    rows = await list_devices(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id)
    await audit(session, ctx, event_type="cc2.softphone.devices_read", resource_type="agent_device", detail={"count": len(rows)})
    await session.commit()
    return {"devices": [row.as_dict() for row in rows]}


@router.delete("/devices/{device_id}")
@endpoint_errors
async def remove_device(
    device_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)),
    session: AsyncSession = Depends(get_session),
):
    row = await revoke_device(session, tenant_id=ctx.tenant_id, user_id=ctx.user_id, device_id=device_id)
    await audit(session, ctx, event_type="cc2.softphone.device_revoked", resource_type="agent_device", resource_id=row.id)
    await session.commit()
    return {"device": row.as_dict()}


@router.post("/calls", status_code=201)
@endpoint_errors
async def outbound(
    payload: SoftphoneCallBody,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1, max_length=256),
    ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)),
    session: AsyncSession = Depends(get_session),
):
    caller = payload.caller_id_number or ctx.tenant.outbound_caller_id or ctx.tenant.twilio_number
    call, replayed = await create_outbound_call(session, ctx, softphone_session_id=payload.session_id, destination=payload.destination, caller_id_number=caller or "", idempotency_key=idempotency_key)
    await audit(session, ctx, event_type="cc2.softphone.outbound", resource_type="softphone_call", resource_id=call.id, detail={"replayed": replayed})
    await session.commit()
    return {"call": await call_payload(session, call), "replayed": replayed}


@router.get("/calls")
@endpoint_errors
async def calls(
    limit: int = Query(default=100, ge=1, le=500),
    ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)),
    session: AsyncSession = Depends(get_session),
):
    rows = await list_calls(session, ctx, supervisor=can_supervise(ctx), limit=limit)
    await audit(session, ctx, event_type="cc2.softphone.calls_read", resource_type="softphone_call", detail={"count": len(rows)})
    await session.commit()
    return {"calls": [await call_payload(session, row) for row in rows]}


@router.get("/calls/{call_id}")
@endpoint_errors
async def get_softphone_call(
    call_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)),
    session: AsyncSession = Depends(get_session),
):
    row = await get_call(session, ctx, call_id, supervisor=can_supervise(ctx))
    await audit(session, ctx, event_type="cc2.softphone.call_read", resource_type="softphone_call", resource_id=row.id)
    await session.commit()
    return {"call": await call_payload(session, row)}


async def _control(call_id: uuid.UUID, action: str, enabled: bool, ctx: TenantContext, session: AsyncSession):
    row = await control_call(session, ctx, call_id=call_id, action=action, enabled=enabled, supervisor=can_supervise(ctx))
    await audit(session, ctx, event_type=f"cc2.softphone.{action}", resource_type="softphone_call", resource_id=row.id, detail={"enabled": enabled})
    await session.commit()
    return {"call": await call_payload(session, row)}


@router.post("/calls/{call_id}/mute")
@endpoint_errors
async def mute(call_id: uuid.UUID, payload: SoftphoneControlBody, ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)), session: AsyncSession = Depends(get_session)):
    return await _control(call_id, "mute", payload.enabled, ctx, session)


@router.post("/calls/{call_id}/hold")
@endpoint_errors
async def hold(call_id: uuid.UUID, payload: SoftphoneControlBody, ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)), session: AsyncSession = Depends(get_session)):
    return await _control(call_id, "hold", payload.enabled, ctx, session)


@router.post("/calls/{call_id}/resume")
@endpoint_errors
async def resume(call_id: uuid.UUID, ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)), session: AsyncSession = Depends(get_session)):
    return await _control(call_id, "hold", False, ctx, session)


@router.post("/calls/{call_id}/record-pause")
@endpoint_errors
async def record_pause(call_id: uuid.UUID, payload: SoftphoneControlBody, ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)), session: AsyncSession = Depends(get_session)):
    row = await set_recording_pause(session, ctx, call_id=call_id, paused=payload.enabled, supervisor=can_supervise(ctx))
    await audit(session, ctx, event_type="cc2.softphone.recording_pause", resource_type="softphone_call", resource_id=row.id, detail={"paused": payload.enabled})
    await session.commit()
    return {"call": await call_payload(session, row)}


@router.post("/calls/{call_id}/transfer")
@endpoint_errors
async def transfer(call_id: uuid.UUID, payload: TransferBody, ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)), session: AsyncSession = Depends(get_session)):
    row, consult = await transfer_call(session, ctx, call_id=call_id, destination=payload.destination, mode=payload.mode, supervisor=can_supervise(ctx))
    await audit(session, ctx, event_type="cc2.softphone.transfer", resource_type="softphone_call", resource_id=row.id, detail={"mode": payload.mode})
    await session.commit()
    return {"call": await call_payload(session, row), "consult_leg": consult.as_dict() if consult else None}


@router.post("/calls/{call_id}/conference")
@endpoint_errors
async def conference(call_id: uuid.UUID, payload: TransferBody, ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)), session: AsyncSession = Depends(get_session)):
    row, consult = await transfer_call(session, ctx, call_id=call_id, destination=payload.destination, mode="warm", supervisor=can_supervise(ctx))
    await audit(session, ctx, event_type="cc2.softphone.conference_added", resource_type="softphone_call", resource_id=row.id, detail={"consult_leg_id": str(consult.id) if consult else None})
    await session.commit()
    return {"call": await call_payload(session, row), "consult_leg": consult.as_dict() if consult else None}


@router.post("/calls/{call_id}/transfer/complete")
@endpoint_errors
async def transfer_complete(call_id: uuid.UUID, ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)), session: AsyncSession = Depends(get_session)):
    row = await complete_warm_transfer(session, ctx, call_id=call_id, supervisor=can_supervise(ctx))
    await audit(session, ctx, event_type="cc2.softphone.transfer_completed", resource_type="softphone_call", resource_id=row.id)
    await session.commit()
    return {"call": await call_payload(session, row)}


@router.post("/calls/{call_id}/transfer/cancel")
@endpoint_errors
async def transfer_cancel(call_id: uuid.UUID, ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)), session: AsyncSession = Depends(get_session)):
    row = await cancel_consultation(session, ctx, call_id=call_id, supervisor=can_supervise(ctx))
    await audit(session, ctx, event_type="cc2.softphone.transfer_cancelled", resource_type="softphone_call", resource_id=row.id)
    await session.commit()
    return {"call": await call_payload(session, row)}


@router.post("/calls/{call_id}/end")
@endpoint_errors
async def end(call_id: uuid.UUID, ctx: TenantContext = Depends(require_permission(Permission.SOFTPHONE_USE)), session: AsyncSession = Depends(get_session)):
    row = await end_call(session, ctx, call_id=call_id, supervisor=can_supervise(ctx))
    await audit(session, ctx, event_type="cc2.softphone.ended", resource_type="softphone_call", resource_id=row.id)
    await session.commit()
    return {"call": await call_payload(session, row)}



__all__ = ["router"]
