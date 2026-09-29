"""Workflow execution state machine used by the durable repository."""

from __future__ import annotations

ALLOWED = {
    "created": {"queued", "running", "cancelled", "failed"},
    "queued": {"running", "cancelled", "failed"},
    "running": {
        "paused",
        "retrying",
        "completed",
        "failed",
        "cancelled",
        "recovering",
        "waiting_approval",
        "timed_out",
    },
    "paused": {"running", "cancelled", "failed"},
    "retrying": {"running", "failed", "cancelled"},
    "recovering": {"running", "failed", "cancelled"},
    "waiting_approval": {"running", "completed", "failed", "cancelled"},
    "completed": set(),
    "failed": {"retrying", "recovering"},
    "cancelled": set(),
    "timed_out": set(),
}

TERMINAL = {"completed", "failed", "cancelled", "timed_out"}


def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in ALLOWED.get(from_state, set())


def is_terminal(state: str) -> bool:
    return state in TERMINAL
