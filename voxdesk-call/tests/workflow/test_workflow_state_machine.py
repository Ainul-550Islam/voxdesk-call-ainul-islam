"""State machine transitions."""
from app.builder.workflow_state import can_transition, ALLOWED

def test_all_valid_from_created():
    assert "queued" in ALLOWED.get("created", set())

def test_terminal_has_no_out():
    assert ALLOWED.get("completed") == set()
