"""Activity timeline. References Call and Appointment rows; does not copy transcripts."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Appointment, Call, Lead
from app.leads.models import LeadActivity, LeadTask
from app.leads.repository import activities_for, history_for

_SUMMARY_MAX = 180


def _clip(value: str | None) -> str:
    text = (value or "").replace("\n", " ").strip()
    if len(text) <= _SUMMARY_MAX:
        return text
    return text[: _SUMMARY_MAX - 1] + "…"


async def record(
    session: AsyncSession,
    lead: Lead,
    *,
    kind: str,
    summary: str,
    actor_id: uuid.UUID | None = None,
    call_id: uuid.UUID | None = None,
    appointment_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
) -> LeadActivity:
    row = LeadActivity(
        lead_id=lead.id,
        tenant_id=lead.tenant_id,
        environment_id=lead.environment_id,
        kind=kind[:32],
        summary=_clip(summary),
        actor_id=actor_id,
        call_id=call_id,
        appointment_id=appointment_id,
        task_id=task_id,
    )
    session.add(row)
    await session.flush()
    return row


async def timeline(
    session: AsyncSession,
    lead: Lead,
    *,
    limit: int,
    offset: int,
) -> list[dict]:
    """Merge stored activities with references to existing source objects."""
    events: list[dict] = []
    for row in await history_for(session, lead.tenant_id, lead.environment_id, lead.id):
        events.append({
            "type": "status",
            "id": str(row.id),
            "occurred_at": row.created_at.isoformat(),
            "summary": _clip(f"{row.from_status or 'none'} -> {row.to_status}: {row.reason}"),
            "source_id": None,
        })
    for row in await activities_for(
        session, lead.tenant_id, lead.environment_id, lead.id, limit=limit, offset=0
    ):
        events.append({
            "type": row.kind,
            "id": str(row.id),
            "occurred_at": row.created_at.isoformat(),
            "summary": row.summary,
            "source_id": str(row.call_id or row.appointment_id or row.task_id or ""),
        })
    calls = (
        await session.execute(
            select(Call.id, Call.status, Call.direction, Call.intent, Call.started_at)
            .where(
                Call.tenant_id == lead.tenant_id,
                Call.environment_id == lead.environment_id,
                Call.lead_id == lead.id,
            )
            .order_by(Call.started_at.desc())
            .limit(limit)
        )
    ).all()
    for call_id, status, direction, intent, started_at in calls:
        events.append({
            "type": "call",
            "id": str(call_id),
            "occurred_at": started_at.isoformat() if started_at else "",
            "summary": _clip(
                f"{getattr(direction, 'value', direction)} {getattr(status, 'value', status)} {intent or ''}"
            ),
            "source_id": str(call_id),
        })
    appointments = (
        await session.execute(
            select(
                Appointment.id,
                Appointment.status,
                Appointment.reason,
                Appointment.starts_at,
            )
            .where(
                Appointment.tenant_id == lead.tenant_id,
                Appointment.environment_id == lead.environment_id,
                Appointment.customer_phone == lead.phone,
            )
            .order_by(Appointment.starts_at.desc())
            .limit(limit)
        )
    ).all()
    for appointment_id, status, reason, starts_at in appointments:
        events.append({
            "type": "appointment",
            "id": str(appointment_id),
            "occurred_at": starts_at.isoformat() if starts_at else "",
            "summary": _clip(f"{getattr(status, 'value', status)} {reason or ''}"),
            "source_id": str(appointment_id),
        })
    tasks = (
        await session.execute(
            select(LeadTask.id, LeadTask.status, LeadTask.title, LeadTask.created_at)
            .where(
                LeadTask.tenant_id == lead.tenant_id,
                LeadTask.environment_id == lead.environment_id,
                LeadTask.lead_id == lead.id,
            )
            .limit(limit)
        )
    ).all()
    for task_id, status, title, created_at in tasks:
        events.append({
            "type": "task",
            "id": str(task_id),
            "occurred_at": created_at.isoformat(),
            "summary": _clip(f"{status} {title}"),
            "source_id": str(task_id),
        })
    events.sort(key=lambda item: item["occurred_at"], reverse=True)
    return events[offset: offset + limit]
