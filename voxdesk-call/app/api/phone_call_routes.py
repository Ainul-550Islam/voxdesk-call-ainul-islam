"""FastAPI routes for Version-Pinned Outbound Phone Call Tests."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Permission, TenantContext, require_permission
from app.core.errors import AppError
from app.db.session import get_session
from app.domain.evaluation_models import PhoneCallTestRequest, TestRunResponse
from app.services import call_service, simulation_service

router = APIRouter(prefix="/api/v1/testing/phone-calls", tags=["testing-phone-calls"])


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
    "/run",
    response_model=TestRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def initiate_phone_call_test_endpoint(
    payload: PhoneCallTestRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        resp = await call_service.initiate_phone_call_test(
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


@router.get("/{run_id}", response_model=TestRunResponse)
async def get_phone_call_test_endpoint(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        return await simulation_service.get_test_run(session, ctx.tenant_id, run_id)
    except AppError as exc:
        _raise_http(exc)
