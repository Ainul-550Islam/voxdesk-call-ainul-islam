"""Shared, explicit live-PSTN helpers for real-only measurement scripts."""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any

import httpx

TERMINAL_TWILIO_STATUSES = {"completed", "failed", "busy", "no-answer", "canceled"}
LIVE_PSTN_REQUIRED_ENV = (
    "VOXDESK_REAL_INTEGRATION",
    "VOXDESK_REAL_API_KEY",
    "VOXDESK_PUBLIC_BASE_URL",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_PHONE_NUMBER",
    "TWILIO_TEST_TO_NUMBER",
)


def missing_live_pstn_env() -> list[str]:
    """Return absent credentials or the explicit live-side-effect opt-in."""
    missing = [
        name
        for name in LIVE_PSTN_REQUIRED_ENV
        if name != "VOXDESK_REAL_INTEGRATION" and not (os.environ.get(name) or "").strip()
    ]
    opt_in = (os.environ.get("VOXDESK_REAL_INTEGRATION") or "").strip().lower()
    if opt_in not in {"1", "true", "yes"}:
        missing.insert(0, "VOXDESK_REAL_INTEGRATION=1")
    return missing


async def create_voxdesk_outbound_call(
    client: httpx.AsyncClient,
    *,
    public_base_url: str,
    api_key: str,
    to_number: str,
    from_number: str,
    timeout_seconds: int = 30,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Create the Call through VoxDesk so it has a DB row and signed media URL."""
    key = idempotency_key or f"real-pstn-{uuid.uuid4().hex}"
    response = await client.post(
        f"{public_base_url.rstrip('/')}/api/calls",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Idempotency-Key": key,
        },
        json={
            "to": to_number,
            "from_number": from_number,
            "provider": "twilio",
            "timeout_seconds": timeout_seconds,
            "record": True,
            "recording_channels": "dual",
        },
    )
    if response.status_code != 201:
        raise RuntimeError(f"VoxDesk outbound call API refused request with HTTP {response.status_code}")
    try:
        doc = response.json()
    except ValueError as exc:
        raise RuntimeError("VoxDesk outbound call API returned invalid JSON") from exc
    if not isinstance(doc, dict):
        raise RuntimeError("VoxDesk outbound call API returned an invalid response")
    call_id = str(doc.get("id") or "").strip()
    call_sid = str(doc.get("call_sid") or "").strip()
    if not call_id or not call_sid.startswith("CA"):
        raise RuntimeError("VoxDesk did not return a persisted call id and Twilio CallSid")
    return {**doc, "id": call_id, "call_sid": call_sid}


async def wait_for_twilio_terminal(
    client: httpx.AsyncClient,
    *,
    api_base: str,
    call_sid: str,
    timeout_seconds: float = 150.0,
    poll_seconds: float = 2.0,
) -> dict[str, Any]:
    """Poll Twilio REST until a terminal call state or a bounded timeout."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"{api_base}/Calls/{call_sid}.json")
        if response.status_code != 200:
            raise RuntimeError(f"Twilio call lookup returned HTTP {response.status_code}")
        try:
            doc = response.json()
        except ValueError as exc:
            raise RuntimeError("Twilio call lookup returned invalid JSON") from exc
        if not isinstance(doc, dict):
            raise RuntimeError("Twilio call lookup returned an invalid document")
        status = str(doc.get("status") or "").strip().lower()
        if status in TERMINAL_TWILIO_STATUSES:
            return doc
        await asyncio.sleep(poll_seconds)
    raise TimeoutError(f"Twilio call did not reach a terminal status within {timeout_seconds:g}s")


async def wait_for_voxdesk_transcript(
    client: httpx.AsyncClient,
    *,
    public_base_url: str,
    api_key: str,
    call_id: str,
    timeout_seconds: float = 45.0,
    poll_seconds: float = 2.0,
) -> dict[str, Any]:
    """Require persisted caller and agent turns through the authenticated replay API."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    headers = {"Authorization": f"Bearer {api_key}"}
    last_doc: dict[str, Any] = {}
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(
            f"{public_base_url.rstrip('/')}/api/calls/{call_id}/replay",
            headers=headers,
        )
        if response.status_code != 200:
            raise RuntimeError(f"VoxDesk replay lookup returned HTTP {response.status_code}")
        try:
            doc = response.json()
        except ValueError as exc:
            raise RuntimeError("VoxDesk replay lookup returned invalid JSON") from exc
        if not isinstance(doc, dict):
            raise RuntimeError("VoxDesk replay lookup returned an invalid document")
        last_doc = doc
        turns = [
            event for event in doc.get("timeline") or []
            if isinstance(event, dict) and event.get("event") == "transcript_turn"
        ]
        speakers = {str(event.get("speaker") or "").lower() for event in turns}
        if str(doc.get("transcript") or "").strip() and {"user", "assistant"}.issubset(speakers):
            return doc
        await asyncio.sleep(poll_seconds)
    raise TimeoutError(
        f"VoxDesk did not persist caller and assistant transcript turns for call {call_id}: "
        f"{len(last_doc.get('timeline') or [])} timeline events observed"
    )


async def wait_for_dual_channel_recordings(
    client: httpx.AsyncClient,
    *,
    api_base: str,
    call_sid: str,
    timeout_seconds: float = 45.0,
    poll_seconds: float = 2.0,
) -> list[dict[str, Any]]:
    """Wait for the dual-channel recording needed for caller-leg analysis."""
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"{api_base}/Calls/{call_sid}/Recordings.json")
        if response.status_code != 200:
            raise RuntimeError(f"Twilio recordings lookup returned HTTP {response.status_code}")
        try:
            doc = response.json()
        except ValueError as exc:
            raise RuntimeError("Twilio recordings lookup returned invalid JSON") from exc
        rows = doc.get("recordings") if isinstance(doc, dict) else None
        recordings = [row for row in rows or [] if isinstance(row, dict) and row.get("sid")]
        dual = []
        for row in recordings:
            try:
                if int(row.get("channels") or 0) == 2:
                    dual.append(row)
            except (TypeError, ValueError):
                continue
        if dual:
            return dual
        await asyncio.sleep(poll_seconds)
    raise TimeoutError(f"Twilio did not publish a dual-channel recording for {call_sid}")
