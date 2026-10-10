"""Graceful shutdown and drain coordinator for API and worker processes (Part 8 / Gate G9).

Drain lifecycle:
1. ``begin_drain()`` enters drain mode immediately upon SIGTERM/SIGINT or
   application lifespan teardown:
   - ``/health/ready`` returns HTTP 503 (``checks.draining = True``) so
     Kubernetes / load balancers stop routing new calls to this pod.
   - New call admission endpoints (``/telephony/voice``, ``/telephony/outbound-answer``,
     ``/telephony/ws``, ``/api/outbound/calls``, ``/api/web-calls``) reject new
     calls with HTTP 503 / WebSocket 1012 while allowing in-flight status
     callbacks (``/telephony/status``, ``/telephony/transfer-status``) to
     complete.
2. ``wait_for_active_calls()`` waits up to ``drain_timeout_seconds`` (bounded
   to fit within Kubernetes ``terminationGracePeriodSeconds``) for active media
   streams (tracked via both registered call SIDs and ``voxdesk_active_calls``)
   to finish naturally.
3. ``flush_outbox_and_jobs()`` performs a bounded final admission and execution
   pass over due ``OutboxEvent`` and ``DurableJob`` rows so events emitted by
   the final calls are not left waiting until another worker's poll tick.
"""
from __future__ import annotations

import asyncio
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core import metrics
from app.core.logging import log

DEFAULT_DRAIN_TIMEOUT_SECONDS = 25.0
DEFAULT_FLUSH_TIMEOUT_SECONDS = 10.0

_NEW_CALL_TWIML_PATHS = frozenset({
    "/telephony/voice",
    "/telephony/outbound-answer",
})

_NEW_CALL_API_PREFIXES = (
    "/api/outbound/calls",
    "/api/v1/calls",
    "/api/web-calls",
    "/api/v1/web-calls",
    "/api/phone-calls",
)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


class DrainCoordinator:
    """Process-local drain coordinator for graceful pod termination."""

    def __init__(self) -> None:
        self._draining: bool = False
        self._drain_reason: str = ""
        self._drain_started_at: float | None = None
        self._active_calls: set[str] = set()

    @property
    def draining(self) -> bool:
        return self._draining

    @property
    def drain_reason(self) -> str:
        return self._drain_reason

    @property
    def drain_started_at(self) -> float | None:
        return self._drain_started_at

    def begin_drain(self, reason: str = "sigterm") -> None:
        if not self._draining:
            self._draining = True
            self._drain_reason = str(reason or "sigterm")
            self._drain_started_at = time.monotonic()
            log.info(
                "shutdown.drain_started",
                reason=self._drain_reason,
                active_calls=self.active_call_count(),
            )

    def reset(self) -> None:
        self._draining = False
        self._drain_reason = ""
        self._drain_started_at = None
        self._active_calls.clear()

    def register_call(self, call_sid: str) -> None:
        if call_sid:
            self._active_calls.add(str(call_sid))

    def unregister_call(self, call_sid: str) -> None:
        if call_sid:
            self._active_calls.discard(str(call_sid))

    def active_call_sids(self) -> tuple[str, ...]:
        return tuple(sorted(self._active_calls))

    def active_call_count(self) -> int:
        gauge_val = 0
        try:
            gauge_val = max(0, int(metrics.ACTIVE_CALLS._value.get()))
        except Exception:
            __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
            gauge_val = 0
        return max(len(self._active_calls), gauge_val)

    @contextmanager
    def track_call(self, call_sid: str) -> Iterator[None]:
        self.register_call(call_sid)
        try:
            yield
        finally:
            self.unregister_call(call_sid)

    async def wait_for_active_calls(
        self,
        *,
        timeout_seconds: float | None = None,
        poll_interval: float = 0.05,
    ) -> dict[str, Any]:
        bound = (
            _env_float("SHUTDOWN_DRAIN_TIMEOUT_SECONDS", DEFAULT_DRAIN_TIMEOUT_SECONDS)
            if timeout_seconds is None
            else max(0.0, float(timeout_seconds))
        )
        started = time.monotonic()
        initial_calls = self.active_call_count()
        deadline = started + bound
        step = max(0.01, min(float(poll_interval), 1.0))

        while True:
            remaining = self.active_call_count()
            if remaining <= 0:
                elapsed = round(time.monotonic() - started, 4)
                return {
                    "drained": True,
                    "initial_calls": initial_calls,
                    "remaining_calls": 0,
                    "elapsed_seconds": elapsed,
                    "timeout_seconds": bound,
                }
            now = time.monotonic()
            if now >= deadline:
                elapsed = round(now - started, 4)
                log.warning(
                    "shutdown.drain_timeout_exceeded",
                    initial_calls=initial_calls,
                    remaining_calls=remaining,
                    elapsed_seconds=elapsed,
                    timeout_seconds=bound,
                    active_call_sids=list(self.active_call_sids()),
                )
                return {
                    "drained": False,
                    "initial_calls": initial_calls,
                    "remaining_calls": remaining,
                    "elapsed_seconds": elapsed,
                    "timeout_seconds": bound,
                }
            await asyncio.sleep(min(step, max(0.005, deadline - now)))

    async def flush_outbox_and_jobs(
        self,
        session_factory: Any = None,
        *,
        max_rounds: int = 10,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        bound = (
            _env_float("SHUTDOWN_FLUSH_TIMEOUT_SECONDS", DEFAULT_FLUSH_TIMEOUT_SECONDS)
            if timeout_seconds is None
            else max(0.0, float(timeout_seconds))
        )
        started = time.monotonic()
        outbox_scheduled = 0
        outbox_exhausted = 0
        jobs_executed = 0
        error_type: str | None = None

        async def _do_flush() -> None:
            nonlocal outbox_scheduled, outbox_exhausted, jobs_executed
            from app.db.session import get_sessionmaker
            from app.jobs.worker import JobWorker
            from app.outbox.dispatcher import dispatch_due

            maker = session_factory or get_sessionmaker()
            async with maker() as session:
                dispatch_res = await dispatch_due(session)
                await session.commit()
                outbox_scheduled += int(dispatch_res.get("scheduled") or 0)
                outbox_exhausted += int(dispatch_res.get("exhausted") or 0)

            worker = JobWorker(maker)
            for _ in range(max(1, int(max_rounds))):
                job = await worker.run_once()
                if job is None:
                    break
                jobs_executed += 1

        try:
            if bound > 0:
                await asyncio.wait_for(_do_flush(), timeout=bound)
            else:
                await _do_flush()
        except Exception as exc:
            error_type = type(exc).__name__
            log.warning(
                "shutdown.flush_incomplete",
                error_type=error_type,
                outbox_scheduled=outbox_scheduled,
                jobs_executed=jobs_executed,
            )

        elapsed = round(time.monotonic() - started, 4)
        return {
            "flushed": error_type is None,
            "outbox_scheduled": outbox_scheduled,
            "outbox_exhausted": outbox_exhausted,
            "jobs_executed": jobs_executed,
            "elapsed_seconds": elapsed,
            "error_type": error_type,
        }

    async def drain_and_shutdown(
        self,
        *,
        reason: str = "sigterm",
        drain_timeout_seconds: float | None = None,
        flush_timeout_seconds: float | None = None,
        flush_outbox: bool = True,
        session_factory: Any = None,
        max_flush_rounds: int = 10,
    ) -> dict[str, Any]:
        self.begin_drain(reason=reason)
        drain_result = await self.wait_for_active_calls(
            timeout_seconds=drain_timeout_seconds
        )
        if flush_outbox:
            flush_result = await self.flush_outbox_and_jobs(
                session_factory=session_factory,
                max_rounds=max_flush_rounds,
                timeout_seconds=flush_timeout_seconds,
            )
        else:
            flush_result = {
                "flushed": True,
                "outbox_scheduled": 0,
                "outbox_exhausted": 0,
                "jobs_executed": 0,
                "elapsed_seconds": 0.0,
                "error_type": None,
            }

        summary = {
            "draining": self._draining,
            "reason": self._drain_reason,
            "calls": drain_result,
            "outbox_jobs": flush_result,
        }
        log.info("shutdown.drain_complete", **summary)
        return summary


_coordinator = DrainCoordinator()


def get_drain_coordinator() -> DrainCoordinator:
    return _coordinator


def is_draining() -> bool:
    return _coordinator.draining


def begin_drain(reason: str = "sigterm") -> None:
    _coordinator.begin_drain(reason=reason)


def reset_drain_state() -> None:
    _coordinator.reset()


def register_call(call_sid: str) -> None:
    _coordinator.register_call(call_sid)


def unregister_call(call_sid: str) -> None:
    _coordinator.unregister_call(call_sid)


def active_call_count() -> int:
    return _coordinator.active_call_count()


def active_call_sids() -> tuple[str, ...]:
    return _coordinator.active_call_sids()


@contextmanager
def track_active_call(call_sid: str) -> Iterator[None]:
    with _coordinator.track_call(call_sid):
        yield


async def wait_for_active_calls(
    *,
    timeout_seconds: float | None = None,
    poll_interval: float = 0.05,
) -> dict[str, Any]:
    return await _coordinator.wait_for_active_calls(
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
    )


async def flush_outbox_and_jobs(
    session_factory: Any = None,
    *,
    max_rounds: int = 10,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    return await _coordinator.flush_outbox_and_jobs(
        session_factory=session_factory,
        max_rounds=max_rounds,
        timeout_seconds=timeout_seconds,
    )


async def drain_and_shutdown(
    *,
    reason: str = "sigterm",
    drain_timeout_seconds: float | None = None,
    flush_timeout_seconds: float | None = None,
    flush_outbox: bool = True,
    session_factory: Any = None,
    max_flush_rounds: int = 10,
) -> dict[str, Any]:
    return await _coordinator.drain_and_shutdown(
        reason=reason,
        drain_timeout_seconds=drain_timeout_seconds,
        flush_timeout_seconds=flush_timeout_seconds,
        flush_outbox=flush_outbox,
        session_factory=session_factory,
        max_flush_rounds=max_flush_rounds,
    )


def _is_new_call_request(method: str, path: str) -> tuple[bool, bool]:
    """Return ``(is_new_call, is_twiml)`` for an incoming HTTP request."""
    if method.upper() != "POST":
        return False, False
    normalized = path.rstrip("/") or "/"
    if normalized in _NEW_CALL_TWIML_PATHS:
        return True, True
    if any(
        normalized == prefix or normalized.startswith(prefix + "/")
        for prefix in _NEW_CALL_API_PREFIXES
    ):
        # Allow ending/controlling an already-active call while draining
        if normalized.endswith(("/end", "/hangup", "/dtmf", "/transfer", "/complete")):
            return False, False
        return True, False
    return False, False


def add_drain_middleware(app: FastAPI) -> None:
    """Reject new call admissions with HTTP 503 when the process is draining."""

    @app.middleware("http")
    async def _drain_middleware(request: Request, call_next):
        if not _coordinator.draining:
            return await call_next(request)

        is_new_call, is_twiml = _is_new_call_request(request.method, request.url.path)
        if not is_new_call:
            return await call_next(request)

        if is_twiml:
            twiml = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                "<Response>"
                "<Say>This node is draining for maintenance. Please try again shortly.</Say>"
                "<Hangup/>"
                "</Response>"
            )
            return PlainTextResponse(
                content=twiml,
                status_code=503,
                media_type="application/xml",
                headers={"Retry-After": "5"},
            )
        return JSONResponse(
            status_code=503,
            content={
                "detail": "node is draining; not accepting new calls",
                "code": "NODE_DRAINING",
            },
            headers={"Retry-After": "5"},
        )
