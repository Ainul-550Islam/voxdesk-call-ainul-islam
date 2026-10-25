"""Opt-in real-carrier human softphone smoke test; no carrier result is simulated."""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def require_live_carrier_opt_in():
    if os.environ.get("VOXDESK_LIVE_TESTS") != "1":
        pytest.skip("set VOXDESK_LIVE_TESTS=1 and CC2_LIVE_* values to run carrier tests")


@pytest_asyncio.fixture
async def carrier_api():
    required = (
        "CC2_LIVE_API_BASE_URL", "CC2_LIVE_BEARER_TOKEN", "CC2_LIVE_ENVIRONMENT_ID",
        "CC2_LIVE_DESTINATION", "CC2_LIVE_CALLER_ID",
    )
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        pytest.skip(f"live carrier configuration missing: {', '.join(missing)}")
    base = os.environ["CC2_LIVE_API_BASE_URL"].rstrip("/")
    if not base.startswith("https://") and os.environ.get("CC2_LIVE_ALLOW_HTTP") != "1":
        pytest.skip("live API must use HTTPS unless CC2_LIVE_ALLOW_HTTP=1 is explicit")
    async with AsyncClient(
        base_url=base,
        headers={"Authorization": f"Bearer {os.environ['CC2_LIVE_BEARER_TOKEN']}"},
        timeout=45,
    ) as client:
        yield client


async def test_live_agent_softphone_registration_call_and_cleanup(carrier_api: AsyncClient):
    current = await carrier_api.get("/api/cc2/softphone/sessions/current")
    current.raise_for_status()
    if current.json()["session"] is not None:
        pytest.skip("dedicated live agent already has a registration; refusing to replace it")
    registered = await carrier_api.post(
        "/api/cc2/softphone/sessions",
        json={
            "environment_id": os.environ["CC2_LIVE_ENVIRONMENT_ID"],
            "device_label": "CC2 required live softphone proof",
        },
        headers={"Idempotency-Key": f"live-required-register-{uuid.uuid4()}"},
    )
    registered.raise_for_status()
    session = registered.json()["session"]
    assert session["token"] and session["websocket_ticket"]
    assert session["ice_servers"]
    session_id = session["id"]
    call_id = None
    try:
        response = await carrier_api.post(
            "/api/cc2/softphone/calls",
            json={
                "session_id": session_id,
                "destination": os.environ["CC2_LIVE_DESTINATION"],
                "caller_id_number": os.environ["CC2_LIVE_CALLER_ID"],
            },
            headers={"Idempotency-Key": f"live-required-call-{uuid.uuid4()}"},
        )
        response.raise_for_status()
        call = response.json()["call"]
        call_id = call["id"]
        assert call["status"] in {"dialing", "waiting_for_agent", "in_progress"}
        # REST acceptance is not represented as human answer or media connection.
        assert call["answered_at"] is None or call["agent_joined_at"] is not None
        customer = next(leg for leg in call["legs"] if leg["kind"] == "customer")
        assert customer["provider_call_id"]
    finally:
        if call_id is not None:
            ended = await carrier_api.post(f"/api/cc2/softphone/calls/{call_id}/end")
            ended.raise_for_status()
            assert ended.json()["call"]["status"] in {"completed", "cancelled", "failed"}
        revoked = await carrier_api.delete(f"/api/cc2/softphone/sessions/{session_id}")
        revoked.raise_for_status()
