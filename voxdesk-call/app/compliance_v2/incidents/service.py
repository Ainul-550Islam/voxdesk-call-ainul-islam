"""Incident register with a real, validated lifecycle.

Transitions are enforced, not merely recorded:

``detected → triaged → contained → resolved → closed``

Each step stamps its own timestamp, writes a timeline entry and audits the
change. A skipped step is refused — an incident that goes from ``detected``
straight to ``closed`` has no containment evidence, and an auditor is right to
ask for it. ``closed`` is terminal; reopening means a new incident that
references the old one, which keeps history honest.

Cross-tenant visibility: an incident may be platform-wide (``tenant_id`` NULL)
or belong to one tenant. A tenant-scoped caller only ever sees its own.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.db.compliance_v2_models import Incident, IncidentTimeline

STATUS_DETECTED = "detected"
STATUS_TRIAGED = "triaged"
STATUS_CONTAINED = "contained"
STATUS_RESOLVED = "resolved"
STATUS_CLOSED = "closed"

#: Allowed forward moves. Anything else is refused with the current status.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    STATUS_DETECTED: (STATUS_TRIAGED,),
    STATUS_TRIAGED: (STATUS_CONTAINED,),
    STATUS_CONTAINED: (STATUS_RESOLVED,),
    STATUS_RESOLVED: (STATUS_CLOSED,),
    STATUS_CLOSED: (),
}

SEVERITIES = ("sev1", "sev2", "sev3", "sev4")

#: The timestamp column each transition stamps.
_STAMP_COLUMN = {
    STATUS_TRIAGED: "triaged_at",
    STATUS_CONTAINED: "contained_at",
    STATUS_RESOLVED: "resolved_at",
    STATUS_CLOSED: "closed_at",
}


class IncidentError(RuntimeError):
    code = "incident_error"


class IncidentNotFound(IncidentError):
    code = "incident_not_found"


class InvalidTransition(IncidentError):
    code = "invalid_transition"


def _utcnow(moment: dt.datetime | None = None) -> dt.datetime:
    value = moment or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


def _reference(moment: dt.datetime) -> str:
    """Human-quotable incident reference: ``INC-20261024-A1B2C3``."""
    return f"INC-{moment.strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"


async def record_timeline(
    session: AsyncSession,
    *,
    incident_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    entry_type: str,
    message: str,
    actor_user_id: uuid.UUID | None = None,
    detail: dict | None = None,
) -> IncidentTimeline:
    row = IncidentTimeline(
        tenant_id=tenant_id,
        incident_id=incident_id,
        entry_type=entry_type[:32],
        message=(message or "")[:2000],
        actor_user_id=actor_user_id,
        detail=dict(detail or {}),
    )
    session.add(row)
    await session.flush()
    return row


async def create_incident(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
    title: str,
    summary: str,
    severity: str,
    created_by: uuid.UUID | None,
    category: str = "security",
    affected_tenants: list[uuid.UUID] | None = None,
    personal_data_involved: bool = False,
    detected_at: dt.datetime | None = None,
) -> Incident:
    """Open an incident at ``detected``. Flushes."""
    if severity not in SEVERITIES:
        raise IncidentError(f"severity must be one of {SEVERITIES}")
    if not (title or "").strip():
        raise IncidentError("an incident needs a title")
    moment = _utcnow(detected_at)
    incident = Incident(
        tenant_id=tenant_id,
        reference=_reference(moment),
        title=title.strip()[:200],
        summary=(summary or "")[:8000],
        severity=severity,
        status=STATUS_DETECTED,
        category=(category or "security")[:64],
        affected_tenants=[str(item) for item in (affected_tenants or [])],
        personal_data_involved=bool(personal_data_involved),
        created_by=created_by,
        detected_at=moment,
    )
    session.add(incident)
    await session.flush()
    await record_timeline(
        session,
        incident_id=incident.id,
        tenant_id=tenant_id,
        entry_type="detected",
        message=f"Incident detected: {incident.title}",
        actor_user_id=created_by,
        detail={"severity": severity, "personal_data_involved": bool(personal_data_involved)},
    )
    if tenant_id is not None:
        await record_event(
            session,
            tenant_id=tenant_id,
            actor_user_id=created_by,
            event_type="incident_detected",
            resource_type="incident",
            resource_id=incident.id,
            detail={"reference": incident.reference, "severity": severity},
        )
    return incident


async def get_incident(
    session: AsyncSession, *, incident_id: uuid.UUID, tenant_id: uuid.UUID | None = None
) -> Incident:
    row = await session.scalar(select(Incident).where(Incident.id == incident_id))
    if row is None:
        raise IncidentNotFound("incident not found")
    if tenant_id is not None and row.tenant_id not in (None, tenant_id):
        # A tenant-scoped caller must not learn about another tenant's incident.
        raise IncidentNotFound("incident not found")
    return row


async def advance(
    session: AsyncSession,
    *,
    incident_id: uuid.UUID,
    to_status: str,
    actor_user_id: uuid.UUID,
    note: str = "",
    tenant_id: uuid.UUID | None = None,
    now: dt.datetime | None = None,
) -> Incident:
    """Move an incident one step forward. Flushes."""
    incident = await get_incident(session, incident_id=incident_id, tenant_id=tenant_id)
    allowed = TRANSITIONS.get(incident.status, ())
    if to_status not in allowed:
        raise InvalidTransition(
            f"cannot move an incident from {incident.status!r} to {to_status!r}"
        )
    moment = _utcnow(now)
    incident.status = to_status
    setattr(incident, _STAMP_COLUMN[to_status], moment)
    await record_timeline(
        session,
        incident_id=incident.id,
        tenant_id=incident.tenant_id,
        entry_type=to_status,
        message=note or f"Incident moved to {to_status}",
        actor_user_id=actor_user_id,
        detail={"from": incident.status, "to": to_status},
    )
    if incident.tenant_id is not None:
        await record_event(
            session,
            tenant_id=incident.tenant_id,
            actor_user_id=actor_user_id,
            event_type=f"incident_{to_status}",
            resource_type="incident",
            resource_id=incident.id,
            detail={"reference": incident.reference},
        )
    await session.flush()
    return incident


async def timeline(
    session: AsyncSession, *, incident_id: uuid.UUID
) -> list[IncidentTimeline]:
    return list(
        (
            await session.execute(
                select(IncidentTimeline)
                .where(IncidentTimeline.incident_id == incident_id)
                .order_by(IncidentTimeline.created_at, IncidentTimeline.id)
            )
        ).scalars()
    )


def as_dict(incident: Incident) -> dict:
    return {
        "id": str(incident.id),
        "reference": incident.reference,
        "tenant_id": str(incident.tenant_id) if incident.tenant_id else None,
        "title": incident.title,
        "summary": incident.summary,
        "severity": incident.severity,
        "status": incident.status,
        "category": incident.category,
        "affected_tenants": list(incident.affected_tenants or []),
        "personal_data_involved": incident.personal_data_involved,
        "detected_at": _aware(incident.detected_at).isoformat() if incident.detected_at else "",
        "triaged_at": _aware(incident.triaged_at).isoformat() if incident.triaged_at else "",
        "contained_at": _aware(incident.contained_at).isoformat() if incident.contained_at else "",
        "resolved_at": _aware(incident.resolved_at).isoformat() if incident.resolved_at else "",
        "closed_at": _aware(incident.closed_at).isoformat() if incident.closed_at else "",
    }


__all__ = [
    "SEVERITIES",
    "STATUS_CLOSED",
    "STATUS_CONTAINED",
    "STATUS_DETECTED",
    "STATUS_RESOLVED",
    "STATUS_TRIAGED",
    "TRANSITIONS",
    "IncidentError",
    "IncidentNotFound",
    "InvalidTransition",
    "advance",
    "as_dict",
    "create_incident",
    "get_incident",
    "record_timeline",
    "timeline",
]
