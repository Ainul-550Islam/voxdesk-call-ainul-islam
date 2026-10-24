"""Provider-neutral idempotent video meeting scheduler for Zoom and Microsoft Teams."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.integrations_v2_models import IntegrationConnection, MeetingRecord
from app.integrations_v2.collab.teams import TeamsProvider
from app.integrations_v2.collab.zoom import ZoomProvider
from app.integrations_v2.core.errors import IntegrationV2Error, ProviderRejected
from app.integrations_v2.register_all import instantiate_provider_v2


async def schedule_meeting(
    session: AsyncSession,
    *,
    connection: IntegrationConnection,
    topic: str,
    when: datetime | None = None,
    duration_minutes: int = 30,
    lead_id: str = "",
    call_id: str = "",
    agenda: str = "",
    idempotency_key: str = "",
    http_client: Any = None,
) -> tuple[MeetingRecord, bool]:
    """Schedule a meeting on Zoom or Microsoft Teams idempotently.

    Returns ``(meeting_record, created_new)``. Never fabricates a ``join_url``;
    raises ``ProviderRejected`` if the upstream response omits a real ``join_url``.
    """
    start_dt = when or (datetime.now(timezone.utc) + timedelta(minutes=15))
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)

    idem = (
        idempotency_key.strip()
        or f"mtg:{connection.id}:{lead_id or call_id or topic}:{int(start_dt.timestamp())}"
    )[:160]

    existing = (
        await session.execute(
            select(MeetingRecord).where(
                MeetingRecord.tenant_id == connection.tenant_id,
                MeetingRecord.idempotency_key == idem,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    adapter = instantiate_provider_v2(connection, http_client=http_client)
    if isinstance(adapter, ZoomProvider):
        raw = await adapter.create_meeting(
            topic=topic,
            start_time=start_dt,
            duration_minutes=duration_minutes,
            agenda=agenda,
        )
    elif isinstance(adapter, TeamsProvider):
        raw = await adapter.create_online_meeting(
            subject=topic,
            start_time=start_dt,
            duration_minutes=duration_minutes,
        )
    else:
        raise IntegrationV2Error(
            f"Provider {connection.provider!r} does not support video meeting creation",
            provider=connection.provider,
        )

    join_url = str(raw.get("join_url") or "").strip()
    ext_id = str(raw.get("meeting_id") or "").strip()
    if not join_url or not join_url.startswith("https://") or not ext_id:
        raise ProviderRejected(
            f"{connection.provider} meeting response did not include a valid join_url and meeting_id",
            provider=connection.provider,
        )

    record = MeetingRecord(
        tenant_id=connection.tenant_id,
        environment_id=connection.environment_id,
        connection_id=connection.id,
        provider=connection.provider,
        lead_id=str(lead_id or "")[:120],
        call_id=str(call_id or "")[:120],
        external_meeting_id=ext_id[:160],
        topic=topic[:240],
        join_url=join_url[:1024],
        start_time=start_dt,
        duration_minutes=max(5, int(duration_minutes)),
        idempotency_key=idem,
        status="scheduled",
    )
    session.add(record)
    await session.flush()
    return record, True
