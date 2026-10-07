"""Prevent a second automation side effect for the same event and action.

The unique key is tenant + automation + business event + action. A worker crash
after the insert still cannot insert a second receipt.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.automation.durable_repository import claim_action


def action_key(event_id: str, automation_id: str, action_id: str) -> str:
    return f"{automation_id}:{event_id}:{action_id}"


async def begin_action(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    organization_id: uuid.UUID | None,
    automation_id: str,
    event_id: str,
    action_id: str,
    job_id: uuid.UUID | None = None,
):
    return await claim_action(
        session,
        tenant_id=tenant_id,
        environment_id=environment_id,
        organization_id=organization_id,
        automation_id=automation_id,
        business_event_id=event_id,
        action_id=action_id,
        job_id=job_id,
    )
