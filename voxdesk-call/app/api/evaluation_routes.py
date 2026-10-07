"""FastAPI routes for Evaluation Rules, Evaluation Results, and Evaluation Reruns.

All endpoints enforce tenant isolation via ``TenantContext`` and map domain
errors to deterministic HTTP status codes.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Permission, TenantContext, require_permission
from app.core.errors import AppError
from app.db.session import get_session
from app.domain.evaluation_models import (
    EvaluationResultResponse,
    EvaluationRuleCreateRequest,
    EvaluationRuleResponse,
    EvaluationRuleUpdateRequest,
    TestRunResponse,
)
from app.services import evaluation_service, simulation_service

router = APIRouter(prefix="/api/v1/evaluations", tags=["evaluations"])


def _raise_http(exc: AppError) -> None:
    raise HTTPException(
        status_code=getattr(exc, "status_code", getattr(exc, "http_status", 400)),
        detail={
            "code": str(getattr(exc.code, "value", exc.code)),
            "message": exc.message,
            "details": getattr(exc, "detail", getattr(exc, "details", None)),
        },
    ) from exc


class RerunEvaluationRequest(BaseModel):
    allow_mock_judge: bool = True


@router.post(
    "/rules",
    response_model=EvaluationRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_rule_endpoint(
    payload: EvaluationRuleCreateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> EvaluationRuleResponse:
    try:
        row = await evaluation_service.create_evaluation_rule(
            session,
            ctx.tenant_id,
            payload,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return EvaluationRuleResponse.model_validate(row)
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.get("/rules", response_model=list[EvaluationRuleResponse])
async def list_rules_endpoint(
    suite_id: uuid.UUID | None = Query(default=None),
    test_case_id: uuid.UUID | None = Query(default=None),
    enabled_only: bool = Query(default=False),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[EvaluationRuleResponse]:
    try:
        rows = await evaluation_service.list_evaluation_rules(
            session,
            ctx.tenant_id,
            suite_id=suite_id,
            test_case_id=test_case_id,
            enabled_only=enabled_only,
        )
        return [EvaluationRuleResponse.model_validate(r) for r in rows]
    except AppError as exc:
        _raise_http(exc)


@router.get("/rules/{rule_id}", response_model=EvaluationRuleResponse)
async def get_rule_endpoint(
    rule_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> EvaluationRuleResponse:
    try:
        row = await evaluation_service.get_evaluation_rule(session, ctx.tenant_id, rule_id)
        return EvaluationRuleResponse.model_validate(row)
    except AppError as exc:
        _raise_http(exc)


@router.patch("/rules/{rule_id}", response_model=EvaluationRuleResponse)
async def update_rule_endpoint(
    rule_id: uuid.UUID,
    payload: EvaluationRuleUpdateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> EvaluationRuleResponse:
    try:
        row = await evaluation_service.update_evaluation_rule(
            session,
            ctx.tenant_id,
            rule_id,
            payload,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return EvaluationRuleResponse.model_validate(row)
    except ValueError as exc:
        await session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.delete("/rules/{rule_id}", response_model=dict[str, Any])
async def delete_rule_endpoint(
    rule_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        await evaluation_service.delete_evaluation_rule(
            session,
            ctx.tenant_id,
            rule_id,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return {"deleted": True, "id": str(rule_id)}
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.get(
    "/runs/{run_id}/results",
    response_model=list[EvaluationResultResponse],
)
async def list_run_evaluation_results_endpoint(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[EvaluationResultResponse]:
    try:
        await simulation_service.get_test_run_row(session, ctx.tenant_id, run_id)
        rows = await evaluation_service.list_evaluation_results_for_run(
            session, ctx.tenant_id, run_id
        )
        return [EvaluationResultResponse.model_validate(r) for r in rows]
    except AppError as exc:
        _raise_http(exc)


@router.post("/runs/{run_id}/rerun", response_model=TestRunResponse)
async def rerun_evaluation_endpoint(
    run_id: uuid.UUID,
    payload: RerunEvaluationRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    """Re-evaluate an existing TestRun against updated rules without rerunning the conversation."""
    try:
        allow_mock = payload.allow_mock_judge if payload is not None else True
        resp = await simulation_service.rerun_evaluation_only(
            session,
            ctx.tenant_id,
            run_id,
            allow_mock_judge=allow_mock,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)
