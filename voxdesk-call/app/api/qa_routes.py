"""QA reviews, scorecards, sampling and exports.

The tenant is the authenticated principal. A client ``tenant_id`` that does
not match is a 404, not a switch.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Header, Query
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
        from app.audit.service import record_event
        await record_event(session, tenant_id=ctx.tenant_id, environment_id=(await get_review(session, ctx.tenant_id, review_id)).environment_id,
            actor_user_id=ctx.user_id, event_type="qa.auto_review_requested", resource_type="qa_auto_review_run", resource_id=run.id)
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


class AutomaticSamplingBody(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(min_length=1, max_length=80)
    scorecard_id: uuid.UUID
    percent: int = Field(ge=0, le=100, strict=True)


async def _automatic_scope(session, ctx, *, write=False):
    from app.environments.context_resolution import resolve_environment
    from app.environments.membership import resolve
    from app.auth.rbac import has_permission
    try:
        env = await resolve_environment(session, user=ctx.user, tenant=ctx.tenant, environment_id=ctx.environment_id)
        if env is None:
            raise HTTPException(409, detail={"code": "NOT_CONFIGURED", "capability": "environment"})
        decision = await resolve(session, ctx.user, env, ctx.tenant)
        if not decision.allowed or env.status != "active":
            raise HTTPException(404, detail="environment not found")
        if not has_permission(decision.role, Permission.QA_WRITE if write else Permission.QA_READ):
            raise HTTPException(403, detail="environment permission denied")
        if write:
            from app.core.rate_limit import rate_limit
            try:
                allowed = await rate_limit(f"qa:sampling:{ctx.tenant_id}:{env.id}", 20, 60)
            except RuntimeError:
                raise HTTPException(501, detail={"code": "NOT_CONFIGURED", "capability": "redis_rate_limit"}) from None
            if not allowed:
                raise HTTPException(429, detail="Rate limit exceeded or backend unavailable", headers={"Retry-After": "60"})
        return env.id
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.post("/api/qa/sampling/automatic", status_code=201)
async def create_automatic_sampling(
    payload: AutomaticSamplingBody,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError
    from app.audit.service import record_event
    from app.qa.models import SamplingRule
    from app.resilience.idempotency import claim_request, complete_request, IdempotencyError
    env_id = await _automatic_scope(session, ctx, write=True)
    try:
        claim = await claim_request(session, tenant_id=ctx.tenant_id, environment_id=env_id,
            operation="qa.sampling.create", key=idempotency_key, request_data=payload.model_dump(mode="json"))
        if claim.replayed:
            row = await session.scalar(select(SamplingRule).where(SamplingRule.id == uuid.UUID(claim.receipt.resource_id),
                SamplingRule.tenant_id == ctx.tenant_id, SamplingRule.environment_id == env_id))
            if row is None:
                raise HTTPException(409, detail="Original rule is unavailable")
            return row.as_dict()
        row = await sampling.create_rule(session, tenant_id=ctx.tenant_id, environment_id=env_id,
            name=payload.name, kind="percentage", percent=payload.percent, scorecard_id=payload.scorecard_id)
        await record_event(session, tenant_id=ctx.tenant_id, environment_id=env_id, actor_user_id=ctx.user_id,
            event_type="qa.sampling_created", resource_type="qa_sampling_rule", resource_id=row.id,
            detail={"scorecard_id": str(row.scorecard_id), "percent": row.percent})
        await complete_request(session, claim.receipt, resource_type="qa_sampling_rule", resource_id=row.id)
        await session.commit()
        return row.as_dict()
    except IdempotencyError as exc:
        raise HTTPException(409, detail={"code": exc.code}) from None
    except IntegrityError:
        raise HTTPException(409, detail="Sampling rule name conflicts with existing configuration") from None
    except QaError as exc:
        raise _qa(exc) from None
    except HierarchyError as exc:
        raise to_http(exc) from None


@router.get("/api/qa/sampling/automatic")
async def list_automatic_sampling(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import select, func
    from app.qa.models import SamplingRule
    env_id = await _automatic_scope(session, ctx)
    scope = (SamplingRule.tenant_id == ctx.tenant_id, SamplingRule.environment_id == env_id,
             SamplingRule.scorecard_id.is_not(None))
    total = await session.scalar(select(func.count(SamplingRule.id)).where(*scope))
    rows = (await session.scalars(select(SamplingRule).where(*scope).order_by(SamplingRule.created_at, SamplingRule.id)
                                  .offset(offset).limit(limit))).all()
    return {"rules": [row.as_dict() for row in rows], "total": total, "limit": limit, "offset": offset}


@router.delete("/api/qa/sampling/automatic/{rule_id}")
async def disable_automatic_sampling(
    rule_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import select
    from app.audit.service import record_event
    from app.qa.models import SamplingRule
    env_id = await _automatic_scope(session, ctx, write=True)
    row = await session.scalar(select(SamplingRule).where(SamplingRule.id == rule_id,
        SamplingRule.tenant_id == ctx.tenant_id, SamplingRule.environment_id == env_id,
        SamplingRule.scorecard_id.is_not(None)).with_for_update())
    if row is None:
        raise HTTPException(404, detail="sampling rule not found")
    row.enabled = False
    await record_event(session, tenant_id=ctx.tenant_id, environment_id=env_id, actor_user_id=ctx.user_id,
        event_type="qa.sampling_disabled", resource_type="qa_sampling_rule", resource_id=row.id)
    await session.commit()
    return {"id": str(row.id), "enabled": False}


@router.get("/api/qa/reviews/{review_id}/auto-review")
async def get_auto_review_status(
    review_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import select
    from app.db.models import DurableJob
    from app.qa.models import QAReview, AutoReviewRun
    from app.qa.auto_review import idempotency_key
    from app.jobs.types import JobType
    env_id = await _automatic_scope(session, ctx)
    review = await session.scalar(select(QAReview).where(QAReview.id == review_id,
        QAReview.tenant_id == ctx.tenant_id, QAReview.environment_id == env_id))
    if review is None:
        raise HTTPException(404, detail="review not found")
    run = await session.scalar(select(AutoReviewRun).where(AutoReviewRun.tenant_id == ctx.tenant_id,
        AutoReviewRun.review_id == review.id, AutoReviewRun.idempotency_key == idempotency_key(review)))
    if run is None:
        return {"review_id": str(review.id), "state": "not_requested", "run": None, "job": None}
    job = await session.scalar(select(DurableJob).where(DurableJob.id == run.job_id,
        DurableJob.tenant_id == ctx.tenant_id, DurableJob.environment_id == env_id,
        DurableJob.organization_id == ctx.tenant.organization_id,
        DurableJob.job_type == JobType.QA_AUTO_REVIEW, DurableJob.idempotency_key == run.idempotency_key))
    valid = job is not None and isinstance(job.payload, dict) and type(job.payload.get("scorecard_version")) is int and job.payload == {"review_id": str(review.id), "scorecard_version": review.scorecard_version,
        "target_tenant_id": str(ctx.tenant_id), "target_environment_id": str(env_id)}
    if not valid:
        state, ledger = "inconsistent", None
    else:
        ledger = {"id": str(job.id), "status": job.status, "attempt_count": job.attempt_count,
                  "last_error_category": job.last_error_category}
        state = job.status
        if not run.rubric_snapshot:
            state = "legacy_unverified"
        elif job.status == "succeeded":
            state = "completed" if run.status == "completed" else "inconsistent"
    return {"review_id": str(review.id), "state": state, "run": run.as_dict(), "job": ledger}


# ===========================================================================
# PART 15 — AI QA analyst (P15-05)
#
# A run is metered and stored; a read of the patterns is not. That split is the
# whole cost model: looking at the last run's clusters is free, asking for a new
# analysis is a billable unit and therefore an explicit POST.
# ===========================================================================

from datetime import datetime as _dt  # noqa: E402

from app.qa import analyst as _analyst  # noqa: E402


class AnalystRunBody(BaseModel):
    start: _dt | None = None
    end: _dt | None = None
    agent_id: str | None = Field(default=None, max_length=120)
    tenant_id: uuid.UUID | None = None


@router.post("/api/qa/analyst/run", status_code=201)
async def post_analyst_run(
    payload: AnalystRunBody,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    tenant = _scope(ctx, None, payload.tenant_id)
    analysis = await _analyst.run_analysis(
        session,
        tenant,
        start=payload.start,
        end=payload.end,
        agent_id=payload.agent_id,
    )
    await session.commit()
    run = analysis["run"]
    return {
        "run_id": str(run.id),
        "calls_analysed": run.calls_analysed,
        "metered_units": run.metered_units,
        "metrics": analysis["metrics"],
        "clusters": analysis["clusters"],
        "repeat_patterns": analysis["repeat_patterns"],
    }


@router.get("/api/qa/analyst/runs")
async def get_analyst_runs(
    limit: int = Query(default=20, ge=1, le=100),
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = await _analyst.list_runs(session, ctx.tenant_id, limit=limit)
    return {
        "runs": [
            {
                "id": str(row.id),
                "agent_id": row.agent_id,
                "calls_analysed": row.calls_analysed,
                "metered_units": row.metered_units,
                "metrics": dict(row.metrics or {}),
                "clusters": list(row.clusters or []),
                "window_start": row.window_start.isoformat() if row.window_start else None,
                "window_end": row.window_end.isoformat() if row.window_end else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    }


@router.get("/api/qa/analyst/patterns")
async def get_analyst_patterns(
    hours: int = Query(default=72, ge=1, le=720),
    agent_id: str | None = Query(default=None, max_length=120),
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    """Repeat contacts and issue clusters for a window, without recording a run."""
    from datetime import timedelta as _td

    end = _dt.now().astimezone().replace(tzinfo=None)
    analysis = await _analyst.analyse_tenant(
        session,
        ctx.tenant_id,
        start=end - _td(hours=hours),
        end=end,
        agent_id=agent_id,
        repeat_window_hours=hours,
    )
    return {
        "hours": hours,
        "agent_id": agent_id,
        "metrics": analysis["metrics"],
        "clusters": analysis["clusters"],
        "repeat_patterns": analysis["repeat_patterns"],
        "metered": False,
    }


# ===========================================================================
# PART 15 (P15-09) — guardrail policy toggles and block history
#
# The policy is per agent (`agent_id='*'` is the tenant default). The history is
# counts and hashes: it answers "how often does this agent try to say something
# harmful, and in which category" without storing what it tried to say.
# ===========================================================================


class GuardrailPolicyBody(BaseModel):
    agent_id: str = Field(default="*", max_length=120)
    enabled_categories: list[str] = Field(default_factory=list)
    block_jailbreak: bool = True
    block_prompt_extraction: bool = True
    hard_stop_phrase: str = Field(
        default="I can't help with that, but I can connect you to a person.",
        min_length=1,
        max_length=400,
    )
    judge_fallback: bool = False


def _policy_dict(row) -> dict:
    from app.ai.guardrails import categories as _categories

    return {
        "id": str(row.id),
        "agent_id": row.agent_id,
        "enabled_categories": list(row.enabled_categories or []),
        "block_jailbreak": bool(row.block_jailbreak),
        "block_prompt_extraction": bool(row.block_prompt_extraction),
        "hard_stop_phrase": row.hard_stop_phrase,
        "judge_fallback": bool(row.judge_fallback),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "known_categories": list(_categories.CATEGORIES),
    }


@router.get("/api/qa/guardrails/policies")
async def get_guardrail_policies(
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    from sqlalchemy import select as _select

    from app.ai.guardrails import categories as _categories
    from app.db.monitoring_models import GuardrailPolicy

    rows = (
        await session.scalars(
            _select(GuardrailPolicy)
            .where(GuardrailPolicy.tenant_id == ctx.tenant_id)
            .order_by(GuardrailPolicy.agent_id)
        )
    ).all()
    return {
        "policies": [_policy_dict(row) for row in rows],
        "categories": list(_categories.CATEGORIES),
        "defaults": _categories.default_policy().as_dict(),
    }


@router.put("/api/qa/guardrails/policies")
async def put_guardrail_policy(
    payload: GuardrailPolicyBody,
    ctx: TenantContext = Depends(require_permission(Permission.QA_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Create or replace one agent's policy.

    Categories are filtered against the known taxonomy rather than rejected
    wholesale: a client that sends one retired category should still be able to
    save the rest, and the response shows exactly what was kept.
    """
    from sqlalchemy import select as _select

    from app.ai.guardrails import categories as _categories
    from app.db.monitoring_models import GuardrailPolicy

    kept = _categories.enabled_categories_validate(payload.enabled_categories)
    dropped = [c for c in payload.enabled_categories if c not in kept]

    row = await session.scalar(
        _select(GuardrailPolicy).where(
            GuardrailPolicy.tenant_id == ctx.tenant_id,
            GuardrailPolicy.agent_id == payload.agent_id,
        )
    )
    if row is None:
        row = GuardrailPolicy(tenant_id=ctx.tenant_id, agent_id=payload.agent_id)
        session.add(row)
    row.enabled_categories = kept
    row.block_jailbreak = payload.block_jailbreak
    row.block_prompt_extraction = payload.block_prompt_extraction
    row.hard_stop_phrase = payload.hard_stop_phrase
    row.judge_fallback = payload.judge_fallback
    row.updated_by = str(ctx.user_id) if ctx.user_id else None
    await session.commit()
    await session.refresh(row)
    return {**_policy_dict(row), "dropped_categories": dropped}


@router.get("/api/qa/guardrails/events")
async def get_guardrail_events(
    hours: int = Query(default=24, ge=1, le=720),
    stage: str | None = Query(default=None, pattern="^(input|output)$"),
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.QA_READ)),
    session: AsyncSession = Depends(get_session),
):
    from datetime import datetime as _dt
    from datetime import timedelta as _td

    from sqlalchemy import select as _select

    from app.db.monitoring_models import GuardrailEvent

    since = _dt.now().astimezone().replace(tzinfo=None) - _td(hours=hours)
    query = _select(GuardrailEvent).where(
        GuardrailEvent.tenant_id == ctx.tenant_id, GuardrailEvent.created_at >= since
    )
    if stage is not None:
        query = query.where(GuardrailEvent.stage == stage)
    rows = (
        await session.scalars(query.order_by(GuardrailEvent.created_at.desc()).limit(limit))
    ).all()
    by_category: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for row in rows:
        by_category[row.category or "unclassified"] = by_category.get(row.category or "unclassified", 0) + 1
        by_reason[row.reason] = by_reason.get(row.reason, 0) + 1
    return {
        "hours": hours,
        "counts": {"by_category": by_category, "by_reason": by_reason, "total": len(rows)},
        "events": [
            {
                "id": str(row.id),
                "call_id": str(row.call_id) if row.call_id else None,
                "agent_id": row.agent_id,
                "stage": row.stage,
                "reason": row.reason,
                "category": row.category,
                "text_hash": row.text_hash[:16],
                "char_count": row.char_count,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "note": "matched text is never stored; text_hash identifies repeats only",
    }
