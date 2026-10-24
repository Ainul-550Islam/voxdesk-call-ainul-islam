"""Access-review campaigns: create, inspect, decide, close.

A campaign is a point-in-time snapshot of the people who can reach the tenant's
data. Decisions are per person and final within a campaign: re-deciding is a 409
rather than a silent overwrite, because "the reviewer changed their mind" must
leave a second, attributable record (a new campaign), not an edited one.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.compliance_v2.common import (
    audit_action,
    claim_idempotent,
    complete_idempotent,
    http_error,
    not_found,
    replay_reference,
    require_human,
    scoped_tenant,
)
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.compliance_v2.soc2 import access_review
from app.db.compliance_v2_models import AccessReviewCampaign, AccessReviewDecision
from app.db.session import get_session

router = APIRouter(prefix="/access-reviews", tags=["compliance-v2-access-review"])

DECISIONS = ("attest", "revoke", "not_applicable")


class CampaignCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    reviewer_ids: list[uuid.UUID] = Field(min_length=1)
    due_days: int = Field(default=14, ge=1, le=365)
    scope_key: str = Field(default="all", max_length=120)
    auto_revoke: bool = True


class CampaignOut(BaseModel):
    id: str
    name: str
    scope_key: str
    status: str
    auto_revoke: bool
    due_at: dt.datetime
    created_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    summary: dict[str, Any]


class DecisionIn(BaseModel):
    user_id: uuid.UUID
    decision: str
    note: str = Field(default="", max_length=500)


class DecisionOut(BaseModel):
    id: str
    user_id: str
    role_at_review: str
    decision: str
    decided_by: str
    decided_at: dt.datetime | None = None
    auto_revoked: bool
    note: str


def _decision_out(row: AccessReviewDecision) -> DecisionOut:
    return DecisionOut(
        id=str(row.id),
        user_id=str(row.user_id),
        role_at_review=row.role_at_review,
        decision=row.decision,
        decided_by=str(row.decided_by or ""),
        decided_at=row.decided_at,
        auto_revoked=bool(row.auto_revoked),
        note=row.note or "",
    )


async def _load(session: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID):
    row = await session.scalar(
        select(AccessReviewCampaign).where(
            AccessReviewCampaign.id == campaign_id,
            AccessReviewCampaign.tenant_id == tenant_id,
        )
    )
    if row is None:
        raise not_found("access_review_campaign")
    return row


@router.get("/campaigns")
async def list_campaigns(
    tenant_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_ACCESS_REVIEW_READ)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    rows = await session.execute(
        select(AccessReviewCampaign)
        .where(AccessReviewCampaign.tenant_id == tenant_id)
        .order_by(AccessReviewCampaign.created_at.desc())
        .limit(limit)
    )
    output: list[dict[str, Any]] = []
    for campaign in rows.scalars():
        decisions = await access_review.list_decisions(
            session, tenant_id=tenant_id, campaign_id=campaign.id
        )
        output.append(
            {
                "id": str(campaign.id),
                "name": campaign.name,
                "status": campaign.status,
                "due_at": campaign.due_at,
                "scope_key": campaign.scope_key,
                **access_review.campaign_summary(campaign, decisions),
            }
        )
    return output


@router.post("/campaigns", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
async def create_campaign(
    tenant_id: uuid.UUID,
    payload: CampaignCreate,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_ACCESS_REVIEW_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    claim = await claim_idempotent(
        session,
        ctx=ctx,
        request=request,
        operation="compliance_v2.access_review.create",
        payload=payload.model_dump(mode="json"),
    )
    replayed = replay_reference(claim, resource_type="access_review_campaign")
    if replayed is not None:
        existing = await access_review.get_campaign(
            session, tenant_id=tenant_id, campaign_id=uuid.UUID(replayed)
        )
        existing_decisions = await access_review.list_decisions(
            session, tenant_id=tenant_id, campaign_id=existing.id
        )
        return CampaignOut(
            id=str(existing.id),
            name=existing.name,
            scope_key=existing.scope_key,
            status=existing.status,
            auto_revoke=bool(existing.auto_revoke),
            due_at=existing.due_at,
            created_at=existing.created_at,
            completed_at=existing.completed_at,
            summary=access_review.campaign_summary(existing, existing_decisions),
        )
    due_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=payload.due_days)
    try:
        campaign = await access_review.create_campaign(
            session,
            tenant_id=tenant_id,
            name=payload.name,
            reviewer_ids=list(payload.reviewer_ids),
            due_at=due_at,
            created_by=ctx.user_id,
            scope_key=payload.scope_key,
            auto_revoke=payload.auto_revoke,
        )
    except access_review.AccessReviewError as exc:
        raise http_error(status.HTTP_409_CONFLICT, exc.code, str(exc)) from None

    decisions = await access_review.list_decisions(
        session, tenant_id=tenant_id, campaign_id=campaign.id
    )
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="access_review_campaign_created",
        resource_type="access_review_campaign",
        resource_id=campaign.id,
        detail={
            "scope_key": campaign.scope_key,
            "population": len(decisions),
            "due_at": due_at.isoformat(),
            "auto_revoke": campaign.auto_revoke,
        },
    )
    await complete_idempotent(
        session, claim, resource_type="access_review_campaign", resource_id=campaign.id
    )
    await session.commit()
    await session.refresh(campaign)
    return CampaignOut(
        id=str(campaign.id),
        name=campaign.name,
        scope_key=campaign.scope_key,
        status=campaign.status,
        auto_revoke=bool(campaign.auto_revoke),
        due_at=campaign.due_at,
        created_at=campaign.created_at,
        completed_at=campaign.completed_at,
        summary=access_review.campaign_summary(campaign, decisions),
    )


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
async def get_campaign(
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_ACCESS_REVIEW_READ)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    campaign = await _load(session, tenant_id, campaign_id)
    decisions = await access_review.list_decisions(
        session, tenant_id=tenant_id, campaign_id=campaign_id
    )
    return CampaignOut(
        id=str(campaign.id),
        name=campaign.name,
        scope_key=campaign.scope_key,
        status=campaign.status,
        auto_revoke=bool(campaign.auto_revoke),
        due_at=campaign.due_at,
        created_at=campaign.created_at,
        completed_at=campaign.completed_at,
        summary=access_review.campaign_summary(campaign, decisions),
    )


@router.get("/campaigns/{campaign_id}/decisions", response_model=list[DecisionOut])
async def list_decisions(
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_ACCESS_REVIEW_READ)),
    session: AsyncSession = Depends(get_session),
):
    scoped_tenant(ctx, tenant_id)
    await _load(session, tenant_id, campaign_id)
    rows = await access_review.list_decisions(
        session, tenant_id=tenant_id, campaign_id=campaign_id
    )
    return [_decision_out(row) for row in rows]


@router.post("/campaigns/{campaign_id}/decisions", response_model=DecisionOut)
async def decide(
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    payload: DecisionIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_ACCESS_REVIEW_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Record one reviewer decision. A revocation takes effect immediately."""
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    if payload.decision not in DECISIONS:
        raise http_error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_decision",
            f"decision must be one of {list(DECISIONS)}",
        )
    await _load(session, tenant_id, campaign_id)
    try:
        row = await access_review.decide(
            session,
            tenant_id=tenant_id,
            campaign_id=campaign_id,
            user_id=payload.user_id,
            actor_user_id=ctx.user_id,
            decision=payload.decision,
            note=payload.note,
        )
    except access_review.CampaignNotFound as exc:
        raise not_found("access_review_decision") from exc
    except access_review.DecisionConflict as exc:
        raise http_error(status.HTTP_409_CONFLICT, exc.code, str(exc)) from None
    except access_review.AccessReviewError as exc:
        raise http_error(status.HTTP_400_BAD_REQUEST, exc.code, str(exc)) from None

    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="access_review_decided",
        resource_type="access_review_decision",
        resource_id=row.id,
        detail={
            "campaign_id": str(campaign_id),
            "user_id": str(payload.user_id),
            "decision": payload.decision,
        },
    )
    await session.commit()
    await session.refresh(row)
    return _decision_out(row)


@router.post("/campaigns/{campaign_id}/close")
async def close_campaign(
    tenant_id: uuid.UUID,
    campaign_id: uuid.UUID,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.COMPLIANCE_ACCESS_REVIEW_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    """Run the campaign's timeout rules now, then report the outcome."""
    require_human(ctx)
    scoped_tenant(ctx, tenant_id)
    await _load(session, tenant_id, campaign_id)
    outcome = await access_review.tick(session, now=None, limit=200)
    campaign = await _load(session, tenant_id, campaign_id)
    decisions = await access_review.list_decisions(
        session, tenant_id=tenant_id, campaign_id=campaign_id
    )
    await audit_action(
        session,
        ctx=ctx,
        request=request,
        event_type="access_review_campaign_closed",
        resource_type="access_review_campaign",
        resource_id=campaign_id,
        detail={"tick": outcome, "status": campaign.status},
    )
    await session.commit()
    return {
        "campaign": {
            "id": str(campaign.id),
            "status": campaign.status,
            "completed_at": campaign.completed_at,
        },
        "summary": access_review.campaign_summary(campaign, decisions),
        "tick": outcome,
    }


__all__ = ["router"]
