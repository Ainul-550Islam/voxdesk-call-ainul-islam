"""Opt-in one-second PCAP smoke test against an already-active lab call.

This does not originate or terminate calls. The returned capture stays in
memory for validation only; evidence records bounded metadata, never packet
bytes, SIP headers, audio, call IDs, or tenant IDs.
"""

from __future__ import annotations

import base64
from urllib.parse import quote

import httpx
import pytest

from app.telephony.pcap import CaptureError, _validate_classic_raw_pcap
from tests.real.conftest import RealEvidenceWriter, require_provider_env

pytestmark = pytest.mark.real_provider


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_sip_gateway_captures_active_call_and_reports_metadata(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
) -> None:
    env = require_provider_env("sip_gateway_pcap", request=request, evidence_writer=evidence_writer)
    base_url = env["VOXDESK_SIP_GATEWAY_URL"].rstrip("/")
    tenant_id = env["VOXDESK_SIP_CAPTURE_TENANT_ID"]
    call_id = env["VOXDESK_SIP_CAPTURE_CALL_ID"]
    payload = {
        "tenant_id": tenant_id,
        "call_id": call_id,
        "duration_seconds": 1,
        "snaplen": 262,
        "direction": "both",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"{base_url}/v1/calls/{quote(call_id, safe='')}/pcap",
                headers={
                    "Authorization": f"Bearer {env['VOXDESK_SIP_GATEWAY_TOKEN']}",
                    "Accept": "application/json",
                },
                json=payload,
            )
        assert response.status_code == 200, (
            f"SIP gateway PCAP endpoint returned HTTP {response.status_code}"
        )
        body = response.json()
        assert body.get("content_type") == "application/vnd.tcpdump.pcap"
        encoded = body.get("data_base64")
        assert isinstance(encoded, str) and encoded
        blob = base64.b64decode(encoded, validate=True)
        assert type(body.get("packet_count")) is int and body["packet_count"] >= 0
        assert type(body.get("truncated")) is bool
        assert len(blob) <= 64 * 1024 * 1024
        parsed_packet_count = _validate_classic_raw_pcap(blob)
        assert parsed_packet_count == body["packet_count"]
        assert parsed_packet_count > 0, "the configured lab call produced no packets in the capture window"

        evidence_writer.record(
            provider="sip_gateway_pcap",
            status="PASS",
            test_node=request.node.nodeid,
            model="voxdesk-sip-gateway-pcap",
            details={
                "evidence_scope": "sip_gateway_call_scoped_pcap",
                "authenticated_capture_observed": True,
                "packet_count": parsed_packet_count,
                "truncated": body["truncated"],
                "capture_bytes": len(blob),
                "classic_raw_pcap_validated": True,
                "capture_persisted": False,
            },
        )
    except Exception as exc:
        evidence_writer.record(
            provider="sip_gateway_pcap",
            status="FAIL",
            test_node=request.node.nodeid,
            reason=f"PCAP integration failed with {type(exc).__name__}",
        )
        if isinstance(exc, CaptureError):
            raise AssertionError("SIP gateway returned an invalid classic DLT_RAW PCAP") from exc
        raise
