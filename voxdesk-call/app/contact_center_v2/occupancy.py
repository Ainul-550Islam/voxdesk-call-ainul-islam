"""Agent reservations and interval-accurate occupancy accounting."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center.agent_state import transition
from app.contact_center.exceptions import AcdError, StaleState
from app.contact_center.models import AgentPresence
from app.contact_center.presence import STALE_SECONDS
from app.contact_center.repository import presence_for, reserve_agent as reserve_presence
from app.contact_center_v2.errors import ContactCenterV2Error, NotFound
from app.contact_center_v2.repository import as_utc, utcnow
from app.db.contact_center_v2_models import AgentReservation, AgentStatePeriod, SoftphoneSession
from app.db.models import User


async def active_softphone_session(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> SoftphoneSession | None:
    moment = now or utcnow()
    return (
        await session.execute(
            select(SoftphoneSession)
            .where(
                SoftphoneSession.tenant_id == tenant_id,
                SoftphoneSession.user_id == user_id,
                SoftphoneSession.status == "active",
                SoftphoneSession.expires_at > moment,
            )
            .order_by(SoftphoneSession.last_heartbeat_at.desc().nullslast(), SoftphoneSession.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def available_agent_ids(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    now: datetime | None = None,
    require_softphone: bool = True,
    environment_id: uuid.UUID | None = None,
) -> list[uuid.UUID]:
    moment = now or utcnow()
    cutoff = moment - timedelta(seconds=STALE_SECONDS)
    rows = (
        await session.execute(
            select(AgentPresence)
            .join(User, User.id == AgentPresence.user_id)
            .where(
                AgentPresence.tenant_id == tenant_id,
                User.tenant_id == tenant_id,
                User.is_active.is_(True),
                AgentPresence.state == "available",
                AgentPresence.active_count < AgentPresence.capacity,
                AgentPresence.last_seen.is_not(None),
                AgentPresence.last_seen >= cutoff,
            )
            .order_by(AgentPresence.last_assigned_at.asc().nullsfirst(), AgentPresence.user_id.asc())
        )
    ).scalars().all()
    result = []
    for row in rows:
        if require_softphone:
            registration = await active_softphone_session(session, tenant_id, row.user_id, now=moment)
            if registration is None or (
                environment_id is not None and registration.environment_id != environment_id
            ):
                continue
        result.append(row.user_id)
    return result


async def reserve_agent(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str,
    resource_id: uuid.UUID,
    now: datetime | None = None,
    require_softphone: bool = True,
    expires_at: datetime | None = None,
) -> AgentReservation:
    if kind not in {"dialer", "softphone", "callback"}:
        raise ContactCenterV2Error("invalid_reservation", "Unknown reservation kind", status_code=422)
    moment = now or utcnow()
    user = await session.get(User, user_id)
    if user is None or user.tenant_id != tenant_id or not user.is_active:
        raise NotFound("agent")
    if require_softphone:
        registration = await active_softphone_session(session, tenant_id, user_id, now=moment)
        if registration is None or registration.environment_id != environment_id:
            raise ContactCenterV2Error(
                "softphone_required",
                "The selected agent has no active softphone registration in this environment",
            )
    presence = await presence_for(session, tenant_id, user_id)
    if presence is None or presence.state != "available":
        raise ContactCenterV2Error("agent_unavailable", "The selected agent is not available")
    if presence.last_seen is None or as_utc(presence.last_seen) < moment - timedelta(seconds=STALE_SECONDS):
        raise ContactCenterV2Error("agent_unavailable", "The selected agent presence is stale")
    existing = (
        await session.execute(
            select(AgentReservation).where(
                AgentReservation.tenant_id == tenant_id,
                AgentReservation.kind == kind,
                AgentReservation.resource_id == resource_id,
                AgentReservation.status == "active",
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.user_id == user_id:
            return existing
        raise ContactCenterV2Error("reservation_conflict", "This work already has an agent reservation")
    try:
        async with session.begin_nested():
            await reserve_presence(session, presence, now=moment)
            row = AgentReservation(
                tenant_id=tenant_id,
                environment_id=environment_id,
                user_id=user_id,
                kind=kind,
                resource_id=resource_id,
                status="active",
                active_key=f"{kind}:{resource_id}",
                expires_at=expires_at,
                created_at=moment,
            )
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        raise ContactCenterV2Error("reservation_conflict", "This work already has an agent reservation") from exc
    except AcdError as exc:
        raise ContactCenterV2Error("agent_unavailable", "The selected agent is not available") from exc
    return row


async def release_agent(
    session: AsyncSession,
    reservation: AgentReservation,
    *,
    now: datetime | None = None,
    wrap_up: bool = True,
    expired: bool = False,
) -> bool:
    if reservation.status != "active":
        return False
    moment = now or utcnow()
    changed = await session.execute(
        update(AgentReservation)
        .where(
            AgentReservation.id == reservation.id,
            AgentReservation.tenant_id == reservation.tenant_id,
            AgentReservation.status == "active",
        )
        .values(
            status="expired" if expired else "released",
            active_key=None,
            released_at=moment,
        )
    )
    if changed.rowcount != 1:
        return False
    presence = await presence_for(session, reservation.tenant_id, reservation.user_id)
    if presence is not None and presence.active_count > 0:
        expected = presence.version
        result = await session.execute(
            update(AgentPresence)
            .where(
                AgentPresence.id == presence.id,
                AgentPresence.tenant_id == presence.tenant_id,
                AgentPresence.version == expected,
                AgentPresence.active_count > 0,
            )
            .values(active_count=AgentPresence.active_count - 1, version=expected + 1)
        )
        if result.rowcount != 1:
            raise StaleState("Agent occupancy changed while releasing a reservation")
        await session.refresh(presence)
        if presence.active_count == 0:
            # The canonical state machine deliberately disallows busy ->
            # available. Any busy period therefore ends in wrap-up; a failed
            # launch that never passed ringing may return directly to available.
            target = "wrap_up" if presence.state == "busy" else "available"
            if presence.state in {"ringing", "busy"} and presence.state != target:
                await transition(session, presence, target, now=moment)
    await session.refresh(reservation)
    return True


async def record_transition(
    session: AsyncSession,
    *,
    presence: AgentPresence,
    previous_state: str,
    previous_changed_at: datetime | None,
    changed_at: datetime,
    source_id: str = "presence",
) -> None:
    """Persist a canonical transition when the tenant has a CC2 production scope.

    Legacy ACD fixtures and tenants without a production environment keep their
    existing behavior; CC2 API flows always create that scope before use.
    """
    environment_id = await _environment_for_agent(session, presence.tenant_id)
    if environment_id is None:
        return
    current = (
        await session.execute(
            select(AgentStatePeriod).where(
                AgentStatePeriod.tenant_id == presence.tenant_id,
                AgentStatePeriod.user_id == presence.user_id,
                AgentStatePeriod.ended_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if current is not None:
        current.ended_at = changed_at
        current.open_key = None
    elif previous_changed_at is not None and as_utc(previous_changed_at) < as_utc(changed_at):
        session.add(
            AgentStatePeriod(
                tenant_id=presence.tenant_id,
                environment_id=environment_id,
                user_id=presence.user_id,
                state=previous_state,
                started_at=previous_changed_at,
                ended_at=changed_at,
                open_key=None,
                source_id=source_id,
            )
        )
    session.add(
        AgentStatePeriod(
            tenant_id=presence.tenant_id,
            environment_id=environment_id,
            user_id=presence.user_id,
            state=presence.state,
            started_at=changed_at,
            ended_at=None,
            open_key=f"{presence.tenant_id}:{presence.user_id}",
            source_id=source_id,
        )
    )
    await session.flush()


async def _environment_for_agent(
    session: AsyncSession, tenant_id: uuid.UUID
) -> uuid.UUID | None:
    from app.db.models import Environment

    return (
        await session.execute(
            select(Environment.id).where(
                Environment.tenant_id == tenant_id,
                Environment.kind == "production",
                Environment.status == "active",
            )
        )
    ).scalar_one_or_none()


async def record_work_mode(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_id: uuid.UUID,
    user_id: uuid.UUID,
    mode: str,
    source_id: str,
    now: datetime | None = None,
) -> None:
    if mode not in {"busy", "hold"}:
        raise ContactCenterV2Error("invalid_work_mode", "Work mode must be busy or hold", status_code=422)
    moment = now or utcnow()
    current = (
        await session.execute(
            select(AgentStatePeriod).where(
                AgentStatePeriod.tenant_id == tenant_id,
                AgentStatePeriod.user_id == user_id,
                AgentStatePeriod.ended_at.is_(None),
            )
        )
    ).scalar_one_or_none()
    if current is not None and current.state == mode:
        return
    if current is not None:
        current.ended_at = moment
        current.open_key = None
    session.add(
        AgentStatePeriod(
            tenant_id=tenant_id,
            environment_id=environment_id,
            user_id=user_id,
            state=mode,
            started_at=moment,
            open_key=f"{tenant_id}:{user_id}",
            source_id=source_id[:80],
        )
    )
    await session.flush()


def _overlap_seconds(start: datetime, end: datetime, lower: datetime, upper: datetime) -> float:
    beginning = max(as_utc(start), lower)
    finish = min(as_utc(end), upper)
    return max(0.0, (finish - beginning).total_seconds())


async def occupancy_report(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    start: datetime,
    end: datetime,
    user_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> dict:
    lower, upper = as_utc(start), as_utc(end)
    if lower >= upper or (upper - lower).total_seconds() > 32 * 86400:
        raise ContactCenterV2Error("invalid_reporting_window", "Use a positive window of at most 32 days", status_code=422)
    moment = as_utc(now or utcnow())
    stmt = select(AgentStatePeriod).where(
        AgentStatePeriod.tenant_id == tenant_id,
        AgentStatePeriod.started_at < upper,
        or_(AgentStatePeriod.ended_at.is_(None), AgentStatePeriod.ended_at > lower),
    )
    if user_id is not None:
        stmt = stmt.where(AgentStatePeriod.user_id == user_id)
    rows = (await session.execute(stmt.order_by(AgentStatePeriod.user_id, AgentStatePeriod.started_at))).scalars().all()
    states = ("available", "ringing", "busy", "hold", "wrap_up", "away", "paused", "offline", "disabled")
    by_agent: dict[uuid.UUID, dict[str, float]] = {}
    for row in rows:
        durations = by_agent.setdefault(row.user_id, {state: 0.0 for state in states})
        durations[row.state] = durations.get(row.state, 0.0) + _overlap_seconds(
            row.started_at, row.ended_at or moment, lower, upper
        )
    items = []
    for agent, durations in sorted(by_agent.items(), key=lambda item: str(item[0])):
        productive = sum(durations.get(state, 0.0) for state in ("ringing", "busy", "hold", "wrap_up"))
        staffed = productive + durations.get("available", 0.0)
        occupancy = Decimal(str(productive / staffed)) if staffed > 0 else None
        items.append({
            "user_id": str(agent),
            "seconds": {state: round(durations.get(state, 0.0), 3) for state in states},
            "occupancy": str(occupancy) if occupancy is not None else None,
            "occupancy_percent": str(occupancy * 100) if occupancy is not None else None,
        })
    total = {state: round(sum(item["seconds"][state] for item in items), 3) for state in states}
    productive = sum(total[state] for state in ("ringing", "busy", "hold", "wrap_up"))
    staffed = productive + total["available"]
    rate = Decimal(str(productive / staffed)) if staffed else None
    return {
        "window_start": lower.isoformat(),
        "window_end": upper.isoformat(),
        "agents": items,
        "total_seconds": total,
        "occupancy": str(rate) if rate is not None else None,
        "occupancy_percent": str(rate * 100) if rate is not None else None,
        "method": "clipped_state_intervals_v1",
    }
