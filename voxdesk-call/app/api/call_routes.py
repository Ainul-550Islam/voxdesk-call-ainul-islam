"""FastAPI routes for Call Test Readiness and Unified Call-Mode Test Runs."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Permission, TenantContext, require_permission
from app.core.errors import AppError
from app.db.session import get_session
from app.domain.evaluation_models import TestRunResponse
from app.services import call_service, simulation_service

router = APIRouter(prefix="/api/v1/testing/calls", tags=["testing-calls"])


def _raise_http(exc: AppError) -> None:
    raise HTTPException(
        status_code=getattr(exc, "status_code", getattr(exc, "http_status", 400)),
        detail={
            "code": str(getattr(exc.code, "value", exc.code)),
            "message": exc.message,
            "details": getattr(exc, "detail", getattr(exc, "details", None)),
        },
    ) from exc


@router.get("/readiness", response_model=dict[str, Any])
async def get_call_test_readiness(
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
) -> dict[str, Any]:
    webrtc_live = call_service.is_webrtc_live_configured()
    carrier_live = call_service.is_telephony_carrier_configured()
    return {
        "tenant_id": str(ctx.tenant_id),
        "webrtc_live_configured": webrtc_live,
        "webrtc_mode": "webrtc_live" if webrtc_live else "simulated_web_audio",
        "telephony_carrier_configured": carrier_live,
        "telephony_mode": "pstn_live" if carrier_live else "carrier_unconfigured",
    }


@router.get("/runs", response_model=list[TestRunResponse])
async def list_call_test_runs(
    mode: str | None = Query(default=None, pattern="^(web_call|phone_call)$"),
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[TestRunResponse]:
    try:
        if mode:
            return await simulation_service.list_test_runs(
                session,
                ctx.tenant_id,
                agent_id=agent_id,
                mode=mode,
                limit=limit,
            )
        web_runs = await simulation_service.list_test_runs(
            session, ctx.tenant_id, agent_id=agent_id, mode="web_call", limit=limit
        )
        phone_runs = await simulation_service.list_test_runs(
            session, ctx.tenant_id, agent_id=agent_id, mode="phone_call", limit=limit
        )
        combined = sorted(
            [*web_runs, *phone_runs],
            key=lambda r: r.created_at,
            reverse=True,
        )
        return combined[:limit]
    except AppError as exc:
        _raise_http(exc)


@router.get("/runs/{run_id}", response_model=TestRunResponse)
async def get_call_test_run(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        return await simulation_service.get_test_run(session, ctx.tenant_id, run_id)
    except AppError as exc:
        _raise_http(exc)
