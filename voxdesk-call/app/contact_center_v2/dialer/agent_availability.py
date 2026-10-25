"""Available and soon-available human-agent estimates from real call/state rows."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import median

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.models import AgentPresence
from app.contact_center_v2.occupancy import available_agent_ids
from app.contact_center_v2.repository import as_utc, utcnow
from app.db.contact_center_v2_models import SoftphoneCall


@dataclass(frozen=True)
class AgentAvailabilityEstimate:
    available_now: int
    soon_available: Decimal
    busy_agents: int
    occupancy: Decimal
    median_talk_seconds: Decimal
    agent_ids: tuple[uuid.UUID, ...]

    @property
    def effective_agents(self) -> Decimal:
        return Decimal(self.available_now) + self.soon_available


def soon_available_weight(
    *, elapsed_seconds: float, remaining_seconds: float, horizon_seconds: float = 120
) -> Decimal:
    """Conservative fractional credit for a busy agent nearing expected wrap-up."""
    if horizon_seconds <= 0:
        raise ValueError("horizon_seconds must be positive")
    if remaining_seconds <= 0:
        return Decimal("1")
    if remaining_seconds >= horizon_seconds or elapsed_seconds < 0:
        return Decimal("0")
    value = Decimal("1") - (Decimal(str(remaining_seconds)) / Decimal(str(horizon_seconds)))
    return max(Decimal("0"), min(Decimal("1"), value)).quantize(Decimal("0.001"))


async def estimate_agent_availability(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    now: datetime | None = None,
) -> AgentAvailabilityEstimate:
    moment = now or utcnow()
    ids = await available_agent_ids(
        session,
        tenant_id,
        now=moment,
        require_softphone=True,
        environment_id=environment_id,
    )
    presences = (
        (
            await session.execute(
                select(AgentPresence).where(
                    AgentPresence.tenant_id == tenant_id,
                    AgentPresence.state.in_(("busy", "wrap_up", "hold")),
                )
            )
        )
        .scalars()
        .all()
    )
    active_calls = (
        (
            await session.execute(
                select(SoftphoneCall).where(
                    SoftphoneCall.tenant_id == tenant_id,
                    SoftphoneCall.environment_id == environment_id,
                    SoftphoneCall.status.in_(("dialing", "waiting_for_agent", "in_progress", "held")),
                )
            )
        )
        .scalars()
        .all()
    )
    durations = [
        max(0.0, (moment - as_utc(row.answered_at or row.created_at)).total_seconds())
        for row in active_calls
    ]
    talk_seconds = Decimal(str(median(durations) if durations else 180.0))
    soon = Decimal("0")
    for row in active_calls:
        if row.agent_user_id in ids or row.answered_at is None:
            continue
        elapsed = max(0.0, (moment - as_utc(row.answered_at)).total_seconds())
        remaining = max(0.0, float(talk_seconds) - elapsed)
        soon += soon_available_weight(elapsed_seconds=elapsed, remaining_seconds=remaining)
    capacity = sum(max(1, int(row.capacity)) for row in presences)
    active = sum(max(0, int(row.active_count)) for row in presences)
    occupancy = Decimal(active) / Decimal(capacity) if capacity else Decimal("0")
    return AgentAvailabilityEstimate(
        available_now=len(ids),
        soon_available=soon.quantize(Decimal("0.001")),
        busy_agents=len(presences),
        occupancy=min(Decimal("1"), occupancy),
        median_talk_seconds=talk_seconds.quantize(Decimal("0.1")),
        agent_ids=tuple(ids),
    )


__all__ = [
    "AgentAvailabilityEstimate",
    "estimate_agent_availability",
    "soon_available_weight",
]
