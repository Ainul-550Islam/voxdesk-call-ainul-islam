"""Opt-in Telnyx Call Control smoke test; it does not prove media-WebSocket audio.

The paid test creates one real call, looks it up through Call Control, and
requests hangup. It does not invoke ``streaming_start``, observe an application
webhook, validate a signed media socket, or assert audio frames reaching the
VoxDesk pipeline. Its ledger scope is intentionally limited to Call Control.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from tests.real.conftest import RealEvidenceWriter, require_provider_env

pytestmark = pytest.mark.real_provider


@pytest.mark.asyncio
@pytest.mark.timeout(180)
async def test_telnyx_call_control_create_lookup_hangup_request(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
) -> None:
    env = require_provider_env("telnyx", request=request, evidence_writer=evidence_writer)
    api_key = env["TELNYX_API_KEY"]
    connection_id = env["TELNYX_CONNECTION_ID"]
    from_number = env["TELNYX_FROM_NUMBER"]
    to_number = env["TELNYX_TO_NUMBER"]
    public_base = env["VOXDESK_PUBLIC_BASE_URL"].rstrip("/")

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    call_control_id: str | None = None
    hangup_accepted = False

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            dial = await client.post(
                "https://api.telnyx.com/v2/calls",
                json={
                    "connection_id": connection_id,
                    "to": to_number,
                    "from": from_number,
                    # This is the mounted application webhook path. Do not point a
                    # real Telnyx callback at the former, unmounted /webhook route.
                    "webhook_url": f"{public_base}/telephony/telnyx/voice",
                    "timeout_secs": 25,
                },
            )
            assert dial.status_code in (200, 201), dial.text
            data = dial.json().get("data") or {}
            call_control_id = str(data.get("call_control_id") or "").strip()
            assert call_control_id, f"Telnyx returned no call_control_id: {data}"

            # The Call Control read endpoint must resolve the newly created call;
            # a 404 is not a successful lookup.
            lookup_deadline = time.monotonic() + 30.0
            call_doc: dict[str, Any] | None = None
            while time.monotonic() < lookup_deadline:
                lookup = await client.get(f"https://api.telnyx.com/v2/calls/{call_control_id}")
                if lookup.status_code == 200:
                    body = lookup.json()
                    candidate = body.get("data") if isinstance(body, dict) else None
                    if isinstance(candidate, dict):
                        call_doc = candidate
                        break
                else:
                    assert lookup.status_code in {404, 409}, lookup.text
                await asyncio.sleep(1.0)
            assert call_doc is not None, f"Telnyx did not expose the created call {call_control_id}"
            returned_id = str(call_doc.get("call_control_id") or "")
            assert returned_id == call_control_id, f"Call Control lookup returned the wrong call: {call_doc}"

            hangup = await client.post(
                f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/hangup",
                json={},
            )
            assert hangup.status_code in {200, 202}, hangup.text
            hangup_accepted = True

            evidence_writer.record(
                provider="telnyx",
                status="PASS",
                test_node=request.node.nodeid,
                model="telnyx-call-control-v2",
                details={
                    "evidence_scope": "call_control_create_lookup_hangup_request",
                    "call_control_id": call_control_id,
                    "call_control_create_observed": True,
                    "call_control_lookup_observed": True,
                    "lookup_status": 200,
                    "call_state_at_lookup": call_doc.get("state") or call_doc.get("status"),
                    "webhook_url_configured": True,
                    "application_webhook_asserted": False,
                    "media_websocket_observed": False,
                    "media_stream_requested": False,
                    "hangup_action_accepted": True,
                },
            )
    except Exception as exc:
        evidence_writer.record(
            provider="telnyx",
            status="FAIL",
            test_node=request.node.nodeid,
            reason=str(exc),
        )
        raise
    finally:
        if call_control_id and not hangup_accepted:
            try:
                async with httpx.AsyncClient(timeout=15.0, headers=headers) as cleanup_client:
                    await cleanup_client.post(
                        f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/hangup",
                        json={},
                    )
            except Exception:
                # Preserve the provider test's primary failure. A stuck test call
                # still warrants operator cleanup in the Telnyx dashboard.
                pass
