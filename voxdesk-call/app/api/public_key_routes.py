"""Prompt 5: Authenticated Tenant-Scoped Public Widget Key Management Routes.

All routes here require private workspace authentication and RBAC permissions:
- `Permission.TENANT_READ` to list/inspect public widget keys
- `Permission.TENANT_UPDATE` to create, update, rotate, or revoke public widget keys

Plaintext public keys (`vdpk_<id_hex>_<secret>`) are returned ONCE upon creation
or rotation; subsequent reads return only `key_prefix` and metadata.
"""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import Permission, TenantContext, require_permission
from app.db.session import get_session
from app.domain.public_widget_models import (
    PublicWidgetKeyCreate,
    PublicWidgetKeyCreatedResponse,
    PublicWidgetKeyRead,
    PublicWidgetKeyRevokeRequest,
    PublicWidgetKeyRotateRequest,
    PublicWidgetKeyUpdate,
)
from app.services.public_key_service import (
    create_public_key,
    get_public_key,
    list_public_keys,
    revoke_public_key,
    rotate_public_key,
    update_public_key,
)

router = APIRouter(prefix="/api/v1/public-keys", tags=["public-widget-keys"])


@router.post(
    "",
    response_model=PublicWidgetKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_widget_public_key(
    payload: PublicWidgetKeyCreate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> PublicWidgetKeyCreatedResponse:
    return await create_public_key(
        session,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        actor_email=ctx.user.email,
        payload=payload,
    )


@router.get("", response_model=list[PublicWidgetKeyRead])
async def list_widget_public_keys(
    agent_id: str | None = Query(None, max_length=120),
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[PublicWidgetKeyRead]:
    return await list_public_keys(
        session,
        tenant_id=ctx.tenant_id,
        agent_id=agent_id,
    )


@router.get("/{key_id}", response_model=PublicWidgetKeyRead)
async def get_widget_public_key(
    key_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
) -> PublicWidgetKeyRead:
    return await get_public_key(
        session,
        tenant_id=ctx.tenant_id,
        key_id=key_id,
    )


@router.patch("/{key_id}", response_model=PublicWidgetKeyRead)
async def update_widget_public_key(
    key_id: uuid.UUID,
    payload: PublicWidgetKeyUpdate,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> PublicWidgetKeyRead:
    return await update_public_key(
        session,
        tenant_id=ctx.tenant_id,
        key_id=key_id,
        actor_user_id=ctx.user_id,
        actor_email=ctx.user.email,
        payload=payload,
    )


@router.post(
    "/{key_id}/rotate",
    response_model=PublicWidgetKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def rotate_widget_public_key(
    key_id: uuid.UUID,
    payload: PublicWidgetKeyRotateRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> PublicWidgetKeyCreatedResponse:
    req = payload or PublicWidgetKeyRotateRequest()
    return await rotate_public_key(
        session,
        tenant_id=ctx.tenant_id,
        key_id=key_id,
        actor_user_id=ctx.user_id,
        actor_email=ctx.user.email,
        payload=req,
    )


@router.post("/{key_id}/revoke", response_model=PublicWidgetKeyRead)
async def revoke_widget_public_key(
    key_id: uuid.UUID,
    payload: PublicWidgetKeyRevokeRequest | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_UPDATE)),
    session: AsyncSession = Depends(get_session),
) -> PublicWidgetKeyRead:
    req = payload or PublicWidgetKeyRevokeRequest()
    return await revoke_public_key(
        session,
        tenant_id=ctx.tenant_id,
        key_id=key_id,
        actor_user_id=ctx.user_id,
        actor_email=ctx.user.email,
        payload=req,
    )
