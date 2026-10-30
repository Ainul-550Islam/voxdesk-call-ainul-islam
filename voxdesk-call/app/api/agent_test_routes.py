"""Durable agent-test session transport routes (Capability 6).

Persists every session in ``TestRun`` via ``app.services.agent_test_session_service``:
- ``POST /api/v1/agents/{agent_id}/test`` — creates a persisted test session + HMAC token + WS URL
- ``POST /api/v1/agent-tests/{session_id}/events`` — processes start / turn / interrupt / stop events
- ``GET /api/v1/agent-tests/{session_id}`` — returns persisted transcript & state (404 cross-tenant, 410 expired)
- ``GET /api/v1/agent-tests/{session_id}/health`` — returns persisted session health
- ``WS /api/v1/agent-tests/{session_id}/ws`` — bidirectional WebSocket backed by persisted session state
"""
from __future__ import annotations

import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Permission, TenantContext, require_permission
from app.db.session import get_session, get_sessionmaker
from app.services.agent_test_session_service import (
    AgentTestExpiredError,
    AgentTestNotConfiguredError,
    AgentTestNotFoundError,
    AgentTestUnauthorizedError,
    create_session,
    get_session_state,
    process_event,
    verify_ws_session,
)

router = APIRouter(prefix="/api/v1", tags=["agent-test"])


class TestSessionCreateIn(BaseModel):
    mode: str = Field(
        default="simulation",
        pattern="^(simulation|live_webrtc|phone_loopback)$",
    )
    initial_prompt_override: Optional[str] = Field(default=None, max_length=2000)
    mock_caller_persona: Optional[str] = Field(default="curious_customer", max_length=80)
    timeout_sec: int = Field(default=120, ge=1, le=600)


class TestTranscriptOut(BaseModel):
    role: str
    text: str
    timestamp: str
    latency_ms: Optional[int] = None
    tool_calls: List[dict[str, Any]] = Field(default_factory=list)


class TestSessionSummaryOut(BaseModel):
    id: str
    session_id: str
    agent_id: str
    state: str
    created_at: str
    transcript: List[TestTranscriptOut] = Field(default_factory=list)


class TestSessionOut(BaseModel):
    id: str
    session_id: str
    agent_id: str
    tenant_id: str
    mode: str
    status: str
    state: str
    ws_url: Optional[str]
    token: str
    expires_at: str
    created_at: str
    transcript: List[TestTranscriptOut] = Field(default_factory=list)
    session: TestSessionSummaryOut


class TestEventIn(BaseModel):
    event: str = Field(
        ...,
        pattern="^(start|stop|end|interrupt|text|utterance|turn|audio)$",
    )
    data: Optional[dict[str, Any]] = Field(default=None)
    payload: Optional[dict[str, Any]] = Field(default=None)
    timestamp: Optional[str] = None


class TestEventOut(BaseModel):
    session_id: str
    event: str
    new_state: str
    at: str
    reply: Optional[str] = None
    transcript: List[TestTranscriptOut] = Field(default_factory=list)
    session: TestSessionSummaryOut


class TestSessionStateOut(BaseModel):
    id: str
    session_id: str
    agent_id: str
    state: str
    transcript: List[TestTranscriptOut]
    latency_ms: int
    error: Optional[str] = None
    created_at: str
    expires_at: Optional[str] = None
    session: TestSessionSummaryOut


def _parse_uuid_or_404(raw: str, resource: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": f"{resource} not found"},
        ) from None


def _raise_service_http_error(exc: Exception) -> None:
    if isinstance(exc, AgentTestNotFoundError):
        raise HTTPException(
            status_code=404,
            detail={"code": "NOT_FOUND", "message": str(exc)},
        ) from None
    if isinstance(exc, AgentTestExpiredError):
        raise HTTPException(
            status_code=410,
            detail={"code": "TEST_SESSION_EXPIRED", "message": str(exc)},
        ) from None
    if isinstance(exc, AgentTestUnauthorizedError):
        raise HTTPException(
            status_code=401,
            detail={"code": "INVALID_SESSION_TOKEN", "message": str(exc)},
        ) from None
    if isinstance(exc, AgentTestNotConfiguredError):
        raise HTTPException(
            status_code=409,
            detail={"code": "NOT_CONFIGURED", "message": str(exc)},
        ) from None
    raise HTTPException(
        status_code=422,
        detail={"code": "VALIDATION_ERROR", "message": str(exc)},
    ) from None


@router.post(
    "/agents/{agent_id}/test",
    response_model=TestSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent_test_session(
    agent_id: str,
    payload: Optional[TestSessionCreateIn] = Body(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestSessionOut:
    if ctx is None or session is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": "Tenant context and session are required."},
        )
    agent_uuid = _parse_uuid_or_404(agent_id, "Agent")
    req = payload or TestSessionCreateIn()
    try:
        created = await create_session(
            session,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            agent_id=agent_uuid,
            mode=req.mode,
            initial_prompt_override=req.initial_prompt_override,
            mock_caller_persona=req.mock_caller_persona,
            timeout_sec=req.timeout_sec,
        )
    except Exception as exc:
        _raise_service_http_error(exc)
        raise

    summary = TestSessionSummaryOut(
        id=created["id"],
        session_id=created["session_id"],
        agent_id=created["agent_id"],
        state=created["state"],
        created_at=created["created_at"],
        transcript=[TestTranscriptOut(**t) for t in created["transcript"]],
    )
    return TestSessionOut(
        **created,
        session=summary,
    )


@router.post("/agent-tests/{session_id}/events", response_model=TestEventOut)
async def send_agent_test_event(
    session_id: str,
    payload: TestEventIn,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestEventOut:
    if ctx is None or session is None or payload is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": "Tenant context, session, and event payload are required."},
        )
    sess_uuid = _parse_uuid_or_404(session_id, "Test session")
    event_data = payload.data if payload.data is not None else (payload.payload or {})
    try:
        res = await process_event(
            session,
            session_id=sess_uuid,
            tenant_id=ctx.tenant_id,
            event=payload.event,
            data=event_data,
            timestamp=payload.timestamp,
        )
    except Exception as exc:
        _raise_service_http_error(exc)
        raise

    transcript_out = [TestTranscriptOut(**t) for t in res["transcript"]]
    summary = TestSessionSummaryOut(
        id=res["session"]["id"],
        session_id=res["session"]["session_id"],
        agent_id=res["session"]["agent_id"],
        state=res["session"]["state"],
        created_at=res["session"]["created_at"],
        transcript=transcript_out,
    )
    return TestEventOut(
        session_id=res["session_id"],
        event=res["event"],
        new_state=res["new_state"],
        at=res["at"],
        reply=res["reply"],
        transcript=transcript_out,
        session=summary,
    )


@router.get("/agent-tests/{session_id}", response_model=TestSessionStateOut)
async def get_agent_test_session(
    session_id: str,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> TestSessionStateOut:
    if ctx is None or session is None:
        raise HTTPException(
            status_code=422,
            detail={"code": "VALIDATION_ERROR", "message": "Tenant context and session are required."},
        )
    sess_uuid = _parse_uuid_or_404(session_id, "Test session")
    try:
        state_data = await get_session_state(
            session, session_id=sess_uuid, tenant_id=ctx.tenant_id
        )
    except Exception as exc:
        _raise_service_http_error(exc)
        raise

    transcript_out = [TestTranscriptOut(**t) for t in state_data["transcript"]]
    summary = TestSessionSummaryOut(
        id=state_data["id"],
        session_id=state_data["session_id"],
        agent_id=state_data["agent_id"],
        state=state_data["state"],
        created_at=state_data["created_at"],
        transcript=transcript_out,
    )
    return TestSessionStateOut(
        id=state_data["id"],
        session_id=state_data["session_id"],
        agent_id=state_data["agent_id"],
        state=state_data["state"],
        transcript=transcript_out,
        latency_ms=state_data["latency_ms"],
        error=state_data["error"],
        created_at=state_data["created_at"],
        expires_at=state_data["expires_at"],
        session=summary,
    )


@router.get("/agent-tests/{session_id}/health", response_model=dict)
async def health_check(
    session_id: str,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    sess_uuid = _parse_uuid_or_404(session_id, "Test session")
    try:
        state_data = await get_session_state(
            session, session_id=sess_uuid, tenant_id=ctx.tenant_id
        )
    except Exception as exc:
        _raise_service_http_error(exc)
        raise
    return {
        "status": "ok",
        "session_id": state_data["session_id"],
        "state": state_data["state"],
        "turns": len(state_data["transcript"]),
        "expires_at": state_data["expires_at"],
    }


@router.websocket("/agent-tests/{session_id}/ws")
async def agent_test_websocket(
    websocket: WebSocket,
    session_id: str,
    token: str = Query(default=""),
) -> None:
    try:
        sess_uuid = uuid.UUID(str(session_id))
    except (TypeError, ValueError):
        await websocket.close(code=4404)
        return

    maker = get_sessionmaker()
    async with maker() as db:
        try:
            _, tenant_id = await verify_ws_session(
                db, session_id=sess_uuid, token=token
            )
            initial = await get_session_state(
                db, session_id=sess_uuid, tenant_id=tenant_id
            )
        except AgentTestUnauthorizedError:
            await websocket.close(code=4401)
            return
        except AgentTestExpiredError:
            await websocket.close(code=4410)
            return
        except AgentTestNotFoundError:
            await websocket.close(code=4404)
            return

    await websocket.accept()
    await websocket.send_json({"type": "session.ready", **initial})

    try:
        while True:
            msg = await websocket.receive_json()
            ev_name = str(msg.get("event") or msg.get("type") or "text")
            ev_data = msg.get("data") if isinstance(msg.get("data"), dict) else msg
            async with maker() as db:
                try:
                    result = await process_event(
                        db,
                        session_id=sess_uuid,
                        tenant_id=tenant_id,
                        event=ev_name,
                        data=ev_data,
                    )
                except AgentTestExpiredError as exc:
                    await websocket.send_json({"type": "error", "code": "TEST_SESSION_EXPIRED", "message": str(exc)})
                    await websocket.close(code=4410)
                    return
                except Exception as exc:
                    await websocket.send_json({"type": "error", "code": "EVENT_REJECTED", "message": str(exc)})
                    continue
            await websocket.send_json({"type": "session.event", **result})
            if result["new_state"] == "COMPLETED":
                await websocket.close(code=1000)
                return
    except WebSocketDisconnect:
        return
