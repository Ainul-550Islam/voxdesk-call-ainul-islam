"""Notification dead-letter and replay. Replay cannot change tenant or environment."""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import NotificationRow, UserRole
from app.notifications.repository import attempts_for, get_notification, record_attempt
from app.resources.exceptions import ResourceUnauthorized
from app.tenancy.isolation import BoundaryDenied

_MAX_REPLAY = 3


def operator_may_replay(role: UserRole | None) -> bool:
    return role is not None and has_permission(role, Permission.CAMPAIGN_RUN)


async def move_to_dlq(
    session: AsyncSession, row: NotificationRow, *, category: str
) -> NotificationRow:
    row.delivery_state = "dead_letter"
    row.error_summary = category[:500]
    await session.flush()
    return row


async def replay(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    notification_id: str,
    role: UserRole | None,
    environment_id: uuid.UUID | None = None,
) -> NotificationRow:
    if not operator_may_replay(role):
        raise ResourceUnauthorized()
    row = await get_notification(session, tenant_id=tenant_id, notification_id=notification_id)
    if row is None:
        raise BoundaryDenied()
    if environment_id is not None and row.environment_id != environment_id:
        raise BoundaryDenied()
    if row.delivery_state != "dead_letter":
        raise BoundaryDenied()
    existing = await attempts_for(session, tenant_id=row.tenant_id, notification_id=row.id)
    if sum(1 for item in existing if item.provider == "replay") >= _MAX_REPLAY:
        raise BoundaryDenied()
    row.delivery_state = "pending"
    row.attempts += 1
    await record_attempt(
        session,
        tenant_id=row.tenant_id,
        environment_id=row.environment_id,
        notification_id=row.id,
        attempt_number=row.attempts,
        provider="replay",
        status="queued",
        error_category="replay",
    )
    return row


async def cancel(session: AsyncSession, row: NotificationRow) -> NotificationRow:
    row.delivery_state = "cancelled"
    await session.flush()
    return row
