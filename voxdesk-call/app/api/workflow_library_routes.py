"""Workflow library API (PART 14, P14-04).

Five surfaces, each answering a question a tenant actually asks:

* `GET  /api/workflows/catalog`      -- what can I install, and what will it ask
                                        for permission?
* `GET  /api/workflows/presets`      -- what can an agent call, with its schema?
* `POST /api/workflows/install`      -- install, with an explicit permission
                                        review naming the reviewer;
* `POST /api/workflows/{key}/enable` -- turn it on (refused without a review);
* `GET  /api/workflows/runs`         -- what ran, and did it need retrying?

The catalogue is public to the tenant on purpose: an operator should be able to
read exactly which scopes an integration wants before installing it.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.session import get_session
from app.workflows import catalog, preset_functions, tool_store

router = APIRouter(prefix="/api/workflow-library", tags=["workflow-library"])


class InstallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_key: str = Field(min_length=1, max_length=80)
    actions: list[str] = Field(min_length=1)
    config: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = False
    reviewed_by: str | None = Field(default=None, max_length=120)


class EnableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True


def _raise(exc: Exception, code: str) -> None:
    mapping = {
        "unknown_integration": status.HTTP_404_NOT_FOUND,
        "unknown_action": status.HTTP_422_UNPROCESSABLE_ENTITY,
        "not_available": status.HTTP_409_CONFLICT,
        "not_installed": status.HTTP_404_NOT_FOUND,
        "review_missing": status.HTTP_409_CONFLICT,
        "no_actions": status.HTTP_422_UNPROCESSABLE_ENTITY,
    }
    raise HTTPException(status_code=mapping.get(code, status.HTTP_400_BAD_REQUEST), detail=str(exc))


@router.get("/catalog")
async def get_catalog(
    only_available: bool = Query(default=False),
    _: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
) -> dict[str, Any]:
    """Every integration in the library, with its actions and scopes."""
    return {
        "integrations": catalog.list_catalog(only_available=only_available),
        "coverage": dict(catalog.coverage()),
    }


@router.get("/presets")
async def get_presets(
    effect: str | None = Query(default=None),
    _: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
) -> dict[str, Any]:
    try:
        return {"presets": preset_functions.list_presets(effect=effect)}
    except preset_functions.PresetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/installs")
async def list_installs(
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    rows = await tool_store.list_installs(session, ctx.tenant_id)
    return {"installs": [tool_store.view(row) for row in rows]}


@router.post("/install", status_code=status.HTTP_201_CREATED)
async def install(
    payload: InstallRequest,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    reviewer = payload.reviewed_by or getattr(ctx, "user_email", None) or str(ctx.tenant_id)
    try:
        row = await tool_store.install(
            session,
            ctx.tenant_id,
            payload.workflow_key,
            actions=payload.actions,
            config=payload.config,
            reviewed_by=reviewer,
            enabled=payload.enabled,
        )
    except catalog.CatalogError as exc:
        _raise(exc, exc.code)
    except tool_store.ToolStoreError as exc:
        _raise(exc, exc.code)
    await session.commit()
    return tool_store.view(row)


@router.post("/{workflow_key}/enable")
async def enable(
    workflow_key: str,
    payload: EnableRequest,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    try:
        row = await tool_store.set_enabled(session, ctx.tenant_id, workflow_key, payload.enabled)
    except tool_store.ToolStoreError as exc:
        _raise(exc, exc.code)
    await session.commit()
    return tool_store.view(row)


@router.delete("/{workflow_key}", status_code=status.HTTP_204_NO_CONTENT)
async def uninstall(
    workflow_key: str,
    ctx: TenantContext = Depends(require_permission(Permission.INTEGRATION_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    removed = await tool_store.uninstall(session, ctx.tenant_id, workflow_key)
    if not removed:
        raise HTTPException(status_code=404, detail=f"{workflow_key} is not installed")
    await session.commit()


@router.get("/review/{workflow_key}")
async def preview_review(
    workflow_key: str,
    actions: list[str] = Query(default=[]),
    _: TenantContext = Depends(require_permission(Permission.INTEGRATION_READ)),
) -> dict[str, Any]:
    """What the consent screen would say, before anything is installed."""
    try:
        scopes, unknown = catalog.permissions_for(workflow_key, actions)
        return {
            "workflow_key": workflow_key,
            "scopes": scopes,
            "unknown_actions": unknown,
            "review_text": catalog.review_text(workflow_key, actions),
        }
    except catalog.CatalogError as exc:
        _raise(exc, exc.code)
