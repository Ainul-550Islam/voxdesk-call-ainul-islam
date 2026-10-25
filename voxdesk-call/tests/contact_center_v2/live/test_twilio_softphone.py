"""Real carrier smoke test. It never treats REST acceptance as an answered call."""
from __future__ import annotations

import os
import uuid

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.live


async def test_live_softphone_registration_outbound_and_end(live_api: AsyncClient):
    current = await live_api.get("/api/cc2/softphone/sessions/current")
    current.raise_for_status()
    if current.json()["session"] is not None:
        pytest.skip("dedicated live agent already has a registration; refusing to replace or revoke it")
    registration = await live_api.post(
        "/api/cc2/softphone/sessions",
        json={
            "environment_id": os.environ["CC2_LIVE_ENVIRONMENT_ID"],
            "device_label": "Explicit CC2 live pytest",
        },
        headers={"Idempotency-Key": f"live-register-{uuid.uuid4()}"},
    )
    registration.raise_for_status()
    session_id = registration.json()["session"]["id"]
    call_id = None
    try:
        response = await live_api.post(
            "/api/cc2/softphone/calls",
            json={
                "session_id": session_id,
                "destination": os.environ["CC2_LIVE_DESTINATION"],
                "caller_id_number": os.environ["CC2_LIVE_CALLER_ID"],
            },
            headers={"Idempotency-Key": f"live-call-{uuid.uuid4()}"},
        )
        response.raise_for_status()
        call = response.json()["call"]
        call_id = call["id"]
        assert call["status"] in {"dialing", "waiting_for_agent", "in_progress"}
        assert call["agent_joined_at"] is None
        customer = next(leg for leg in call["legs"] if leg["kind"] == "customer")
        assert customer["provider_call_id"]
        assert customer["answered_at"] is None or call["answered_at"] is not None
    finally:
        if call_id is not None:
            ended = await live_api.post(f"/api/cc2/softphone/calls/{call_id}/end")
            ended.raise_for_status()
            assert ended.json()["call"]["status"] in {"completed", "cancelled", "failed"}
        revoked = await live_api.delete(f"/api/cc2/softphone/sessions/{session_id}")
        revoked.raise_for_status()
