"""Background loops for the compliance control plane.

The API never does this work inline: a SIEM destination that is slow must not
slow a customer's request, and a daily evidence run must happen even on a day
nobody logs in. The loops therefore live here and are started by the scheduler
process (``scripts/scheduler.py``) through :func:`start_loops`, which the Batch
03 apply script adds as one marker-guarded line.

Design rules, shared with the scheduler's own loops:

* every loop owns its session and commits before sleeping;
* a failing cycle logs a stable event name and continues — one broken source
  must not stop the others;
* the stop event is the scheduler's, so a SIGTERM stops these loops at the same
  moment as the rest of the worker;
* each cycle is *idempotent*: the tick functions only move rows that are due, so
  a restart cannot double-send or double-count.

Intervals are deliberately coarse. A SIEM batch every 30 seconds keeps a
security team's dashboards current without hammering a collector; everything
else is measured in minutes or hours because the underlying state changes that
slowly.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
from typing import Any, Awaitable, Callable, Iterable, Sequence

import structlog

from app.compliance_v2.settings import ComplianceV2Settings, get_compliance_v2_settings

log = structlog.get_logger(__name__)

#: Interval names are also the metric/job labels, so they are stable strings.
SIEM_INTERVAL_SECONDS = 30
DSAR_INTERVAL_SECONDS = 300
BREACH_CLOCK_INTERVAL_SECONDS = 300
ACCESS_REVIEW_INTERVAL_SECONDS = 3600
BREAK_GLASS_INTERVAL_SECONDS = 300
SESSION_LOGIN_INTERVAL_SECONDS = 600
EVIDENCE_INTERVAL_SECONDS = 86400

#: The loops this module owns.
LOOP_NAMES = (
    "siem_dispatch",
    "dsar_tick",
    "breach_clock",
    "access_review",
    "break_glass_expiry",
    "session_logoff",
    "evidence_daily",
)


def _session_maker():
    from app.db.session import get_sessionmaker

    return get_sessionmaker()


async def _sleep(stop: asyncio.Event, seconds: float) -> None:
    """Sleep, but wake immediately on stop."""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=max(0.1, float(seconds)))


def _record(name: str, ok: bool) -> None:
    """Best-effort metric; observability must never break a loop."""
    try:
        from app.core import observability

        observability.record_job_run(name, ok=ok)
    except Exception:  # noqa: BLE001 - metrics are not the job
        return


async def siem_dispatch_cycle(settings: ComplianceV2Settings | None = None) -> dict[str, Any]:
    """Deliver one batch to every due sink, across all tenants."""
    from app.compliance_v2.siem.dispatcher import dispatch_tick

    cfg = settings or get_compliance_v2_settings()
    if not cfg.siem_enabled:
        return {"skipped": "siem_disabled"}
    maker = _session_maker()
    async with maker() as session:
        outcome = await dispatch_tick(session, settings=cfg)
        await session.commit()
    return outcome


async def dsar_tick_cycle() -> dict[str, Any]:
    """Flag DSAR requests that are past their statutory due date."""
    from app.compliance_v2.dsar.service import tick

    maker = _session_maker()
    async with maker() as session:
        outcome = await tick(session)
        await session.commit()
    return outcome


async def breach_clock_cycle() -> dict[str, Any]:
    """Send reminders and escalate overdue regulatory clocks."""
    from app.compliance_v2.incidents.breach_clock import tick

    maker = _session_maker()
    async with maker() as session:
        outcome = await tick(session)
        await session.commit()
    return outcome


async def access_review_cycle() -> dict[str, Any]:
    """Apply campaign timeouts (and auto-revoke undecided access)."""
    from app.compliance_v2.soc2.access_review import tick

    maker = _session_maker()
    async with maker() as session:
        outcome = await tick(session)
        await session.commit()
    return outcome


async def break_glass_expiry_cycle() -> dict[str, Any]:
    """Close break-glass grants whose window has elapsed."""
    from app.compliance_v2.hipaa.break_glass import expire_due

    maker = _session_maker()
    async with maker() as session:
        outcome = await expire_due(session)
        await session.commit()
    return outcome


async def session_logoff_cycle() -> dict[str, Any]:
    """Revoke idle/absolute-expired sessions tenant by tenant."""
    from sqlalchemy import select

    from app.compliance_v2.hipaa import phi_tagging, session_policy
    from app.db.models import Tenant

    maker = _session_maker()
    totals = {"tenants": 0, "checked": 0, "idle": 0, "absolute": 0}
    async with maker() as session:
        tenants = list((await session.execute(select(Tenant.id))).scalars())
    for tenant_id in tenants:
        async with maker() as session:
            policy = await phi_tagging.load_policy(session, tenant_id)
            limits = session_policy.limits_for(hipaa_mode=policy.hipaa_mode)
            outcome = await session_policy.sweep(session, tenant_id=tenant_id, limits=limits)
            await session.commit()
        totals["tenants"] += 1
        for key in ("checked", "idle", "absolute"):
            totals[key] += int(outcome.get(key, 0))
    return totals


async def evidence_daily_cycle() -> dict[str, Any]:
    """Run the evidence collectors once a day and persist the snapshot."""
    from app.compliance_v2.soc2.runner import run_evidence

    maker = _session_maker()
    async with maker() as session:
        run = await run_evidence(session, tenant_id=None, created_by=None)
        await session.commit()
    return {"run_id": run.run_id, "counts": run.counts, "ok": run.ok}


@contextlib.asynccontextmanager
async def _guard(name: str, stop: asyncio.Event, interval: float, cycle: Callable[[], Awaitable[Any]]):
    """Run ``cycle`` on an interval until ``stop`` is set."""
    while not stop.is_set():
        started = dt.datetime.now(dt.timezone.utc)
        try:
            outcome = await cycle()
            _record(f"compliance_v2.{name}", ok=True)
            log.info(
                "compliance_v2.tick",
                loop=name,
                outcome=_short(outcome),
                elapsed_ms=int((dt.datetime.now(dt.timezone.utc) - started).total_seconds() * 1000),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the loop must survive one bad cycle
            _record(f"compliance_v2.{name}", ok=False)
            log.error("compliance_v2.tick_failed", loop=name, error=f"{type(exc).__name__}: {exc}"[:300])
        await _sleep(stop, interval)


def _short(outcome: Any) -> str:
    text = repr(outcome)
    return text if len(text) <= 300 else text[:297] + "..."


def loops(stop: asyncio.Event) -> list[tuple[str, Awaitable[None]]]:
    """The coroutines to run, paired with their names."""
    return [
        ("siem_dispatch", _guard("siem_dispatch", stop, SIEM_INTERVAL_SECONDS, siem_dispatch_cycle)),
        ("dsar_tick", _guard("dsar_tick", stop, DSAR_INTERVAL_SECONDS, dsar_tick_cycle)),
        (
            "breach_clock",
            _guard("breach_clock", stop, BREACH_CLOCK_INTERVAL_SECONDS, breach_clock_cycle),
        ),
        (
            "access_review",
            _guard("access_review", stop, ACCESS_REVIEW_INTERVAL_SECONDS, access_review_cycle),
        ),
        (
            "break_glass_expiry",
            _guard("break_glass_expiry", stop, BREAK_GLASS_INTERVAL_SECONDS, break_glass_expiry_cycle),
        ),
        (
            "session_logoff",
            _guard("session_logoff", stop, SESSION_LOGIN_INTERVAL_SECONDS, session_logoff_cycle),
        ),
        (
            "evidence_daily",
            _guard("evidence_daily", stop, EVIDENCE_INTERVAL_SECONDS, evidence_daily_cycle),
        ),
    ]


async def run_all(stop: asyncio.Event) -> None:
    """Await every loop (for a caller that prefers gather over tasks)."""
    await asyncio.gather(*(coroutine for _, coroutine in loops(stop)))


def start_loops(stop: asyncio.Event) -> Sequence[asyncio.Task]:
    """Create one task per loop on the running event loop.

    Returns the tasks so the caller can keep a reference (an unreferenced task
    can be garbage-collected mid-await) and cancel them on shutdown.
    """
    tasks: list[asyncio.Task] = []
    for name, coroutine in loops(stop):
        task = asyncio.create_task(coroutine, name=f"compliance_v2:{name}")
        tasks.append(task)

        def _done(finished: asyncio.Task, loop_name: str = name) -> None:
            if finished.cancelled():
                return
            exc = finished.exception()
            if exc is not None:
                log.error(
                    "compliance_v2.loop_stopped",
                    loop=loop_name,
                    error=f"{type(exc).__name__}: {exc}"[:300],
                )

        task.add_done_callback(_done)
    log.info("compliance_v2.loops_started", loops=[name for name, _ in loops(stop)])
    return tasks


async def stop_loops(tasks: Iterable[asyncio.Task], *, timeout: float = 5.0) -> None:
    """Cancel loops and wait for them to unwind."""
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait(pending, timeout=timeout)


__all__ = [
    "ACCESS_REVIEW_INTERVAL_SECONDS",
    "BREACH_CLOCK_INTERVAL_SECONDS",
    "BREAK_GLASS_INTERVAL_SECONDS",
    "DSAR_INTERVAL_SECONDS",
    "EVIDENCE_INTERVAL_SECONDS",
    "LOOP_NAMES",
    "SESSION_LOGIN_INTERVAL_SECONDS",
    "SIEM_INTERVAL_SECONDS",
    "access_review_cycle",
    "breach_clock_cycle",
    "break_glass_expiry_cycle",
    "dsar_tick_cycle",
    "evidence_daily_cycle",
    "loops",
    "run_all",
    "session_logoff_cycle",
    "siem_dispatch_cycle",
    "start_loops",
    "stop_loops",
]
