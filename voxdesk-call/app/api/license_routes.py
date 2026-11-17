"""Licensing endpoints for self-hosted / white-label deployments (Step 9).

    POST /api/billing/license/issue    owner: mint an HMAC-signed license
    POST /api/billing/license/verify   operator: validate a license token

The verify endpoint is unauthenticated (it has to work on a box with no user
provisioned yet); it reveals nothing beyond whether a token is valid.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, record_audit, require_role
from app.billing import licensing
from app.db.models import AuditAction, UserRole
from app.db.session import get_session

router = APIRouter(prefix="/api/billing/license", tags=["billing-license"])


class IssueLicenseIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"plan": "self_hosted_enterprise", "seats": 25, "expires_in_days": 365}]})

    plan: str = Field(min_length=1, max_length=32)
    seats: int = Field(ge=1, le=100000)
    expires_in_days: int = Field(ge=1, le=3650)


class LicenseOut(BaseModel):
    license: str
    tenant_id: str
    plan: str
    seats: int
    expires_at: int


class VerifyLicenseIn(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"license": "eyJhbGciOiJIUzI1NiJ9..."}]})

    license: str


@router.post("/issue", response_model=LicenseOut)
async def issue(
    payload: IssueLicenseIn,
    ctx: TenantContext = Depends(require_role(UserRole.OWNER)),
    session: AsyncSession = Depends(get_session),
):
    """Mint a signed licence for a self-hosted installation.

    Owner-only. The signature is HMAC over the licence claims, so the installation can verify itself offline.
    """
    import time

    expires_at = int(time.time()) + payload.expires_in_days * 86400
    token = licensing.issue_license(
        tenant_id=str(ctx.tenant_id),
        plan=payload.plan,
        seats=payload.seats,
        expires_at=expires_at,
    )
    await record_audit(
        session,
        action=AuditAction.LICENSE_ISSUED,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        actor_email=ctx.user.email,
        detail={"plan": payload.plan, "seats": payload.seats},
    )
    return LicenseOut(
        license=token,
        tenant_id=str(ctx.tenant_id),
        plan=payload.plan,
        seats=payload.seats,
        expires_at=expires_at,
    )


@router.post("/verify")
async def verify(payload: VerifyLicenseIn):
    """Verify a licence signature and its claims.

    Unauthenticated because a self-hosted installation has no account with us; the signature is the credential.
    """
    info = licensing.verify_license(payload.license)
    if info is None:
        return {"valid": False}
    return {
        "valid": True,
        "tenant_id": info.tenant_id,
        "plan": info.plan,
        "seats": info.seats,
        "expires_at": info.expires_at,
        "issued_at": info.issued_at,
    }
