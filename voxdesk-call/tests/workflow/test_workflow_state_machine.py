from __future__ import annotations

import pytest

from app.builder.workflow_state import ALLOWED, TERMINAL, can_transition, is_terminal


def test_all_states_defined() -> None:
    assert "created" in ALLOWED
    assert "running" in ALLOWED
    assert "completed" in ALLOWED
    assert len(ALLOWED["completed"]) == 0


@pytest.mark.parametrize(
    ("from_state", "to_state", "expected"),
    [
        ("created", "queued", True),
        ("created", "running", True),
        ("created", "completed", False),
        ("queued", "running", True),
        ("running", "paused", True),
        ("running", "waiting_approval", True),
        ("running", "recovering", True),
        ("running", "completed", True),
        ("running", "timed_out", True),
        ("paused", "running", True),
        ("paused", "completed", False),
        ("waiting_approval", "completed", True),
        ("waiting_approval", "running", True),
        ("failed", "retrying", True),
        ("failed", "recovering", True),
        ("failed", "completed", False),
        ("completed", "running", False),
        ("cancelled", "running", False),
        ("timed_out", "running", False),
        ("unknown_state", "running", False),
    ],
)
def test_transition_matrix(from_state: str, to_state: str, expected: bool) -> None:
    assert can_transition(from_state, to_state) is expected


def test_terminal_states_and_recovery_paths() -> None:
    assert TERMINAL == {"completed", "failed", "cancelled", "timed_out"}
    for state in ("completed", "cancelled", "timed_out"):
        assert is_terminal(state) is True
        assert ALLOWED[state] == set()
    # failed is marked terminal for normal flow but explicitly permits retry/recovery
    assert is_terminal("failed") is True
    assert ALLOWED["failed"] == {"retrying", "recovering"}
