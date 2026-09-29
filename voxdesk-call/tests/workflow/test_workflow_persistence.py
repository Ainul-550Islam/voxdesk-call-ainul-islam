"""Durable workflow persistence — repository/store tests."""

from app.builder.workflow_state import can_transition, is_terminal


def test_state_machine_basic():
    assert can_transition("created", "queued")
    assert not can_transition("completed", "running")
    assert is_terminal("completed")
