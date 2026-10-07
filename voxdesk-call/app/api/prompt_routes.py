"""Prompt registry API.

Drafts need ``tenant:update``. Publishing or rolling back a production prompt
needs ``security:settings``, which a viewer and an admin do not both hold.
A foreign tenant id is a 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.identity.events import emit
from app.auth.permissions import Permission
from app.db.models import AuditAction
from app.db.session import get_session
from app.ai.models import AIPrompt, AIPromptVersion
from app.ai.prompts.registry import (
    code_prompt_view,
    create_draft,
    list_prompts,
    require_prompt,
)
from app.ai.prompts.rollout import configure, choose
from app.ai.prompts.versioning import publish, require_version
from app.organization.service import Actor
from app.tenancy.isolation import HierarchyError, LifecycleDenied, client_ip, to_http
from app.tenancy.policy import bind_tenant, require_permission as require_can

router = APIRouter(prefix="/api/tenants/{tenant_id}/ai/prompts", tags=["ai-prompts"])


class DraftIn(BaseModel):
    prompt_key: str = Field(min_length=2, max_length=80)
    body: str = Field(min_length=1, max_length=16000)
    tenant_id: uuid.UUID | None = None
    environment_id: uuid.UUID | None = None


class PublishIn(BaseModel):
    version: int
    environment_kind: str = "production"
    tenant_id: uuid.UUID | None = None


class RollbackIn(BaseModel):
    version: int
    environment_kind: str = "production"
    tenant_id: uuid.UUID | None = None


class RolloutIn(BaseModel):
    stable_version: int
    canary_version: int | None = None
    percent: int = Field(ge=0, le=100)
    salt: str = Field(min_length=1, max_length=64)
    environment_kind: str = "production"
    environment_id: uuid.UUID | None = None
    tenant_id: uuid.UUID | None = None


def _public_version(row: AIPromptVersion) -> dict:
    return {
        "version": row.version_number,
        "status": row.status,
        "checksum": row.checksum,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "body": row.body,
    }


def _public_prompt(row: AIPrompt) -> dict:
    return {
        "id": str(row.id),
        "prompt_key": row.prompt_key,
        "status": row.status,
        "environment_scope": row.environment_scope,
        "current_version": row.current_version,
    }


async def _denied(session, ctx, operation: str) -> None:
    await emit(
        session,
        AuditAction.AUTHZ_DENIED,
        tenant_id=ctx.tenant_id,
        actor_user_id=ctx.user_id,
        actor_email=ctx.user.email,
        detail={"operation": operation, "reason": "permission_denied"},
        commit=False,
    )
    await session.commit()


def _production(kind: str) -> bool:
    return kind == "production"


@router.get("")
async def get_prompts(
    tenant_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        bind_tenant(ctx, tenant_id)
        rows = await list_prompts(session, ctx.tenant_id)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {
        "prompts": [_public_prompt(row) for row in rows],
        "code_prompt": code_prompt_view(ctx.tenant),
    }


@router.post("", status_code=201)
async def post_draft(
    tenant_id: uuid.UUID,
    payload: DraftIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        bind_tenant(ctx, tenant_id, payload.tenant_id)
        if not ctx.can(Permission.TENANT_UPDATE):
            await _denied(session, ctx, "prompt_draft")
            require_can(ctx, Permission.TENANT_UPDATE)
        prompt, version = await create_draft(
            session,
            ctx.tenant,
            prompt_key=payload.prompt_key,
            body=payload.body,
            author_user_id=ctx.user_id,
            environment_id=payload.environment_id,
        )
        who = Actor(
            user_id=ctx.user_id,
            email=ctx.user.email,
            tenant_id=ctx.tenant_id,
            ip_address=client_ip(request),
        )
        await emit(
            session,
            AuditAction.SECURITY_SETTINGS_CHANGED,
            tenant_id=ctx.tenant_id,
            actor_user_id=who.user_id,
            actor_email=who.email,
            ip_address=who.ip_address,
            detail={
                "operation": "prompt_draft_created",
                "prompt_key": prompt.prompt_key,
                "version": version.version_number,
                "checksum": version.checksum,
            },
            commit=False,
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {**_public_prompt(prompt), "version": _public_version(version)}


@router.get("/{prompt_key}/versions/{version}")
async def get_version(
    tenant_id: uuid.UUID,
    prompt_key: str,
    version: int,
    environment_id: uuid.UUID | None = None,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        bind_tenant(ctx, tenant_id)
        prompt = await require_prompt(
            session, ctx.tenant_id, prompt_key, environment_id=environment_id
        )
        row = await require_version(session, prompt, version)
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _public_version(row)


@router.post("/{prompt_key}/publish")
async def post_publish(
    tenant_id: uuid.UUID,
    prompt_key: str,
    payload: PublishIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        bind_tenant(ctx, tenant_id, payload.tenant_id)
        needed = (
            Permission.SECURITY_SETTINGS
            if _production(payload.environment_kind)
            else Permission.TENANT_UPDATE
        )
        if not ctx.can(needed):
            await _denied(session, ctx, "prompt_publish")
            require_can(ctx, needed)
        prompt = await require_prompt(session, ctx.tenant_id, prompt_key)
        if prompt.status == "retired":
            raise LifecycleDenied("A retired prompt cannot be published")
        version = await require_version(session, prompt, payload.version)
        await publish(session, version, prompt)
        await emit(
            session,
            AuditAction.SECURITY_SETTINGS_CHANGED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            actor_email=ctx.user.email,
            ip_address=client_ip(request),
            detail={
                "operation": "prompt_published",
                "prompt_key": prompt.prompt_key,
                "version": version.version_number,
                "checksum": version.checksum,
                "environment_kind": payload.environment_kind,
            },
            commit=False,
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _public_version(version)


@router.post("/{prompt_key}/rollback")
async def post_rollback(
    tenant_id: uuid.UUID,
    prompt_key: str,
    payload: RollbackIn,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        bind_tenant(ctx, tenant_id, payload.tenant_id)
        needed = (
            Permission.SECURITY_SETTINGS
            if _production(payload.environment_kind)
            else Permission.TENANT_UPDATE
        )
        if not ctx.can(needed):
            await _denied(session, ctx, "prompt_rollback")
            require_can(ctx, needed)
        prompt = await require_prompt(session, ctx.tenant_id, prompt_key)
        version = await require_version(session, prompt, payload.version)
        if version.status != "published":
            raise LifecycleDenied("Rollback target must be a published version")
        before = version.body
        prompt.current_version = version.version_number
        prompt.updated_at = datetime.utcnow()
        await session.flush()
        if version.body != before:
            raise LifecycleDenied("Rollback must not change the published body")
        await emit(
            session,
            AuditAction.SECURITY_SETTINGS_CHANGED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            actor_email=ctx.user.email,
            ip_address=client_ip(request),
            detail={
                "operation": "prompt_rolled_back",
                "prompt_key": prompt.prompt_key,
                "version": version.version_number,
                "checksum": version.checksum,
            },
            commit=False,
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {
        "prompt_key": prompt.prompt_key,
        "current_version": prompt.current_version,
        "checksum": version.checksum,
    }


@router.post("/{prompt_key}/retire")
async def post_retire(
    tenant_id: uuid.UUID,
    prompt_key: str,
    request: Request,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        bind_tenant(ctx, tenant_id)
        if not ctx.can(Permission.SECURITY_SETTINGS):
            await _denied(session, ctx, "prompt_retire")
            require_can(ctx, Permission.SECURITY_SETTINGS)
        prompt = await require_prompt(session, ctx.tenant_id, prompt_key)
        prompt.status = "retired"
        prompt.updated_at = datetime.utcnow()
        await emit(
            session,
            AuditAction.SECURITY_SETTINGS_CHANGED,
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            actor_email=ctx.user.email,
            ip_address=client_ip(request),
            detail={"operation": "prompt_retired", "prompt_key": prompt.prompt_key},
            commit=False,
        )
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return _public_prompt(prompt)


@router.put("/{prompt_key}/rollout")
async def put_rollout(
    tenant_id: uuid.UUID,
    prompt_key: str,
    payload: RolloutIn,
    ctx: TenantContext = Depends(require_permission(Permission.TENANT_READ)),
    session: AsyncSession = Depends(get_session),
):
    try:
        bind_tenant(ctx, tenant_id, payload.tenant_id)
        needed = (
            Permission.SECURITY_SETTINGS
            if _production(payload.environment_kind)
            else Permission.TENANT_UPDATE
        )
        if not ctx.can(needed):
            await _denied(session, ctx, "prompt_rollout")
            require_can(ctx, needed)
        prompt = await require_prompt(
            session, ctx.tenant_id, prompt_key, environment_id=payload.environment_id
        )
        row = await configure(
            session,
            prompt,
            stable_version=payload.stable_version,
            canary_version=payload.canary_version,
            percent=payload.percent,
            salt=payload.salt,
            environment_id=payload.environment_id,
        )
        assigned = choose(ctx.tenant_id, prompt.prompt_key, row)
        await session.commit()
    except HierarchyError as exc:
        raise to_http(exc) from None
    return {
        "stable_version": row.stable_version,
        "canary_version": row.canary_version,
        "percent": row.percent,
        "assigned_version": assigned,
    }
