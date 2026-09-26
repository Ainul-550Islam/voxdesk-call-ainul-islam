"""Durable SLA clocks. Deadlines live on the inbox row, not in process memory."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import InboxThreadState
from app.inbox.repository import require_thread

FIRST_RESPONSE_SECONDS = 300
RESOLUTION_HOURS = 24


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


async def start(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    call_id: uuid.UUID,
    now: datetime | None = None,
) -> InboxThreadState:
    moment = now or datetime.now(timezone.utc)
    row = await require_thread(
        session, tenant_id=tenant_id, environment_id=environment_id, call_id=call_id
    )
    row.first_response_deadline = _iso(moment + timedelta(seconds=FIRST_RESPONSE_SECONDS))
    row.resolution_deadline = _iso(moment + timedelta(hours=RESOLUTION_HOURS))
    row.sla_deadline_at = row.first_response_deadline
    row.sla_state = "running"
    row.sla_paused = False
    await session.flush()
    return row


async def evaluate(
    session: AsyncSession,
    row: InboxThreadState,
    *,
    now: datetime | None = None,
) -> InboxThreadState:
    if row.sla_paused or row.sla_state == "met":
        return row
    moment = now or datetime.now(timezone.utc)
    deadline = row.resolution_deadline or row.sla_deadline_at
    if deadline and moment.isoformat() > deadline and row.sla_state != "breached":
        row.sla_state = "breached"
        row.sla_breached_at = _iso(moment)
        await session.flush()
    return row


async def pause(session: AsyncSession, row: InboxThreadState) -> InboxThreadState:
    row.sla_paused = True
    row.sla_state = "paused"
    await session.flush()
    return row


async def resume(session: AsyncSession, row: InboxThreadState) -> InboxThreadState:
    row.sla_paused = False
    row.sla_state = "running"
    await session.flush()
    return row
