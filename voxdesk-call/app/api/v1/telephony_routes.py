"""
app/api/v1/telephony_routes.py
Authenticated API endpoints + WebSocket media gateway for Prompt 6 — Telephony / Voice Runtime.
Enforces tenant isolation (`organization_id` / `tenant_id`), RBAC, and strict schema validation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401 - ensure ORM registry is initialized before auth imports
from app.auth.dependencies import Permission, TenantContext, require_permission
from app.auth.rbac import has_permission
from app.auth.jwt import decode_access_token
from app.db.session import get_session
from app.environments.membership import resolve as resolve_environment_membership
from app.environments.resource_scope import resolve_scope
from app.resilience.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyPreviousFailure,
)
from app.db.telephony_models import TelephonyCallSession
from app.services.telephony_usage import TelephonyUsageService
from app.telephony.call_session import CallSessionManager, serialize_call_session
from app.telephony.exceptions import MediaSessionError
from app.telephony.phone_numbers import PhoneNumberService
from app.telephony.realtime import (
    RealtimeVoiceSessionOrchestrator,
    issue_media_stream_token,
    verify_media_stream_token,
)
from app.telephony.runtime import TelephonyRuntimeService
from app.telephony.schemas import (
    CallDtmfRequest,
    CallDtmfResponse,
    CallHangupRequest,
    CallSessionListResponse,
    CallSessionResponse,
    CallTransferRequest,
    CallTransferResponse,
    OutboundCallCreateRequest,
    PhoneNumberBindAgentRequest,
    PhoneNumberCreateRequest,
    PhoneNumberListResponse,
    PhoneNumberResponse,
    PhoneNumberUpdateRequest,
    SipConnectionCreateRequest,
    SipConnectionListResponse,
    SipConnectionResponse,
    SipConnectionTestRequest,
    TelephonyHealthStatusResponse,
)
from app.telephony.sip import SipConnectionService
from app.telephony.transfer import CallTransferService

router = APIRouter(prefix="/api/v1/telephony", tags=["Telephony Runtime"])


# ---------------------------------------------------------------------------
# Phone Number Endpoints
# ---------------------------------------------------------------------------


@router.get("/phone-numbers", response_model=PhoneNumberListResponse)
async def list_phone_numbers(
    status_filter: str | None = Query(default=None, alias="status"),
    environment_id: UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> PhoneNumberListResponse:
    svc = PhoneNumberService(session)
    return await svc.list_phone_numbers(
        tenant_id=ctx.tenant_id,
        environment_id=environment_id,
        status=status_filter,
    )


@router.post(
    "/phone-numbers",
    response_model=PhoneNumberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_phone_number(
    payload: PhoneNumberCreateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> PhoneNumberResponse:
    svc = PhoneNumberService(session)
    result = await svc.create_phone_number(
        tenant_id=ctx.tenant_id,
        payload=payload,
    )
    await session.commit()
    return result


@router.get(
    "/phone-numbers/{phone_number_id}",
    response_model=PhoneNumberResponse,
)
async def get_phone_number(
    phone_number_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> PhoneNumberResponse:
    svc = PhoneNumberService(session)
    return await svc.get_phone_number(
        tenant_id=ctx.tenant_id,
        phone_number_id=phone_number_id,
    )


@router.patch(
    "/phone-numbers/{phone_number_id}",
    response_model=PhoneNumberResponse,
)
async def update_phone_number(
    phone_number_id: UUID,
    payload: PhoneNumberUpdateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> PhoneNumberResponse:
    svc = PhoneNumberService(session)
    result = await svc.update_phone_number(
        tenant_id=ctx.tenant_id,
        phone_number_id=phone_number_id,
        payload=payload,
    )
    await session.commit()
    return result


@router.delete("/phone-numbers/{phone_number_id}")
async def delete_phone_number(
    phone_number_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    svc = PhoneNumberService(session)
    result = await svc.delete_phone_number(
        tenant_id=ctx.tenant_id,
        phone_number_id=phone_number_id,
    )
    await session.commit()
    return result


@router.post(
    "/phone-numbers/{phone_number_id}/bind-agent",
    response_model=PhoneNumberResponse,
)
async def bind_phone_number_agent(
    phone_number_id: UUID,
    payload: PhoneNumberBindAgentRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> PhoneNumberResponse:
    svc = PhoneNumberService(session)
    result = await svc.bind_agent(
        tenant_id=ctx.tenant_id,
        phone_number_id=phone_number_id,
        payload=payload,
    )
    await session.commit()
    return result


# ---------------------------------------------------------------------------
# SIP Connection Endpoints
# ---------------------------------------------------------------------------


@router.get("/sip-connections", response_model=SipConnectionListResponse)
async def list_sip_connections(
    environment_id: UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> SipConnectionListResponse:
    svc = SipConnectionService(session)
    return await svc.list_sip_connections(
        tenant_id=ctx.tenant_id,
        environment_id=environment_id,
    )


@router.post(
    "/sip-connections",
    response_model=SipConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_sip_connection(
    payload: SipConnectionCreateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> SipConnectionResponse:
    svc = SipConnectionService(session)
    result = await svc.create_sip_connection(
        tenant_id=ctx.tenant_id,
        payload=payload,
    )
    await session.commit()
    return result


@router.post(
    "/sip-connections/{sip_connection_id}/test",
    response_model=SipConnectionResponse,
)
async def test_sip_connection(
    sip_connection_id: UUID,
    payload: SipConnectionTestRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> SipConnectionResponse:
    svc = SipConnectionService(session)
    try:
        result = await svc.test_sip_connection(
            tenant_id=ctx.tenant_id,
            sip_connection_id=sip_connection_id,
            payload=payload,
        )
        await session.commit()
        return result
    except Exception:
        await session.commit()
        raise


# ---------------------------------------------------------------------------
# Outbound & Call Control Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/calls/outbound",
    response_model=CallSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_outbound_call(
    payload: OutboundCallCreateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> CallSessionResponse:
    if ctx.environment_id is not None and payload.environment_id not in (None, ctx.environment_id):
        raise HTTPException(
            status_code=403,
            detail={"code": "environment_scope_mismatch", "message": "Credential is bound to another environment."},
        )
    scope = await resolve_scope(
        session,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        explicit_environment_id=payload.environment_id or ctx.environment_id,
        for_write=True,
    )
    access = await resolve_environment_membership(session, ctx.user, scope, ctx.tenant)
    if (
        not access.allowed
        or access.role is None
        or not has_permission(access.role, Permission.CALL_WRITE)
    ):
        raise HTTPException(
            status_code=403,
            detail={"code": "environment_forbidden", "message": "Outbound calls are not permitted in this environment."},
        )

    scoped_payload = payload.model_copy(update={"environment_id": scope.id})
    svc = TelephonyRuntimeService(session)
    try:
        result = await svc.initiate_outbound_call(
            tenant_id=ctx.tenant_id,
            payload=scoped_payload,
            environment_id=scope.id,
        )
    except IdempotencyConflict:
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_conflict", "message": "Idempotency-Key was already used for a different request."},
        ) from None
    except IdempotencyInProgress:
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_in_progress", "message": "The request is still in progress or requires reconciliation."},
        ) from None
    except IdempotencyPreviousFailure:
        raise HTTPException(
            status_code=409,
            detail={"code": "idempotency_previous_failure", "message": "The prior request failed; use a new key for a new attempt."},
        ) from None
    return result


@router.get("/calls", response_model=CallSessionListResponse)
async def list_calls(
    status_filter: str | None = Query(default=None, alias="status"),
    direction: str | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
) -> CallSessionListResponse:
    mgr = CallSessionManager(session)
    return await mgr.list_call_sessions(
        tenant_id=ctx.tenant_id,
        status=status_filter,
        direction=direction,
        agent_id=agent_id,
        limit=limit,
    )


@router.get("/calls/{call_id}", response_model=CallSessionResponse)
async def get_call(
    call_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
) -> CallSessionResponse:
    mgr = CallSessionManager(session)
    row = await mgr.get_call_session(tenant_id=ctx.tenant_id, call_id=call_id)
    return serialize_call_session(row)


@router.post("/calls/{call_id}/hangup", response_model=CallSessionResponse)
async def hangup_call(
    call_id: UUID,
    payload: CallHangupRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> CallSessionResponse:
    svc = TelephonyRuntimeService(session)
    result = await svc.hangup_call(
        tenant_id=ctx.tenant_id,
        call_id=call_id,
        payload=payload,
    )
    await session.commit()
    return result


@router.post("/calls/{call_id}/dtmf", response_model=CallDtmfResponse)
async def send_call_dtmf(
    call_id: UUID,
    payload: CallDtmfRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> CallDtmfResponse:
    svc = TelephonyRuntimeService(session)
    result = await svc.send_call_dtmf(
        tenant_id=ctx.tenant_id,
        call_id=call_id,
        payload=payload,
    )
    await session.commit()
    return result


@router.post("/calls/{call_id}/transfer", response_model=CallTransferResponse)
async def transfer_call(
    call_id: UUID,
    payload: CallTransferRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> CallTransferResponse:
    svc = TelephonyRuntimeService(session)
    result = await svc.transfer_call(
        tenant_id=ctx.tenant_id,
        call_id=call_id,
        payload=payload,
    )
    await session.commit()
    return result


@router.get(
    "/calls/{call_id}/transfers",
    response_model=list[CallTransferResponse],
)
async def list_call_transfers(
    call_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.CALL_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[CallTransferResponse]:
    mgr = CallSessionManager(session)
    await mgr.get_call_session(tenant_id=ctx.tenant_id, call_id=call_id)
    xfer_svc = CallTransferService(session)
    return await xfer_svc.list_call_transfers(
        tenant_id=ctx.tenant_id,
        call_session_id=call_id,
    )


@router.post("/calls/{call_id}/media-token")
async def create_call_media_token(
    call_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    mgr = CallSessionManager(session)
    row = await mgr.get_call_session(tenant_id=ctx.tenant_id, call_id=call_id)
    token = issue_media_stream_token(call_id=row.id, tenant_id=ctx.tenant_id)
    return {
        "call_id": str(row.id),
        "stream_token": token,
        "ws_path": f"/api/v1/telephony/calls/{row.id}/media-stream?token={token}",
    }


@router.post("/calls/{call_id}/media-event")
async def post_call_media_event(
    call_id: UUID,
    message: dict[str, Any],
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    mgr = CallSessionManager(session)
    row = await mgr.get_call_session(tenant_id=ctx.tenant_id, call_id=call_id)
    orchestrator = RealtimeVoiceSessionOrchestrator(session)
    response = await orchestrator.handle_ws_message(
        call_session=row,
        message=message,
    )
    await session.commit()
    return response


# ---------------------------------------------------------------------------
# Real-Time Media Gateway WebSocket Endpoint
# ---------------------------------------------------------------------------


@router.websocket("/calls/{call_id}/media-stream")
async def call_media_stream_ws(
    websocket: WebSocket,
    call_id: UUID,
    token: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> None:
    resolved_tenant_id: UUID | None = None
    auth_header = websocket.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        jwt_str = auth_header.split(" ", 1)[1].strip()
        try:
            claims = decode_access_token(jwt_str)
            tid_raw = getattr(claims, "tenant_id", None)
            if tid_raw:
                resolved_tenant_id = UUID(str(tid_raw))
        except Exception:
            resolved_tenant_id = None

    row = (
        await session.execute(
            select(TelephonyCallSession).where(TelephonyCallSession.id == call_id)
        )
    ).scalar_one_or_none()
    if row is None:
        await websocket.close(code=4404, reason="Call session not found")
        return

    token_ok = bool(
        token and verify_media_stream_token(token, call_id=row.id, tenant_id=row.tenant_id)
    )
    jwt_ok = bool(resolved_tenant_id and resolved_tenant_id == row.tenant_id)
    if not (token_ok or jwt_ok):
        await websocket.close(code=4401, reason="Unauthorized media stream session")
        return

    await websocket.accept()
    orchestrator = RealtimeVoiceSessionOrchestrator(session)
    try:
        while True:
            data = await websocket.receive_json()
            if not isinstance(data, dict):
                await websocket.send_json(
                    {"type": "media.error", "error": "Payload must be a JSON object"}
                )
                continue
            try:
                reply = await orchestrator.handle_ws_message(
                    call_session=row,
                    message=data,
                )
                await session.commit()
                await websocket.send_json(reply)
                if reply.get("type") == "media.stopped":
                    break
            except MediaSessionError as exc:
                await session.rollback()
                await websocket.send_json(
                    {
                        "type": "media.error",
                        "code": exc.code,
                        "error": str(exc),
                    }
                )
    except WebSocketDisconnect:
        orchestrator_stop = {"type": "media.stop"}
        try:
            await orchestrator.handle_ws_message(
                call_session=row, message=orchestrator_stop
            )
            await session.commit()
        except Exception:
            await session.rollback()


# ---------------------------------------------------------------------------
# Runtime Health & Usage Endpoints
# ---------------------------------------------------------------------------


@router.get("/health", response_model=TelephonyHealthStatusResponse)
async def get_telephony_health(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> TelephonyHealthStatusResponse:
    svc = TelephonyRuntimeService(session)
    return await svc.get_health_status(tenant_id=ctx.tenant_id)


@router.get("/usage")
async def get_telephony_usage(
    include_simulations: bool = Query(default=False),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    usage_svc = TelephonyUsageService(session)
    return await usage_svc.get_tenant_usage_summary(
        tenant_id=ctx.tenant_id,
        include_simulations=include_simulations,
    )
