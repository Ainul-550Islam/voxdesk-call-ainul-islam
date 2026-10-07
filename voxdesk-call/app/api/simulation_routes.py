"""FastAPI routes for Test Suites, Version-Pinned Test Cases, LLM Playground, Multi-Turn Simulations, and Batch Suite Runs."""
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
    BatchRunAggregationSummary,
    BatchSuiteRunRequest,
    LLMPlaygroundRunRequest,
    MultiTurnSimulationRequest,
    TestCaseCreateRequest,
    TestCaseResponse,
    TestCaseUpdateRequest,
    TestRunResponse,
    TestSuiteCreateRequest,
    TestSuiteResponse,
    TestSuiteUpdateRequest,
)
from app.services import simulation_service

router = APIRouter(prefix="/api/v1/testing", tags=["testing-simulations"])


def _raise_http(exc: AppError) -> None:
    raise HTTPException(
        status_code=getattr(exc, "status_code", getattr(exc, "http_status", 400)),
        detail={
            "code": str(getattr(exc.code, "value", exc.code)),
            "message": exc.message,
            "details": getattr(exc, "detail", getattr(exc, "details", None)),
        },
    ) from exc


def _case_to_response(row: Any) -> TestCaseResponse:
    return TestCaseResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        suite_id=row.suite_id,
        agent_id=row.agent_id,
        agent_kind=row.agent_kind,
        agent_version_id=row.agent_version_id,
        agent_version_number=row.agent_version_number,
        name=row.name,
        mode=row.mode,
        input_messages=list(row.input_messages or []),
        dynamic_variables=dict(row.dynamic_variables or {}),
        metadata=dict(row.metadata_json or {}),
        expected_rules=list(row.expected_rules or []),
        enabled=bool(row.enabled),
        archived_at=row.archived_at,
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SingleCaseRunRequest(BaseModel):
    agent_version_override: int | None = None
    dynamic_variables_override: dict[str, Any] = {}
    allow_mock_fallback: bool = True


# ----------------------------------------------------------- Test Suites CRUD


@router.post(
    "/suites",
    response_model=TestSuiteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_suite_endpoint(
    payload: TestSuiteCreateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestSuiteResponse:
    try:
        resp = await simulation_service.create_test_suite(
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


@router.get("/suites", response_model=list[TestSuiteResponse])
async def list_suites_endpoint(
    status_filter: str | None = Query(default=None, alias="status"),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[TestSuiteResponse]:
    try:
        return await simulation_service.list_test_suites(
            session,
            ctx.tenant_id,
            status=status_filter,
        )
    except AppError as exc:
        _raise_http(exc)


@router.get("/suites/{suite_id}", response_model=TestSuiteResponse)
async def get_suite_endpoint(
    suite_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> TestSuiteResponse:
    try:
        row = await simulation_service.get_test_suite(session, ctx.tenant_id, suite_id)
        return await simulation_service._enrich_suite_response(session, row)
    except AppError as exc:
        _raise_http(exc)


@router.patch("/suites/{suite_id}", response_model=TestSuiteResponse)
async def update_suite_endpoint(
    suite_id: uuid.UUID,
    payload: TestSuiteUpdateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestSuiteResponse:
    try:
        resp = await simulation_service.update_test_suite(
            session,
            ctx.tenant_id,
            suite_id,
            payload,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.post("/suites/{suite_id}/archive", response_model=TestSuiteResponse)
async def archive_suite_endpoint(
    suite_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestSuiteResponse:
    try:
        resp = await simulation_service.archive_test_suite(
            session,
            ctx.tenant_id,
            suite_id,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


# ------------------------------------------------------------ Test Cases CRUD


@router.post(
    "/cases",
    response_model=TestCaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_case_endpoint(
    payload: TestCaseCreateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    try:
        row = await simulation_service.create_test_case(
            session,
            ctx.tenant_id,
            payload,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return _case_to_response(row)
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.get("/cases", response_model=list[TestCaseResponse])
async def list_cases_endpoint(
    suite_id: uuid.UUID | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    enabled_only: bool = Query(default=False),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[TestCaseResponse]:
    try:
        rows = await simulation_service.list_test_cases(
            session,
            ctx.tenant_id,
            suite_id=suite_id,
            agent_id=agent_id,
            enabled_only=enabled_only,
        )
        return [_case_to_response(r) for r in rows]
    except AppError as exc:
        _raise_http(exc)


@router.get("/cases/{case_id}", response_model=TestCaseResponse)
async def get_case_endpoint(
    case_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    try:
        row = await simulation_service.get_test_case(session, ctx.tenant_id, case_id)
        return _case_to_response(row)
    except AppError as exc:
        _raise_http(exc)


@router.patch("/cases/{case_id}", response_model=TestCaseResponse)
async def update_case_endpoint(
    case_id: uuid.UUID,
    payload: TestCaseUpdateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    try:
        row = await simulation_service.update_test_case(
            session,
            ctx.tenant_id,
            case_id,
            payload,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return _case_to_response(row)
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.delete("/cases/{case_id}", response_model=dict[str, Any])
async def delete_case_endpoint(
    case_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        await simulation_service.delete_test_case(
            session,
            ctx.tenant_id,
            case_id,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return {"deleted": True, "id": str(case_id)}
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


# ----------------------------------------- Execution: Case, Batch, Playground


@router.post("/cases/{case_id}/run", response_model=TestRunResponse)
async def run_case_endpoint(
    case_id: uuid.UUID,
    payload: SingleCaseRunRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        req = payload or SingleCaseRunRequest()
        resp = await simulation_service.run_single_test_case(
            session,
            ctx.tenant_id,
            case_id,
            agent_version_override=req.agent_version_override,
            dynamic_variables_override=req.dynamic_variables_override,
            allow_mock_fallback=req.allow_mock_fallback,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.post(
    "/suites/{suite_id}/run-batch",
    response_model=BatchRunAggregationSummary,
)
async def run_batch_suite_endpoint(
    suite_id: uuid.UUID,
    payload: BatchSuiteRunRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> BatchRunAggregationSummary:
    try:
        req = payload or BatchSuiteRunRequest()
        resp = await simulation_service.run_batch_suite(
            session,
            ctx.tenant_id,
            suite_id,
            case_ids=req.case_ids,
            agent_version_override=req.agent_version_override,
            dynamic_variables_override=req.dynamic_variables_override,
            allow_mock_fallback=req.allow_mock_fallback,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.post("/playground/run", response_model=TestRunResponse)
async def run_llm_playground_endpoint(
    payload: LLMPlaygroundRunRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        resp = await simulation_service.run_llm_playground(
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


@router.post("/simulations/run", response_model=TestRunResponse)
async def run_multi_turn_simulation_endpoint(
    payload: MultiTurnSimulationRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        resp = await simulation_service.run_multi_turn_simulation(
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


# ------------------------------------------------------------ Runs Inspection


@router.get("/runs", response_model=list[TestRunResponse])
async def list_runs_endpoint(
    suite_id: uuid.UUID | None = Query(default=None),
    test_case_id: uuid.UUID | None = Query(default=None),
    batch_id: str | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    agent_version_number: int | None = Query(default=None),
    mode: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[TestRunResponse]:
    try:
        return await simulation_service.list_test_runs(
            session,
            ctx.tenant_id,
            suite_id=suite_id,
            test_case_id=test_case_id,
            batch_id=batch_id,
            agent_id=agent_id,
            agent_version_number=agent_version_number,
            mode=mode,
            status=status_filter,
            limit=limit,
        )
    except AppError as exc:
        _raise_http(exc)


@router.get("/runs/{run_id}", response_model=TestRunResponse)
async def get_run_endpoint(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        return await simulation_service.get_test_run(session, ctx.tenant_id, run_id)
    except AppError as exc:
        _raise_http(exc)


@router.post("/runs/{run_id}/cancel", response_model=TestRunResponse)
async def cancel_run_endpoint(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        resp = await simulation_service.cancel_test_run(
            session,
            ctx.tenant_id,
            run_id,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)
