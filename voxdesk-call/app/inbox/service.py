"""Durable inbox mutations. Each change is one transaction against the row."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InboxThreadState
from app.inbox.concurrency import compare_and_set
from app.inbox.repository import require_thread


async def read_thread(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    call_id: uuid.UUID,
) -> InboxThreadState:
    return await require_thread(
        session, tenant_id=tenant_id, environment_id=environment_id, call_id=call_id
    )


async def mark_read(
    session: AsyncSession, row: InboxThreadState, *, tenant_id: uuid.UUID, environment_id: uuid.UUID
) -> InboxThreadState:
    return await compare_and_set(
        session, row, tenant_id=tenant_id, environment_id=environment_id, values={"unread": 0}
    )


async def mark_unread(
    session: AsyncSession, row: InboxThreadState, *, tenant_id: uuid.UUID, environment_id: uuid.UUID
) -> InboxThreadState:
    return await compare_and_set(
        session, row, tenant_id=tenant_id, environment_id=environment_id, values={"unread": 1}
    )


async def set_priority(
    session: AsyncSession,
    row: InboxThreadState,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    priority: str,
) -> InboxThreadState:
    return await compare_and_set(
        session,
        row,
        tenant_id=tenant_id,
        environment_id=environment_id,
        values={"priority": priority},
    )


async def add_tag(
    session: AsyncSession,
    row: InboxThreadState,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    tag: str,
) -> InboxThreadState:
    tags = list(row.tags or [])
    if tag not in tags:
        tags.append(tag)
    return await compare_and_set(
        session, row, tenant_id=tenant_id, environment_id=environment_id, values={"tags": tags}
    )


async def remove_tag(
    session: AsyncSession,
    row: InboxThreadState,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    tag: str,
) -> InboxThreadState:
    tags = [item for item in (row.tags or []) if item != tag]
    return await compare_and_set(
        session, row, tenant_id=tenant_id, environment_id=environment_id, values={"tags": tags}
    )


async def add_note(
    session: AsyncSession,
    row: InboxThreadState,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    body: str,
    author: str,
    at: str,
) -> InboxThreadState:
    notes = list(row.notes or [])
    notes.append({"body": body[:2000], "author": author[:64], "at": at})
    return await compare_and_set(
        session, row, tenant_id=tenant_id, environment_id=environment_id, values={"notes": notes}
    )


async def close_thread(
    session: AsyncSession, row: InboxThreadState, *, tenant_id: uuid.UUID, environment_id: uuid.UUID
) -> InboxThreadState:
    return await compare_and_set(
        session,
        row,
        tenant_id=tenant_id,
        environment_id=environment_id,
        values={"status": "closed"},
    )


async def reopen_thread(
    session: AsyncSession, row: InboxThreadState, *, tenant_id: uuid.UUID, environment_id: uuid.UUID
) -> InboxThreadState:
    return await compare_and_set(
        session, row, tenant_id=tenant_id, environment_id=environment_id, values={"status": "open"}
    )
