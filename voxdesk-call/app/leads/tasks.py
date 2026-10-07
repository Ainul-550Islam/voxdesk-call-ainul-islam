"""Lead tasks. Completion is a status change, not a delete."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Lead
from app.leads.exceptions import InvalidTransition
from app.leads.models import LeadTask
from app.leads.repository import get_task, user_in_tenant

_OPEN = {"open", "assigned", "in_progress"}
_ALLOWED = {
    "open": {"assigned", "in_progress", "cancelled"},
    "assigned": {"in_progress", "completed", "cancelled"},
    "in_progress": {"completed", "cancelled"},
    "completed": set(),
    "cancelled": set(),
}


async def create_task(
    session: AsyncSession,
    lead: Lead,
    *,
    title: str,
    assignee_id: uuid.UUID | None = None,
    due_at: datetime | None = None,
) -> LeadTask:
    cleaned = title.strip()
    if not cleaned or len(cleaned) > 160:
        raise InvalidTransition("Task title is required and must be at most 160 characters")
    status = "open"
    if assignee_id is not None:
        await user_in_tenant(session, lead.tenant_id, assignee_id)
        status = "assigned"
    row = LeadTask(
        lead_id=lead.id,
        tenant_id=lead.tenant_id,
        environment_id=lead.environment_id,
        title=cleaned,
        status=status,
        assignee_id=assignee_id,
        due_at=due_at,
    )
    session.add(row)
    await session.flush()
    return row


async def change_task(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    task_id: uuid.UUID,
    status: str | None = None,
    assignee_id: uuid.UUID | None = None,
) -> LeadTask:
    row = await get_task(session, tenant_id, environment_id, task_id)
    if assignee_id is not None:
        await user_in_tenant(session, tenant_id, assignee_id)
        row.assignee_id = assignee_id
        if row.status == "open":
            row.status = "assigned"
    if status is not None:
        target = status.strip().lower()
        if target not in _ALLOWED.get(row.status, set()):
            raise InvalidTransition(f"Cannot move a task from {row.status} to {target}")
        row.status = target
        if target == "completed":
            row.completed_at = datetime.utcnow()
    row.updated_at = datetime.utcnow()
    await session.flush()
    return row
