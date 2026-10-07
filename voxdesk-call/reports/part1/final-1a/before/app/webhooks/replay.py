"""Authorized webhook replay. Scope and subscription ownership cannot change."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import Permission
from app.auth.rbac import has_permission
from app.db.models import UserRole, WebhookDelivery
from app.resources.exceptions import ResourceUnauthorized
from app.tenancy.isolation import BoundaryDenied
from app.webhooks.repository import get_subscription

_MAX_REPLAY = 3


def operator_may_replay(role: UserRole | None) -> bool:
    return role is not None and has_permission(role, Permission.CAMPAIGN_RUN)


async def replay_delivery(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    delivery_id: uuid.UUID,
    subscription_id: uuid.UUID,
    role: UserRole | None,
) -> WebhookDelivery:
    if not operator_may_replay(role):
        raise ResourceUnauthorized()
    subscription = await get_subscription(
        session, tenant_id=tenant_id, subscription_id=subscription_id
    )
    if subscription is None:
        raise BoundaryDenied()
    delivery = await session.get(WebhookDelivery, delivery_id)
    if (
        delivery is None
        or delivery.tenant_id != tenant_id
        or delivery.subscription_id != subscription.id
    ):
        raise BoundaryDenied()
    if delivery.status not in {"dead_letter", "failed", "permanent_failure"}:
        raise BoundaryDenied()
    if delivery.replay_count >= _MAX_REPLAY:
        raise BoundaryDenied()
    delivery.replay_count += 1
    delivery.status = "queued"
    delivery.next_attempt_at = datetime.now(timezone.utc)
    delivery.completed_at = None
    await session.flush()
    return delivery
