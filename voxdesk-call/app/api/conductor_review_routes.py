"""Review-focused FastAPI routes for Conductor (`/api/v1/conductor/reviews`).

Provides grouped change views, side-by-side old/new inspection, risk/readiness
review summaries, and batch review actions for the Conductor review surface.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Permission, TenantContext, require_permission
from app.core.errors import AppError
from app.db.session import get_session
from app.domain.conductor_models import (
    ConductorChangeReviewRequest,
    ConductorProposalDiffResponse,
    ConductorProposalResponse,
    ConductorProposalReviewRequest,
)
from app.security.policy import ConductorAction, enforce_conductor_permission
from app.services import conductor_service

router = APIRouter(prefix="/api/v1/conductor/reviews", tags=["conductor-reviews"])


def _raise_http(exc: AppError) -> None:
    raise HTTPException(
        status_code=getattr(exc, "status_code", getattr(exc, "http_status", 400)),
        detail={
            "code": str(getattr(exc.code, "value", exc.code)),
            "message": exc.message,
            "details": getattr(exc, "detail", getattr(exc, "details", None)),
        },
    ) from exc


@router.get("/proposals/{proposal_id}/summary")
async def get_proposal_review_summary_endpoint(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        enforce_conductor_permission(ctx, ConductorAction.VIEW_CONDUCTOR)
        prop = await conductor_service.get_proposal(
            session, ctx.tenant_id, proposal_id
        )
        diff = await conductor_service.get_proposal_diff(
            session, ctx.tenant_id, proposal_id
        )
        await session.commit()
        return {
            "proposal_id": str(prop.id),
            "agent_id": prop.agent_id,
            "status": prop.status,
            "base_version_number": prop.base_version_number,
            "resulting_version_number": prop.resulting_version_number,
            "validation_status": prop.validation_status,
            "validation_report": prop.validation_report,
            "simulation_status": prop.simulation_status,
            "simulation_summary": prop.simulation_summary,
            "risk_summary": prop.risk_summary,
            "total_changes": diff.total_changes,
            "approved_changes": diff.approved_changes,
            "rejected_changes": diff.rejected_changes,
            "pending_changes": diff.pending_changes,
            "grouped_by_section": {
                k: [item.model_dump(mode="json") for item in v]
                for k, v in diff.grouped_by_section.items()
            },
            "side_by_side": diff.side_by_side,
            "can_apply": (
                prop.status in {"APPROVED", "PARTIALLY_APPROVED"}
                and prop.validation_status == "valid"
                and diff.approved_changes > 0
            ),
            "production_published": False,
            "ready_to_publish": prop.ready_to_publish,
        }
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.get(
    "/proposals/{proposal_id}/grouped-changes",
    response_model=ConductorProposalDiffResponse,
)
async def get_grouped_changes_endpoint(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalDiffResponse:
    try:
        enforce_conductor_permission(ctx, ConductorAction.VIEW_CONDUCTOR)
        return await conductor_service.get_proposal_diff(
            session, ctx.tenant_id, proposal_id
        )
    except AppError as exc:
        _raise_http(exc)


@router.post(
    "/proposals/{proposal_id}/accept-safe",
    response_model=ConductorProposalResponse,
)
async def accept_safe_changes_endpoint(
    proposal_id: uuid.UUID,
    payload: ConductorProposalReviewRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    try:
        enforce_conductor_permission(ctx, ConductorAction.APPROVE_PROPOSAL)
        resp = await conductor_service.approve_proposal(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            actor_user_id=ctx.user_id,
            reason=(payload.reason if payload and payload.reason else "Accepted all low-risk changes"),
            safe_only=True,
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.post(
    "/proposals/{proposal_id}/changes/{change_id}/accept",
    response_model=ConductorProposalResponse,
)
async def accept_single_change_review_endpoint(
    proposal_id: uuid.UUID,
    change_id: uuid.UUID,
    payload: ConductorChangeReviewRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    try:
        enforce_conductor_permission(ctx, ConductorAction.APPROVE_PROPOSAL)
        resp = await conductor_service.approve_one_change(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            change_id=change_id,
            actor_user_id=ctx.user_id,
            reason=(payload.reason if payload else ""),
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)
