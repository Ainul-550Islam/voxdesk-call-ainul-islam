"""FastAPI routes for the Conductor AI Control Plane (`/api/v1/conductor`).

Exposes:
- Session creation, listing, and retrieval
- Scoped context assembly
- Natural-language / structured proposal creation
- Proposal retrieval, changes, deterministic diff, validation, simulation, and evidence
- Granular per-change and per-proposal approve, reject, undo, and transactional apply
- Resulting immutable AgentVersion lookup
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Permission, TenantContext, require_permission
from app.core.errors import AppError
from app.db.session import get_session
from app.domain.conductor_models import (
    ConductorApplyProposalRequest,
    ConductorApplyResultResponse,
    ConductorChangeResponse,
    ConductorChangeReviewRequest,
    ConductorEvidenceResponse,
    ConductorPromptRequest,
    ConductorProposalDiffResponse,
    ConductorProposalResponse,
    ConductorProposalReviewRequest,
    ConductorSessionCreateRequest,
    ConductorSessionResponse,
    ConductorSimulateProposalRequest,
)
from app.security.policy import ConductorAction, enforce_conductor_permission
from app.services import conductor_context_service, conductor_service

router = APIRouter(prefix="/api/v1/conductor", tags=["conductor"])


def _raise_http(exc: AppError) -> NoReturn:
    raise HTTPException(
        status_code=getattr(exc, "status_code", getattr(exc, "http_status", 400)),
        detail={
            "code": str(getattr(exc.code, "value", exc.code)),
            "message": exc.message,
            "details": getattr(exc, "detail", getattr(exc, "details", None)),
        },
    ) from exc


@asynccontextmanager
async def _operation(
    session: AsyncSession,
    ctx: TenantContext,
    action: ConductorAction,
    *,
    commit: bool = False,
) -> AsyncIterator[None]:
    """Preserve route-specific policy and atomic commit/rollback in one boundary."""
    try:
        enforce_conductor_permission(ctx, action)
        yield
        if commit:
            await session.commit()
    except AppError as exc:
        if commit:
            await session.rollback()
        _raise_http(exc)


# -------------------------------------------------------------------- Sessions


@router.post(
    "/sessions",
    response_model=ConductorSessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session_endpoint(
    payload: ConductorSessionCreateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> ConductorSessionResponse:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=True):
        resp = await conductor_service.create_conductor_session(
            session,
            tenant_id=ctx.tenant_id,
            payload=payload,
            actor_user_id=ctx.user_id,
        )
        return resp


@router.get("/sessions", response_model=list[ConductorSessionResponse])
async def list_sessions_endpoint(
    agent_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[ConductorSessionResponse]:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        return await conductor_service.list_conductor_sessions(
            session,
            ctx.tenant_id,
            agent_id=agent_id,
            limit=limit,
        )


@router.get("/sessions/{session_id}", response_model=ConductorSessionResponse)
async def get_session_endpoint(
    session_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> ConductorSessionResponse:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        return await conductor_service.get_conductor_session(
            session, ctx.tenant_id, session_id
        )


@router.get("/context")
async def get_scoped_context_endpoint(
    agent_id: str = Query(..., min_length=1),
    agent_kind: str = Query(default="voice"),
    base_version_number: int | None = Query(default=None, ge=1),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        return await conductor_context_service.assemble_conductor_context(
            session,
            tenant_id=ctx.tenant_id,
            agent_id=agent_id,
            agent_kind=agent_kind,
            base_version_number=base_version_number,
        )


# ------------------------------------------------------------------- Proposals


@router.post(
    "/proposals",
    response_model=ConductorProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_conductor_request_endpoint(
    payload: ConductorPromptRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.CREATE_PROPOSAL, commit=True):
        resp = await conductor_service.create_proposal_from_request(
            session,
            tenant_id=ctx.tenant_id,
            payload=payload,
            actor_user_id=ctx.user_id,
        )
        return resp


@router.get("/proposals", response_model=list[ConductorProposalResponse])
async def list_proposals_endpoint(
    agent_id: str | None = Query(default=None),
    session_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[ConductorProposalResponse]:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        return await conductor_service.list_proposals(
            session,
            ctx.tenant_id,
            agent_id=agent_id,
            session_id=session_id,
            limit=limit,
        )


@router.get("/proposals/{proposal_id}", response_model=ConductorProposalResponse)
async def get_proposal_endpoint(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=True):
        resp = await conductor_service.get_proposal(
            session, ctx.tenant_id, proposal_id
        )
        return resp


@router.get(
    "/proposals/{proposal_id}/changes",
    response_model=list[ConductorChangeResponse],
)
async def get_proposal_changes_endpoint(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[ConductorChangeResponse]:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        rows = await conductor_service.get_proposal_changes(
            session, ctx.tenant_id, proposal_id
        )
        return [ConductorChangeResponse.model_validate(r) for r in rows]


@router.get(
    "/proposals/{proposal_id}/diff",
    response_model=ConductorProposalDiffResponse,
)
async def get_proposal_diff_endpoint(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalDiffResponse:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        return await conductor_service.get_proposal_diff(
            session, ctx.tenant_id, proposal_id
        )


@router.get(
    "/proposals/{proposal_id}/evidence",
    response_model=list[ConductorEvidenceResponse],
)
async def list_proposal_evidence_endpoint(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[ConductorEvidenceResponse]:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        await conductor_service.get_proposal(session, ctx.tenant_id, proposal_id)
        rows = await conductor_service.list_proposal_evidence(
            session, ctx.tenant_id, proposal_id
        )
        return [ConductorEvidenceResponse.model_validate(r) for r in rows]


# --------------------------------------------------- Validation & Simulation


@router.post(
    "/proposals/{proposal_id}/validate",
    response_model=ConductorProposalResponse,
)
async def validate_proposal_endpoint(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.CREATE_PROPOSAL, commit=True):
        resp = await conductor_service.validate_proposal(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            actor_user_id=ctx.user_id,
        )
        return resp


@router.post(
    "/proposals/{proposal_id}/simulate",
    response_model=ConductorProposalResponse,
)
async def simulate_proposal_endpoint(
    proposal_id: uuid.UUID,
    payload: ConductorSimulateProposalRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.CREATE_PROPOSAL, commit=True):
        resp = await conductor_service.simulate_proposal(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            payload=payload or ConductorSimulateProposalRequest(),
            actor_user_id=ctx.user_id,
        )
        return resp


# ------------------------------------------- Human Review, Undo, & Safe Apply


@router.post(
    "/proposals/{proposal_id}/changes/{change_id}/approve",
    response_model=ConductorProposalResponse,
)
async def approve_change_endpoint(
    proposal_id: uuid.UUID,
    change_id: uuid.UUID,
    payload: ConductorChangeReviewRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.APPROVE_PROPOSAL, commit=True):
        resp = await conductor_service.approve_one_change(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            change_id=change_id,
            actor_user_id=ctx.user_id,
            reason=(payload.reason if payload else ""),
        )
        return resp


@router.post(
    "/proposals/{proposal_id}/changes/{change_id}/reject",
    response_model=ConductorProposalResponse,
)
async def reject_change_endpoint(
    proposal_id: uuid.UUID,
    change_id: uuid.UUID,
    payload: ConductorChangeReviewRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.REJECT_PROPOSAL, commit=True):
        resp = await conductor_service.reject_one_change(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            change_id=change_id,
            actor_user_id=ctx.user_id,
            reason=(payload.reason if payload else ""),
        )
        return resp


@router.post(
    "/proposals/{proposal_id}/changes/{change_id}/undo",
    response_model=ConductorProposalResponse,
)
async def undo_change_endpoint(
    proposal_id: uuid.UUID,
    change_id: uuid.UUID,
    payload: ConductorChangeReviewRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.APPROVE_PROPOSAL, commit=True):
        resp = await conductor_service.undo_one_change(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            change_id=change_id,
            actor_user_id=ctx.user_id,
            reason=(payload.reason if payload else ""),
        )
        return resp


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=ConductorProposalResponse,
)
async def approve_proposal_endpoint(
    proposal_id: uuid.UUID,
    payload: ConductorProposalReviewRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.APPROVE_PROPOSAL, commit=True):
        resp = await conductor_service.approve_proposal(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            actor_user_id=ctx.user_id,
            reason=(payload.reason if payload else ""),
            safe_only=bool(payload.safe_only if payload else False),
        )
        return resp


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=ConductorProposalResponse,
)
async def reject_proposal_endpoint(
    proposal_id: uuid.UUID,
    payload: ConductorProposalReviewRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.REJECT_PROPOSAL, commit=True):
        resp = await conductor_service.reject_proposal(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            actor_user_id=ctx.user_id,
            reason=(payload.reason if payload else ""),
        )
        return resp


@router.post(
    "/proposals/{proposal_id}/undo",
    response_model=ConductorProposalResponse,
)
async def undo_proposal_endpoint(
    proposal_id: uuid.UUID,
    payload: ConductorProposalReviewRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorProposalResponse:
    async with _operation(session, ctx, ConductorAction.APPROVE_PROPOSAL, commit=True):
        resp = await conductor_service.undo_proposal(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            actor_user_id=ctx.user_id,
            reason=(payload.reason if payload else ""),
        )
        return resp


@router.post(
    "/proposals/{proposal_id}/apply",
    response_model=ConductorApplyResultResponse,
)
async def apply_proposal_endpoint(
    proposal_id: uuid.UUID,
    payload: ConductorApplyProposalRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> ConductorApplyResultResponse:
    async with _operation(session, ctx, ConductorAction.APPLY_PROPOSAL, commit=True):
        resp = await conductor_service.apply_proposal(
            session,
            tenant_id=ctx.tenant_id,
            proposal_id=proposal_id,
            payload=payload or ConductorApplyProposalRequest(),
            actor_user_id=ctx.user_id,
        )
        return resp


@router.get("/proposals/{proposal_id}/resulting-version")
async def get_resulting_version_endpoint(
    proposal_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    async with _operation(session, ctx, ConductorAction.VIEW_CONDUCTOR, commit=False):
        return await conductor_service.get_proposal_resulting_version(
            session, ctx.tenant_id, proposal_id
        )
