"""Real Twilio PSTN call through VoxDesk, with persisted media/transcript evidence.

The destination must be an explicitly authorized, controlled test handset/IVR that
answers and speaks a known phrase. This test starts a paid outbound call through
VoxDesk's public API; it never calls Twilio's inbound ``/telephony/voice`` route
as if that were an outbound answer handler.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from typing import Any

import httpx
import pytest

from tests.real.conftest import RealEvidenceWriter, require_provider_env

pytestmark = pytest.mark.real_provider


async def _wait_for_twilio_terminal(
    client: httpx.AsyncClient,
    *,
    api_base: str,
    call_sid: str,
    timeout_seconds: float = 150.0,
) -> dict[str, Any]:
    terminal = {"completed", "busy", "failed", "no-answer", "canceled"}
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = await client.get(f"{api_base}/Calls/{call_sid}.json")
        assert response.status_code == 200, response.text
        latest = response.json()
        if str(latest.get("status") or "").lower() in terminal:
            return latest
        await asyncio.sleep(2.5)
    raise AssertionError(f"Twilio call {call_sid} did not become terminal before timeout: {latest}")


async def _wait_for_recordings(
    client: httpx.AsyncClient,
    *,
    api_base: str,
    call_sid: str,
    timeout_seconds: float = 45.0,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        response = await client.get(f"{api_base}/Calls/{call_sid}/Recordings.json")
        assert response.status_code == 200, response.text
        recordings = response.json().get("recordings") or []
        if recordings:
            return recordings
        await asyncio.sleep(2.0)
    raise AssertionError(f"Twilio did not publish a recording for completed call {call_sid}")


async def _wait_for_replay_turns(
    client: httpx.AsyncClient,
    *,
    public_base: str,
    call_id: str,
    timeout_seconds: float = 45.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    latest: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = await client.get(f"{public_base}/api/calls/{call_id}/replay")
        assert response.status_code == 200, response.text
        latest = response.json()
        turns = [
            event for event in latest.get("timeline") or []
            if isinstance(event, dict) and event.get("event") == "transcript_turn"
        ]
        speakers = {str(event.get("speaker") or "").lower() for event in turns}
        if str(latest.get("transcript") or "").strip() and {"user", "assistant"}.issubset(speakers):
            return latest
        await asyncio.sleep(2.0)
    raise AssertionError(
        f"VoxDesk replay did not persist both caller and agent transcript turns: {latest}"
    )


@pytest.mark.asyncio
@pytest.mark.timeout(300)
async def test_twilio_real_call_routes_media_persists_transcript_and_cleans_up(
    request: pytest.FixtureRequest,
    evidence_writer: RealEvidenceWriter,
) -> None:
    env = require_provider_env("twilio", request=request, evidence_writer=evidence_writer)
    account_sid = env["TWILIO_ACCOUNT_SID"]
    auth_token = env["TWILIO_AUTH_TOKEN"]
    from_number = env["TWILIO_PHONE_NUMBER"]
    to_number = env["TWILIO_TEST_TO_NUMBER"]
    public_base = env["VOXDESK_PUBLIC_BASE_URL"].rstrip("/")
    api_key = env["VOXDESK_REAL_API_KEY"]

    twilio_api = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}"
    auth = (account_sid, auth_token)
    vox_headers = {"Authorization": f"Bearer {api_key}"}
    call_id: str | None = None
    call_sid: str | None = None
    call_completed = False
    recording_sids: list[str] = []

    try:
        # Use the application outbound endpoint so it creates the durable Call
        # row and supplies /telephony/outbound-answer + the signed media stream URL.
        # Calling Twilio directly at /telephony/voice would exercise the inbound
        # number lookup and can incorrectly return the unconfigured-number prompt.
        async with httpx.AsyncClient(timeout=45.0) as app_client:
            create = await app_client.post(
                f"{public_base}/api/calls",
                headers={
                    **vox_headers,
                    "Idempotency-Key": f"real-twilio-{uuid.uuid4().hex}",
                },
                json={
                    "to": to_number,
                    "from_number": from_number,
                    "provider": "twilio",
                    "timeout_seconds": 30,
                    "record": True,
                    "recording_channels": "dual",
                },
            )
            assert create.status_code == 201, create.text
            call_doc = create.json()
            call_id = str(call_doc.get("id") or "")
            call_sid = str(call_doc.get("call_sid") or "")
            assert call_id, f"VoxDesk did not return the persisted call id: {call_doc}"
            assert call_sid.startswith("CA"), f"VoxDesk did not return a Twilio CallSid: {call_doc}"

        async with httpx.AsyncClient(timeout=30.0, auth=auth) as twilio_client:
            final_doc = await _wait_for_twilio_terminal(
                twilio_client, api_base=twilio_api, call_sid=call_sid
            )
            status = str(final_doc.get("status") or "").lower()
            call_completed = status == "completed"
            assert call_completed, f"Expected completed PSTN call, got {status!r}: {final_doc}"

            recordings = await _wait_for_recordings(
                twilio_client, api_base=twilio_api, call_sid=call_sid
            )
            recording_sids = [str(row["sid"]) for row in recordings if row.get("sid")]
            assert recording_sids, f"Twilio returned no recording SID for {call_sid}"
            channel_counts = {int(row.get("channels") or 1) for row in recordings}
            assert 2 in channel_counts, (
                f"Expected a dual-channel caller/agent recording, got channels={sorted(channel_counts)}"
            )

        # A 2xx PSTN call is not enough: independently read the persisted VoxDesk
        # call and replay endpoint, and require at least one real transcript turn.
        async with httpx.AsyncClient(timeout=30.0, headers=vox_headers) as app_client:
            search = await app_client.post(
                f"{public_base}/api/calls/search",
                json={"search": call_sid, "limit": 10},
            )
            assert search.status_code == 200, search.text
            rows = search.json().get("calls") or []
            matching = [row for row in rows if str(row.get("call_sid") or "") == call_sid]
            assert matching, f"CallSid {call_sid} is not visible in the authenticated VoxDesk call search"
            assert str(matching[0].get("id") or "") == call_id

            replay_doc = await _wait_for_replay_turns(
                app_client, public_base=public_base, call_id=call_id
            )
            transcript = str(replay_doc.get("transcript") or "").strip()
            timeline_turns = [
                event for event in replay_doc.get("timeline") or []
                if isinstance(event, dict) and event.get("event") == "transcript_turn"
            ]
            speakers = {str(event.get("speaker") or "").lower() for event in timeline_turns}
            assert {"user", "assistant"}.issubset(speakers), (
                f"Expected caller and agent speech turns in replay, observed {sorted(speakers)}"
            )

        # Remove the test recording only after all assertions have consumed it.
        recording_count = len(recording_sids)
        async with httpx.AsyncClient(timeout=20.0, auth=auth) as cleanup_client:
            for recording_sid in recording_sids:
                deleted = await cleanup_client.delete(f"{twilio_api}/Recordings/{recording_sid}.json")
                assert deleted.status_code in {200, 204, 404}, deleted.text
        recording_sids.clear()

        evidence_writer.record(
            provider="twilio",
            status="PASS",
            test_node=request.node.nodeid,
            model="twilio-programmable-voice",
            details={
                "call_sid": call_sid,
                "call_id": call_id,
                "call_status": status,
                "duration_s": final_doc.get("duration"),
                "recording_count": recording_count,
                "recording_channels": sorted(channel_counts),
                "transcript_turns": len(timeline_turns),
                "transcript_sha256": hashlib.sha256(transcript.encode("utf-8")).hexdigest(),
                "provider_fakes": False,
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
        # Best-effort cleanup on assertion/network failure; cleanup failure must not
        # replace the original evidence failure. On a successful path, the call is
        # already terminal and recordings were explicitly deleted before PASS.
        if call_sid and (not call_completed or recording_sids):
            try:
                async with httpx.AsyncClient(timeout=20.0, auth=auth) as cleanup_client:
                    if not call_completed:
                        await cleanup_client.post(
                            f"{twilio_api}/Calls/{call_sid}.json",
                            data={"Status": "completed"},
                        )
                    for recording_sid in recording_sids:
                        await cleanup_client.delete(f"{twilio_api}/Recordings/{recording_sid}.json")
            except Exception:
                pass
