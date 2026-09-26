"""Durable notification delivery attempts. Recipient secrets are not stored here."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NotificationDeliveryAttempt, NotificationRow


async def get_notification(
    session: AsyncSession, *, tenant_id: uuid.UUID, notification_id: str
) -> NotificationRow | None:
    row = await session.get(NotificationRow, notification_id)
    if row is None or row.tenant_id != tenant_id:
        return None
    return row


async def record_attempt(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    notification_id: str,
    attempt_number: int,
    provider: str,
    status: str,
    response_class: str = "",
    error_category: str = "",
) -> NotificationDeliveryAttempt:
    row = NotificationDeliveryAttempt(
        tenant_id=tenant_id,
        environment_id=environment_id,
        notification_id=notification_id,
        attempt_number=attempt_number,
        provider=provider,
        status=status,
        response_class=response_class[:32],
        error_category=error_category[:64],
    )
    session.add(row)
    await session.flush()
    return row


async def attempts_for(
    session: AsyncSession, *, tenant_id: uuid.UUID, notification_id: str
) -> list[NotificationDeliveryAttempt]:
    rows = (
        (
            await session.execute(
                select(NotificationDeliveryAttempt)
                .where(
                    NotificationDeliveryAttempt.tenant_id == tenant_id,
                    NotificationDeliveryAttempt.notification_id == notification_id,
                )
                .order_by(NotificationDeliveryAttempt.attempt_number)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)
