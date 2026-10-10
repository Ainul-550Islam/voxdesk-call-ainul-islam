"""FastAPI routes for Test Suites, Test Cases, LLM Playground, Multi-Turn Simulation, Simulated Caller, Regression-from-Call, and Batch Execution (`/api/v1/testing`)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Permission, TenantContext, require_permission
from app.core.errors import AppError
from app.db.models import TestCase
from app.db.session import get_session
from app.domain.evaluation_models import (
    AgentKind,
    BatchRunAggregationSummary,
    BatchSuiteRunRequest,
    InlineEvaluationRuleSpec,
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
from app.services import regression_from_calls, simulation_service

router = APIRouter(prefix="/api/v1/testing", tags=["testing-simulation"])


class SimulatedCallerRunRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)
    agent_kind: AgentKind = AgentKind.VOICE
    agent_version_number: int = Field(..., ge=1)
    suite_id: UUID | None = None
    test_case_id: UUID | None = None
    persona: str = Field(..., min_length=1, max_length=500)
    goal: str = Field(..., min_length=1, max_length=1000)
    variables: dict[str, Any] = Field(default_factory=dict)
    interruption_style: str = Field(default="normal", max_length=32)
    seed: int | None = None
    max_turns: int = Field(default=5, ge=1, le=25)
    success_criteria: list[str] = Field(default_factory=list)
    evaluation_rules: list[InlineEvaluationRuleSpec] = Field(default_factory=list)
    allow_mock_fallback: bool = False


class RegressionFromCallRequest(BaseModel):
    suite_id: UUID | None = None
    name: str | None = Field(default=None, max_length=200)
    agent_id: str | None = Field(default=None, max_length=128)
    agent_version_number: int | None = Field(default=None, ge=1)
    redact_pii: bool = True
    pii_policy: dict[str, bool] | None = None


def _raise_http(exc: AppError) -> None:
    raise HTTPException(
        status_code=getattr(exc, "status_code", getattr(exc, "http_status", 400)),
        detail={
            "code": str(getattr(exc.code, "value", exc.code)),
            "message": exc.message,
            "details": getattr(exc, "detail", getattr(exc, "details", None)),
        },
    ) from exc


def _case_to_response(row: TestCase) -> TestCaseResponse:
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


# ------------------------------------------------------------------ TestSuites


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
            session, ctx.tenant_id, payload, actor_user_id=ctx.user_id
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
    return await simulation_service.list_test_suites(
        session, ctx.tenant_id, status=status_filter
    )


@router.get("/suites/{suite_id}", response_model=TestSuiteResponse)
async def get_suite_endpoint(
    suite_id: UUID,
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
    suite_id: UUID,
    payload: TestSuiteUpdateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestSuiteResponse:
    try:
        resp = await simulation_service.update_test_suite(
            session, ctx.tenant_id, suite_id, payload, actor_user_id=ctx.user_id
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.post("/suites/{suite_id}/archive", response_model=TestSuiteResponse)
async def archive_suite_endpoint(
    suite_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestSuiteResponse:
    try:
        resp = await simulation_service.archive_test_suite(
            session, ctx.tenant_id, suite_id, actor_user_id=ctx.user_id
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


# ------------------------------------------------------------------- TestCases


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
            session, ctx.tenant_id, payload, actor_user_id=ctx.user_id
        )
        await session.commit()
        return _case_to_response(row)
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.get("/cases", response_model=list[TestCaseResponse])
async def list_cases_endpoint(
    suite_id: UUID | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    enabled_only: bool = Query(default=False),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[TestCaseResponse]:
    rows = await simulation_service.list_test_cases(
        session,
        ctx.tenant_id,
        suite_id=suite_id,
        agent_id=agent_id,
        enabled_only=enabled_only,
    )
    return [_case_to_response(r) for r in rows]


@router.get("/cases/{case_id}", response_model=TestCaseResponse)
async def get_case_endpoint(
    case_id: UUID,
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
    case_id: UUID,
    payload: TestCaseUpdateRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    try:
        row = await simulation_service.update_test_case(
            session, ctx.tenant_id, case_id, payload, actor_user_id=ctx.user_id
        )
        await session.commit()
        return _case_to_response(row)
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.delete("/cases/{case_id}")
async def delete_case_endpoint(
    case_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        await simulation_service.delete_test_case(
            session, ctx.tenant_id, case_id, actor_user_id=ctx.user_id
        )
        await session.commit()
        return {"deleted": True, "id": str(case_id)}
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.post("/cases/{case_id}/run", response_model=TestRunResponse)
async def run_single_case_endpoint(
    case_id: UUID,
    request: Request,
    agent_version_override: int | None = Query(default=None, ge=1),
    allow_mock_fallback: bool = Query(default=True),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    eff_version = agent_version_override
    eff_fallback = allow_mock_fallback
    try:
        body = await request.json()
    except Exception:
        __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
        body = None
    if isinstance(body, dict):
        if eff_version is None and body.get("agent_version_override") is not None:
            eff_version = int(body["agent_version_override"])
        if "allow_mock_fallback" in body and body["allow_mock_fallback"] is not None:
            eff_fallback = bool(body["allow_mock_fallback"])
    try:
        resp = await simulation_service.run_single_test_case(
            session,
            ctx.tenant_id,
            case_id,
            agent_version_override=eff_version,
            allow_mock_fallback=eff_fallback,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.post(
    "/regressions/from-call/{call_id}",
    response_model=TestCaseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_regression_from_call_endpoint(
    call_id: UUID,
    payload: RegressionFromCallRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestCaseResponse:
    req = payload or RegressionFromCallRequest()
    try:
        row = await regression_from_calls.create_regression_test_from_call(
            session,
            ctx.tenant_id,
            call_id,
            suite_id=req.suite_id,
            name=req.name,
            agent_id=req.agent_id,
            agent_version_number=req.agent_version_number,
            redact_pii=req.redact_pii,
            pii_policy=req.pii_policy,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return _case_to_response(row)
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


# ------------------------------------------- Playground, Simulation & Batch Run


@router.post("/suites/{suite_id}/run-batch", response_model=BatchRunAggregationSummary)
async def run_batch_suite_endpoint(
    suite_id: UUID,
    payload: BatchSuiteRunRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> BatchRunAggregationSummary:
    req = payload or BatchSuiteRunRequest()
    try:
        summary = await simulation_service.run_batch_suite(
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
        return summary
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.post("/playground/run", response_model=TestRunResponse)
async def run_playground_endpoint(
    payload: LLMPlaygroundRunRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        resp = await simulation_service.run_llm_playground(
            session, ctx.tenant_id, payload, actor_user_id=ctx.user_id
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.post("/simulations/run", response_model=TestRunResponse)
async def run_simulation_endpoint(
    payload: MultiTurnSimulationRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        resp = await simulation_service.run_multi_turn_simulation(
            session, ctx.tenant_id, payload, actor_user_id=ctx.user_id
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.post("/simulations/caller-run", response_model=TestRunResponse)
async def run_simulated_caller_endpoint(
    payload: SimulatedCallerRunRequest,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        resp = await simulation_service.execute_simulated_caller_run(
            session,
            ctx.tenant_id,
            agent_id=payload.agent_id,
            agent_version_number=payload.agent_version_number,
            agent_kind=payload.agent_kind.value,
            suite_id=payload.suite_id,
            test_case_id=payload.test_case_id,
            persona=payload.persona,
            goal=payload.goal,
            variables=payload.variables,
            interruption_style=payload.interruption_style,
            seed=payload.seed,
            max_turns=payload.max_turns,
            success_criteria=payload.success_criteria,
            inline_rules=[r.model_dump(mode="json") for r in payload.evaluation_rules],
            allow_mock_fallback=payload.allow_mock_fallback,
            actor_user_id=ctx.user_id,
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)


@router.get("/kpis")
async def get_testing_kpis_endpoint(
    suite_id: UUID | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    return await simulation_service.compute_simulation_kpis(
        session,
        ctx.tenant_id,
        suite_id=suite_id,
        agent_id=agent_id,
    )


@router.get("/runs", response_model=list[TestRunResponse])
async def list_runs_endpoint(
    suite_id: UUID | None = Query(default=None),
    test_case_id: UUID | None = Query(default=None),
    batch_id: str | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    agent_version_number: int | None = Query(default=None, ge=1),
    mode: str | None = Query(default=None),
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[TestRunResponse]:
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


@router.get("/runs/{run_id}", response_model=TestRunResponse)
async def get_run_endpoint(
    run_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        return await simulation_service.get_test_run(session, ctx.tenant_id, run_id)
    except AppError as exc:
        _raise_http(exc)


@router.post("/runs/{run_id}/cancel", response_model=TestRunResponse)
async def cancel_run_endpoint(
    run_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> TestRunResponse:
    try:
        resp = await simulation_service.cancel_test_run(
            session, ctx.tenant_id, run_id, actor_user_id=ctx.user_id
        )
        await session.commit()
        return resp
    except AppError as exc:
        await session.rollback()
        _raise_http(exc)
