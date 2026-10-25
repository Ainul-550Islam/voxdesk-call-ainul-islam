"""Supervisor wallboard projections assembled from durable queue/state rows."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .priority import priority_for_queue, queue_wait_snapshot
from .queue_manager import wallboard
from .skills import requirements_for_queue


async def wallboard_snapshot(session: AsyncSession, *, tenant_id: uuid.UUID) -> dict[str, Any]:
    result = await wallboard(session, tenant_id=tenant_id)
    for queue in result["queues"]:
        queue_id = uuid.UUID(queue["queue_id"])
        config = await priority_for_queue(session, tenant_id=tenant_id, queue_id=queue_id)
        requirements = await requirements_for_queue(session, tenant_id=tenant_id, queue_id=queue_id)
        wait = await queue_wait_snapshot(session, tenant_id=tenant_id, queue_id=queue_id)
        queue["estimated_wait_seconds"] = wait["estimated_wait_seconds"]
        queue["estimate_method"] = wait["estimate_method"]
        queue["priority"] = config.priority if config else queue.get("priority", 0)
        queue["skill_requirements"] = [
            {
                "skill_id": str(row.skill_id),
                "min_proficiency": row.min_proficiency,
                "required": row.required,
                "weight": row.weight,
            }
            for row in requirements
        ]
    return result


__all__ = ["wallboard", "wallboard_snapshot"]
