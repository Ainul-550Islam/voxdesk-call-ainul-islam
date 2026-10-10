"""
Locust load test for VoxDesk — safe by default (Step 7 & Part 8 / Gate G9).

Run against a deployed API (local compose or staging):

    pip install locust
    locust -f loadtest/locustfile.py --host http://localhost:8000

Then open http://localhost:8089 and start a run.

Included user classes:
* ``VoxDeskUser`` (`HttpUser`): probes liveness, readiness, metrics, and the
  API mix scenarios (calls list, analytics overview, webhook subscriptions).
* ``VoiceWsUser`` (`User`, imported from ``loadtest/voice_ws_user.py``): drives
  synthetic Twilio Media-Stream WebSocket calls against the real ``/telephony/ws``
  pipeline using recorded caller audio and deterministic provider fakes.

Safety contract (see docs/LOAD-TESTING.md for the full rules):
* Both ``VoxDeskUser`` and ``VoiceWsUser`` call ``validate_target(self.host)``
  from ``loadtest/safety.py`` in ``on_start()``.
* A **remote host is refused** with ``StopUser`` unless the operator sets
  ``LOADTEST_ALLOW_REMOTE=1``. ``localhost`` / ``127.0.0.1`` / ``[::1]`` are
  always allowed.
"""
from __future__ import annotations

import os

from locust import HttpUser, between, task
from locust.exception import StopUser

# The safety guard is a pure module so it can be unit-tested without Locust.
# Locust runs this file with its own directory on sys.path; tests import it
# as the `loadtest` package. Either way the guard is the same code.
try:
    from .safety import validate_target
    from .voice_ws_user import VoiceWsUser
except ImportError:  # pragma: no cover - locust script path, not the package
    from safety import validate_target
    from voice_ws_user import VoiceWsUser

__all__ = ["VoxDeskUser", "VoiceWsUser"]


def _auth_headers() -> dict[str, str]:
    token = os.environ.get("LOADTEST_BEARER_TOKEN", "").strip()
    if token:
        return {"Authorization": f"Bearer {token}"}
    api_key = os.environ.get("LOADTEST_API_KEY", "").strip()
    if api_key:
        return {"X-API-Key": api_key}
    return {}


class VoxDeskUser(HttpUser):
    wait_time = between(0.5, 2.0)

    def on_start(self):
        reason = validate_target(self.host)
        if reason:
            # Hard stop: a mis-aimed host must not become a production test.
            raise StopUser(reason)
        self._headers = _auth_headers()

    @task(3)
    def liveness(self):
        # Process is up and answering.
        self.client.get("/health", name="health")

    @task(2)
    def readiness(self):
        # DB answers. A 503 here is the signal to scale/repair, not a crash.
        self.client.get("/health/ready", name="health/ready")

    @task(2)
    def calls_list(self):
        # API mix scenario 1: paginated call history list (/api/calls).
        # 200 when LOADTEST_BEARER_TOKEN is set; 401/403 when unauthenticated.
        with self.client.get(
            "/api/calls?limit=20",
            headers=self._headers,
            name="api/calls",
            catch_response=True,
        ) as resp:
            if resp.status_code not in (200, 401, 403):
                resp.failure(f"unexpected /api/calls status {resp.status_code}")
            else:
                resp.success()

    @task(2)
    def analytics_overview(self):
        # API mix scenario 2: tenant analytics summary (/api/analytics/overview).
        with self.client.get(
            "/api/analytics/overview",
            headers=self._headers,
            name="api/analytics/overview",
            catch_response=True,
        ) as resp:
            if resp.status_code not in (200, 401, 403, 404):
                resp.failure(f"unexpected /api/analytics/overview status {resp.status_code}")
            else:
                resp.success()

    @task(1)
    def webhooks_list(self):
        # API mix scenario 3: webhook subscriptions list (/api/webhooks).
        with self.client.get(
            "/api/webhooks",
            headers=self._headers,
            name="api/webhooks",
            catch_response=True,
        ) as resp:
            if resp.status_code not in (200, 401, 403):
                resp.failure(f"unexpected /api/webhooks status {resp.status_code}")
            else:
                resp.success()

    @task(1)
    def metrics_scrape(self):
        # Read-only; 200 (enabled), 401 (token-gated) and 404 (disabled) are
        # all legitimate and must never be a load-test failure.
        with self.client.get("/metrics", name="metrics", catch_response=True) as resp:
            if resp.status_code not in (200, 401, 404):
                resp.failure(f"unexpected metrics status {resp.status_code}")
            else:
                resp.success()

    @task(1)
    def unknown_api_returns_404(self):
        # SPA fallback must never swallow API paths -- cheap invariant check.
        with self.client.get("/api/loadtest-probe", catch_response=True) as resp:
            if resp.status_code != 404:
                resp.failure(f"expected 404, got {resp.status_code}")
            else:
                resp.success()
