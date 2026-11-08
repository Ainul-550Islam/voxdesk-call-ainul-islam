"""Real outbound signed webhook delivery and HMAC signature verification against a public receiver."""

from __future__ import annotations

import json
import os
import secrets
import time
import uuid

import httpx
import pytest

from app.webhooks.delivery import deliver
from app.webhooks.signing import sign, verify
from tests.real.conftest import RealEvidenceWriter, require_provider_env

pytestmark = pytest.mark.real_provider


@pytest.mark.asyncio
@pytest.mark.timeout(45)
async def test_deliver_signed_webhook_to_public_receiver_and_verify_signature(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
) -> None:
    env = require_provider_env(
        "webhook_receiver",
        request=request,
        evidence_writer=evidence_writer,
    )
    receiver_url = env["VOXDESK_REAL_WEBHOOK_RECEIVER_URL"]
    signing_secret = (os.environ.get("VOXDESK_REAL_WEBHOOK_SECRET") or "").strip() or (
        "whsec_" + secrets.token_hex(24)
    )
    event_id = f"evt_real_{uuid.uuid4().hex[:16]}"
    payload_bytes = json.dumps(
        {
            "id": event_id,
            "type": "call.analyzed",
            "created_at": int(time.time()),
            "data": {
                "call_id": str(uuid.uuid4()),
                "call_successful": True,
                "sentiment": "positive",
            },
        },
        separators=(",", ":"),
    ).encode("utf-8")

    t0 = time.perf_counter()
    try:
        result = await deliver(
            url=receiver_url,
            body=payload_bytes,
            secret=signing_secret,
            event_id=event_id,
            timeout_seconds=15.0,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert result.kind == "delivered" and 200 <= result.status < 300, (
            f"Webhook delivery failed: kind={result.kind}, status={result.status}, category={result.category}"
        )

        # Verify our local signature scheme and echoed headers when receiver echoes JSON (e.g. httpbin/webhook.site)
        now_ts = int(time.time())
        expected_sig = sign(signing_secret, payload_bytes, timestamp=now_ts)
        assert verify(signing_secret, payload_bytes, expected_sig, now=now_ts) is True

        inspect_url = (os.environ.get("VOXDESK_REAL_WEBHOOK_INSPECT_URL") or "").strip()
        echoed_verified = False
        if inspect_url:
            async with httpx.AsyncClient(timeout=15.0) as client:
                echo_resp = await client.get(inspect_url)
                assert echo_resp.status_code == 200, echo_resp.text
                echo_doc = echo_resp.json()
                headers_map = {str(k).lower(): str(v) for k, v in (echo_doc.get("headers") or {}).items()}
                sig_hdr = headers_map.get("x-voxdesk-signature", "")
                assert sig_hdr and verify(signing_secret, payload_bytes, sig_hdr), (
                    f"Echoed signature header failed verification: {sig_hdr!r}"
                )
                echoed_verified = True

        evidence_writer.record(
            provider="webhook_receiver",
            status="PASS",
            test_node=request.node.nodeid,
            latency_ms=result.latency_ms or elapsed_ms,
            model="hmac-sha256-v1",
            details={
                "http_status": result.status,
                "event_id": event_id,
                "echoed_signature_verified": echoed_verified,
            },
        )
    except Exception as exc:
        evidence_writer.record(
            provider="webhook_receiver",
            status="FAIL",
            test_node=request.node.nodeid,
            reason=str(exc),
        )
        raise
