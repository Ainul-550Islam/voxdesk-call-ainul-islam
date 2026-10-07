"""Tenant and user notification preferences.

A false preference suppresses optional channels. Security alerts are not
suppressed by a user preference.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import NotificationPreference

_CHANNEL_FIELD = {
    "email": "email_enabled",
    "sms": "sms_enabled",
    "in_app": "in_app_enabled",
    "webhook": "webhook_enabled",
}


async def get_or_create(
    session: AsyncSession, *, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> NotificationPreference:
    row = (
        await session.execute(
            select(NotificationPreference).where(
                NotificationPreference.tenant_id == tenant_id,
                NotificationPreference.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    row = NotificationPreference(tenant_id=tenant_id, user_id=user_id)
    session.add(row)
    await session.flush()
    return row


def allows(preference: NotificationPreference, *, channel: str, category: str) -> bool:
    if category == "security":
        return True
    field = _CHANNEL_FIELD.get(channel)
    if field is None:
        return False
    if category == "operational" and not preference.operational_alerts:
        return False
    return bool(getattr(preference, field))
