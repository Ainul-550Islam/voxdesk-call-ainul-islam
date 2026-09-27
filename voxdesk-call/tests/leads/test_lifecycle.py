"""The canonical lead status lifecycle (Batch 06).

``app/leads/lifecycle.py`` is the only legitimate lead-status state machine:
every mutation path in the product (API, workflow engine, AI tool, SMS
STOP/START, telephony) must go through it, transitions are validated against
the stored matrix, writes are compare-and-set, every accepted change appends
status history, and do-not-call is terminal — nothing silently reverses it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.agent.functions import FunctionHandlers
from app.channels.messaging import set_opt_out
from app.db.models import LeadStatus
from app.domain.workflow_models import (
    NodeType, WorkflowAction, WorkflowDefinition, WorkflowNode,
)
from app.leads import lifecycle
from app.leads.activities import history_for
from app.leads.exceptions import ClaimConflict, InvalidTransition
from app.services import workflow_service
from tests.conftest import make_call, make_lead

APP_ROOT = Path(__file__).resolve().parents[2] / "app"


# ------------------------------------------------------------ state matrix ---

VALID_PAIRS = sorted(
    (current.value, target.value)
    for current, targets in lifecycle._ALLOWED.items()
    for target in targets
)

INVALID_PAIRS = sorted(
    (current.value, target.value)
    for current in LeadStatus
    for target in LeadStatus
    if target not in lifecycle._ALLOWED[current]
)


async def _lead_at(db, tenant, status: LeadStatus):
    """A lead walked to `status` through legal transitions only."""
    index = [member.value for member in LeadStatus].index(status.value)
    lead = await make_lead(db, tenant, phone=f"+15550400{index:04d}")
    walk = {
        LeadStatus.NEW: [],
        LeadStatus.QUEUED: ["queued"],
        LeadStatus.CALLED: ["queued", "called"],
        LeadStatus.QUALIFIED: ["queued", "called", "qualified"],
        LeadStatus.UNQUALIFIED: ["queued", "called", "unqualified"],
        LeadStatus.FAILED: ["queued", "failed"],
        LeadStatus.DNC: ["do_not_call"],
    }[status]
    for step in walk:
        await lifecycle.transition(
            db, lead, step, reason="walk", source="test"
        )
    await db.commit()
    return lead


@pytest.mark.parametrize("current,target", VALID_PAIRS)
async def test_every_valid_transition_is_accepted(db, tenant_a, current, target):
    lead = await _lead_at(db, tenant_a, LeadStatus(current))
    before = len(await history_for(db, lead.tenant_id, lead.environment_id, lead.id))

    await lifecycle.transition(db, lead, target, reason="matrix", source="test")
    await db.commit()

    assert lifecycle.status_value(lead.status) == target
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert len(history) == before + 1
    assert history[-1].from_status == current
    assert history[-1].to_status == target
    assert history[-1].sequence == before + 1


@pytest.mark.parametrize("current,target", INVALID_PAIRS)
async def test_every_invalid_transition_is_refused(db, tenant_a, current, target):
    lead = await _lead_at(db, tenant_a, LeadStatus(current))
    with pytest.raises(InvalidTransition):
        await lifecycle.transition(db, lead, target, reason="matrix", source="test")
    await db.rollback()
    assert lifecycle.status_value(lead.status) == current


async def test_do_not_call_is_terminal(db, tenant_a):
    lead = await _lead_at(db, tenant_a, LeadStatus.DNC)
    for target in LeadStatus:
        with pytest.raises(InvalidTransition):
            await lifecycle.transition(
                db, lead, target.value, reason="revive", source="test"
            )
        await db.rollback()
    assert lead.status is LeadStatus.DNC


async def test_aliases_store_the_canonical_value(db, tenant_a):
    lead = await make_lead(db, tenant_a, phone="+15550400099")
    await lifecycle.transition(db, lead, "converted", reason="alias", source="test")
    await db.commit()
    assert lead.status is LeadStatus.QUALIFIED
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert history[-1].to_status == "qualified"
    assert "alias:converted" in history[-1].reason


async def test_compare_and_set_rejects_a_stale_belief(db, tenant_a):
    lead = await make_lead(db, tenant_a, phone="+15550400098")
    await lifecycle.transition(db, lead, "queued", reason="first", source="test")
    await db.commit()
    with pytest.raises(ClaimConflict):
        await lifecycle.transition(
            db, lead, "called", reason="stale", source="test", expected=LeadStatus.NEW
        )
    await db.rollback()
    await db.refresh(lead)  # the rollback expired the instance
    assert lead.status is LeadStatus.QUEUED


# ------------------------------------------------------ mutation paths use it ---

async def test_api_status_change_writes_history(db, tenant_a):
    from app.leads import service

    lead = await make_lead(db, tenant_a, phone="+15550400001")
    await service.change_status(
        db, tenant_id=tenant_a.id, lead_id=lead.id, target="qualified",
        reason="api test", actor_id=None,
    )
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert lead.status is LeadStatus.QUALIFIED
    assert [(h.from_status, h.to_status) for h in history] == [("new", "qualified")]
    assert history[-1].source == "api"


def _workflow(tenant_id: str, action: WorkflowAction, workflow_id: str) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=workflow_id, tenant_id=tenant_id, name=workflow_id, entry_node="act",
        nodes=(
            WorkflowNode(id="act", type=NodeType.ACTION, action=action, next="end"),
            WorkflowNode(id="end", type=NodeType.TERMINAL),
        ),
    )


async def test_workflow_status_action_writes_history(db, tenant_a):
    lead = await make_lead(db, tenant_a, phone="+15550400002")
    definition = _workflow(
        str(tenant_a.id), WorkflowAction("update_lead_status", {"status": "qualified"}),
        "wf-lifecycle-qualified",
    )
    workflow_service.create_workflow(str(tenant_a.id), definition)
    workflow_service.publish_workflow(str(tenant_a.id), definition.id)
    await workflow_service.execute_workflow(
        str(tenant_a.id), definition.id, {"lead_id": str(lead.id)}, session=db
    )
    await db.commit()
    await db.refresh(lead)

    assert lead.status is LeadStatus.QUALIFIED
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert history[-1].from_status == "new" and history[-1].to_status == "qualified"
    assert history[-1].source == "workflow"


async def test_workflow_cannot_reverse_do_not_call(db, tenant_a):
    lead = await _lead_at(db, tenant_a, LeadStatus.DNC)
    definition = _workflow(
        str(tenant_a.id), WorkflowAction("update_lead_status", {"status": "qualified"}),
        "wf-lifecycle-dnc",
    )
    workflow_service.create_workflow(str(tenant_a.id), definition)
    workflow_service.publish_workflow(str(tenant_a.id), definition.id)
    execution = await workflow_service.execute_workflow(
        str(tenant_a.id), definition.id, {"lead_id": str(lead.id)}, session=db
    )
    await db.commit()
    await db.refresh(lead)

    assert lead.status is LeadStatus.DNC  # terminal, never silently reversed
    step = next(s for s in execution.steps if s.node_id == "act")
    assert step.status == "failed"
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert history[-1].to_status == "do_not_call"  # no new rows after the DNC


async def test_ai_tool_dnc_goes_through_consent_and_history(db, tenant_a):
    call = await make_call(db, tenant_a)  # from_number="+15551230000"
    handlers = FunctionHandlers(db, tenant_a, call)
    result = await handlers.mark_do_not_call(reason="customer asked")
    await db.commit()

    assert result["ok"] is True
    from sqlalchemy import select

    from app.db.models import Lead

    lead = (
        await db.execute(
            select(Lead).where(
                Lead.tenant_id == tenant_a.id, Lead.phone == call.from_number
            )
        )
    ).scalar_one()
    assert lead.status is LeadStatus.DNC
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    # canonical creation history + the consent-driven DNC transition
    assert [(h.from_status, h.to_status) for h in history] == [
        (None, "new"), ("new", "do_not_call"),
    ]
    assert history[0].source == "agent_tool"
    assert history[-1].source == "consent"
    assert "consent:voice:denied" in history[-1].reason


async def test_messaging_stop_sets_dnc_and_start_never_reverses(db, tenant_a):
    phone = "+15550400003"
    await set_opt_out(db, tenant_a, phone, True)
    await db.commit()

    from sqlalchemy import select

    from app.db.models import Lead

    lead = (
        await db.execute(
            select(Lead).where(Lead.tenant_id == tenant_a.id, Lead.phone == phone)
        )
    ).scalar_one()
    assert lead.status is LeadStatus.DNC
    history = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert history[-1].to_status == "do_not_call"
    assert history[-1].source == "consent"

    # START records an SMS grant only; the voice DNC stays terminal
    await set_opt_out(db, tenant_a, phone, False)
    await db.commit()
    await db.refresh(lead)
    assert lead.status is LeadStatus.DNC
    after = await history_for(db, lead.tenant_id, lead.environment_id, lead.id)
    assert len(after) == len(history)  # no status transition happened


# ------------------------------------------------------- direct-write audit ---

def test_no_direct_lead_status_assignments_outside_the_lifecycle():
    """Requirement: the product code contains no ``lead.status = LeadStatus.X``
    style writes. The state machine module mirrors the persisted value onto
    the in-memory object *after* its guarded UPDATE (``lead.status = target``)
    and the atomic dial claim updates through ``.values(...)`` — both are the
    canonical implementation, not bypasses."""
    direct = re.compile(r"^\s*\w+\.status\s*=\s*LeadStatus\.", re.MULTILINE)
    offenders = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for match in direct.finditer(text):
            line_no = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(APP_ROOT.parent)}:{line_no}")
    assert offenders == []


def test_only_the_lifecycle_mirrors_status_onto_the_model():
    """The single in-memory ``lead.status = target`` mirror lives in
    lifecycle.transition, immediately after the compare-and-set UPDATE."""
    mirror = re.compile(r"^\s*lead\.status\s*=\s*target\s*$", re.MULTILINE)
    hits = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        if mirror.search(path.read_text(encoding="utf-8")):
            hits.append(str(path.relative_to(APP_ROOT.parent)))
    assert hits == ["app/leads/lifecycle.py"]
