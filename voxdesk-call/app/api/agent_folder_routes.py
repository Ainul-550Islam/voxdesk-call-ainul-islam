"""Agent folders API (PART 12, P12-25).

A folder tree, capped at a sane depth so "move to folder" cannot build a cycle, and ordered by an
explicit `position` because alphabetical ordering of "Inbound" / "inbound" surprises people.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission
from app.db.component_models import AgentFolder
from app.db.models import Agent
from app.db.session import get_session

router = APIRouter(tags=["agent-folders"])

MAX_FOLDER_DEPTH = 5


class FolderCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    parent_id: uuid.UUID | None = None
    position: int = 0


class FolderMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    parent_id: uuid.UUID | None = None
    position: int = 0


class AgentMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    folder_id: uuid.UUID | None = None


def _serialize(folder: AgentFolder) -> dict:
    return {
        "id": str(folder.id),
        "name": folder.name,
        "parent_id": str(folder.parent_id) if folder.parent_id else None,
        "position": folder.position,
    }


async def _depth(session: AsyncSession, tenant_id: uuid.UUID, folder_id: uuid.UUID | None) -> int:
    depth = 0
    cursor = folder_id
    seen: set[uuid.UUID] = set()
    while cursor is not None:
        if cursor in seen:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="folder cycle detected")
        seen.add(cursor)
        row = (
            await session.execute(
                select(AgentFolder).where(
                    AgentFolder.id == cursor, AgentFolder.tenant_id == tenant_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="folder not found")
        depth += 1
        cursor = row.parent_id
    return depth


@router.get("/api/agent-folders")
async def list_folders(
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_READ)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    rows = (
        await session.execute(
            select(AgentFolder)
            .where(AgentFolder.tenant_id == ctx.tenant_id)
            .order_by(AgentFolder.position, AgentFolder.name)
        )
    ).scalars().all()
    return {"folders": [_serialize(folder) for folder in rows]}


@router.post("/api/agent-folders", status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: FolderCreate,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if payload.parent_id is not None:
        depth = await _depth(session, ctx.tenant_id, payload.parent_id)
        if depth >= MAX_FOLDER_DEPTH:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"folders nest at most {MAX_FOLDER_DEPTH} deep",
            )
    folder = AgentFolder(
        tenant_id=ctx.tenant_id,
        parent_id=payload.parent_id,
        name=payload.name,
        position=payload.position,
    )
    session.add(folder)
    await session.commit()
    return _serialize(folder)


@router.patch("/api/agent-folders/{folder_id}")
async def move_folder(
    folder_id: uuid.UUID,
    payload: FolderMove,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    folder = (
        await session.execute(
            select(AgentFolder).where(
                AgentFolder.id == folder_id, AgentFolder.tenant_id == ctx.tenant_id
            )
        )
    ).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="folder not found")
    if payload.parent_id == folder.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="a folder cannot contain itself")
    if payload.parent_id is not None:
        await _depth(session, ctx.tenant_id, payload.parent_id)
    folder.parent_id = payload.parent_id
    folder.position = payload.position
    await session.commit()
    return _serialize(folder)


@router.delete("/api/agent-folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: uuid.UUID,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> None:
    folder = (
        await session.execute(
            select(AgentFolder).where(
                AgentFolder.id == folder_id, AgentFolder.tenant_id == ctx.tenant_id
            )
        )
    ).scalar_one_or_none()
    if folder is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="folder not found")
    # Agents are not deleted with a folder; they become unfiled.
    for agent in (
        await session.execute(
            select(Agent).where(Agent.tenant_id == ctx.tenant_id, Agent.folder_id == folder.id)
        )
    ).scalars().all():
        agent.folder_id = None
    await session.delete(folder)
    await session.commit()


@router.patch("/api/agents/{agent_id}/folder")
async def move_agent(
    agent_id: uuid.UUID,
    payload: AgentMove,
    ctx: TenantContext = Depends(require_permission(Permission.AGENT_STATE_WRITE)),
    session: AsyncSession = Depends(get_session),
) -> dict:
    agent = (
        await session.execute(
            select(Agent).where(Agent.id == agent_id, Agent.tenant_id == ctx.tenant_id)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")
    if payload.folder_id is not None:
        await _depth(session, ctx.tenant_id, payload.folder_id)
    agent.folder_id = payload.folder_id
    await session.commit()
    return {"agent_id": str(agent.id), "folder_id": str(agent.folder_id) if agent.folder_id else None}
