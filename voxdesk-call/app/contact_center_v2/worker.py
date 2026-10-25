"""Bounded CC2 maintenance entrypoints over the canonical durable scheduler.

Durable-job execution remains in :mod:`app.jobs.worker`; these functions only
recover CC2 rows and run maintenance transitions. Every feed query is bounded
and uses the database's single-claim primitive where rows can be recovered by
more than one worker.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import and_, func, select

from app.contact_center_v2.acd import sweep_overflow
from app.contact_center_v2.assist.wrapup_service import reconcile_crm_writebacks
from app.contact_center_v2.repository import as_utc, utcnow
from app.contact_center_v2.settings import settings
from app.contact_center_v2.softphone import expire_sessions, sweep_agent_join_timeouts
from app.core import observability
from app.db.contact_center_v2_models import (
    AcdQueuePolicy,
    AgentStatePeriod,
    CallbackRequest,
    DialerPolicy,
)
from app.db.session import get_sessionmaker

logger = structlog.get_logger()


async def run_session_maintenance_once(session) -> dict[str, int]:
    """Expire registrations and enforce the human agent-join deadline."""
    return {
        "expired": int(await expire_sessions(session)),
        "timed_out": int(await sweep_agent_join_timeouts(session)),
    }


async def run_wrapup_maintenance_once(session) -> int:
    """Reconcile only provider-acknowledged CRM writebacks."""
    return int(await reconcile_crm_writebacks(session))


async def run_overflow_maintenance_once(session, *, tenant_id) -> dict[str, int]:
    """Run the canonical queue overflow sweep for one tenant."""
    return await sweep_overflow(session, tenant_id=tenant_id)


async def run_callback_feed_once(session, *, now: datetime | None = None) -> int:
    """Re-admit scheduled callbacks whose durable dispatch row is absent.

    Normal callback execution is claimed by ``JobWorker``. This recovery path
    never calls a carrier and never redials a running/ambiguous callback.
    """
    from app.contact_center_v2.acd.callbacks import _enqueue_attempt

    moment = now or utcnow()
    rows = (
        await session.execute(
            select(CallbackRequest)
            .where(
                CallbackRequest.status == "scheduled",
                CallbackRequest.job_id.is_(None),
                func.coalesce(CallbackRequest.next_attempt_at, CallbackRequest.scheduled_at) <= moment,
            )
            .order_by(CallbackRequest.scheduled_at, CallbackRequest.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    for callback in rows:
        available_at = callback.next_attempt_at or callback.scheduled_at or moment
        await _enqueue_attempt(session, callback, available_at=available_at)
    if rows:
        await session.flush()
    return len(rows)


async def run_dialer_feed_once(session, *, now: datetime | None = None) -> int:
    """Recover the next durable tick for every running campaign, idempotently."""
    from app.contact_center_v2.dialer.dialer_service import _schedule_tick

    moment = now or utcnow()
    policies = (
        await session.execute(
            select(DialerPolicy)
            .where(DialerPolicy.status == "running")
            .order_by(DialerPolicy.updated_at, DialerPolicy.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()
    for policy in policies:
        # The campaign/bucket idempotency key makes this safe alongside the
        # ordinary post-tick scheduler and across multiple maintenance workers.
        await _schedule_tick(session, policy, now=moment)
    return len(policies)


async def run_agent_state_rollup_once(session, *, now: datetime | None = None) -> int:
    """Repair open intervals when a presence transition was recovered late.

    Only offline/disabled presence rows with a contradictory open period are
    changed. The last presence observation bounds the interval; no occupancy
    time is invented for active agents.
    """
    from app.contact_center.models import AgentPresence

    moment = now or utcnow()
    rows = (
        await session.execute(
            select(AgentStatePeriod, AgentPresence)
            .join(
                AgentPresence,
                and_(
                    AgentPresence.tenant_id == AgentStatePeriod.tenant_id,
                    AgentPresence.user_id == AgentStatePeriod.user_id,
                ),
            )
            .where(
                AgentStatePeriod.ended_at.is_(None),
                AgentPresence.state.in_(("offline", "disabled")),
                AgentStatePeriod.state != AgentPresence.state,
            )
            .order_by(AgentStatePeriod.started_at, AgentStatePeriod.id)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    ).all()
    repaired = 0
    for period, presence in rows:
        observed = as_utc(presence.last_seen) if presence.last_seen else moment
        ended = min(moment, max(as_utc(period.started_at), observed))
        period.ended_at = ended
        period.open_key = None
        await session.flush()
        session.add(
            AgentStatePeriod(
                tenant_id=period.tenant_id,
                environment_id=period.environment_id,
                user_id=period.user_id,
                state=presence.state,
                started_at=ended,
                ended_at=None,
                open_key=f"{period.tenant_id}:{period.user_id}",
                source_id="scheduler:presence_reconcile",
            )
        )
        repaired += 1
    if repaired:
        await session.flush()
    return repaired


async def run_maintenance_once(*, session_factory: Any = None) -> dict[str, int]:
    """Run one bounded maintenance pass and report real row transitions."""
    maker = session_factory or get_sessionmaker()
    errors = 0
    async with maker() as session:
        from app.contact_center_v2.jobs import (
            abandon_rollup,
            callback_dispatch_tick,
            occupancy_rollup,
            pacing_tick_guard,
            preview_reservation_reaper,
            stale_device_reaper,
        )

        session_result = await run_session_maintenance_once(session)
        expired = session_result["expired"]
        timed_out = session_result["timed_out"]
        crm_reconciled = await run_wrapup_maintenance_once(session)
        abandon_rolled = await abandon_rollup(session)
        devices_reaped = await stale_device_reaper(session)
        preview_reaped = await preview_reservation_reaper(session)
        callback_fed = await callback_dispatch_tick(session)
        dialer_fed = await pacing_tick_guard(session)
        intervals_repaired = await occupancy_rollup(session)
        tenant_ids = (
            await session.execute(select(AcdQueuePolicy.tenant_id).distinct())
        ).scalars().all()
        await session.commit()
    overflowed = 0
    for tenant_id in tenant_ids:
        try:
            async with maker() as session:
                result = await run_overflow_maintenance_once(session, tenant_id=tenant_id)
                await session.commit()
                overflowed += result["overflowed"]
        except Exception as exc:
            errors += 1
            logger.error(
                "scheduler.cc2_tenant_sweep_failed",
                tenant_id=str(tenant_id),
                error_type=type(exc).__name__,
            )
    if any((expired, timed_out, overflowed, crm_reconciled, abandon_rolled,
            devices_reaped, preview_reaped, callback_fed, dialer_fed, intervals_repaired)):
        logger.info(
            "scheduler.cc2_maintenance",
            expired=expired,
            timed_out=timed_out,
            overflowed=overflowed,
            crm_reconciled=crm_reconciled,
            abandon_rolled=abandon_rolled,
            devices_reaped=devices_reaped,
            preview_reaped=preview_reaped,
            callback_fed=callback_fed,
            dialer_fed=dialer_fed,
            intervals_repaired=intervals_repaired,
        )
    # Keep the established result schema consumed by the scheduler and tests.
    return {
        "expired": int(expired),
        "timed_out": int(timed_out),
        "overflowed": int(overflowed),
        "crm_reconciled": int(crm_reconciled),
        "errors": errors,
    }


async def maintenance_loop(
    *,
    stop: asyncio.Event,
    session_factory: Any = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> None:
    """Run maintenance until the existing scheduler's stop event is set."""
    maker = session_factory or get_sessionmaker()
    interval = min(settings.dialer_tick_seconds, settings.acd_overflow_sweep_seconds)
    while not stop.is_set():
        try:
            result = await run_maintenance_once(session_factory=maker)
            observability.record_job_run("contact_center_v2", ok=result["errors"] == 0)
        except Exception as exc:
            observability.record_job_run("contact_center_v2", ok=False)
            logger.error("scheduler.cc2_maintenance_failed", error_type=type(exc).__name__)
        await sleep(interval)


async def _standalone_main() -> None:
    """Run the maintenance loop, or one explicit CronJob pass with --once."""
    import argparse
    import signal

    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="run one maintenance pass and exit")
    args = parser.parse_args()
    if args.once:
        result = await run_maintenance_once()
        if result["errors"]:
            raise SystemExit(1)
        return
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    await maintenance_loop(stop=stop)


if __name__ == "__main__":  # pragma: no cover - process entry point
    asyncio.run(_standalone_main())


__all__ = [
    "maintenance_loop",
    "run_agent_state_rollup_once",
    "run_callback_feed_once",
    "run_dialer_feed_once",
    "run_maintenance_once",
    "run_overflow_maintenance_once",
    "run_session_maintenance_once",
    "run_wrapup_maintenance_once",
]
