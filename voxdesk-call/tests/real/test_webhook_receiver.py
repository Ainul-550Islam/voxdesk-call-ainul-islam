"""Opt-in real webhook delivery with positive and negative receiver verification.

The receiver must reject a fresh but corrupted HMAC with a 4xx, return
``signature_verified: true`` for a valid HMAC, and expose the captured raw
body/headers at its inspection URL. A 2xx echo alone is not verification.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid

import httpx
import pytest

from app.core.ssrf import validate_resolved_outbound_url
from app.webhooks.delivery import deliver
from app.webhooks.signing import sign, verify
from tests.real.conftest import RealEvidenceWriter, require_provider_env

pytestmark = pytest.mark.real_provider


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_deliver_signed_webhook_to_receiver_accepts_valid_and_rejects_invalid_signature(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
) -> None:
    env = require_provider_env(
        "webhook_receiver",
        request=request,
        evidence_writer=evidence_writer,
    )
    receiver_url = env["VOXDESK_REAL_WEBHOOK_RECEIVER_URL"]
    inspect_url = env["VOXDESK_REAL_WEBHOOK_INSPECT_URL"]
    signing_secret = env["VOXDESK_REAL_WEBHOOK_SECRET"]
    event_id = f"evt_real_{uuid.uuid4().hex[:16]}"
    invalid_event_id = f"evt_invalid_{uuid.uuid4().hex[:16]}"
    call_id = str(uuid.uuid4())
    created_at = int(time.time())

    def payload_for(identifier: str) -> bytes:
        return json.dumps(
            {
                "id": identifier,
                "type": "call.analyzed",
                "created_at": created_at,
                "data": {
                    "call_id": call_id,
                    "call_successful": True,
                    "sentiment": "positive",
                },
            },
            separators=(",", ":"),
        ).encode("utf-8")

    payload_bytes = payload_for(event_id)
    invalid_payload_bytes = payload_for(invalid_event_id)

    try:
        await validate_resolved_outbound_url(receiver_url, require_https=True)
        valid_signature_for_invalid_body = sign(signing_secret, invalid_payload_bytes)
        timestamp_header = valid_signature_for_invalid_body.split(",", 1)[0].partition("=")[2]
        signature_key, digest = valid_signature_for_invalid_body.split(",", 1)[1].split("=", 1)
        corrupted_digest = ("0" if digest[0] != "0" else "1") + digest[1:]
        invalid_signature = f"t={timestamp_header},{signature_key}={corrupted_digest}"
        assert verify(signing_secret, invalid_payload_bytes, valid_signature_for_invalid_body)
        assert not verify(signing_secret, invalid_payload_bytes, invalid_signature), (
            "Negative control must retain a fresh timestamp and corrupt only the HMAC"
        )

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            invalid_response = await client.post(
                receiver_url,
                content=invalid_payload_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-VoxDesk-Signature": invalid_signature,
                    "X-VoxDesk-Timestamp": timestamp_header,
                    "X-VoxDesk-Event": invalid_event_id,
                },
            )
        assert 400 <= invalid_response.status_code < 500, (
            "Receiver must reject a fresh but corrupted HMAC with a 4xx; "
            f"got {invalid_response.status_code}: {invalid_response.text[:500]}"
        )
        try:
            invalid_response_doc = invalid_response.json()
        except (TypeError, json.JSONDecodeError):
            invalid_response_doc = {}
        if isinstance(invalid_response_doc, dict):
            assert invalid_response_doc.get("signature_verified") is not True, (
                f"Receiver claimed that an invalid signature was verified: {invalid_response_doc}"
            )

        result = await deliver(
            url=receiver_url,
            body=payload_bytes,
            secret=signing_secret,
            event_id=event_id,
            timeout_seconds=15.0,
        )
        assert result.kind == "success" and 200 <= result.status < 300, (
            f"Webhook delivery failed: kind={result.kind}, status={result.status}, category={result.category}"
        )

        # The receiver's 2xx body is a claim made by the remote verification
        # endpoint. The separate inspection endpoint below confirms the exact
        # request body and signed headers that it received.
        try:
            response_doc = json.loads(result.response_snippet)
        except (TypeError, json.JSONDecodeError) as exc:
            raise AssertionError(
                "Receiver response must be JSON with signature_verified=true"
            ) from exc
        assert response_doc.get("signature_verified") is True, (
            f"Remote receiver did not report signature verification: {response_doc}"
        )
        assert response_doc.get("event_id") == event_id

        await validate_resolved_outbound_url(inspect_url, require_https=True)
        async with httpx.AsyncClient(timeout=15.0) as client:
            inspect_response = await client.get(
                inspect_url,
                params={"event_id": event_id},
            )
        assert inspect_response.status_code == 200, inspect_response.text
        captured = inspect_response.json()
        assert captured.get("event_id") == event_id
        assert captured.get("signature_verified") is True

        headers_map = {
            str(key).lower(): str(value)
            for key, value in (captured.get("headers") or {}).items()
        }
        signature_header = headers_map.get("x-voxdesk-signature", "")
        timestamp_header = headers_map.get("x-voxdesk-timestamp", "")
        event_header = headers_map.get("x-voxdesk-event", "")
        assert signature_header and verify(signing_secret, payload_bytes, signature_header), (
            "The receiver's echoed signature does not verify against the original exact body"
        )
        assert timestamp_header == signature_header.split(",", 1)[0].partition("=")[2]
        assert event_header == event_id

        received_body = captured.get("body")
        if isinstance(received_body, str):
            received_bytes = received_body.encode("utf-8")
        elif isinstance(received_body, dict):
            received_bytes = json.dumps(received_body, separators=(",", ":")).encode("utf-8")
        else:
            raise AssertionError("Inspection response must include raw JSON text or an equivalent body object")
        assert received_bytes == payload_bytes, "Receiver captured a different body than the signed payload"

        evidence_writer.record(
            provider="webhook_receiver",
            status="PASS",
            test_node=request.node.nodeid,
            latency_ms=result.latency_ms,
            model="hmac-sha256-v1",
            details={
                "evidence_scope": "outbound_webhook_signature_verification",
                "http_status": result.status,
                "event_id": event_id,
                "remote_signature_verified": True,
                "remote_invalid_signature_rejected": True,
                "invalid_signature_http_status": invalid_response.status_code,
                "echoed_signature_verified": True,
                "exact_body_verified": received_bytes == payload_bytes,
                "body_sha256": hashlib.sha256(payload_bytes).hexdigest(),
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
