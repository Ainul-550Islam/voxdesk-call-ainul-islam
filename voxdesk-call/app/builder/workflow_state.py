"""Workflow execution state machine — strict transitions."""
from __future__ import annotations

ALLOWED = {
    "created": {"queued", "cancelled"},
    "queued": {"running", "cancelled"},
    "running": {"paused", "retrying", "completed", "failed", "cancelled", "recovering"},
    "paused": {"running", "cancelled", "failed"},
    "retrying": {"running", "failed", "cancelled"},
    "recovering": {"running", "failed", "cancelled"},
    "completed": set(),
    "failed": {"retrying", "recovering"},
    "cancelled": set(),
}

TERMINAL = {"completed", "failed", "cancelled"}

def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in ALLOWED.get(from_state, set())

def is_terminal(state: str) -> bool:
    return state in TERMINAL
