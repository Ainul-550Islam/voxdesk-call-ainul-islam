"""Final-transcript ingestion boundary used by MonitorBus and assist routes."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .service import capture_transcript, ingest_transcript, turns_for_call


async def ingest_final_turn(
    session: AsyncSession,
    *,
    call_id: uuid.UUID,
    speaker: str,
    text: str,
    seq: int | None = None,
) -> Any:
    return await ingest_transcript(
        session, call_id=call_id, speaker=speaker, text=text, seq=seq, is_final=True
    )


async def capture_monitor_turn(call_id: str | uuid.UUID, text: str, *, speaker: str = "caller", seq: int | None = None) -> bool:
    return await capture_transcript(call_id, text, speaker=speaker, seq=seq, is_final=True)


__all__ = ["capture_monitor_turn", "capture_transcript", "ingest_final_turn", "ingest_transcript", "turns_for_call"]
