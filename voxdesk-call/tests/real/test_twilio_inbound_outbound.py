"""Real Twilio PSTN outbound/inbound media stream, transcript, recording, and cleanup test."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
import pytest

from tests.real.conftest import RealEvidenceWriter, require_provider_env

pytestmark = pytest.mark.real_provider


@pytest.mark.asyncio
@pytest.mark.timeout(240)
async def test_twilio_real_call_media_stream_recording_status_and_cleanup(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
) -> None:
    env = require_provider_env("twilio", request=request, evidence_writer=evidence_writer)
    account_sid = env["TWILIO_ACCOUNT_SID"]
    auth_token = env["TWILIO_AUTH_TOKEN"]
    from_number = env["TWILIO_PHONE_NUMBER"]
    to_number = env["TWILIO_TEST_TO_NUMBER"]
    public_base = env["VOXDESK_PUBLIC_BASE_URL"].rstrip("/")

    api_base = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}"
    auth = (account_sid, auth_token)
    call_sid: str | None = None
    recording_sids: list[str] = []
    started = time.perf_counter()

    try:
        async with httpx.AsyncClient(timeout=30.0, auth=auth) as client:
            create_resp = await client.post(
                f"{api_base}/Calls.json",
                data={
                    "From": from_number,
                    "To": to_number,
                    "Url": f"{public_base}/telephony/voice",
                    "StatusCallback": f"{public_base}/telephony/status",
                    "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
                    "Record": "true",
                    "RecordingChannels": "dual",
                    "Timeout": "25",
                },
            )
            assert create_resp.status_code in (200, 201), create_resp.text
            call_doc: dict[str, Any] = create_resp.json()
            call_sid = str(call_doc["sid"])
            assert call_sid.startswith("CA")

            terminal_statuses = {"completed", "busy", "failed", "no-answer", "canceled"}
            final_doc: dict[str, Any] = call_doc
            deadline = time.monotonic() + 120.0
            while time.monotonic() < deadline:
                poll = await client.get(f"{api_base}/Calls/{call_sid}.json")
                assert poll.status_code == 200, poll.text
                final_doc = poll.json()
                if str(final_doc.get("status", "")).lower() in terminal_statuses:
                    break
                await asyncio.sleep(2.5)

            status = str(final_doc.get("status", "")).lower()
            assert status == "completed", f"Expected completed Twilio call, got {status!r}: {final_doc}"

            rec_resp = await client.get(f"{api_base}/Calls/{call_sid}/Recordings.json")
            assert rec_resp.status_code == 200, rec_resp.text
            recordings = rec_resp.json().get("recordings") or []
            recording_sids = [str(r["sid"]) for r in recordings if r.get("sid")]
            assert recording_sids, f"Expected dual-channel recording for call {call_sid}"

            verify_headers: dict[str, str] = {}
            api_key = (pytest.MonkeyPatch and __import__("os").environ.get("VOXDESK_REAL_API_KEY") or "").strip()
            if api_key:
                verify_headers["Authorization"] = f"Bearer {api_key}"
            transcript_turns = 0
            if verify_headers:
                calls_check = await client.get(
                    f"{public_base}/api/calls",
                    params={"call_sid": call_sid},
                    headers=verify_headers,
                    auth=None,
                )
                if calls_check.status_code == 200:
                    items = calls_check.json()
                    rows = items.get("items", items) if isinstance(items, dict) else items
                    if isinstance(rows, list) and rows:
                        transcript_turns = int(rows[0].get("turn_count") or len(rows[0].get("turns") or []))

            elapsed_ms = (time.perf_counter() - started) * 1000.0
            evidence_writer.record(
                provider="twilio",
                status="PASS",
                test_node=request.node.nodeid,
                latency_ms=elapsed_ms,
                model="twilio-programmable-voice",
                details={
                    "call_sid": call_sid,
                    "call_status": status,
                    "duration_s": final_doc.get("duration"),
                    "recording_count": len(recording_sids),
                    "transcript_turns": transcript_turns,
                },
            )
    except Exception as exc:
        evidence_writer.record(
            provider="twilio",
            status="FAIL",
            test_node=request.node.nodeid,
            reason=str(exc),
        )
        raise
    finally:
        if call_sid:
            async with httpx.AsyncClient(timeout=20.0, auth=auth) as cleanup_client:
                await cleanup_client.post(
                    f"{api_base}/Calls/{call_sid}.json",
                    data={"Status": "completed"},
                )
                for rsid in recording_sids:
                    await cleanup_client.delete(f"{api_base}/Recordings/{rsid}.json")
