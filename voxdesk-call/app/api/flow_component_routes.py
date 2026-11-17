"""Flow component API (PART 12, P12-20).

CRUD, versions, usage and "propagate as draft". Every query is tenant-scoped, and the propagation
endpoint deliberately does **not** touch a live agent: it returns the plan that
`app.builder.components.ComponentRegistry.propagation_plan` computes, and creating drafts is a
separate, explicit call.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.builder.components import (
    COMPONENT_SYNC_MODES,
    ComponentBinding,
    ComponentError,
    ComponentRegistry,
    FlowComponent as FlowComponentLogic,
    FlowComponentVersion as FlowComponentVersionLogic,
    graph_hash,
)
from app.db.component_models import AgentComponentBinding, FlowComponent, FlowComponentVersion
from app.db.models import Agent
from app.db.session import get_session

router = APIRouter(tags=["flow-components"])


class ComponentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    graph: dict


class ComponentVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    graph: dict
    changelog: str = ""


class BindingCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: uuid.UUID
    version: int = Field(ge=1)
    sync_mode: str = "snapshot"


def _serialize(component: FlowComponent, versions: list[FlowComponentVersion]) -> dict:
    return {
        "id": str(component.id),
        "name": component.name,
        "description": component.description,
        "is_archived": component.is_archived,
        "versions": [
            {
                "version": version.version,
                "content_hash": version.content_hash,
                "changelog": version.changelog,
                "published": version.published,
                "published_at": version.published_at.isoformat() if version.published_at else None,
            }
            for version in versions
        ],
    }


async def _load_component(
    session: AsyncSession, tenant_id: uuid.UUID, component_id: uuid.UUID
) -> FlowComponent:
    component = (
        await session.execute(
            select(FlowComponent).where(
                FlowComponent.id == component_id, FlowComponent.tenant_id == tenant_id
            )
        )
    ).scalar_one_or_none()
    if component is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="component not found")
    return component


async def _versions(
    session: AsyncSession, component_id: uuid.UUID
) -> list[FlowComponentVersion]:
    return list(
        (
            await session.execute(
                select(FlowComponentVersion)
                .where(FlowComponentVersion.component_id == component_id)
                .order_by(FlowComponentVersion.version)
            )
        )
        .scalars()
        .all()
    )


@router.get("/api/flow-components")
async def list_components(
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = (
        await session.execute(
            select(FlowComponent)
            .where(FlowComponent.tenant_id == ctx.tenant_id, FlowComponent.is_archived.is_(False))
            .order_by(FlowComponent.name)
        )
    ).scalars().all()
    return {
        "components": [
            _serialize(component, await _versions(session, component.id)) for component in rows
        ]
    }


@router.post("/api/flow-components", status_code=status.HTTP_201_CREATED)
async def create_component(
    payload: ComponentCreate,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    try:
        # Validate the graph through the same logic the runner uses, before it is stored.
        FlowComponentVersionLogic(1, payload.graph)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    exists = (
        await session.execute(
            select(FlowComponent).where(
                FlowComponent.tenant_id == ctx.tenant_id, FlowComponent.name == payload.name
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="a component with that name exists")

    component = FlowComponent(
        tenant_id=ctx.tenant_id,
        name=payload.name,
        description=payload.description,
        created_by=str(getattr(ctx, "user_id", "") or ""),
    )
    session.add(component)
    await session.flush()
    version = FlowComponentVersion(
        tenant_id=ctx.tenant_id,
        component_id=component.id,
        version=1,
        graph=payload.graph,
        content_hash=graph_hash(payload.graph),
        changelog="initial",
    )
    session.add(version)
    await session.commit()
    return _serialize(component, [version])


@router.post("/api/flow-components/{component_id}/versions")
async def add_version(
    component_id: uuid.UUID,
    payload: ComponentVersionCreate,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    component = await _load_component(session, ctx.tenant_id, component_id)
    versions = await _versions(session, component.id)
    next_number = max((version.version for version in versions), default=0) + 1
    try:
        FlowComponentVersionLogic(next_number, payload.graph)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    version = FlowComponentVersion(
        tenant_id=ctx.tenant_id,
        component_id=component.id,
        version=next_number,
        graph=payload.graph,
        content_hash=graph_hash(payload.graph),
        changelog=payload.changelog,
    )
    session.add(version)
    await session.commit()
    return {"version": version.version, "content_hash": version.content_hash}


@router.post("/api/flow-components/{component_id}/publish")
async def publish_version(
    component_id: uuid.UUID,
    version: int,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    component = await _load_component(session, ctx.tenant_id, component_id)
    versions = await _versions(session, component.id)
    target = next((item for item in versions if item.version == version), None)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="version not found")
    # Exactly one published version, as the registry enforces in memory.
    for item in versions:
        item.published = item.version == version
    await session.commit()
    return {"component_id": str(component.id), "published_version": version}


@router.get("/api/flow-components/{component_id}/usage")
async def component_usage(
    component_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    component = await _load_component(session, ctx.tenant_id, component_id)
    bindings = (
        await session.execute(
            select(AgentComponentBinding).where(
                AgentComponentBinding.component_id == component.id,
                AgentComponentBinding.tenant_id == ctx.tenant_id,
            )
        )
    ).scalars().all()
    return {
        "count": len(bindings),
        "bindings": [
            {
                "agent_id": str(binding.agent_id),
                "version": binding.version,
                "sync_mode": binding.sync_mode,
                "needs_attention": binding.sync_mode == "snapshot",
            }
            for binding in bindings
        ],
    }


@router.post("/api/flow-components/{component_id}/bindings", status_code=status.HTTP_201_CREATED)
async def bind_agent(
    component_id: uuid.UUID,
    payload: BindingCreate,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    component = await _load_component(session, ctx.tenant_id, component_id)
    if payload.sync_mode not in COMPONENT_SYNC_MODES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"sync_mode must be one of {list(COMPONENT_SYNC_MODES)}",
        )
    versions = await _versions(session, component.id)
    if not any(version.version == payload.version for version in versions):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="version not found")

    agent = (
        await session.execute(
            select(Agent).where(Agent.id == payload.agent_id, Agent.tenant_id == ctx.tenant_id)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    binding = AgentComponentBinding(
        tenant_id=ctx.tenant_id,
        environment_id=getattr(agent, "environment_id", None),
        agent_id=agent.id,
        component_id=component.id,
        version=payload.version,
        sync_mode=payload.sync_mode,
    )
    session.add(binding)
    await session.commit()
    return {"id": str(binding.id), "agent_id": str(binding.agent_id), "version": binding.version}


@router.get("/api/flow-components/{component_id}/propagation-plan")
async def propagation_plan(
    component_id: uuid.UUID,
    new_version: int,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """What a new version *would* mean for the dependents — read-only, nothing is written."""
    component = await _load_component(session, ctx.tenant_id, component_id)
    versions = await _versions(session, component.id)
    if not any(version.version == new_version for version in versions):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="version not found")

    registry = ComponentRegistry()
    registry.register(
        FlowComponentLogic(
            component_id=str(component.id),
            name=component.name,
            versions=tuple(
                FlowComponentVersionLogic(
                    version=version.version, graph=version.graph, published=version.published
                )
                for version in versions
            ),
        )
    )
    bindings = (
        await session.execute(
            select(AgentComponentBinding).where(
                AgentComponentBinding.component_id == component.id,
                AgentComponentBinding.tenant_id == ctx.tenant_id,
            )
        )
    ).scalars().all()
    for binding in bindings:
        registry.bind(
            ComponentBinding(
                agent_id=str(binding.agent_id),
                component_id=str(component.id),
                version=binding.version,
                sync_mode=binding.sync_mode,
            )
        )
    try:
        plan = registry.propagation_plan(str(component.id), new_version)
    except ComponentError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {
        "component_id": str(component.id),
        "new_version": new_version,
        "touches_live": False,
        "plan": plan,
    }


@router.get("/api/flow-components/stats")
async def component_stats(
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    total = (
        await session.execute(
            select(func.count())
            .select_from(FlowComponent)
            .where(FlowComponent.tenant_id == ctx.tenant_id)
        )
    ).scalar_one()
    bindings = (
        await session.execute(
            select(func.count())
            .select_from(AgentComponentBinding)
            .where(AgentComponentBinding.tenant_id == ctx.tenant_id)
        )
    ).scalar_one()
    return {"components": int(total), "bindings": int(bindings)}
