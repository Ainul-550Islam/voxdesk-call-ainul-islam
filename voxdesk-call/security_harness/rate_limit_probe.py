"""Rate-limit probes for the endpoints that cost money or guessable secrets.

Two layers are checked, because they fail differently:

* the **deployment middleware** (``app.core.rate_limit.add_rate_limit_middleware``)
  which is off by default and enabled per environment;
* the **identity sinks** — MFA verification, password reset, invitations — which
  are throttled in *every* deployment because each attempt costs a real message
  or narrows a guess space.

The probe drives the limiter through its public interface and asserts the
boundary exactly: ``limit`` requests allowed, the next refused, and a distinct
key unaffected. It also asserts that a refusal is *not* sticky: after the window
elapses the key works again, which is the property a fixed-window
implementation can silently break.

A limiter that is configured but not wired (positional argument bug, middleware
added after routing) is the failure this exists to catch, so the probe reads the
mounted middleware list as well.
"""

from __future__ import annotations

import asyncio
from typing import Any

from security_harness.models import FAIL, PASS, REVIEW, SKIP, HarnessReport

#: Sinks that must be throttled regardless of deployment configuration, each
#: with the limit the product documents for it.
IDENTITY_SINKS: dict[str, int] = {
    "mfa_verify": 10,
    "password_reset": 5,
    "invitation_send": 20,
}


def _in_process_limiter(limit: int, window: float = 60.0) -> Any:
    from app.core.rate_limit import _SlidingWindow

    return _SlidingWindow(), limit, window


def probe_sliding_window(*, limit: int = 3, window: float = 60.0) -> list[tuple[str, str, dict]]:
    """Boundary, per-key isolation and window recovery, on the real backend."""
    backend, allowed_limit, span = _in_process_limiter(limit, window)
    rows: list[tuple[str, str, dict]] = []

    results = [backend.allow("probe:key", allowed_limit, span, now=1000.0) for _ in range(limit)]
    overflow = backend.allow("probe:key", allowed_limit, span, now=1000.0)
    if all(results) and not overflow:
        rows.append(("rate_limit.boundary", PASS, {"limit": limit}))
    else:
        rows.append(
            (
                "rate_limit.boundary",
                FAIL,
                {"allowed": results, "overflow_allowed": overflow, "limit": limit},
            )
        )

    other_key = backend.allow("probe:other", allowed_limit, span, now=1000.0)
    rows.append(
        (
            "rate_limit.key_isolation",
            PASS if other_key else FAIL,
            {"other_key_allowed": other_key},
        )
    )

    recovered = backend.allow("probe:key", allowed_limit, span, now=1000.0 + span + 1)
    rows.append(
        (
            "rate_limit.window_recovery",
            PASS if recovered else FAIL,
            {"recovered": recovered, "window": span},
        )
    )
    return rows


async def probe_identity_sink(action: str = "mfa_verify", limit: int = 3) -> list[tuple[str, str, dict]]:
    """Drive ``allow_identity_action`` and assert it refuses past the budget."""
    from app.core import rate_limit as module

    module.reset_identity_limiter()
    who = f"harness-{action}"
    allowed = [
        await module.allow_identity_action(action=action, who=who, limit=limit, window=60.0)
        for _ in range(limit)
    ]
    refused = await module.allow_identity_action(action=action, who=who, limit=limit, window=60.0)
    distinct = await module.allow_identity_action(
        action=action, who=f"{who}-other", limit=limit, window=60.0
    )
    rows = [
        (
            f"rate_limit.identity_sink.{action}",
            PASS if all(allowed) and not refused else FAIL,
            {"allowed": allowed, "refused": refused, "limit": limit},
        ),
        (
            f"rate_limit.identity_sink_isolation.{action}",
            PASS if distinct else FAIL,
            {"distinct_subject_allowed": distinct},
        ),
    ]
    module.reset_identity_limiter()
    return rows


def middleware_enabled(app: Any) -> bool:
    """True when the deployment rate-limit middleware is mounted.

    ``add_rate_limit_middleware`` installs a plain function middleware, which
    Starlette wraps in ``BaseHTTPMiddleware`` with the function passed as
    ``dispatch``. The wrapper's own name is not stable, so the mangled name of
    the inner function is what identifies it.
    """
    for middleware in getattr(app, "user_middleware", []):
        options = getattr(middleware, "kwargs", {}) or {}
        dispatch = options.get("dispatch")
        name = getattr(dispatch, "__name__", "") if dispatch is not None else ""
        if "_rate_limit_middleware" in name:
            return True
    return False


def run(app: Any = None, *, live_client: Any = None) -> HarnessReport:
    """Run every rate-limit probe that this environment can support."""
    report = HarnessReport(suite="rate_limits")

    for name, probe_status, evidence in probe_sliding_window():
        report.record(name, probe_status, evidence=evidence)

    for sink, limit in IDENTITY_SINKS.items():
        try:
            rows = asyncio.run(probe_identity_sink(sink, limit=3))
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                rows = loop.run_until_complete(probe_identity_sink(sink, limit=3))
            finally:
                loop.close()
        for name, probe_status, evidence in rows:
            report.record(name, probe_status, evidence=evidence)

    if app is None:
        report.record("rate_limit.middleware", SKIP, "no application supplied")
        return report

    if middleware_enabled(app):
        report.record("rate_limit.middleware", PASS, "deployment limiter is mounted")
    else:
        report.record(
            "rate_limit.middleware",
            REVIEW,
            "deployment limiter is not mounted; correct when RATE_LIMIT_ENABLED is false, "
            "a finding on any internet-facing deployment",
        )

    if live_client is None:
        report.record(
            "rate_limit.live_429",
            SKIP,
            "no HTTP client supplied; the 429 response path is exercised in the test suite",
        )
        return report

    from app.core.config import settings

    if not getattr(settings, "rate_limit_enabled", False):
        report.record(
            "rate_limit.live_429",
            SKIP,
            "rate limiting is disabled in this configuration (RATE_LIMIT_ENABLED=false)",
        )
        return report

    budget = max(1, int(getattr(settings, "rate_limit_burst", 60)))
    statuses = []
    for _ in range(budget + 3):
        response = live_client.get("/api/v1/agents")
        statuses.append(response.status_code)
    if 429 in statuses:
        report.record(
            "rate_limit.live_429",
            PASS,
            f"429 returned after {statuses.index(429) + 1} requests",
            evidence={"budget": budget},
        )
    else:
        report.record(
            "rate_limit.live_429",
            FAIL,
            "the limiter is enabled but no 429 was returned within the budget",
            evidence={"budget": budget},
        )
    return report


__all__ = [
    "IDENTITY_SINKS",
    "middleware_enabled",
    "probe_identity_sink",
    "probe_sliding_window",
    "run",
]
