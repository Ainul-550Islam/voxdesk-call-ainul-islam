"""Durable workflow persistence and recovery invariants.

These tests use the shared SQLAlchemy fixtures and service, rather than the
old process-local workflow registry. They deliberately open separate sessions
for reads and duplicate execution attempts.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.builder.workflow_repository import WorkflowRepository
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.db.models import Base, WorkflowExecution as WorkflowExecutionRow
from app.domain.workflow_models import (
    Condition,
    ExecutionStatus,
    NodeType,
    WorkflowAction,
    WorkflowDefinition,
    WorkflowNode,
)
from app.services import workflow_service
from tests.conftest import make_tenant


def _terminal(tenant_id: str, workflow_id: str = "durable-terminal") -> WorkflowDefinition:
    return WorkflowDefinition(
        id=workflow_id,
        tenant_id=tenant_id,
        name=workflow_id,
        entry_node="end",
        nodes=(WorkflowNode(id="end", type=NodeType.TERMINAL),),
    )


def _branch(tenant_id: str, workflow_id: str = "durable-branch") -> WorkflowDefinition:
    return WorkflowDefinition(
        id=workflow_id,
        tenant_id=tenant_id,
        name=workflow_id,
        entry_node="gate",
        nodes=(
            WorkflowNode(
                id="gate",
                type=NodeType.CONDITION,
                condition=Condition(field="ok", operator="eq", value=True),
                next="end",
            ),
            WorkflowNode(id="end", type=NodeType.TERMINAL),
        ),
    )


def _approval(tenant_id: str, workflow_id: str = "durable-approval") -> WorkflowDefinition:
    return WorkflowDefinition(
        id=workflow_id,
        tenant_id=tenant_id,
        name=workflow_id,
        entry_node="approval",
        nodes=(
            WorkflowNode(
                id="approval",
                type=NodeType.APPROVAL,
                approver_role="manager",
                next="end",
            ),
            WorkflowNode(id="end", type=NodeType.TERMINAL),
        ),
    )


async def _published(db, tenant_id: str, definition: WorkflowDefinition) -> None:
    await workflow_service.create_workflow(tenant_id, definition, session=db)
    await workflow_service.publish_workflow(tenant_id, definition.id, session=db)


async def test_workflow_persists_across_sessions(db, sessionmaker_, tenant_a):
    tenant_id = str(tenant_a.id)
    definition = _terminal(tenant_id, "across-sessions")
    await _published(db, tenant_id, definition)

    async with sessionmaker_() as second_session:
        loaded = await workflow_service.get_workflow(
            tenant_id, definition.id, session=second_session
        )
        executions = await workflow_service.execute_workflow(
            tenant_id, definition.id, {"source": "second-session"}, session=second_session
        )
        assert loaded.id == definition.id
        assert executions.status is ExecutionStatus.COMPLETED

    async with sessionmaker_() as third_session:
        history = await workflow_service.execution_history(
            tenant_id, definition.id, session=third_session
        )
        assert len(history) == 1
        assert history[0].status is ExecutionStatus.COMPLETED


async def test_versioning_keeps_published_graph_immutable(db, tenant_a):
    tenant_id = str(tenant_a.id)
    original = _terminal(tenant_id, "immutable-published")
    await _published(db, tenant_id, original)
    repo = WorkflowRepository(db)
    workflow_row = await repo.get_workflow(tenant_a.id, original.id)
    old_version_id = workflow_row["published_version_id"]

    draft = _branch(tenant_id, original.id)
    await workflow_service.version_workflow(tenant_id, original.id, draft, session=db)
    current = await repo.get_workflow(tenant_a.id, original.id)
    assert current["published_version_id"] == old_version_id
    assert current["current_version_id"] != old_version_id

    old_version = await repo.get_version(old_version_id, tenant_id=tenant_a.id)
    assert old_version["graph_config"]["entry_node"] == "end"
    # Execution must use the published version, not the mutable current draft.
    execution = await workflow_service.execute_workflow(tenant_id, original.id, {}, session=db)
    assert execution.status is ExecutionStatus.COMPLETED
    assert [step.node_id for step in execution.steps] == ["end"]

    versions = await workflow_service.version_history(tenant_id, original.id, session=db)
    assert {version.version for version in versions} == {1, 2}


async def test_illegal_transition_and_optimistic_stale_write_are_rejected(db, tenant_a):
    tenant_id = str(tenant_a.id)
    definition = _terminal(tenant_id, "state-guards")
    await _published(db, tenant_id, definition)
    execution = await workflow_service.execute_workflow(tenant_id, definition.id, {}, session=db)
    repo = WorkflowRepository(db)
    row = await repo.get_execution(execution.id, tenant_id=tenant_a.id)
    with pytest.raises(BadRequestError, match="cannot move execution"):
        await repo.transition_execution(
            execution.id,
            "running",
            tenant_id=tenant_a.id,
            expected_version=row["concurrency_version"],
        )

    # Use a waiting execution for the stale optimistic-write assertion.
    running_definition = _approval(tenant_id, "heartbeat-guards")
    await _published(db, tenant_id, running_definition)
    waiting = await workflow_service.execute_workflow(
        tenant_id, running_definition.id, {}, session=db
    )
    running_row = await repo.get_execution(waiting.id, tenant_id=tenant_a.id)
    first = await repo.heartbeat_execution(
        waiting.id,
        tenant_id=tenant_a.id,
        expected_version=running_row["concurrency_version"],
    )
    with pytest.raises(ConflictError, match="heartbeat lost"):
        await repo.heartbeat_execution(
            waiting.id,
            tenant_id=tenant_a.id,
            expected_version=running_row["concurrency_version"],
        )
    assert first["concurrency_version"] == running_row["concurrency_version"] + 1


async def test_tenant_isolation_applies_to_workflows_versions_and_executions(
    db, tenant_a, tenant_b
):
    definition = _terminal(str(tenant_a.id), "tenant-bound")
    await _published(db, str(tenant_a.id), definition)
    repo = WorkflowRepository(db)
    row = await repo.get_workflow(tenant_a.id, definition.id)
    execution = await workflow_service.execute_workflow(
        str(tenant_a.id), definition.id, {}, session=db
    )
    assert await repo.get_workflow(tenant_b.id, definition.id) is None
    assert await repo.get_version(row["published_version_id"], tenant_id=tenant_b.id) is None
    assert await repo.get_execution(execution.id, tenant_id=tenant_b.id) is None
    with pytest.raises(NotFoundError):
        await workflow_service.inspect_execution(str(tenant_b.id), execution.id, session=db)


async def test_explicit_idempotency_replay_conflict_and_rollback(db, tenant_a):
    tenant_id = str(tenant_a.id)
    tenant_uuid = tenant_a.id
    definition = _terminal(tenant_id, "explicit-idempotency")
    await _published(db, tenant_id, definition)
    first = await workflow_service.execute_workflow(
        tenant_id,
        definition.id,
        {"value": 1},
        session=db,
        idempotency_key="request-1",
    )
    replay = await workflow_service.execute_workflow(
        tenant_id,
        definition.id,
        {"value": 1},
        session=db,
        idempotency_key="request-1",
    )
    assert replay.id == first.id
    with pytest.raises(ConflictError, match="different request"):
        await workflow_service.execute_workflow(
            tenant_id,
            definition.id,
            {"value": 2},
            session=db,
            idempotency_key="request-1",
        )
    repo = WorkflowRepository(db)
    assert len(await repo.list_executions(tenant_uuid, definition.id)) == 1
    # The failed conflict rolls back its transaction; the session remains usable.
    another = await workflow_service.execute_workflow(
        tenant_id,
        definition.id,
        {"value": 3},
        session=db,
        idempotency_key="request-2",
    )
    assert another.id != first.id


async def test_concurrent_duplicate_requests_create_one_execution(tmp_path):
    # The shared fixture intentionally uses an in-memory SQLite connection.
    # Use a file-backed database here so two AsyncSessions have independent
    # connections and exercise the unique reservation race realistically.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'workflow.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as setup_session:
        tenant = await make_tenant(setup_session, "Concurrent Tenant")
        tenant_id = str(tenant.id)
        definition = _terminal(tenant_id, "concurrent-idempotency")
        await _published(setup_session, tenant_id, definition)

    async def invoke():
        async with maker() as session:
            return await workflow_service.execute_workflow(
                tenant_id,
                definition.id,
                {"same": True},
                session=session,
                idempotency_key="concurrent-key",
            )

    results = await asyncio.gather(invoke(), invoke())
    assert results[0].id == results[1].id
    async with maker() as verify_session:
        rows = await WorkflowRepository(verify_session).list_executions(tenant.id, definition.id)
        assert len(rows) == 1
    await engine.dispose()


async def test_checkpoint_recovery_resumes_at_persisted_node(db, tenant_a):
    tenant_id = str(tenant_a.id)
    definition = _branch(tenant_id, "checkpoint-recovery")
    await _published(db, tenant_id, definition)
    repo = WorkflowRepository(db)
    workflow = await repo.get_workflow(tenant_a.id, definition.id)
    execution = await repo.create_execution(
        workflow["db_id"],
        workflow["published_version_id"],
        tenant_a.id,
        {"payload": {"ok": True}, "summary": {"ok": True}, "idempotency_key": "recovery"},
        idempotency_key="recovery",
        request_fingerprint=workflow_service.request_fingerprint({"ok": True}),
        current_node_id="gate",
    )
    await db.commit()
    checkpoint = {
        "steps": [
            {
                "node_id": "gate",
                "status": "executed",
                "detail": "saved",
                "attempt": 1,
                "at": "saved",
            }
        ]
    }
    await repo.transition_execution(
        execution["id"],
        "running",
        current_node_id="end",
        checkpoint=checkpoint,
        tenant_id=tenant_a.id,
    )
    await db.commit()
    await db.execute(
        update(WorkflowExecutionRow)
        .where(WorkflowExecutionRow.id == execution["db_id"])
        .values(last_heartbeat_at=datetime.now(timezone.utc) - timedelta(hours=1))
    )
    await db.commit()

    recovered = await workflow_service.recover_executions(
        tenant_id, session=db, stale_after_seconds=1
    )
    assert len(recovered) == 1
    assert recovered[0].status is ExecutionStatus.COMPLETED
    assert [step.node_id for step in recovered[0].steps] == ["gate", "end"]
    assert [step.node_id for step in recovered[0].steps].count("gate") == 1
    stored = await repo.get_execution(execution["id"], tenant_id=tenant_a.id)
    assert stored["status"] == ExecutionStatus.COMPLETED.value
    assert stored["recovery_count"] == 1


async def test_approval_resume_uses_persisted_checkpoint(db, tenant_a):
    tenant_id = str(tenant_a.id)
    definition = _approval(tenant_id)
    await _published(db, tenant_id, definition)
    waiting = await workflow_service.execute_workflow(tenant_id, definition.id, {}, session=db)
    assert waiting.status is ExecutionStatus.WAITING_APPROVAL
    approved = await workflow_service.approve_execution(tenant_id, waiting.id, session=db)
    assert approved.status is ExecutionStatus.COMPLETED
    assert [step.node_id for step in approved.steps] == ["approval", "end"]
    assert approved.steps[0].status == "executed"


async def test_nonterminal_path_is_not_reported_as_completed(db, tenant_a):
    tenant_id = str(tenant_a.id)
    definition = WorkflowDefinition(
        id="nonterminal-path",
        tenant_id=tenant_id,
        name="nonterminal-path",
        entry_node="start",
        nodes=(
            WorkflowNode(id="start", type=NodeType.TRIGGER, next="orphaned-action"),
            WorkflowNode(
                id="orphaned-action",
                type=NodeType.ACTION,
                action=WorkflowAction(name="mark_resolved"),
            ),
            WorkflowNode(id="unreachable-terminal", type=NodeType.TERMINAL),
        ),
    )
    await _published(db, tenant_id, definition)

    execution = await workflow_service.execute_workflow(tenant_id, definition.id, {}, session=db)

    assert execution.status is ExecutionStatus.FAILED
    assert execution.error == "workflow ended without reaching a terminal node"


async def test_cycle_is_persisted_as_timed_out(db, tenant_a, monkeypatch):
    tenant_id = str(tenant_a.id)
    definition = WorkflowDefinition(
        id="cyclic-path",
        tenant_id=tenant_id,
        name="cyclic-path",
        entry_node="loop",
        nodes=(
            WorkflowNode(id="loop", type=NodeType.RETRY, next="loop"),
            WorkflowNode(id="unreachable-terminal", type=NodeType.TERMINAL),
        ),
    )
    await _published(db, tenant_id, definition)
    monkeypatch.setattr(workflow_service, "MAX_STEPS", 3)

    execution = await workflow_service.execute_workflow(tenant_id, definition.id, {}, session=db)

    assert execution.status is ExecutionStatus.TIMED_OUT
    assert execution.error == "step limit exceeded (possible cycle)"
    assert len(execution.steps) == 3
