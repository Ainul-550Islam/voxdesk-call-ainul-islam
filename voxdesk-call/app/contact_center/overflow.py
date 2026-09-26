"""Overflow decisions.

The policy is the JSON object already stored on the queue. Overflow never
invents an agent. Callback is requested only after a durable job exists.
Escalate is accepted only after telephony accepts the transfer request.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.contact_center.exceptions import QueueUnavailable
from app.contact_center.models import Queue, QueueEntry

KINDS = ("none", "callback", "escalate", "voicemail")


@dataclass(frozen=True)
class OverflowDecision:
    action: str
    detail: str = ""


def normalize_policy(raw: dict | str | None) -> dict:
    if raw is None or raw == "":
        raw = {"kind": "none"}
    if isinstance(raw, str):
        raw = {"kind": raw}
    if not isinstance(raw, dict):
        raise QueueUnavailable("overflow policy must be an object")
    kind = str(raw.get("kind") or "none").strip().lower()
    if kind not in KINDS:
        raise QueueUnavailable(f"Unknown overflow policy {kind}")
    try:
        wait = int(raw.get("max_wait_seconds") or 0)
        depth = int(raw.get("max_waiting") or 0)
    except (TypeError, ValueError) as exc:
        raise QueueUnavailable("overflow limits must be integers") from exc
    number = str(raw.get("escalation_number") or "").strip()
    if kind == "escalate" and not number:
        raise QueueUnavailable("escalate requires an escalation number")
    if wait < 0 or depth < 0:
        raise QueueUnavailable("overflow limits must be >= 0")
    return {
        "kind": kind,
        "max_wait_seconds": wait,
        "max_waiting": depth,
        "escalation_number": number,
    }


def policy_of(queue: Queue) -> dict:
    """Read a stored policy. A missing escalation number stays a deny, not a raise."""
    raw = queue.overflow_policy or {}
    if not isinstance(raw, dict):
        raw = {}
    kind = str(raw.get("kind") or "none").strip().lower()
    if kind not in KINDS:
        kind = "none"
    try:
        wait = int(raw.get("max_wait_seconds") or 0)
        depth = int(raw.get("max_waiting") or 0)
    except (TypeError, ValueError):
        wait, depth = 0, 0
    return {
        "kind": kind,
        "max_wait_seconds": max(0, wait),
        "max_waiting": max(0, depth),
        "escalation_number": str(raw.get("escalation_number") or "").strip(),
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def due(queue: Queue, entry: QueueEntry, *, now: datetime, waiting: int) -> bool:
    if not queue.enabled:
        return True
    policy = policy_of(queue)
    if policy["max_wait_seconds"] > 0:
        waited = (now - _as_utc(entry.enqueued_at)).total_seconds()
        if waited >= policy["max_wait_seconds"]:
            return True
    if policy["max_waiting"] > 0 and waiting > policy["max_waiting"]:
        return True
    return False


def decide(queue: Queue) -> OverflowDecision:
    policy = policy_of(queue)
    kind = policy["kind"]
    if kind == "callback":
        return OverflowDecision("callback")
    if kind == "escalate":
        if not policy["escalation_number"]:
            return OverflowDecision("escalate_unavailable", "missing escalation number")
        return OverflowDecision("escalate", policy["escalation_number"])
    if kind == "voicemail":
        return OverflowDecision("voicemail")
    return OverflowDecision("leave_queued")
