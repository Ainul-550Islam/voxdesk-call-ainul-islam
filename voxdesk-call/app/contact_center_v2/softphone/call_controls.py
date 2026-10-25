"""Named call-control operations backed by the authoritative provider service."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .service import (
    cancel_consultation,
    complete_warm_transfer,
    control_call,
    create_outbound_call,
    end_call,
    get_call,
    list_calls,
    set_recording_pause,
    transfer_call,
)


async def mute_call(session: AsyncSession, context: Any, *, call_id: uuid.UUID, muted: bool = True, **kwargs: Any):
    return await control_call(session, context, call_id=call_id, action="mute", enabled=muted, **kwargs)


async def hold_call(session: AsyncSession, context: Any, *, call_id: uuid.UUID, held: bool = True, **kwargs: Any):
    return await control_call(session, context, call_id=call_id, action="hold", enabled=held, **kwargs)


async def resume_call(session: AsyncSession, context: Any, *, call_id: uuid.UUID, **kwargs: Any):
    return await control_call(session, context, call_id=call_id, action="hold", enabled=False, **kwargs)


async def record_pause(session: AsyncSession, context: Any, *, call_id: uuid.UUID, paused: bool = True, **kwargs: Any):
    return await set_recording_pause(session, context, call_id=call_id, paused=paused, **kwargs)


async def conference_add(session: AsyncSession, context: Any, *, call_id: uuid.UUID, destination: str, **kwargs: Any):
    return await transfer_call(session, context, call_id=call_id, destination=destination, mode="warm", **kwargs)


async def hangup_call(session: AsyncSession, context: Any, *, call_id: uuid.UUID, **kwargs: Any):
    return await end_call(session, context, call_id=call_id, **kwargs)


async def blind_transfer(session: AsyncSession, context: Any, *, call_id: uuid.UUID, destination: str, **kwargs: Any):
    return await transfer_call(session, context, call_id=call_id, destination=destination, mode="blind", **kwargs)


async def warm_transfer(session: AsyncSession, context: Any, *, call_id: uuid.UUID, destination: str, **kwargs: Any):
    return await transfer_call(session, context, call_id=call_id, destination=destination, mode="warm", **kwargs)


__all__ = [
    "blind_transfer",
    "cancel_consultation",
    "complete_warm_transfer",
    "conference_add",
    "control_call",
    "create_outbound_call",
    "end_call",
    "get_call",
    "hangup_call",
    "hold_call",
    "list_calls",
    "mute_call",
    "record_pause",
    "resume_call",
    "set_recording_pause",
    "transfer_call",
    "warm_transfer",
]
