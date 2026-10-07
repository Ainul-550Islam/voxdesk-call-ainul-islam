"""Evaluation API.

Runs score outputs with the existing harness. This endpoint does not call a
live provider: an unconfigured executor is a failure, not a made-up score.
Results omit prompt and model text.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.identity.events import emit
from app.auth.permissions import Permission
from app.db.models import AuditAction
from app.db.session import get_session
from app.ai.gateway import cases_from_payload, score_cases
from app.ai.models import AIEvalDataset, AIEvalRun, GovernanceError
from app.tenancy.isolation import HierarchyError, NotFound, ValidationFailed, client_ip, to_http
from app.tenancy.policy import bind_tenant, require_permission as require_can

router = APIRouter(prefix="/api/tenants/{tenant_id}/ai/evals", tags=["ai-evals"])

MAX_CASES = 20


class DatasetIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    cases: list[dict] = Field(default_factory=list)
    tenant_id: uuid.UUID | None = None


class RunIn(BaseModel):
    dataset_id: uuid.UUID
    outputs: dict[str, str] | None = None
    tenant_id: uuid.UUID | None = None


def _dataset_out(row: AIEvalDataset) -> dict:
    return {
        "id": str(row.id),
        "name": row.name,
        "case_count": len(row.cases or []),
    }


@router.get("/datasets")
async def list_datasets(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        bind_tenant(ctx, tenant_id)
        rows = await session.scalars(
            select(AIEvalDataset)
            .where(AIEvalDataset.tenant_id == ctx.tenant_id)
            .order_by(AIEvalDataset.name)
        )
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"datasets": [_dataset_out(row) for row in rows]}


@router.post("/datasets", status_code=201)
async def create_dataset(
    tenant_id: uuid.UUID,
    payload: DatasetIn,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        bind_tenant(ctx, tenant_id, payload.tenant_id)
        if not ctx.can(Permission.TENANT_UPDATE):
            require_can(ctx, Permission.TENANT_UPDATE)
        if len(payload.cases) > MAX_CASES:
            raise ValidationFailed("Evaluation dataset exceeds the case limit")
        cases_from_payload(payload.cases)
        row = AIEvalDataset(tenant_id=ctx.tenant_id, name=payload.name.strip(), cases=payload.cases)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _dataset_out(row)


@router.post("/runs", status_code=201)
async def create_run(
    tenant_id: uuid.UUID,
    payload: RunIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        bind_tenant(ctx, tenant_id, payload.tenant_id)
        if not ctx.can(Permission.TENANT_UPDATE):
            await emit(
                session,
                AuditAction.AUTHZ_DENIED,
                tenant_id=ctx.tenant_id,
                actor_user_id=ctx.user_id,
                actor_email=ctx.user.email,
                ip_address=client_ip(request),
                detail={"operation": "eval_run", "reason": "permission_denied"},
                commit=False,
            )
            await session.commit()
            require_can(ctx, Permission.TENANT_UPDATE)
        dataset = await session.get(AIEvalDataset, payload.dataset_id)
        if dataset is None or dataset.tenant_id != ctx.tenant_id:
            raise NotFound()
        cases = cases_from_payload(list(dataset.cases or []))
        if payload.outputs is None:
            result = {
                "passed": False,
                "cases": [],
                "reason": "executor_not_configured",
                "score_invented": False,
                "prompt_stored_in_result": False,
            }
            status = "failed"
        else:

            def _model(prompt: str) -> str:
                # The prompt is the harness input. It is not copied into the stored result.
                for case in cases:
                    if case.prompt == prompt:
                        return payload.outputs.get(case.name, "")
                return ""

            result = score_cases(cases, _model)
            status = "completed" if result["passed"] or result["cases"] else "failed"
        run = AIEvalRun(
            tenant_id=ctx.tenant_id,
            dataset_id=dataset.id,
            status=status,
            actor_user_id=ctx.user_id,
            result=result,
            case_limit=MAX_CASES,
            finished_at=datetime.utcnow(),
        )
        session.add(run)
        await emit(
            session,
            AuditAction.SECURITY_SETTINGS_CHANGED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            actor_email=ctx.user.email,
            ip_address=client_ip(request),
            detail={
                "operation": "eval_run",
                "dataset_id": str(dataset.id),
                "status": status,
                "passed": bool(result.get("passed")),
            },
            commit=False,
        )
        await session.commit()
        await session.refresh(run)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"id": str(run.id), "status": run.status, "result": run.result}


@router.get("/runs/{run_id}")
async def get_run(
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        bind_tenant(ctx, tenant_id)
        run = await session.get(AIEvalRun, run_id)
        if run is None or run.tenant_id != ctx.tenant_id:
            raise NotFound()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"id": str(run.id), "status": run.status, "result": run.result}


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        bind_tenant(ctx, tenant_id)
        run = await session.get(AIEvalRun, run_id)
        if run is None or run.tenant_id != ctx.tenant_id:
            raise NotFound()
        if run.status == "completed":
            raise GovernanceError("A completed evaluation cannot be cancelled")
        run.cancel_requested = True
        run.status = "cancelled"
        run.finished_at = datetime.utcnow()
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"id": str(run.id), "status": run.status}
