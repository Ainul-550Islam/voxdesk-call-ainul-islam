"""FastAPI routes for Version-Pinned Web Call Test Sessions."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Permission, TenantContext, require_permission
from app.core.errors import AppError
from app.db.session import get_session
from app.domain.evaluation_models import (
    TestRunResponse,
    WebCallSessionEventRequest,
    WebCallSessionRequest,
)
from app.services import call_service, simulation_service

router = APIRouter(prefix="/api/v1/testing/web-calls", tags=["testing-web-calls"])


def _raise_http(exc: AppError) -> None:
    raise HTTPException(
        status_code=getattr(exc, "status_code", getattr(exc, "http_status", 400)),
        detail={
            "code": str(getattr(exc.code, "value", exc.code)),
            "message": exc.message,
            "details": getattr(exc, "detail", getattr(exc, "details", None)),
        },
    ) from exc


@router.post(
    "/sessions",
    response_model=TestRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_web_call_session_endpoint(
    payload: WebCallSessionRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        resp = await call_service.start_web_call_test_session(
            session,
            ctx.tenant_id,
            payload,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.post("/sessions/{run_id}/events", response_model=TestRunResponse)
async def send_web_call_event_endpoint(
    run_id: uuid.UUID,
    payload: WebCallSessionEventRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        resp = await call_service.send_web_call_event(
            session,
            ctx.tenant_id,
            run_id,
            payload,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.get("/sessions/{run_id}", response_model=TestRunResponse)
async def get_web_call_session_endpoint(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        return await simulation_service.get_test_run(session, ctx.tenant_id, run_id)
    except AppError as exc:
        _raise_http(exc)
