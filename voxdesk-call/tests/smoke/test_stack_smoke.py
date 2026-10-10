"""Pytest wrapper around `scripts/stack_smoke.py` (`@pytest.mark.docker`).

Skipped automatically unless the live `voxdesk-check` Docker stack is reachable
on `127.0.0.1:8000` (so normal unit/integration pytest runs never fail when
Docker is down). When executed with `pytest tests/smoke/test_stack_smoke.py -m docker`,
exercises all 4 live stack smoke suites:
  1. HTTP & authenticated owner/tenant/agent flow
  2. Realtime gateway (Go) + media engine (Rust) health, readiness, metrics, and signed ingest
  3. Twilio `/telephony/voice` HMAC signature verification (valid 200 `<Stream>` vs invalid 403)
  4. Twilio `/telephony/ws` media stream handshake (`connected -> start -> media -> stop`) + DB verification
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import pytest

from scripts.stack_smoke import (
    _run_call_ws_async,
    run_http_smoke,
    run_realtime_smoke,
    run_twilio_smoke,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
HTTP_BASE = os.environ.get("VOXDESK_CHECK_HTTP_BASE", "http://127.0.0.1:8000")
WS_BASE = os.environ.get("VOXDESK_CHECK_WS_BASE", "ws://127.0.0.1:8000")
GATEWAY_BASE = os.environ.get("VOXDESK_CHECK_GATEWAY_BASE", "http://127.0.0.1:8790")
MEDIA_BASE = os.environ.get("VOXDESK_CHECK_MEDIA_BASE", "http://127.0.0.1:9001")
ENV_FILE = REPO_ROOT / os.environ.get("VOXDESK_CHECK_ENV_FILE", ".env.check")


def _stack_reachable() -> bool:
    try:
        r = httpx.get(f"{HTTP_BASE}/health", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        not _stack_reachable(),
        reason=f"voxdesk-check stack is not reachable at {HTTP_BASE}",
    ),
]


def test_docker_stack_http_smoke() -> None:
    res = run_http_smoke(HTTP_BASE, ENV_FILE)
    assert res["status"] == "PASS"
    assert all(c["status"] == "PASS" for c in res["checks"])


def test_docker_stack_realtime_and_media_smoke() -> None:
    res = run_realtime_smoke(GATEWAY_BASE, MEDIA_BASE, ENV_FILE)
    assert res["status"] == "PASS"
    assert all(c["status"] == "PASS" for c in res["checks"])


def test_docker_stack_twilio_webhook_hmac_smoke() -> None:
    res = run_twilio_smoke(HTTP_BASE, ENV_FILE)
    assert res["status"] == "PASS"
    assert all(c["status"] == "PASS" for c in res["checks"])


@pytest.mark.asyncio
async def test_docker_stack_call_websocket_smoke() -> None:
    res = await _run_call_ws_async(WS_BASE, ENV_FILE)
    assert res["status"] == "PASS"
    assert all(c["status"] == "PASS" for c in res["checks"])
