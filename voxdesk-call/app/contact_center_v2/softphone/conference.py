"""Provider-backed conference bridging and agent-leg reconciliation."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .service import (
    complete_warm_transfer,
    prepare_acd_call,
    prepare_answered_dialer_call,
    record_agent_leg,
)


def conference_name(call_id: uuid.UUID) -> str:
    return f"voxdesk-{call_id}"


async def bridge_acd_call(session: AsyncSession, *, entry_id: uuid.UUID, agent_user_id: uuid.UUID, adapter: Any = None):
    return await prepare_acd_call(session, entry_id=entry_id, agent_user_id=agent_user_id, adapter=adapter)


async def join_agent_leg(
    session: AsyncSession,
    *,
    call_id: uuid.UUID,
    softphone_session_id: uuid.UUID,
    provider_call_id: str,
    from_uri: str,
):
    return await record_agent_leg(
        session,
        call_id=call_id,
        softphone_session_id=softphone_session_id,
        provider_call_id=provider_call_id,
        from_uri=from_uri,
    )


__all__ = [
    "bridge_acd_call",
    "complete_warm_transfer",
    "conference_name",
    "join_agent_leg",
    "prepare_acd_call",
    "prepare_answered_dialer_call",
    "record_agent_leg",
]
