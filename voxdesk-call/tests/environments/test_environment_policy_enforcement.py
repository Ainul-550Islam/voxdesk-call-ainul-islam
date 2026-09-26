"""Production, staging and development write policy."""

from __future__ import annotations

import uuid

from app.db.models import Environment, UserRole
from app.environments.policy_inheritance import weakens
from app.environments.resource_policy import assert_parent_not_weakened, write_allowed
from app.resources.lifecycle import read_allowed
import pytest
from app.resources.exceptions import InvalidResourceTransition


def _environment(kind: str, status: str = "active") -> Environment:
    return Environment(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), name=kind, slug=kind, kind=kind,
        status=status, is_default=kind == "production",
    )


def test_production_rejects_an_unauthorized_write_and_accepts_an_admin():
    production = _environment("production")
    assert write_allowed(production, UserRole.VIEWER, action="create", rbac_allows=False) is False
    assert write_allowed(production, UserRole.ADMIN, action="create", rbac_allows=True) is True
    assert write_allowed(production, UserRole.ADMIN, action="archive", rbac_allows=True) is False
    assert write_allowed(production, UserRole.OWNER, action="archive", rbac_allows=True) is True


def test_staging_and_development_follow_their_role_floors():
    staging = _environment("staging")
    development = _environment("development")
    assert write_allowed(staging, UserRole.ADMIN, action="create", rbac_allows=True) is True
    assert write_allowed(staging, UserRole.MANAGER, action="create", rbac_allows=True) is False
    assert write_allowed(development, UserRole.MANAGER, action="create", rbac_allows=True) is True
    assert write_allowed(development, UserRole.AGENT, action="create", rbac_allows=True) is False


def test_suspended_and_archived_block_writes_and_reads_follow_policy():
    suspended = _environment("production", "suspended")
    archived = _environment("staging", "archived")
    assert write_allowed(suspended, UserRole.OWNER, action="create", rbac_allows=True) is False
    assert write_allowed(archived, UserRole.OWNER, action="create", rbac_allows=True) is False
    assert read_allowed(suspended, UserRole.VIEWER) is True
    assert read_allowed(archived, UserRole.VIEWER) is False
    assert read_allowed(archived, UserRole.ADMIN) is True


def test_a_child_environment_cannot_weaken_a_parent_control():
    parent = {"mfa_required": True}
    assert weakens(parent, {"mfa_required": False}) is True
    with pytest.raises(InvalidResourceTransition):
        assert_parent_not_weakened(parent, {"mfa_required": False})


@pytest.mark.asyncio
async def test_an_automation_worker_cannot_execute_into_another_environment():
    from app.domain.automation_models import (
        AutomationDefinition,
        AutomationStatus,
        ExecutionPolicy,
        TriggerEvent,
    )
    from app.domain.workflow_models import WorkflowAction
    from app.services import automation_service

    tenant_id = str(uuid.uuid4())
    bound = str(uuid.uuid4())
    automation = AutomationDefinition(
        id="auto-scope",
        tenant_id=tenant_id,
        name="scoped",
        event=TriggerEvent.LEAD_CREATED,
        actions=(WorkflowAction("enqueue_notification", {"template_id": "none"}),),
        policy=ExecutionPolicy(max_per_event=1),
        status=AutomationStatus.ENABLED,
        environment_id=bound,
    )
    denied = await automation_service.execute_automation(
        tenant_id, automation, "evt-scope", {"target_environment_id": str(uuid.uuid4())},
    )
    assert denied.status == "cancelled"
    assert denied.last_error == "cross_environment_denied"
    allowed = await automation_service.execute_automation(
        tenant_id, automation, "evt-scope-ok", {"environment_id": bound},
    )
    assert allowed.last_error != "cross_environment_denied"
