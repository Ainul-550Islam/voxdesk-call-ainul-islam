#!/usr/bin/env python3
"""Run one platform-v2 maintenance cycle and exit.

This is the Kubernetes entry point for the nine Batch 07 CronJobs
(``infra/helm/voxdesk/templates/wfm-and-platform-cronjobs.yaml``): each CronJob
runs one cycle, in its own pod, then exits. The long-running scheduler process
(``python -m scripts.scheduler``) runs the same cycles on a timer; having both is
deliberate, because a deployment may run the scheduler or may prefer cron, and the
work itself must not fork into two implementations.

    python3 scripts/platform_jobs.py --list
    python3 scripts/platform_jobs.py wfm_rollup
    python3 scripts/platform_jobs.py wfm_forecast --json /tmp/forecast.json

Exit codes:

* ``0`` — the cycle ran, or another worker held the lock and this pod did nothing;
* ``1`` — the cycle failed (the error is logged; the pod restarts per CronJob policy);
* ``2`` — the arguments were wrong or the cycle name is unknown.

The lock matters even with cron: a suspended-then-resumed CronJob can fire twice,
and a rollup that runs twice must not double-count anything. Each cycle therefore
takes the same Redis/DB tick lock the scheduler uses, with a TTL long enough to
cover the interval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    from app.platform_v2.jobs import CYCLE_LOCK_TTL_SECONDS, LOOP_NAMES

    parser = argparse.ArgumentParser(description="Run one platform-v2 maintenance cycle")
    parser.add_argument("cycle", nargs="?", help=f"one of: {', '.join(LOOP_NAMES)}")
    parser.add_argument("--list", action="store_true", help="print the cycle names and exit")
    parser.add_argument("--json", dest="json_path", help="write the result to this file")
    parser.add_argument(
        "--lock-ttl",
        type=int,
        default=CYCLE_LOCK_TTL_SECONDS,
        help=f"lock TTL in seconds (default {CYCLE_LOCK_TTL_SECONDS}); shared with the scheduler",
    )
    parser.add_argument(
        "--no-lock",
        action="store_true",
        help="skip the tick lock (only for a one-off manual run in a single-replica environment)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)

    from app.platform_v2.jobs import CYCLES, LOOP_NAMES

    if args.list:
        for name in LOOP_NAMES:
            print(name)
        return 0
    if not args.cycle:
        print("a cycle name is required (or --list)", file=sys.stderr)
        return 2
    if args.cycle not in CYCLES:
        print(f"unknown cycle {args.cycle!r}; known: {', '.join(LOOP_NAMES)}", file=sys.stderr)
        return 2
    try:
        return asyncio.run(_run(args.cycle, args.json_path, args.lock_ttl, args.no_lock))
    except KeyboardInterrupt:  # pragma: no cover - interactive use only
        return 1


async def _run(cycle: str, json_path: str | None, lock_ttl: int, no_lock: bool) -> int:
    import structlog

    from app.core.logging import log  # noqa: F401  (configures structlog)
    from app.core.tick_lock import tick_lock
    from app.db.session import get_sessionmaker
    from app.platform_v2.jobs import CYCLES

    logger = structlog.get_logger()
    run_id = uuid.uuid4()
    lock_name = f"platform_v2:{cycle}"
    logger.info(
        "platform_jobs.start",
        cycle=cycle,
        run_id=str(run_id),
        lock=lock_name,
        locked=not no_lock,
    )

    async def run_cycle() -> tuple[int, Any | None]:
        maker = get_sessionmaker()
        async with maker() as session:
            try:
                result: Any = await CYCLES[cycle](session)
                await session.commit()
                return 0, result
            except Exception as exc:  # noqa: BLE001 - the exit code carries the failure
                await session.rollback()
                logger.error(
                    "platform_jobs.failed",
                    cycle=cycle,
                    run_id=str(run_id),
                    error=exc.__class__.__name__,
                    message=str(exc)[:500],
                )
                _emit(
                    {
                        "cycle": cycle,
                        "status": "failed",
                        "run_id": str(run_id),
                        "error": exc.__class__.__name__,
                        "message": str(exc)[:500],
                    },
                    json_path,
                )
                return 1, None

    if no_lock:
        status_code, result = await run_cycle()
    else:
        async with tick_lock(lock_name, ttl_seconds=max(60, lock_ttl)) as acquired:
            if not acquired:
                # Not a failure: another replica owns this cycle. The CronJob exits 0 so
                # Kubernetes does not count a skipped pass as a broken job.
                logger.info("platform_jobs.skipped_lock_held", cycle=cycle, run_id=str(run_id))
                _emit({"cycle": cycle, "status": "skipped_lock_held", "run_id": str(run_id)}, json_path)
                return 0
            status_code, result = await run_cycle()

    if status_code:
        return status_code
    logger.info("platform_jobs.done", cycle=cycle, run_id=str(run_id), result=str(result)[:300])
    _emit({"cycle": cycle, "status": "ok", "run_id": str(run_id), "result": result}, json_path)
    return 0


def _emit(payload: dict[str, Any], json_path: str | None) -> None:
    rendered = json.dumps(payload, default=str, sort_keys=True)
    print(rendered)
    if json_path:
        Path(json_path).write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
