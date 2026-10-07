"""Tenant skills.

A skill name is unique inside the tenant. Proficiency is an integer from 1
to 5. There is no VIP skill and no inferred value.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.contact_center import skills
from app.contact_center.exceptions import AcdError
from app.contact_center.repository import get_skill, list_skills
from app.db.session import get_session
from app.tenancy.context import reject_client_override
from app.tenancy.isolation import HierarchyError, to_http
from app.tenancy.policy import bind_tenant

router = APIRouter(tags=["skills"])


class SkillBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    tenant_id: uuid.UUID | None = None


class SkillPatch(BaseModel):
    enabled: bool
    tenant_id: uuid.UUID | None = None


class AgentSkillBody(BaseModel):
    user_id: uuid.UUID
    proficiency: int = Field(default=1, ge=1, le=5)
    weight: int = 0
    tenant_id: uuid.UUID | None = None


def _scope(ctx: TenantContext, tenant_id: uuid.UUID | None, claimed: uuid.UUID | None) -> uuid.UUID:
    reject_client_override(ctx, claimed)
    if tenant_id is not None:
        bind_tenant(ctx, tenant_id, claimed)
    return ctx.tenant_id


@router.get("/api/skills")
@router.get("/api/tenants/{tenant_id}/skills")
async def get_skills(
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.SKILL_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, None)
        rows = await list_skills(session, tenant)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {"skills": [row.as_dict() for row in rows]}


@router.post("/api/skills", status_code=201)
@router.post("/api/tenants/{tenant_id}/skills", status_code=201)
async def post_skill(
    payload: SkillBody,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.SKILL_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, payload.tenant_id)
        row = await skills.create_skill(session, tenant_id=tenant, name=payload.name)
        await session.commit()
    except AcdError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.patch("/api/skills/{skill_id}")
@router.patch("/api/tenants/{tenant_id}/skills/{skill_id}")
async def patch_skill(
    skill_id: uuid.UUID,
    payload: SkillPatch,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.SKILL_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, payload.tenant_id)
        row = await get_skill(session, tenant, skill_id)
        await skills.set_enabled(session, row, payload.enabled)
        await session.commit()
    except AcdError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()


@router.post("/api/skills/{skill_id}/agents", status_code=201)
@router.post("/api/tenants/{tenant_id}/skills/{skill_id}/agents", status_code=201)
async def post_agent_skill(
    skill_id: uuid.UUID,
    payload: AgentSkillBody,
    tenant_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.SKILL_WRITE)),
    session: AsyncSession = Depends(get_session),
):
    try:
        tenant = _scope(ctx, tenant_id, payload.tenant_id)
        row = await skills.assign_skill(
            session,
            tenant_id=tenant,
            user_id=payload.user_id,
            skill_id=skill_id,
            proficiency=payload.proficiency,
            weight=payload.weight,
        )
        await session.commit()
    except AcdError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.as_dict()) from None
    except HierarchyError as exc:
        raise to_http(exc) from None
    return row.as_dict()
