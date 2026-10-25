"""Tenant/environment-scoped, idempotent campaign state transitions."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.errors import Conflict, Validation
from app.db.contact_center_v2_models import DialerPolicy

from .dialer_service import pause_campaign, policy_for, start_campaign

CampaignAction = Literal["start", "pause", "stop"]


@dataclass(frozen=True)
class CampaignStateResult:
    policy: DialerPolicy
    job_id: str | None = None
    replayed: bool = False


async def transition_campaign_state(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID | None,
    policy_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    action: CampaignAction,
) -> CampaignStateResult:
    policy = await policy_for(session, tenant_id, policy_id)
    if environment_id is not None and policy.environment_id != environment_id:
        # Do not reveal that another environment owns the policy.
        from app.contact_center_v2.errors import NotFound

        raise NotFound("dialer policy")
    if action == "start":
        if policy.status == "running":
            return CampaignStateResult(policy=policy, replayed=True)
        if policy.status in {"completed", "stopped"}:
            raise Conflict("Closed campaigns cannot be restarted")
        result = await start_campaign(
            session,
            tenant_id=tenant_id,
            policy_id=policy_id,
            actor_id=actor_user_id,
        )
        return CampaignStateResult(policy=result[0], job_id=str(result[1]))
    if action == "pause":
        if policy.status == "paused":
            return CampaignStateResult(policy=policy, replayed=True)
        if policy.status in {"completed", "stopped"}:
            raise Conflict("Closed campaigns cannot be paused")
        return CampaignStateResult(
            policy=await pause_campaign(session, tenant_id=tenant_id, policy_id=policy_id)
        )
    if action == "stop":
        if policy.status in {"stopped", "completed"}:
            return CampaignStateResult(policy=policy, replayed=True)
        return CampaignStateResult(
            policy=await pause_campaign(
                session, tenant_id=tenant_id, policy_id=policy_id, stopped=True
            )
        )
    raise Validation("Unknown campaign state action")


__all__ = ["CampaignAction", "CampaignStateResult", "transition_campaign_state"]
