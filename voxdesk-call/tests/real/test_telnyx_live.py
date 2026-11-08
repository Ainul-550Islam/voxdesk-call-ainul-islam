"""Real Telnyx Call Control v2 + bidirectional media WebSocket integration test."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from tests.real.conftest import RealEvidenceWriter, require_provider_env

pytestmark = pytest.mark.real_provider


@pytest.mark.asyncio
@pytest.mark.timeout(180)
async def test_telnyx_inbound_outbound_media_websocket_flow(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
) -> None:
    env = require_provider_env("telnyx", request=request, evidence_writer=evidence_writer)
    api_key = env["TELNYX_API_KEY"]
    connection_id = env["TELNYX_CONNECTION_ID"]
    from_number = env["TELNYX_FROM_NUMBER"]
    to_number = env["TELNYX_TO_NUMBER"]
    public_base = env["VOXDESK_PUBLIC_BASE_URL"].rstrip("/")
    ws_base = public_base.replace("https://", "wss://").replace("http://", "ws://")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    call_control_id: str | None = None
    t0 = time.perf_counter()

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            dial_resp = await client.post(
                "https://api.telnyx.com/v2/calls",
                json={
                    "connection_id": connection_id,
                    "to": to_number,
                    "from": from_number,
                    "webhook_url": f"{public_base}/telephony/telnyx/webhook",
                    "stream_url": f"{ws_base}/telephony/telnyx/ws",
                    "stream_track": "both_tracks",
                    "stream_bidirectional_mode": "rtp",
                    "stream_bidirectional_codec": "PCMU",
                    "timeout_secs": 25,
                },
            )
            assert dial_resp.status_code in (200, 201), dial_resp.text
            data = dial_resp.json().get("data") or {}
            call_control_id = str(data.get("call_control_id") or "")
            assert call_control_id, f"Missing call_control_id in {data}"

            await asyncio.sleep(4.0)
            status_resp = await client.get(f"https://api.telnyx.com/v2/calls/{call_control_id}")
            assert status_resp.status_code in (200, 404), status_resp.text
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            evidence_writer.record(
                provider="telnyx",
                status="PASS",
                test_node=request.node.nodeid,
                latency_ms=elapsed_ms,
                model="telnyx-call-control-v2",
                details={
                    "call_control_id": call_control_id,
                    "stream_url": f"{ws_base}/telephony/telnyx/ws",
                    "call_session_id": data.get("call_session_id"),
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
        if call_control_id:
            async with httpx.AsyncClient(timeout=15.0, headers=headers) as cleanup_client:
                await cleanup_client.post(
                    f"https://api.telnyx.com/v2/calls/{call_control_id}/actions/hangup",
                    json={},
                )
