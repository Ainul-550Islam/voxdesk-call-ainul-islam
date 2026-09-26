"""Durable assignment. Exactly one agent wins a simultaneous claim."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InboxThreadState
from app.inbox.concurrency import compare_and_set
from app.inbox.repository import require_thread
from app.tenancy.isolation import Conflict


async def claim(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    call_id: uuid.UUID,
    agent_id: str,
) -> InboxThreadState:
    row = await require_thread(
        session, tenant_id=tenant_id, environment_id=environment_id, call_id=call_id
    )
    if row.assignee_id and row.assignee_id != agent_id:
        raise Conflict("thread already assigned")
    return await compare_and_set(
        session,
        row,
        tenant_id=tenant_id,
        environment_id=environment_id,
        values={"assignee_id": agent_id, "status": "assigned"},
    )


async def release(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    call_id: uuid.UUID,
    agent_id: str,
) -> InboxThreadState:
    row = await require_thread(
        session, tenant_id=tenant_id, environment_id=environment_id, call_id=call_id
    )
    if row.assignee_id != agent_id:
        raise Conflict("only the assignee can release")
    return await compare_and_set(
        session,
        row,
        tenant_id=tenant_id,
        environment_id=environment_id,
        values={"assignee_id": "", "status": "open"},
    )


async def assign(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    call_id: uuid.UUID,
    agent_id: str,
) -> InboxThreadState:
    row = await require_thread(
        session, tenant_id=tenant_id, environment_id=environment_id, call_id=call_id
    )
    return await compare_and_set(
        session,
        row,
        tenant_id=tenant_id,
        environment_id=environment_id,
        values={"assignee_id": agent_id, "status": "assigned"},
    )
