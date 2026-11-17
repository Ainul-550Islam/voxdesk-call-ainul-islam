"""Contact memory settings, brief and recall API (PART 14, P14-05).

The fact CRUD already exists (`/contacts/{id}/memory/...`). What is here is the
layer on top of it:

* `GET/PATCH /api/memory/settings`     -- tenant switches and the workspace prompt;
* `POST /api/memory/{contact}/brief`   -- rebuild the running brief now;
* `PUT  /api/memory/{contact}/brief`   -- operator edit (audited, redacted);
* `DELETE /api/memory/{contact}/brief` -- clear the brief, keep the facts;
* `GET  /api/memory/{contact}/recall`  -- exactly what an agent would receive;
* `POST /api/memory/backfill`          -- build briefs for existing contacts.

`recall` is the endpoint that makes the feature debuggable: an operator can see
the block the agent gets, and whether it is enough to claim a known caller.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.services import contact_memory_service

router = APIRouter(prefix="/api/memory", tags=["contact-memory"])


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    save_enabled: bool | None = None
    recall_enabled: bool | None = None
    brief_enabled: bool | None = None
    workspace_prompt: str | None = Field(default=None, max_length=2000)
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class BriefEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    brief: str = Field(max_length=2400)


@router.get("/settings")
async def read_settings(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    row = await contact_memory_service.get_settings(session, ctx.tenant_id)
    await session.commit()
    return contact_memory_service.settings_view(row)


@router.patch("/settings")
async def patch_settings(
    payload: SettingsPatch,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        row = await contact_memory_service.update_settings(
            session,
            ctx.tenant_id,
            updated_by=getattr(ctx, "user_email", None) or str(ctx.tenant_id),
            save_enabled=payload.save_enabled,
            recall_enabled=payload.recall_enabled,
            brief_enabled=payload.brief_enabled,
            workspace_prompt=payload.workspace_prompt,
            retention_days=payload.retention_days,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await session.commit()
    return contact_memory_service.settings_view(row)


@router.post("/{contact_id}/brief")
async def rebuild_brief(
    contact_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        row = await contact_memory_service.build_brief(session, ctx.tenant_id, contact_id)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await session.commit()
    return contact_memory_service.brief_view(row) or {}


@router.put("/{contact_id}/brief")
async def edit_brief(
    contact_id: UUID,
    payload: BriefEdit,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        row = await contact_memory_service.set_brief(
            session,
            ctx.tenant_id,
            contact_id,
            payload.brief,
            edited_by=getattr(ctx, "user_email", None) or str(ctx.tenant_id),
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    await session.commit()
    return contact_memory_service.brief_view(row) or {}


@router.delete("/{contact_id}/brief", status_code=status.HTTP_204_NO_CONTENT)
async def clear_brief(
    contact_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await contact_memory_service.clear_brief(session, ctx.tenant_id, contact_id)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    await session.commit()


@router.get("/{contact_id}/recall")
async def recall(
    contact_id: UUID,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """The exact block an agent receives at the start of a call."""
    settings = await contact_memory_service.get_settings(session, ctx.tenant_id)
    entries = await contact_memory_service.list_entries(session, ctx.tenant_id, contact_id)
    brief = await contact_memory_service.build_brief(
        session, ctx.tenant_id, contact_id, persist=False
    )
    result = contact_memory_service.recall(
        entries=entries,
        brief=brief if settings.brief_enabled else None,
        workspace_prompt=settings.workspace_prompt if settings.recall_enabled else "",
    )
    result["recall_enabled"] = settings.recall_enabled
    result["brief_enabled"] = settings.brief_enabled
    await session.rollback()
    return result


@router.post("/backfill")
async def backfill(
    limit: int = 100,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    if not 1 <= limit <= 1000:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 1000")
    result = await contact_memory_service.backfill_briefs(session, ctx.tenant_id, limit=limit)
    await session.commit()
    return result
