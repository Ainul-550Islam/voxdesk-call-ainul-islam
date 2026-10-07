"""Version-one customer event payload contracts; reference-only by default.

The outbox envelope carries tenant, environment scope, event identity and time.
Payloads deliberately exclude transcripts, phone numbers and recording URLs.
Adding a payload field requires an explicit schema change, not arbitrary extras.
"""
from __future__ import annotations

from copy import deepcopy

from jsonschema import Draft202012Validator, FormatChecker

VERSION = 1
CALL_EVENTS = frozenset({
    "call_started", "call_ended", "call_analyzed", "transfer_started",
    "transfer_completed", "transfer_failed", "voicemail_detected",
    "dtmf_received", "recording_ready",
})
EVENT_NAMES = CALL_EVENTS | {"batch_started", "batch_completed", "agent_published"}


def schema_for(event: str) -> dict:
    """Return a fresh schema so callers cannot mutate the canonical contract."""
    if event not in EVENT_NAMES:
        raise ValueError("Unknown customer webhook event")
    identity = "call_id" if event in CALL_EVENTS else (
        "batch_id" if event.startswith("batch_") else "agent_id"
    )
    properties = {identity: {"type": "string", "format": "uuid"}}
    required = [identity]
    if event in CALL_EVENTS:
        properties.update({
            "status": {"enum": ["ringing", "in_progress", "completed", "failed",
                                "no_answer", "cancelled", "transferred"]},
            "direction": {"enum": ["inbound", "outbound"]},
            "duration_seconds": {"type": "number", "minimum": 0},
        })
        required.extend(["status", "direction", "duration_seconds"])
    if event == "call_analyzed":
        properties["analysis_result_ids"] = {
            "type": "array", "maxItems": 100,
            "items": {"type": "string", "format": "uuid"},
        }
        required.append("analysis_result_ids")
    if event == "recording_ready":
        properties["recording_id"] = {"type": "string", "format": "uuid"}
        required.append("recording_id")
    if event == "agent_published":
        properties["version_id"] = {"type": "string", "format": "uuid"}
        required.append("version_id")
    return deepcopy({
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:voxdesk:webhooks:{event}:v{VERSION}",
        "type": "object", "additionalProperties": False,
        "required": required, "properties": properties,
    })


def validate_payload(event: str, payload: dict) -> None:
    """Raise on an unknown event, invalid identity or undocumented/PII field."""
    Draft202012Validator(schema_for(event), format_checker=FormatChecker()).validate(payload)


def matches_subscription(event: str, filters: list[str], payload: dict) -> bool:
    """Canonical filters plus explicit, status-aware legacy compatibility."""
    if not filters or event in filters:
        return True
    aliases = {
        "call.started": "call_started",
        "call.recording.ready": "recording_ready", "call.analysis.completed": "call_analyzed",
        "transfer.requested": "transfer_started", "transfer.completed": "transfer_completed",
        "call.transferred": "transfer_completed", "transfer.failed": "transfer_failed",
        "batch.started": "batch_started", "batch.completed": "batch_completed",
        "agent.published": "agent_published",
    }
    if any(aliases.get(value) == event for value in filters):
        return True
    if event == "call_started" and "call.answered" in filters:
        return payload.get("status") == "in_progress"
    if event == "call_ended":
        status = payload.get("status")
        return ("call.completed" in filters and status == "completed") or (
            "call.failed" in filters and status in {"failed", "no_answer", "cancelled"})
    return False
