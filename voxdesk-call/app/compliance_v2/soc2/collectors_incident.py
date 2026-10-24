"""Incident and privacy evidence: register statistics, clocks and DSAR timeliness.

The register is the ``compliance_incidents`` / ``compliance_breach_clocks`` /
``compliance_dsar_requests`` data this batch writes. An empty register is not
evidence of good behaviour — it is evidence of *no recorded incidents*, which is
what the payload says. A clock past its deadline, or a DSAR past its due date,
is a finding.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance_v2.soc2.collectors_base import CollectorResult, EvidenceCollector, item
from app.db.compliance_v2_models import BreachClock, DsarRequest, Incident

CONTROL_PRIVACY = "P1.1"
CONTROL_CONSENT = "P3.1"
CONTROL_DSAR = "P4.2"


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _aware(value: dt.datetime | None) -> dt.datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value


class IncidentCollector(EvidenceCollector):
    """Incident register, breach clocks and subject-request timeliness."""

    name = "incident"
    control_ids = (CONTROL_PRIVACY, CONTROL_CONSENT, CONTROL_DSAR)

    async def collect(self, session: AsyncSession) -> "CollectorResult":
        now = _utcnow()

        status_rows = (
            await session.execute(
                select(Incident.status, func.count(Incident.id)).group_by(Incident.status)
            )
        ).all()
        status_counts = {str(status): int(count) for status, count in status_rows}
        total_incidents = sum(status_counts.values())

        ack_rows = (
            await session.execute(
                select(Incident.detected_at, Incident.triaged_at).where(
                    Incident.triaged_at.is_not(None)
                )
            )
        ).all()
        ack_minutes = [
            (_aware(triaged) - _aware(detected)).total_seconds() / 60
            for detected, triaged in ack_rows
            if detected is not None and triaged is not None
        ]
        resolve_rows = (
            await session.execute(
                select(Incident.detected_at, Incident.resolved_at).where(
                    Incident.resolved_at.is_not(None)
                )
            )
        ).all()
        resolve_hours = [
            (_aware(resolved) - _aware(detected)).total_seconds() / 3600
            for detected, resolved in resolve_rows
            if detected is not None and resolved is not None
        ]

        clock_rows = (
            await session.execute(
                select(BreachClock.status, BreachClock.deadline_at, BreachClock.jurisdiction)
            )
        ).all()
        overdue = [
            {
                "jurisdiction": row.jurisdiction,
                "deadline_at": _aware(row.deadline_at).isoformat() if row.deadline_at else "",
                "status": row.status,
            }
            for row in clock_rows
            if row.status in {"open", "overdue"}
            and row.deadline_at is not None
            and _aware(row.deadline_at) < now
        ]

        dsar_rows = (
            await session.execute(
                select(DsarRequest.status, func.count(DsarRequest.id)).group_by(DsarRequest.status)
            )
        ).all()
        dsar_status = {str(status): int(count) for status, count in dsar_rows}
        overdue_dsar = (
            await session.execute(
                select(func.count(DsarRequest.id)).where(
                    DsarRequest.due_at.is_not(None),
                    DsarRequest.due_at < now,
                    DsarRequest.status.notin_(("completed", "rejected")),
                )
            )
        ).scalar_one()

        consent_rows = (
            await session.execute(
                select(func.count(Incident.id)).where(Incident.personal_data_involved.is_(True))
            )
        ).scalar_one()

        items = [
            item(
                self.name,
                CONTROL_PRIVACY,
                "compliance_incidents",
                {
                    "incidents": total_incidents,
                    "status_counts": status_counts,
                    "mean_time_to_acknowledge_minutes": (
                        round(sum(ack_minutes) / len(ack_minutes), 2) if ack_minutes else None
                    ),
                    "mean_time_to_resolve_hours": (
                        round(sum(resolve_hours) / len(resolve_hours), 2) if resolve_hours else None
                    ),
                    "incidents_involving_personal_data": int(consent_rows),
                    "open_overdue_clocks": overdue,
                },
            ),
            item(
                self.name,
                CONTROL_CONSENT,
                "compliance_breach_clocks",
                {
                    "clocks": len(clock_rows),
                    "open": sum(1 for row in clock_rows if row.status == "open"),
                    "notified": sum(1 for row in clock_rows if row.status == "notified"),
                    "overdue": len(overdue),
                },
            ),
            item(
                self.name,
                CONTROL_DSAR,
                "compliance_dsar_requests",
                {
                    "requests": sum(dsar_status.values()),
                    "status_counts": dsar_status,
                    "overdue": int(overdue_dsar),
                },
            ),
        ]

        findings: list[str] = []
        if overdue:
            findings.append(f"{len(overdue)} breach notification deadline(s) have passed unnotified")
        if int(overdue_dsar):
            findings.append(f"{int(overdue_dsar)} subject request(s) are past their due date")

        return self._result(configured=True, items=items, findings=findings)


__all__ = ["IncidentCollector"]
