"""Every Batch 02-05 maintenance pass is scheduled exactly once, under a lock.

The three v2 packages each ship a ``jobs.py``. At ``e3b64fd`` not one of them was
called by ``scripts/scheduler.py``: SIEM delivery, DSAR clocks, breach reminders,
access-review expiry, break-glass expiry, session logoff, the daily evidence run,
the dialer feed, callback dispatch, occupancy rollup, stale-device reaping,
preview-lease expiry, token refresh, Teams subscription renewal, writeback retry,
webhook replay GC and incremental CRM sync all only ran when a test invoked them.

These tests compare the scheduler's tick table against those modules' own exports,
so a pass that exists and is not scheduled fails here — and they prove the lock is
real rather than decorative by contending for it.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: The contact-center entry points that take a session and are therefore not
#: "tick-shaped" by signature. Each is a distinct recovery path.
CC2_TICKS = (
    "pacing_tick_guard",
    "callback_dispatch_tick",
    "preview_reservation_reaper",
    "abandon_rollup",
    "occupancy_rollup",
    "stale_device_reaper",
)

#: The compliance loops. The module's own ``LOOP_NAMES`` is the source of truth
#: for the seven labels; the mapping here is name -> cycle export.
COMPLIANCE_CYCLES = (
    "siem_dispatch_cycle",
    "dsar_tick_cycle",
    "breach_clock_cycle",
    "access_review_cycle",
    "break_glass_expiry_cycle",
    "session_logoff_cycle",
    "evidence_daily_cycle",
)


def _scheduler():
    import scripts.scheduler as scheduler

    return scheduler


def test_the_tick_table_is_the_caller_of_every_cc2_pass():
    scheduler = _scheduler()
    targets = [tick.target for tick in scheduler.scheduled_v2_ticks()]
    for name in CC2_TICKS:
        assert targets.count(name) == 1, f"{name} registered {targets.count(name)} times"
    from app.contact_center_v2 import jobs as cc2_jobs

    for name in CC2_TICKS:
        assert callable(getattr(cc2_jobs, name)), name


def test_the_tick_table_is_the_caller_of_every_compliance_cycle():
    scheduler = _scheduler()
    targets = [tick.target for tick in scheduler.scheduled_v2_ticks()]
    from app.compliance_v2 import jobs as compliance_jobs

    assert set(compliance_jobs.LOOP_NAMES) == {
        "siem_dispatch",
        "dsar_tick",
        "breach_clock",
        "access_review",
        "break_glass_expiry",
        "session_logoff",
        "evidence_daily",
    }
    for name in COMPLIANCE_CYCLES:
        assert targets.count(name) == 1, f"{name} registered {targets.count(name)} times"
        assert inspect.iscoroutinefunction(getattr(compliance_jobs, name)), name


def test_the_tick_table_is_the_caller_of_the_integrations_cycle():
    scheduler = _scheduler()
    targets = [tick.target for tick in scheduler.scheduled_v2_ticks()]
    assert targets.count("run_scheduled_integrations_v2_cycle") == 1
    from app.integrations_v2 import jobs as int2_jobs

    assert inspect.iscoroutinefunction(int2_jobs.run_scheduled_integrations_v2_cycle)


def test_platform_v2_tick_table_covers_every_cycle_and_shares_cli_locks(monkeypatch):
    from app.platform_v2.jobs import (
        CYCLE_INTERVAL_SECONDS,
        CYCLE_LOCK_TTL_SECONDS,
        CYCLES,
        LOOP_NAMES,
    )
    from app.platform_v2.settings import get_plat2_settings

    monkeypatch.setattr(get_plat2_settings(), "scheduler_enabled", True)
    ticks = _scheduler().scheduled_v2_ticks()
    platform_ticks = {tick.target: tick for tick in ticks if tick.name.startswith("platform_v2.")}

    assert tuple(LOOP_NAMES) == tuple(CYCLE_INTERVAL_SECONDS)
    assert set(CYCLES) == set(LOOP_NAMES)
    assert set(platform_ticks) == set(LOOP_NAMES)
    for cycle in LOOP_NAMES:
        tick = platform_ticks[cycle]
        assert tick.interval_seconds == CYCLE_INTERVAL_SECONDS[cycle]
        assert tick.lock == f"platform_v2:{cycle}"
        assert tick.lock_ttl_seconds == CYCLE_LOCK_TTL_SECONDS
        assert inspect.iscoroutinefunction(CYCLES[cycle])


def test_no_tick_is_registered_twice():
    ticks = _scheduler().scheduled_v2_ticks()
    names = [tick.name for tick in ticks]
    locks = [tick.lock for tick in ticks]
    assert len(names) == len(set(names)), names
    assert len(locks) == len(set(locks)), locks
    assert all(tick.lock.strip() for tick in ticks), [tick.name for tick in ticks if not tick.lock]


def test_every_tick_has_a_positive_interval_and_a_callable():
    for tick in _scheduler().scheduled_v2_ticks():
        assert tick.interval_seconds > 0, tick.name
        assert callable(tick.run), tick.name


def test_the_lock_helper_actually_excludes_a_second_holder():
    """The lock is not decorative: a competing pass must be refused."""
    from app.core.tick_lock import tick_lock

    async def scenario() -> tuple[bool, bool]:
        async with tick_lock("test:exclusive") as first:
            async with tick_lock("test:exclusive") as second:
                return first, second

    first, second = asyncio.run(scenario())
    assert first is True
    assert second is False


def test_a_tick_body_is_skipped_when_another_worker_holds_the_lock():
    from app.core.tick_lock import tick_lock
    import scripts.scheduler as scheduler

    calls: list[str] = []

    async def body() -> str:
        calls.append("ran")
        return "ran"

    tick = scheduler.ScheduledTick(
        name="unit-test-tick",
        target="unit_test",
        interval_seconds=1.0,
        lock="unit-test-tick",
        run=body,
    )

    async def scenario():
        async with tick_lock(tick.lock):
            return await tick.execute()

    assert asyncio.run(scenario()) is None
    assert calls == []
    assert asyncio.run(tick.execute()) == "ran"
    assert calls == ["ran"]


def test_the_scheduler_runs_the_v2_loop_from_main():
    source = (ROOT / "scripts" / "scheduler.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    main = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
    )
    gathered = {
        node.func.id
        for node in ast.walk(main)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "v2_ticks_loop"
    }
    assert gathered, "main() never starts v2_ticks_loop"


def test_each_v2_tick_is_disabled_by_its_own_settings_flag(monkeypatch):
    """A drained node must be able to switch a whole package's passes off."""
    from app.compliance_v2.settings import get_compliance_v2_settings
    from app.contact_center_v2.settings import get_cc2_settings
    from app.integrations_v2.settings import get_int2_settings
    from app.platform_v2.settings import get_plat2_settings

    cc2 = get_cc2_settings()
    compliance = get_compliance_v2_settings()
    int2 = get_int2_settings()
    plat2 = get_plat2_settings()

    monkeypatch.setattr(cc2, "maintenance_enabled", False, raising=False)
    monkeypatch.setattr(compliance, "scheduler_enabled", False, raising=False)
    monkeypatch.setattr(int2, "scheduler_enabled", False, raising=False)
    monkeypatch.setattr(plat2, "scheduler_enabled", False, raising=False)

    assert _scheduler().scheduled_v2_ticks() == []


@pytest.mark.parametrize(
    "module_name",
    [
        "app.contact_center_v2.jobs",
        "app.compliance_v2.jobs",
        "app.platform_v2.jobs",
    ],
    ids=["contact-center-v2", "compliance-v2", "platform-v2"],
)
def test_the_v2_tick_modules_do_not_register_duplicate_job_handlers(module_name: str):
    """Two handlers for one job type means the wrong code runs, silently."""
    import importlib

    from app.jobs.types import JobType, handler_for

    importlib.import_module(module_name)
    source = (ROOT / Path(*module_name.split(".")).with_suffix(".py")).read_text(encoding="utf-8")
    assigned = [
        line.split("register_handler_function(")[1].split(",")[0].strip()
        for line in source.splitlines()
        if line.startswith("register_handler_function(")
    ]
    assert len(assigned) == len(set(assigned)), f"{module_name} registers a job type twice"
    for reference in assigned:
        assert reference.startswith("JobType."), reference
        job_type = getattr(JobType, reference.split(".", 1)[1])
        assert handler_for(job_type) is not None, f"{job_type} has no handler"
