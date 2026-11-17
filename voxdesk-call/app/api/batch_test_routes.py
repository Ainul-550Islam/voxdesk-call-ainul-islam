# File: app/api/batch_test_routes.py — create cases, run them in bulk, poll the run
"""Batch test API (PART 15, P15-08).

Shape: create cases, start a run, poll the run. The run is executed in the
request here because the executor is bounded by the case count; on a deployment
that wants it off the request path, the same ``execute_run`` is what a worker
calls, and the run row is already the job record.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.services import batch_test_service as service

router = APIRouter(prefix="/api/batch-tests", tags=["batch-tests"])


class CaseBody(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    user_message: str = Field(min_length=1, max_length=8000)
    expected_contains: list[str] = Field(default_factory=list)
    forbidden_contains: list[str] = Field(default_factory=list)
    max_latency_ms: int | None = Field(default=None, ge=1, le=600_000)
    agent_version_number: int | None = Field(default=None, ge=1)
    enabled: bool = True


class StartRunBody(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    case_ids: list[uuid.UUID] | None = None
    run_now: bool = True


class ImportBody(BaseModel):
    agent_id: str = Field(min_length=1, max_length=120)
    payload: Any


def _error(exc: service.BatchTestError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.as_dict())


@router.get("/cases")
async def list_cases(
    agent_id: str = Query(min_length=1, max_length=120),
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await service.list_cases(session, ctx.tenant_id, agent_id)
    return {"cases": [service.case_as_dict(row) for row in rows], "agent_id": agent_id}


@router.post("/cases", status_code=201)
async def create_case(
    payload: CaseBody,
    agent_id: str = Query(min_length=1, max_length=120),
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        row, action = await service.upsert_case(
            session,
            ctx.tenant_id,
            agent_id=agent_id,
            name=payload.name,
            user_message=payload.user_message,
            expected_contains=payload.expected_contains,
            forbidden_contains=payload.forbidden_contains,
            max_latency_ms=payload.max_latency_ms,
            agent_version_number=payload.agent_version_number,
            enabled=payload.enabled,
        )
        await session.commit()
    except service.BatchTestError as exc:
        raise _error(exc) from None
    return {**service.case_as_dict(row), "outcome": action}


@router.delete("/cases/{case_id}", status_code=204)
async def delete_case(
    case_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    if not await service.delete_case(session, ctx.tenant_id, case_id):
        raise HTTPException(status_code=404, detail={"code": "not_found"})
    await session.commit()
    return None


@router.post("/import")
async def import_cases(
    payload: ImportBody,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        report = await service.import_cases(
            session, ctx.tenant_id, agent_id=payload.agent_id, payload=payload.payload
        )
        await session.commit()
    except service.BatchTestError as exc:
        raise _error(exc) from None
    return report.as_dict()


@router.get("/export")
async def export_cases(
    agent_id: str = Query(min_length=1, max_length=120),
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await service.export_cases(session, ctx.tenant_id, agent_id)


@router.post("/runs", status_code=201)
async def start_run(
    payload: StartRunBody,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        run = await service.start_run(
            session,
            ctx.tenant_id,
            agent_id=payload.agent_id,
            created_by=str(ctx.user_id) if ctx.user_id else None,
        )
    except service.BatchTestError as exc:
        raise _error(exc) from None

    if not payload.run_now:
        await session.commit()
        return service.run_as_dict(run)

    executor = await service.default_executor(session, ctx.tenant_id)
    run = await service.execute_run(
        session, run, executor=executor, case_ids=payload.case_ids
    )
    await session.commit()
    return service.run_as_dict(run)


@router.get("/runs")
async def list_runs(
    agent_id: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=20, ge=1, le=100),
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await service.list_runs(
        session, ctx.tenant_id, agent_id=agent_id, limit=limit
    )
    return {"runs": [service.run_as_dict(row) for row in rows]}


@router.get("/runs/{run_id}")
async def get_run(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        run = await service.get_run(session, ctx.tenant_id, run_id)
    except service.BatchTestError as exc:
        raise _error(exc) from None
    results = await service.list_results(session, run.id)
    return {
        "run": service.run_as_dict(run),
        "results": [service.result_as_dict(row) for row in results],
    }


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        run = await service.cancel_run(session, ctx.tenant_id, run_id)
        await session.commit()
    except service.BatchTestError as exc:
        raise _error(exc) from None
    return service.run_as_dict(run)
