"""QA reviews, scorecards, sampling and exports.

The tenant is the authenticated principal. A client ``tenant_id`` that does
not match is a 404, not a switch.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.qa import calibration, compliance, exports, metrics, rubrics, sampling, service
from app.qa.exceptions import QaError
from app.qa.repository import get_review, get_scorecard, list_reviews, list_scorecards
from app.tenancy.context import reject_client_override
from app.tenancy.isolation import HierarchyError, to_http
from app.tenancy.policy import bind_tenant

router = APIRouter(tags=["qa"])


class ScorecardBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    sections: list[dict]
    pass_threshold: int = Field(default=7000, ge=0, le=10000)
    description: str = ""
    tenant_id: uuid.UUID | None = None


class ReviewBody(BaseModel):
    call_id: uuid.UUID
    scorecard_id: uuid.UUID
    priority: int = 0
    idempotency_key: str | None = None
    environment_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


class AssignBody(BaseModel):
    user_id: uuid.UUID
    tenant_id: uuid.UUID | None = None


class ScoreBody(BaseModel):
    item_id: uuid.UUID
    score: int | None = None
    na: bool = False
    override_reason: str = ""


class ScoresBody(BaseModel):
    items: list[ScoreBody]
    tenant_id: uuid.UUID | None = None


class ClaimBody(BaseModel):
    tenant_id: uuid.UUID | None = None


class CoachingBody(BaseModel):
    agent_user_id: uuid.UUID
    category: str
    recommendation: str = ""
    priority: str = "normal"
    turn_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


class SampleBody(BaseModel):
    name: str
    kind: str
    window_key: str
    call_ids: list[uuid.UUID]
    percent: int = 0
    sample_count: int = 0
    min_per_agent: int = 0
    disposition: str = ""
    tenant_id: uuid.UUID | None = None


def _scope(ctx: TenantContext, tenant_id: uuid.UUID | None, claimed: uuid.UUID | None) -> uuid.UUID:
    reject_client_override(ctx, claimed)
    if tenant_id is not None:
        bind_tenant(ctx, tenant_id, claimed)
    return ctx.tenant_id


def _qa(exc: QaError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.as_dict())


@router.get("/api/qa/reviews")
async def get_reviews(
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await list_reviews(session, ctx.tenant_id)
    return {"reviews": [row.as_dict() for row in rows]}


@router.post("/api/qa/reviews", status_code=201)
async def post_review(
    payload: ReviewBody,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, None, payload.tenant_id)
        row, outcome = await service.create_review(
            session,
            tenant_id=tenant,
            call_id=payload.call_id,
            scorecard_id=payload.scorecard_id,
            actor_id=ctx.user_id,
            idempotency_key=payload.idempotency_key,
            priority=payload.priority,
            environment_id=payload.environment_id,
        )
        await session.commit()
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    body = row.as_dict()
    body["outcome"] = outcome
    return body


@router.get("/api/qa/reviews/{review_id}")
async def get_one(
    review_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await get_review(session, ctx.tenant_id, review_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.post("/api/qa/reviews/{review_id}/assign")
async def post_assign(
    review_id: uuid.UUID,
    payload: AssignBody,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, None, payload.tenant_id)
        row, outcome = await service.assign_review(
            session,
            tenant_id=tenant,
            review_id=review_id,
            assignee_id=payload.user_id,
            actor_id=ctx.user_id,
        )
        await session.commit()
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    body = row.as_dict()
    body["outcome"] = outcome
    return body


@router.post("/api/qa/reviews/{review_id}/scores")
async def post_scores(
    review_id: uuid.UUID,
    payload: ScoresBody,
    ctx: TenantContext = Depends(require_permission(Permission.QA_REVIEW)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _scope(ctx, None, payload.tenant_id)
        row = await service.save_scores(
            session,
            tenant_id=ctx.tenant_id,
            review_id=review_id,
            actor_id=ctx.user_id,
            scores=[item.model_dump() for item in payload.items],
        )
        await session.commit()
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.post("/api/qa/reviews/{review_id}/submit")
async def post_submit(
    review_id: uuid.UUID,
    payload: ClaimBody | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.QA_REVIEW)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _scope(ctx, None, None if payload is None else payload.tenant_id)
        row = await service.submit_review(
            session, tenant_id=ctx.tenant_id, review_id=review_id, actor_id=ctx.user_id
        )
        await session.commit()
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.post("/api/qa/reviews/{review_id}/finalize")
async def post_finalize(
    review_id: uuid.UUID,
    payload: ClaimBody | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.QA_FINALIZE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _scope(ctx, None, None if payload is None else payload.tenant_id)
        row, outcome = await service.finalize_review(
            session, tenant_id=ctx.tenant_id, review_id=review_id, actor_id=ctx.user_id
        )
        await session.commit()
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    body = row.as_dict()
    body["outcome"] = outcome
    return body


@router.post("/api/qa/reviews/{review_id}/reopen")
async def post_reopen(
    review_id: uuid.UUID,
    payload: ClaimBody | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.QA_FINALIZE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _scope(ctx, None, None if payload is None else payload.tenant_id)
        row, outcome = await service.reopen_review(
            session, tenant_id=ctx.tenant_id, review_id=review_id, actor_id=ctx.user_id
        )
        await session.commit()
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    body = row.as_dict()
    body["outcome"] = outcome
    return body


@router.post("/api/qa/reviews/{review_id}/auto-review")
async def post_auto(
    review_id: uuid.UUID,
    payload: ClaimBody | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _scope(ctx, None, None if payload is None else payload.tenant_id)
        run, outcome = await service.request_auto_review(
            session,
            tenant_id=ctx.tenant_id,
            review_id=review_id,
            actor_id=ctx.user_id,
            organization_id=ctx.organization_id,
        )
        await session.commit()
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    body = run.as_dict()
    body["outcome"] = outcome
    return body


@router.post("/api/qa/reviews/{review_id}/coaching", status_code=201)
async def post_coaching(
    review_id: uuid.UUID,
    payload: CoachingBody,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _scope(ctx, None, payload.tenant_id)
        row = await service.add_coaching(
            session,
            tenant_id=ctx.tenant_id,
            review_id=review_id,
            actor_id=ctx.user_id,
            agent_user_id=payload.agent_user_id,
            category=payload.category,
            recommendation=payload.recommendation,
            priority=payload.priority,
            turn_id=payload.turn_id,
        )
        await session.commit()
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.get("/api/qa/scorecards")
async def get_scorecards(
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await list_scorecards(session, ctx.tenant_id)
    return {"scorecards": [row.as_dict() for row in rows]}


@router.post("/api/qa/scorecards", status_code=201)
async def post_scorecard(
    payload: ScorecardBody,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, None, payload.tenant_id)
        row = await rubrics.create_scorecard(
            session,
            tenant_id=tenant,
            name=payload.name,
            sections=payload.sections,
            pass_threshold=payload.pass_threshold,
            description=payload.description,
        )
        await session.commit()
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.get("/api/qa/scorecards/{scorecard_id}")
async def get_scorecard_one(
    scorecard_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await get_scorecard(session, ctx.tenant_id, scorecard_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.get("/api/qa/metrics")
async def get_metrics(
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    return await metrics.snapshot(session, ctx.tenant_id)


@router.get("/api/qa/exports")
async def get_export(
    fmt: str = "json",
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        rows = await exports.rows_for(session, ctx.tenant_id, actor_tenant_id=ctx.tenant_id)
    except QaError as exc:
        raise _qa(exc) from None
    if fmt == "csv":
        return Response(content=exports.to_csv(rows), media_type="text/csv")
    return Response(content=exports.to_json(rows), media_type="application/json")


@router.post("/api/qa/sampling/run")
async def post_sample(
    payload: SampleBody,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, None, payload.tenant_id)
        rule = await sampling.create_rule(
            session,
            tenant_id=tenant,
            name=payload.name,
            kind=payload.kind,
            percent=payload.percent,
            sample_count=payload.sample_count,
            min_per_agent=payload.min_per_agent,
            disposition=payload.disposition,
        )
        rows = await sampling.run_rule(
            session,
            tenant_id=tenant,
            rule_id=rule.id,
            window_key=payload.window_key,
            call_ids=payload.call_ids,
        )
        await session.commit()
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"rule": rule.as_dict(), "selections": [row.as_dict() for row in rows]}


@router.post("/api/qa/calibration", status_code=201)
async def post_calibration(
    payload: ClaimBody,
    ctx: TenantContext = Depends(require_permission(Permission.QA_FINALIZE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        _scope(ctx, None, payload.tenant_id)
        row = await calibration.start(
            session, tenant_id=ctx.tenant_id, name="calibration", created_by=ctx.user_id
        )
        await session.commit()
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.post("/api/qa/compliance/policies", status_code=201)
async def post_policy(
    payload: dict,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        reject_client_override(ctx, payload.get("tenant_id") and uuid.UUID(str(payload["tenant_id"])))
        row = await compliance.create_policy(
            session,
            tenant_id=ctx.tenant_id,
            code=str(payload.get("code") or ""),
            category=str(payload.get("category") or ""),
            severity=str(payload.get("severity") or "medium"),
            required_phrase=str(payload.get("required_phrase") or ""),
        )
        await session.commit()
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


# ----------------------- Prompt 3: Evidence-Backed TestRun QA Scorecard Routes


@router.get("/api/qa/test-runs/{run_id}/scorecard")
async def get_test_run_qa_scorecard(
    run_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    from app.core.errors import AppError
    from app.services import qa_service

    try:
        return await qa_service.get_run_qa_scorecard(session, ctx.tenant_id, run_id)
    except AppError as exc:
        raise HTTPException(
            status_code=getattr(exc, "status_code", getattr(exc, "http_status", 400)),
            detail={
                "code": str(getattr(exc.code, "value", exc.code)),
                "message": exc.message,
                "details": getattr(exc, "detail", getattr(exc, "details", None)),
            },
        ) from exc


@router.get("/api/qa/agents/{agent_id}/summary")
async def get_agent_qa_summary_endpoint(
    agent_id: str,
    agent_version_number: int | None = None,
    limit: int = 100,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    from app.core.errors import AppError
    from app.services import qa_service

    try:
        return await qa_service.get_agent_version_qa_summary(
            session,
            ctx.tenant_id,
            agent_id,
            agent_version_number=agent_version_number,
            limit=limit,
        )
    except AppError as exc:
        raise HTTPException(
            status_code=getattr(exc, "status_code", getattr(exc, "http_status", 400)),
            detail={
                "code": str(getattr(exc.code, "value", exc.code)),
                "message": exc.message,
                "details": getattr(exc, "detail", getattr(exc, "details", None)),
            },
        ) from exc


@router.get("/api/qa/agents/{agent_id}/compare-versions")
async def compare_agent_versions_qa_endpoint(
    agent_id: str,
    version_a: int,
    version_b: int,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    from app.core.errors import AppError
    from app.services import qa_service

    try:
        return await qa_service.compare_agent_versions_qa(
            session,
            ctx.tenant_id,
            agent_id,
            version_a=version_a,
            version_b=version_b,
        )
    except AppError as exc:
        raise HTTPException(
            status_code=getattr(exc, "status_code", getattr(exc, "http_status", 400)),
            detail={
                "code": str(getattr(exc.code, "value", exc.code)),
                "message": exc.message,
                "details": getattr(exc, "detail", getattr(exc, "details", None)),
            },
        ) from exc
