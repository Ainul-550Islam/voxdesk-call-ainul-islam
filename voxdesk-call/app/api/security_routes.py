from __future__ import annotations
import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.models import HumanApproval, PrivacyPolicy, PublicWebhookEndpoint
from app.db.session import get_session
from app.security.approvals import ApprovalError, decide, request

router = APIRouter(prefix="/api/security", tags=["security"])


class ApprovalRequest(BaseModel):
    action: str = Field(min_length=1, max_length=120)
    subject_ref: str = Field(min_length=1, max_length=255)
    payload: dict = Field(default_factory=dict)
    ttl_seconds: int = Field(default=900, ge=1, le=86400)


class ApprovalDecision(BaseModel):
    approved: bool
    payload: dict | None = None


class PolicyBody(BaseModel):
    mode: str = "mask"
    rules: dict = Field(default_factory=dict)


class WebhookEndpointRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    secret_ref: str


@router.get("/approvals")
async def list_approvals(
    ctx: TenantContext = Depends(require_permission(Permission.APPROVAL_READ)),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        (
            await session.execute(
                select(HumanApproval)
                .where(HumanApproval.tenant_id == ctx.tenant_id)
                .order_by(HumanApproval.created_at.desc())
                .limit(100)
            )
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(r.id),
            "action": r.action,
            "subject_ref": r.subject_ref,
            "status": r.status,
            "expires_at": r.expires_at.isoformat(),
        }
        for r in rows
    ]


@router.post("/approvals", status_code=201)
async def create_approval(
    body: ApprovalRequest,
    ctx: TenantContext = Depends(require_permission(Permission.APPROVAL_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await request(
            session,
            tenant_id=ctx.tenant_id,
            action=body.action,
            subject_ref=body.subject_ref,
            payload=body.payload,
            requested_by=ctx.user_id,
            ttl_seconds=body.ttl_seconds,
        )
        await session.commit()
    except ApprovalError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"id": str(row.id), "status": row.status, "expires_at": row.expires_at.isoformat()}


@router.post("/approvals/{approval_id}/decision")
async def decide_approval(
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    ctx: TenantContext = Depends(require_permission(Permission.APPROVAL_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        row = await decide(
            session,
            tenant_id=ctx.tenant_id,
            approval_id=approval_id,
            actor_user_id=ctx.user_id,
            approved=body.approved,
            payload=body.payload,
        )
        await session.commit()
    except ApprovalError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"id": str(row.id), "status": row.status}


@router.get("/webhook-endpoints")
async def list_webhook_endpoints(
    ctx: TenantContext = Depends(require_permission(Permission.SECURITY_SETTINGS)),
    session: AsyncSession = Depends(get_session),
):
    rows = (
        (
            await session.execute(
                select(PublicWebhookEndpoint)
                .where(PublicWebhookEndpoint.tenant_id == ctx.tenant_id)
                .order_by(PublicWebhookEndpoint.name)
            )
        )
        .scalars()
        .all()
    )
    return [{"id": str(row.id), "name": row.name, "enabled": row.enabled} for row in rows]


@router.post("/webhook-endpoints", status_code=201)
async def create_webhook_endpoint(
    body: WebhookEndpointRequest,
    ctx: TenantContext = Depends(require_permission(Permission.SECURITY_SETTINGS)),
    session: AsyncSession = Depends(get_session),
):
    if not body.secret_ref.startswith("secret://"):
        raise HTTPException(422, "secret_ref must be an opaque secret:// reference")
    row = PublicWebhookEndpoint(
        tenant_id=ctx.tenant_id,
        name=body.name,
        secret_ref=body.secret_ref,
    )
    session.add(row)
    await session.commit()
    return {"id": str(row.id), "name": row.name, "enabled": row.enabled}


@router.get("/privacy-policy")
async def get_policy(
    ctx: TenantContext = Depends(require_permission(Permission.SECURITY_SETTINGS)),
    session: AsyncSession = Depends(get_session),
):
    row = (
        await session.execute(select(PrivacyPolicy).where(PrivacyPolicy.tenant_id == ctx.tenant_id))
    ).scalar_one_or_none()
    return {"mode": row.mode, "rules": row.rules} if row else {"mode": "mask", "rules": {}}


@router.put("/privacy-policy")
async def put_policy(
    body: PolicyBody,
    ctx: TenantContext = Depends(require_permission(Permission.SECURITY_SETTINGS)),
    session: AsyncSession = Depends(get_session),
):
    if body.mode not in {"mask", "tokenize"}:
        raise HTTPException(422, "mode must be mask or tokenize")
    row = (
        await session.execute(select(PrivacyPolicy).where(PrivacyPolicy.tenant_id == ctx.tenant_id))
    ).scalar_one_or_none()
    if row is None:
        row = PrivacyPolicy(tenant_id=ctx.tenant_id)
        session.add(row)
    row.mode, row.rules = body.mode, body.rules
    await session.commit()
    return {"mode": row.mode, "rules": row.rules}
