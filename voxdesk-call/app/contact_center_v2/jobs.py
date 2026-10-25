"""Durable-job handlers and bounded, idempotent CC2 maintenance entrypoints.

The canonical :mod:`app.jobs.worker` remains the only durable-job executor.
These maintenance functions recover CC2 state and admit work to that queue;
they never create a second queue or call a carrier from a scheduler loop.
"""
from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.contact_center_v2.repository import utcnow
from app.contact_center_v2.settings import settings
from app.db.contact_center_v2_models import (
    AgentDevice,
    DialerAttempt,
    DialerPolicy,
    SoftphoneSession,
)
from app.db.session import get_sessionmaker
from app.jobs.models import PermanentJobError, RetryableJobError
from app.jobs.types import JobType, register_handler_function


def _uuid(payload: dict, key: str) -> uuid.UUID:
    try:
        return uuid.UUID(str((payload or {}).get(key) or ""))
    except (ValueError, TypeError, AttributeError) as exc:
        raise PermanentJobError("invalid_payload", f"{key} must be a UUID") from exc


_LOCAL_LOCKS: dict[str, asyncio.Lock] = {}
_REDIS_CLIENT: Any = None
_REDIS_URL = ""
_REDIS_LOOP: Any = None
_RELEASE_LOCK = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def _redis_for(client: Any = None) -> Any:
    """Return the configured Redis client, or None in explicit local mode."""
    global _REDIS_CLIENT, _REDIS_URL, _REDIS_LOOP
    if client is not None:
        return client
    from app.core.config import settings as core_settings

    if not core_settings.redis_url:
        return None
    loop = asyncio.get_running_loop()
    if (
        _REDIS_CLIENT is None
        or _REDIS_URL != core_settings.redis_url
        or _REDIS_LOOP is not loop
    ):
        import redis.asyncio as redis

        _REDIS_CLIENT = redis.from_url(core_settings.redis_url)
        _REDIS_URL = core_settings.redis_url
        _REDIS_LOOP = loop
    return _REDIS_CLIENT


@asynccontextmanager
async def _maintenance_guard(name: str, client: Any):
    """Prevent overlapping maintenance passes without hiding lock failures."""
    if client is None:
        lock = _LOCAL_LOCKS.setdefault(name, asyncio.Lock())
        if lock.locked():
            yield False
            return
        async with lock:
            yield True
        return
    key = f"cc2:maintenance:{name}"
    token = uuid.uuid4().hex
    acquired = await client.set(key, token, nx=True, px=120_000)
    if not acquired:
        yield False
        return
    try:
        yield True
    finally:
        await client.eval(_RELEASE_LOCK, 1, key, token)


async def _guarded(
    name: str,
    redis_client: Any,
    operation: Callable[[Any], Awaitable[int]],
) -> int:
    """Return zero when another worker owns the pass; never run unguarded."""
    client = _redis_for(redis_client)
    async with _maintenance_guard(name, client) as acquired:
        if not acquired:
            return 0
        return await operation(client)


async def pacing_tick_guard(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    redis_client: Any = None,
) -> int:
    """Admit due durable dialer ticks for running campaigns, once per bucket."""
    async def operation(_client: Any) -> int:
        from app.contact_center_v2.worker import run_dialer_feed_once

        return await run_dialer_feed_once(session, now=now)

    return await _guarded("pacing_tick", redis_client, operation)


async def abandon_rollup(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    redis_client: Any = None,
) -> int:
    """Reconcile durable attempt transitions and verify the Redis projection."""
    async def operation(client: Any) -> int:
        from app.contact_center_v2.dialer.abandon_tracker import AbandonTracker

        moment = now or utcnow()
        policies = (
            await session.execute(
                select(DialerPolicy)
                .order_by(DialerPolicy.updated_at, DialerPolicy.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        created = 0
        for policy in policies:
            tracker = AbandonTracker(session, redis_client=client)
            attempts = (
                await session.execute(
                    select(DialerAttempt)
                    .where(
                        DialerAttempt.tenant_id == policy.tenant_id,
                        DialerAttempt.policy_id == policy.id,
                    )
                    .order_by(DialerAttempt.updated_at, DialerAttempt.id)
                    .limit(1000)
                )
            ).scalars().all()
            for attempt in attempts:
                created += await tracker.reconcile_attempt(attempt)
            counts = await tracker.rolling_counts(
                tenant_id=policy.tenant_id,
                campaign_id=policy.campaign_id,
                now=moment,
            )
            if not counts["consistent"]:
                raise RuntimeError("Redis and Postgres abandonment ledgers are inconsistent")
        return created

    return await _guarded("abandon_rollup", redis_client, operation)


async def callback_dispatch_tick(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    redis_client: Any = None,
) -> int:
    """Recover scheduled callbacks whose durable dispatch row is absent."""
    async def operation(_client: Any) -> int:
        from app.contact_center_v2.worker import run_callback_feed_once

        return await run_callback_feed_once(session, now=now)

    return await _guarded("callback_dispatch", redis_client, operation)


async def occupancy_rollup(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    redis_client: Any = None,
) -> int:
    """Close contradictory open intervals before occupancy is read."""
    async def operation(_client: Any) -> int:
        from app.contact_center_v2.worker import run_agent_state_rollup_once

        return await run_agent_state_rollup_once(session, now=now)

    return await _guarded("occupancy_rollup", redis_client, operation)


async def stale_device_reaper(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    redis_client: Any = None,
) -> int:
    """Disable devices whose heartbeat expired and expire their registrations."""
    async def operation(_client: Any) -> int:
        moment = now or utcnow()
        cutoff = moment - timedelta(seconds=settings.softphone_session_ttl_seconds)
        rows = (
            await session.execute(
                select(AgentDevice)
                .where(
                    AgentDevice.status == "active",
                    or_(
                        and_(
                            AgentDevice.last_heartbeat_at.is_(None),
                            AgentDevice.created_at < cutoff,
                        ),
                        AgentDevice.last_heartbeat_at < cutoff,
                    ),
                )
                .order_by(AgentDevice.created_at, AgentDevice.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        disabled = 0
        for device in rows:
            live_session = (
                await session.execute(
                    select(SoftphoneSession.id)
                    .where(
                        SoftphoneSession.tenant_id == device.tenant_id,
                        SoftphoneSession.device_id == device.id,
                        SoftphoneSession.status == "active",
                        SoftphoneSession.last_heartbeat_at >= cutoff,
                    )
                    .limit(1)
                )
            ).first()
            if live_session is not None:
                continue
            device.status = "disabled"
            device.revoked_at = moment
            await session.execute(
                update(SoftphoneSession)
                .where(
                    SoftphoneSession.tenant_id == device.tenant_id,
                    SoftphoneSession.device_id == device.id,
                    SoftphoneSession.status == "active",
                )
                .values(status="expired", active_device_key=None)
            )
            disabled += 1
        if disabled:
            await session.flush()
        return disabled

    return await _guarded("stale_device_reaper", redis_client, operation)


async def preview_reservation_reaper(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    redis_client: Any = None,
) -> int:
    """Release expired preview leases and return contacts to the eligible pool."""
    async def operation(_client: Any) -> int:
        from app.contact_center_v2.dialer.dialer_service import expire_preview_reservations

        return await expire_preview_reservations(session, now=now)

    return await _guarded("preview_reservation_reaper", redis_client, operation)


def make_dialer_handler(session_factory=None):
    async def handle(job) -> None:
        from app.contact_center_v2.dialer.dial_worker import execute_dialer_tick
        from app.contact_center_v2.errors import ContactCenterV2Error
        from app.db.contact_center_v2_models import DialerPolicy

        policy_id = _uuid(job.payload, "policy_id")
        maker = session_factory or get_sessionmaker()
        async with maker() as session:
            policy = await session.get(DialerPolicy, policy_id)
            if policy is None or policy.tenant_id != job.tenant_id:
                raise PermanentJobError("policy_not_found")
            if job.environment_id is not None and policy.environment_id != job.environment_id:
                raise PermanentJobError("environment_mismatch")
            try:
                await execute_dialer_tick(
                    session,
                    tenant_id=job.tenant_id,
                    policy_id=policy.id,
                    environment_id=job.environment_id,
                )
            except ContactCenterV2Error:
                # Compliance/configuration changes stop automatic dialing. They
                # are not transient worker errors and never trigger a retry storm.
                policy.status = "paused"
                await session.commit()
                return
            await session.commit()

    return handle


def make_assist_handler(session_factory=None, *, executor=None):
    async def handle(job) -> None:
        from app.contact_center_v2.assist import process_turn, publish_suggestion_notices
        from app.db.contact_center_v2_models import AssistTurn

        turn_id = _uuid(job.payload, "turn_id")
        maker = session_factory or get_sessionmaker()
        async with maker() as session:
            turn = await session.get(AssistTurn, turn_id)
            if turn is None or turn.tenant_id != job.tenant_id:
                raise PermanentJobError("assist_turn_not_found")
            if job.environment_id is not None and turn.environment_id != job.environment_id:
                raise PermanentJobError("environment_mismatch")
            try:
                rows = await process_turn(
                    session, tenant_id=job.tenant_id, turn_id=turn.id, executor=executor
                )
            except RetryableJobError:
                # Persist the bounded failure category while the durable queue
                # owns retry/DLQ transitions.
                await session.commit()
                raise
            await session.commit()
            await publish_suggestion_notices(rows, tenant_id=job.tenant_id, call_id=turn.call_id)

    return handle


def make_callback_handler(session_factory=None, *, adapter=None):
    async def handle(job) -> None:
        from app.contact_center_v2.acd.callbacks import ensure_legacy_callback, execute_callback
        from app.db.contact_center_v2_models import CallbackRequest

        maker = session_factory or get_sessionmaker()
        async with maker() as session:
            if (job.payload or {}).get("callback_id"):
                callback_id = _uuid(job.payload, "callback_id")
                callback = await session.get(CallbackRequest, callback_id)
            elif (job.payload or {}).get("entry_id"):
                callback = await ensure_legacy_callback(
                    session,
                    tenant_id=job.tenant_id,
                    entry_id=_uuid(job.payload, "entry_id"),
                    job_id=job.id,
                )
            else:
                raise PermanentJobError("invalid_payload", "callback_id or entry_id is required")
            if callback is None or callback.tenant_id != job.tenant_id:
                raise PermanentJobError("callback_not_found")
            if job.environment_id is not None and callback.environment_id != job.environment_id:
                raise PermanentJobError("environment_mismatch")
            await execute_callback(
                session, tenant_id=job.tenant_id, callback_id=callback.id, adapter=adapter
            )
            await session.commit()

    return handle


handle_dialer_start = make_dialer_handler()
handle_dialer_tick = make_dialer_handler()
handle_assist_turn = make_assist_handler()
handle_callback = make_callback_handler()

register_handler_function(JobType.CC2_DIALER_START, handle_dialer_start)
register_handler_function(JobType.CC2_DIALER_TICK, handle_dialer_tick)
register_handler_function(JobType.CC2_ASSIST_TURN, handle_assist_turn)
register_handler_function(JobType.ACD_CALLBACK, handle_callback)

__all__ = [
    "abandon_rollup",
    "callback_dispatch_tick",
    "handle_assist_turn",
    "handle_callback",
    "handle_dialer_start",
    "handle_dialer_tick",
    "make_assist_handler",
    "make_callback_handler",
    "make_dialer_handler",
    "occupancy_rollup",
    "pacing_tick_guard",
    "preview_reservation_reaper",
    "stale_device_reaper",
]
