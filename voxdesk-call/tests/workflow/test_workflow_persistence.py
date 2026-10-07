from __future__ import annotations

from app.builder.workflow_schemas import (
    ExecutionCreate,
    ExecutionRead,
    WorkflowCreate,
    WorkflowVersionRead,
)
from app.builder.workflow_state import ALLOWED, TERMINAL, can_transition, is_terminal
from app.builder.workflow_store import WorkflowStore


def test_state_machine() -> None:
    assert can_transition("created", "running")
    assert can_transition("running", "completed")
    assert not can_transition("completed", "running")
    assert is_terminal("completed")
    assert is_terminal("cancelled")


def test_workflow_schemas_strict_validation() -> None:
    wf = WorkflowCreate(
        name="Inbound Support Flow",
        slug="inbound-support-flow",
        description="Tier-1 triage and order lookup",
    )
    assert wf.name == "Inbound Support Flow"
    assert wf.slug == "inbound-support-flow"

    version = WorkflowVersionRead(
        id="ver-001",
        version_number=3,
        status="published",
        checksum="abc123def456",
    )
    assert version.version_number == 3
    assert version.status == "published"

    exec_create = ExecutionCreate(
        workflow_version_id="ver-001",
        input_payload={"caller": "+15551234567", "locale": "en-US"},
    )
    assert exec_create.input_payload["locale"] == "en-US"

    exec_read = ExecutionRead(
        id="exec-001",
        status="running",
        current_node_id="verify_caller",
    )
    assert exec_read.current_node_id == "verify_caller"


def test_workflow_store_protocol_surface() -> None:
    required_methods = {
        "create_workflow",
        "get_workflow",
        "list_workflows",
        "update_workflow",
        "create_version",
        "get_version",
        "create_execution",
        "get_execution",
        "transition_execution",
        "update_execution_state",
        "heartbeat_execution",
        "recover_executions",
        "list_executions",
    }
    for method in required_methods:
        assert hasattr(WorkflowStore, method), f"WorkflowStore missing {method}"
    assert TERMINAL.issubset(set(ALLOWED.keys()))
