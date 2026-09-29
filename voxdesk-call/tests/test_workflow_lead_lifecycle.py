"""Workflow engine → lead lifecycle (Batch 06).

``workflow_service._lead_status_change`` no longer assigns ``lead.status``
directly. Every workflow-driven status change goes through the canonical
``app.leads.lifecycle.transition``: the stored matrix is enforced (a
do-not-call is never silently reversed), status history is written with
source ``workflow``, failures come back as ``failed`` steps instead of
exceptions, and the workflow engine's own semantics (registry, publishing,
idempotent executions, retries) are unchanged.
"""

from __future__ import annotations

import pytest

from app.core.errors import NotFoundError
from app.db.models import LeadStatus
from app.domain.workflow_models import (
    ExecutionStatus,
    NodeType,
    WorkflowAction,
    WorkflowDefinition,
    WorkflowNode,
)
from app.leads import lifecycle
from app.leads.activities import history_for
from app.services import workflow_service
from tests.conftest import make_lead


# ----------------------------------------------------------------- helpers ---


def _lead_workflow(tenant_id: str, workflow_id: str, action: WorkflowAction) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=workflow_id,
        tenant_id=tenant_id,
        name=workflow_id,
        entry_node="act",
        nodes=(
            WorkflowNode(id="act", type=NodeType.ACTION, action=action, next="end"),
            WorkflowNode(id="end", type=NodeType.TERMINAL),
        ),
    )


async def _activate(tenant_id: str, definition: WorkflowDefinition, session) -> None:
    await workflow_service.create_workflow(tenant_id, definition, session=session)
    await workflow_service.publish_workflow(tenant_id, definition.id, session=session)


def _step(execution, node_id: str = "act"):
    return next(step for step in execution.steps if step.node_id == node_id)


# ------------------------------------------------------------ happy paths ---


async def test_update_lead_status_executes_with_history(db, tenant_a):
    lead = await make_lead(db, tenant_a, phone="+15551100001")
    definition = _lead_workflow(
        str(tenant_a.id),
        "wf-b06-qualified",
        WorkflowAction("update_lead_status", {"status": "qualified"}),
    )
    await _activate(str(tenant_a.id), definition, db)

    execution = await workflow_service.execute_workflow(
        str(tenant_a.id), definition.id, {"lead_id": str(lead.id)}, session=db
    )
    await db.commit()
    await db.refresh(lead)

    assert execution.status is ExecutionStatus.COMPLETED
    assert _step(execution).status == "executed"
    assert lead.status is LeadStatus.QUALIFIED

    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert [(h.from_status, h.to_status) for h in history] == [("new", "qualified")]
    assert history[0].source == "workflow"
    assert history[0].reason == "workflow_action"


async def test_apply_dnc_executes_with_history(db, tenant_a):
    lead = await make_lead(db, tenant_a, phone="+15551100002")
    definition = _lead_workflow(
        str(tenant_a.id),
        "wf-b06-dnc",
        WorkflowAction("apply_dnc", {}),
    )
    await _activate(str(tenant_a.id), definition, db)

    execution = await workflow_service.execute_workflow(
        str(tenant_a.id), definition.id, {"lead_id": str(lead.id)}, session=db
    )
    await db.commit()
    await db.refresh(lead)

    assert execution.status is ExecutionStatus.COMPLETED
    assert lead.status is LeadStatus.DNC
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert [(h.from_status, h.to_status) for h in history] == [("new", "do_not_call")]
    assert history[0].source == "workflow"


async def test_status_sequence_keeps_continuous_history(db, tenant_a):
    lead = await make_lead(db, tenant_a, phone="+15551100003")
    for workflow_id, status in (
        ("wf-b06-seq-1", "queued"),
        ("wf-b06-seq-2", "called"),
        ("wf-b06-seq-3", "qualified"),
    ):
        definition = _lead_workflow(
            str(tenant_a.id),
            workflow_id,
            WorkflowAction("update_lead_status", {"status": status}),
        )
        await _activate(str(tenant_a.id), definition, db)
        await workflow_service.execute_workflow(
            str(tenant_a.id), definition.id, {"lead_id": str(lead.id)}, session=db
        )
        await db.commit()

    await db.refresh(lead)
    assert lead.status is LeadStatus.QUALIFIED
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert [(h.from_status, h.to_status) for h in history] == [
        ("new", "queued"),
        ("queued", "called"),
        ("called", "qualified"),
    ]
    assert [h.sequence for h in history] == [1, 2, 3]


# ------------------------------------------------------------- guard rails ---


async def test_workflow_cannot_reverse_a_do_not_call(db, tenant_a):
    lead = await make_lead(db, tenant_a, phone="+15551100004")
    await lifecycle.transition(
        db, lead, "do_not_call", reason="consent:voice:denied", source="consent"
    )
    await db.commit()
    before = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)

    definition = _lead_workflow(
        str(tenant_a.id),
        "wf-b06-revive",
        WorkflowAction("update_lead_status", {"status": "qualified"}),
    )
    await _activate(str(tenant_a.id), definition, db)
    execution = await workflow_service.execute_workflow(
        str(tenant_a.id), definition.id, {"lead_id": str(lead.id)}, session=db
    )
    await db.commit()
    await db.refresh(lead)

    assert execution.status is ExecutionStatus.FAILED
    assert _step(execution).status == "failed"
    assert lead.status is LeadStatus.DNC  # terminal — never silently reversed
    after = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert len(after) == len(before)  # the refused change wrote nothing


async def test_workflow_cannot_make_an_illegal_transition(db, tenant_a):
    lead = await make_lead(db, tenant_a, phone="+15551100005")
    await lifecycle.transition(db, lead, "qualified", reason="test", source="api")
    await db.commit()

    definition = _lead_workflow(
        str(tenant_a.id),
        "wf-b06-illegal",
        WorkflowAction("update_lead_status", {"status": "queued"}),
    )
    await _activate(str(tenant_a.id), definition, db)
    execution = await workflow_service.execute_workflow(
        str(tenant_a.id), definition.id, {"lead_id": str(lead.id)}, session=db
    )
    await db.commit()
    await db.refresh(lead)

    # qualified -> queued is not in the stored matrix
    assert execution.status is ExecutionStatus.FAILED
    assert lead.status is LeadStatus.QUALIFIED


async def test_unknown_status_string_fails_the_step(db, tenant_a):
    lead = await make_lead(db, tenant_a, phone="+15551100006")
    definition = _lead_workflow(
        str(tenant_a.id),
        "wf-b06-unknown",
        WorkflowAction("update_lead_status", {"status": "banana"}),
    )
    await _activate(str(tenant_a.id), definition, db)
    execution = await workflow_service.execute_workflow(
        str(tenant_a.id), definition.id, {"lead_id": str(lead.id)}, session=db
    )
    await db.commit()
    await db.refresh(lead)

    assert execution.status is ExecutionStatus.FAILED
    assert _step(execution).status == "failed"
    assert lead.status is LeadStatus.NEW


async def test_invalid_and_foreign_lead_ids_fail_closed(db, tenant_a, tenant_b):
    foreign_lead = await make_lead(db, tenant_b, phone="+15551100007")
    definition = _lead_workflow(
        str(tenant_a.id),
        "wf-b06-missing",
        WorkflowAction("update_lead_status", {"status": "qualified"}),
    )
    await _activate(str(tenant_a.id), definition, db)

    invalid = await workflow_service.execute_workflow(
        str(tenant_a.id), definition.id, {"lead_id": "not-a-uuid"}, session=db
    )
    assert _step(invalid).status == "failed"
    assert "invalid lead_id" in _step(invalid).detail

    foreign = await workflow_service.execute_workflow(
        str(tenant_a.id),
        definition.id,
        {"lead_id": str(foreign_lead.id)},
        session=db,
    )
    assert _step(foreign).status == "failed"
    assert "lead not found in tenant" in _step(foreign).detail


async def test_missing_lead_id_fails_closed(db, tenant_a):
    definition = _lead_workflow(
        str(tenant_a.id),
        "wf-b06-noid",
        WorkflowAction("update_lead_status", {"status": "qualified"}),
    )
    await _activate(str(tenant_a.id), definition, db)
    execution = await workflow_service.execute_workflow(
        str(tenant_a.id), definition.id, {}, session=db
    )
    assert _step(execution).status == "failed"
    assert "requires a tenant lead" in _step(execution).detail


# ----------------------------------------------------- engine invariants ---


async def test_execution_idempotency_is_unchanged(db, tenant_a):
    """Replaying the same trigger must not run the status change twice: the
    idempotency key still short-circuits, so history stays a single row."""
    lead = await make_lead(db, tenant_a, phone="+15551100008")
    definition = _lead_workflow(
        str(tenant_a.id),
        "wf-b06-idem",
        WorkflowAction("update_lead_status", {"status": "qualified"}),
    )
    await _activate(str(tenant_a.id), definition, db)

    payload = {"lead_id": str(lead.id)}
    first = await workflow_service.execute_workflow(
        str(tenant_a.id), definition.id, payload, session=db
    )
    second = await workflow_service.execute_workflow(
        str(tenant_a.id), definition.id, payload, session=db
    )
    await db.commit()

    assert first.id == second.id  # same execution returned
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert len(history) == 1


async def test_workflow_service_tenant_isolation_is_unchanged(db, tenant_a, tenant_b):
    definition = _lead_workflow(
        str(tenant_a.id),
        "wf-b06-isolation",
        WorkflowAction("update_lead_status", {"status": "qualified"}),
    )
    await workflow_service.create_workflow(str(tenant_a.id), definition, session=db)
    with pytest.raises(NotFoundError):
        await workflow_service.get_workflow(str(tenant_b.id), definition.id, session=db)
