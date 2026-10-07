"""Durable workflow service over the project's database repository.

Workflow definitions, immutable versions, executions, checkpoints, recovery
state, and idempotency are persisted through ``WorkflowRepository``. This
module contains domain validation and the deterministic graph interpreter; it
contains no process-local workflow registry.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.builder.workflow_repository import WorkflowRepository
from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.core.logging import log
from app.db.rls import set_tenant_context
from app.db.models import Lead, LeadStatus
from app.domain.agent_models import stable_id
from app.domain.workflow_models import (
    ExecutionStatus,
    NodeType,
    WorkflowAction,
    WorkflowDefinition,
    WorkflowExecution,
    WorkflowNode,
    WorkflowStatus,
    WorkflowStep,
    can_transition as can_workflow_transition,
    execution_is_terminal,
)

MAX_STEPS = 200
MAX_PAYLOAD_BYTES = 20_000


def _tenant_uuid(tenant_id: str | uuid.UUID) -> uuid.UUID:
    try:
        return tenant_id if isinstance(tenant_id, uuid.UUID) else uuid.UUID(str(tenant_id))
    except (TypeError, ValueError) as exc:
        raise BadRequestError("tenant context must be a UUID") from exc


def _workflow_payload(definition: WorkflowDefinition) -> dict[str, Any]:
    return {
        "id": definition.id,
        "tenant_id": definition.tenant_id,
        "name": definition.name,
        "version": definition.version,
        "status": definition.status.value,
        "trigger": definition.trigger,
        "entry_node": definition.entry_node,
        "description": definition.description,
        "nodes": [_node_payload(node) for node in definition.nodes],
    }


def _node_payload(node: WorkflowNode) -> dict[str, Any]:
    return {
        "id": node.id,
        "type": node.type.value,
        "action": (
            {"name": node.action.name, "params": node.action.params}
            if node.action is not None
            else None
        ),
        "condition": (
            {
                "field": node.condition.field,
                "operator": node.condition.operator,
                "value": node.condition.value,
            }
            if node.condition is not None
            else None
        ),
        "branches": [
            {
                "field": condition.field,
                "operator": condition.operator,
                "value": condition.value,
                "target": target,
            }
            for condition, target in node.branches
        ],
        "default_next": node.default_next,
        "next": node.next,
        "delay_seconds": node.delay_seconds,
        "timeout_seconds": node.timeout_seconds,
        "retry_limit": node.retry_limit,
        "approver_role": node.approver_role,
    }


def _condition(value: dict[str, Any] | None):
    if not value:
        return None
    from app.domain.workflow_models import Condition

    return Condition(
        field=str(value.get("field", "")),
        operator=str(value.get("operator", "eq")),
        value=value.get("value"),
    )


def _definition_from_payload(
    payload: dict[str, Any], *, status: str | None = None
) -> WorkflowDefinition:
    nodes: list[WorkflowNode] = []
    for item in payload.get("nodes", []):
        action_payload = item.get("action") or {}
        action = (
            WorkflowAction(str(action_payload.get("name", "")), action_payload.get("params") or {})
            if action_payload
            else None
        )
        branches = tuple(
            (_condition(branch), str(branch.get("target", "")))
            for branch in item.get("branches", [])
        )
        nodes.append(
            WorkflowNode(
                id=str(item.get("id", "")),
                type=NodeType(str(item.get("type", NodeType.TERMINAL.value))),
                action=action,
                condition=_condition(item.get("condition")),
                branches=branches,
                default_next=str(item.get("default_next", "")),
                next=str(item.get("next", "")),
                delay_seconds=int(item.get("delay_seconds", 0)),
                timeout_seconds=int(item.get("timeout_seconds", 30)),
                retry_limit=int(item.get("retry_limit", 3)),
                approver_role=str(item.get("approver_role", "")),
            )
        )
    return WorkflowDefinition(
        id=str(payload.get("id", "")),
        tenant_id=str(payload.get("tenant_id", "")),
        name=str(payload.get("name", "")),
        version=int(payload.get("version", 1)),
        status=WorkflowStatus(status or str(payload.get("status", WorkflowStatus.DRAFT.value))),
        trigger=str(payload.get("trigger", "")),
        entry_node=str(payload.get("entry_node", "")),
        nodes=tuple(nodes),
        description=str(payload.get("description", "")),
    )


def _dt(value: datetime | str | None) -> str:
    if value is None:
        return ""
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _execution_from_row(row: dict[str, Any], workflow_id: str) -> WorkflowExecution:
    step_rows = _json_dict(row.get("output_metadata")).get("steps", [])
    steps = tuple(
        WorkflowStep(
            node_id=str(item.get("node_id", "")),
            status=str(item.get("status", "")),
            detail=str(item.get("detail", "")),
            attempt=int(item.get("attempt", 1)),
            at=str(item.get("at", "")),
        )
        for item in step_rows
        if isinstance(item, dict)
    )
    error_metadata = _json_dict(row.get("error_metadata"))
    return WorkflowExecution(
        id=str(row["id"]),
        workflow_id=workflow_id,
        tenant_id=str(row["tenant_id"]),
        idempotency_key=str(_json_dict(row.get("input_payload")).get("idempotency_key", row["id"])),
        status=ExecutionStatus(str(row["status"])),
        current_node=str(row.get("current_node_id") or ""),
        input_summary=_json_dict(row.get("input_payload")).get("summary", {}),
        steps=steps,
        started_at=_dt(row.get("started_at")),
        finished_at=_dt(row.get("finished_at")),
        error=str(error_metadata.get("message", "")),
    )


def _json_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _workflow_from_row(row: dict[str, Any]) -> WorkflowDefinition:
    payload = _json_dict(row.get("definition"))
    payload.setdefault("id", row["id"])
    payload.setdefault("tenant_id", row["tenant_id"])
    payload.setdefault("name", row["name"])
    payload.setdefault("description", row.get("description", ""))
    payload["version"] = int(payload.get("version", 1))
    return _definition_from_payload(payload, status=str(row.get("status", "draft")))


def execution_key(tenant_id: str, workflow_id: str, payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return stable_id(tenant_id, workflow_id, canonical)


def request_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_workflow(definition: WorkflowDefinition) -> list[str]:
    return definition.validate()


def _require_session(session: AsyncSession | None) -> AsyncSession:
    if session is None:
        raise BadRequestError("a database session is required for workflow persistence")
    return session


async def _commit(session: AsyncSession, tenant_id: str | uuid.UUID) -> None:
    await session.commit()
    # ``set_config(..., true)`` is transaction-scoped. Reinstall the
    # authenticated tenant after each service commit before the next query.
    await set_tenant_context(session, _tenant_uuid(tenant_id))


async def _rollback(session: AsyncSession, tenant_id: str | uuid.UUID) -> None:
    await session.rollback()
    await set_tenant_context(session, _tenant_uuid(tenant_id))


async def create_workflow(
    tenant_id: str,
    definition: WorkflowDefinition,
    *,
    session: AsyncSession | None = None,
    created_by: uuid.UUID | None = None,
) -> WorkflowDefinition:
    session = _require_session(session)
    if definition.tenant_id != tenant_id:
        raise BadRequestError("workflow does not belong to this tenant")
    problems = validate_workflow(definition)
    if problems:
        raise BadRequestError("; ".join(problems))
    repo = WorkflowRepository(session)
    try:
        row = await repo.create_workflow(
            _tenant_uuid(tenant_id),
            definition.name,
            _workflow_payload(definition),
            slug=definition.id,
            created_by=created_by,
        )
        if row.get("current_version_id") is None:
            version = await repo.create_version(
                definition.id,
                _workflow_payload(definition),
                definition.version,
                tenant_id=_tenant_uuid(tenant_id),
                created_by=created_by,
            )
            row = await repo.update_workflow(
                _tenant_uuid(tenant_id),
                definition.id,
                definition=_workflow_payload(definition),
                current_version_id=version["id"],
            )
        await _commit(session, tenant_id)
    except Exception:
        await _rollback(session, tenant_id)
        raise
    return _workflow_from_row(row)


async def get_workflow(
    tenant_id: str,
    workflow_id: str,
    *,
    session: AsyncSession | None = None,
) -> WorkflowDefinition:
    session = _require_session(session)
    row = await WorkflowRepository(session).get_workflow(_tenant_uuid(tenant_id), workflow_id)
    if row is None:
        raise NotFoundError("workflow not found")
    return _workflow_from_row(row)


async def list_workflows(
    tenant_id: str, *, session: AsyncSession | None = None
) -> list[WorkflowDefinition]:
    session = _require_session(session)
    rows = await WorkflowRepository(session).list_workflows(_tenant_uuid(tenant_id))
    return [_workflow_from_row(row) for row in rows]


async def version_history(
    tenant_id: str,
    workflow_id: str,
    *,
    session: AsyncSession | None = None,
) -> list[WorkflowDefinition]:
    session = _require_session(session)
    rows = await WorkflowRepository(session).list_versions(_tenant_uuid(tenant_id), workflow_id)
    if not rows:
        existing = await WorkflowRepository(session).get_workflow(
            _tenant_uuid(tenant_id), workflow_id
        )
        if existing is None:
            raise NotFoundError("workflow not found")
    return [
        _definition_from_payload(
            row["graph_config"], status="active" if row["status"] == "published" else "draft"
        )
        for row in rows
    ]


async def version_workflow(
    tenant_id: str,
    workflow_id: str,
    definition: WorkflowDefinition,
    *,
    session: AsyncSession | None = None,
    created_by: uuid.UUID | None = None,
) -> WorkflowDefinition:
    session = _require_session(session)
    current = await get_workflow(tenant_id, workflow_id, session=session)
    if definition.tenant_id != tenant_id:
        raise BadRequestError("workflow does not belong to this tenant")
    problems = validate_workflow(definition)
    if problems:
        raise BadRequestError("; ".join(problems))
    if definition.identity() == current.identity():
        return current
    next_version = current.version + 1
    new_definition = definition.with_version(next_version)
    repo = WorkflowRepository(session)
    try:
        version = await repo.create_version(
            workflow_id,
            _workflow_payload(new_definition),
            next_version,
            tenant_id=_tenant_uuid(tenant_id),
            created_by=created_by,
        )
        row = await repo.update_workflow(
            _tenant_uuid(tenant_id),
            workflow_id,
            definition=_workflow_payload(new_definition),
            current_version_id=version["id"],
        )
        await _commit(session, tenant_id)
    except Exception:
        await _rollback(session, tenant_id)
        raise
    return _workflow_from_row(row)


async def _transition_workflow(
    tenant_id: str,
    workflow_id: str,
    target: WorkflowStatus,
    *,
    session: AsyncSession | None = None,
) -> WorkflowDefinition:
    session = _require_session(session)
    current = await get_workflow(tenant_id, workflow_id, session=session)
    if not can_workflow_transition(current.status, target):
        raise BadRequestError(f"cannot move workflow {current.status.value} -> {target.value}")
    repo = WorkflowRepository(session)
    try:
        row = await repo.transition_workflow(
            _tenant_uuid(tenant_id),
            workflow_id,
            new_status=target.value,
            current_version_id=(
                (await repo.get_workflow(_tenant_uuid(tenant_id), workflow_id))[
                    "current_version_id"
                ]
            ),
        )
        await _commit(session, tenant_id)
    except Exception:
        await _rollback(session, tenant_id)
        raise
    return _workflow_from_row(row)


async def publish_workflow(
    tenant_id: str, workflow_id: str, *, session: AsyncSession | None = None
) -> WorkflowDefinition:
    session = _require_session(session)
    repo = WorkflowRepository(session)
    try:
        row = await repo.get_workflow(_tenant_uuid(tenant_id), workflow_id)
        if row is None:
            raise NotFoundError("workflow not found")
        if row["current_version_id"] is None:
            raise BadRequestError("workflow has no version to publish")
        row = await repo.publish_version(
            _tenant_uuid(tenant_id), workflow_id, row["current_version_id"]
        )
        await _commit(session, tenant_id)
    except Exception:
        await _rollback(session, tenant_id)
        raise
    return _workflow_from_row(row)


async def pause_workflow(
    tenant_id: str, workflow_id: str, *, session: AsyncSession | None = None
) -> WorkflowDefinition:
    return await _transition_workflow(
        tenant_id, workflow_id, WorkflowStatus.PAUSED, session=session
    )


async def resume_workflow(
    tenant_id: str, workflow_id: str, *, session: AsyncSession | None = None
) -> WorkflowDefinition:
    return await _transition_workflow(
        tenant_id, workflow_id, WorkflowStatus.ACTIVE, session=session
    )


async def archive_workflow(
    tenant_id: str, workflow_id: str, *, session: AsyncSession | None = None
) -> WorkflowDefinition:
    return await _transition_workflow(
        tenant_id, workflow_id, WorkflowStatus.ARCHIVED, session=session
    )


async def clone_workflow(
    tenant_id: str,
    workflow_id: str,
    *,
    new_name: str,
    session: AsyncSession | None = None,
    created_by: uuid.UUID | None = None,
) -> WorkflowDefinition:
    source = await get_workflow(tenant_id, workflow_id, session=session)
    clone_id = stable_id(tenant_id, new_name)
    clone = replace(source, id=clone_id, name=new_name, version=1, status=WorkflowStatus.DRAFT)
    return await create_workflow(tenant_id, clone, session=session, created_by=created_by)


# ------------------------------------------------------------------ actions ---


async def _execute_action(
    tenant_id: str,
    action: WorkflowAction,
    payload: dict[str, Any],
    session: AsyncSession | None,
) -> tuple[str, str]:
    name = action.name
    params = action.params
    if name == "update_lead_status":
        return await _handle_update_lead_status(tenant_id, params, payload, session)
    if name == "apply_dnc":
        return await _handle_apply_dnc(tenant_id, params, payload, session)
    if name == "record_escalation_intent":
        destination = str(params.get("destination", ""))[:64]
        return (
            "scheduled_intent",
            f"escalation intent recorded (destination={destination or 'default'})",
        )
    if name == "create_followup_intent":
        return (
            "scheduled_intent",
            f"followup intent recorded (lead={payload.get('lead_id', 'n/a')})",
        )
    if name == "enqueue_notification":
        template = str(params.get("template_id", ""))
        business_key = str(payload.get("business_key", payload.get("lead_id", "")))
        from app.services import notification_service

        notification_service.enqueue_system_notification(
            tenant_id, template, business_key, variables=payload
        )
        return "executed", f"notification enqueued (template={template or 'default'})"
    if name == "add_conversation_tag":
        return "executed", f"tag recorded ({str(params.get('tag', ''))[:64]})"
    if name == "mark_resolved":
        return "executed", "resolution recorded"
    return "unsupported", f"no handler for {name}"


async def _handle_update_lead_status(
    tenant_id: str, params: dict, payload: dict, session: AsyncSession | None
) -> tuple[str, str]:
    lead_id = payload.get("lead_id") or params.get("lead_id")
    target = params.get("status", "")
    if session is None or not lead_id:
        return "failed", f"lead status update requires a tenant lead ({lead_id or 'unspecified'})"
    return await _lead_status_change(session, tenant_id, str(lead_id), target)


async def _handle_apply_dnc(
    tenant_id: str, params: dict, payload: dict, session: AsyncSession | None
) -> tuple[str, str]:
    lead_id = payload.get("lead_id") or params.get("lead_id")
    if session is None or not lead_id:
        return "failed", f"dnc update requires a tenant lead ({lead_id or 'unspecified'})"
    return await _lead_status_change(session, tenant_id, str(lead_id), LeadStatus.DNC.value)


async def _lead_status_change(
    session: AsyncSession, tenant_id: str, lead_id: str, target: str
) -> tuple[str, str]:
    from app.leads import lifecycle
    from app.leads.exceptions import ClaimConflict, InvalidTransition

    try:
        parsed = uuid.UUID(str(lead_id))
    except ValueError:
        return "failed", f"invalid lead_id {lead_id!r}"
    lead = await session.get(Lead, parsed)
    if lead is None or str(lead.tenant_id) != str(tenant_id):
        return "failed", "lead not found in tenant"
    try:
        await lifecycle.transition(
            session,
            lead,
            target or LeadStatus.QUALIFIED.value,
            reason="workflow_action",
            source="workflow",
        )
    except (InvalidTransition, ClaimConflict) as exc:
        return "failed", str(exc)
    return "executed", f"lead {lead_id} -> {lifecycle.status_value(lead.status)}"


# ---------------------------------------------------------------- execution ---


async def _checkpoint(
    session: AsyncSession,
    repo: WorkflowRepository,
    execution_id: str,
    tenant_id: uuid.UUID,
    current_node: str,
    steps: list[WorkflowStep],
) -> None:
    await repo.transition_execution(
        execution_id,
        "running",
        current_node_id=current_node,
        checkpoint={"steps": [_step_payload(step) for step in steps]},
        tenant_id=tenant_id,
    )
    await _commit(session, tenant_id)


def _step_payload(step: WorkflowStep) -> dict[str, Any]:
    return {
        "node_id": step.node_id,
        "status": step.status,
        "detail": step.detail,
        "attempt": step.attempt,
        "at": step.at,
    }


async def _definition_for_execution(
    repo: WorkflowRepository,
    tenant_id: uuid.UUID,
    workflow_id: uuid.UUID | str,
    version_id: uuid.UUID | str,
) -> WorkflowDefinition:
    version = await repo.get_version(version_id, tenant_id=tenant_id)
    if version is None or str(version["workflow_db_id"]) != str(workflow_id):
        raise NotFoundError("workflow version not found")
    return _definition_from_payload(version["graph_config"], status=WorkflowStatus.ACTIVE.value)


def _execution_from_checkpoint(
    row: dict[str, Any],
    workflow_id: str,
) -> WorkflowExecution:
    checkpoint = _json_dict(row.get("checkpoint"))
    hydrated = dict(row)
    hydrated["status"] = ExecutionStatus.RUNNING.value
    hydrated["output_metadata"] = {"steps": checkpoint.get("steps", [])}
    return _execution_from_row(hydrated, workflow_id)


async def execute_workflow(
    tenant_id: str,
    workflow_id: str,
    payload: dict[str, Any],
    *,
    session: AsyncSession | None = None,
    idempotency_key: str | None = None,
) -> WorkflowExecution:
    session = _require_session(session)
    if (
        not isinstance(payload, dict)
        or len(json.dumps(payload, default=str).encode("utf-8")) > MAX_PAYLOAD_BYTES
    ):
        raise BadRequestError("payload must be a mapping of at most 20KB")
    definition = await get_workflow(tenant_id, workflow_id, session=session)
    if definition.status is not WorkflowStatus.ACTIVE:
        raise BadRequestError(f"workflow is {definition.status.value}; publish it first")
    repo = WorkflowRepository(session)
    workflow_row = await repo.get_workflow(_tenant_uuid(tenant_id), workflow_id)
    if workflow_row is None or workflow_row["published_version_id"] is None:
        raise BadRequestError("workflow has no published version")
    published_version = await repo.get_version(
        workflow_row["published_version_id"], tenant_id=_tenant_uuid(tenant_id)
    )
    if published_version is None:
        raise BadRequestError("published workflow version is unavailable")
    # Execution always interprets the immutable published graph, never the
    # workflow's mutable current draft metadata.
    definition = _definition_from_payload(
        published_version["graph_config"], status=WorkflowStatus.ACTIVE.value
    )
    key = idempotency_key or execution_key(tenant_id, workflow_id, payload)
    if len(key) > 128 or not key.strip():
        raise BadRequestError("Idempotency-Key must be 1–128 characters")
    fingerprint = request_fingerprint(payload)
    try:
        row = await repo.create_execution(
            workflow_row["db_id"],
            workflow_row["published_version_id"],
            _tenant_uuid(tenant_id),
            {"summary": _summarize_payload(payload), "idempotency_key": key, "payload": payload},
            idempotency_key=key,
            request_fingerprint=fingerprint,
            current_node_id=definition.entry_node,
        )
        await _commit(session, tenant_id)
    except Exception:
        await _rollback(session, tenant_id)
        raise
    if row.get("_idempotent_existing") or row["status"] != "running":
        return _execution_from_row(row, workflow_id)
    execution = _execution_from_row(row, workflow_id)
    try:
        execution = await _walk(
            tenant_id,
            definition,
            execution,
            payload,
            session,
            checkpoint=lambda current, steps: _checkpoint(
                session, repo, execution.id, _tenant_uuid(tenant_id), current, steps
            ),
        )
        output = {"steps": [_step_payload(step) for step in execution.steps]}
        row = await repo.transition_execution(
            execution.id,
            execution.status.value,
            current_node_id=execution.current_node,
            checkpoint={"steps": output["steps"]},
            tenant_id=_tenant_uuid(tenant_id),
            error_metadata={"message": execution.error} if execution.error else {},
            output_metadata=output,
        )
        await _commit(session, tenant_id)
    except Exception as exc:
        await _rollback(session, tenant_id)
        try:
            row = await repo.transition_execution(
                execution.id,
                ExecutionStatus.FAILED.value,
                current_node_id=execution.current_node,
                checkpoint={"steps": [_step_payload(step) for step in execution.steps]},
                tenant_id=_tenant_uuid(tenant_id),
                error_metadata={"message": str(exc)[:500]},
            )
            await _commit(session, tenant_id)
        except Exception:
            await _rollback(session, tenant_id)
        raise
    log.info(
        "workflow.executed",
        tenant_id=tenant_id,
        workflow_id=workflow_id[:8],
        execution_id=execution.id[:8],
        status=execution.status.value,
    )
    return _execution_from_row(row, workflow_id)


def _summarize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, value in list(payload.items())[:40]:
        if isinstance(value, str):
            summary[key] = value[:60]
        elif isinstance(value, (int, float, bool)) or value is None:
            summary[key] = value
        else:
            summary[key] = type(value).__name__
    return summary


async def _walk(
    tenant_id: str,
    definition: WorkflowDefinition,
    execution: WorkflowExecution,
    payload: dict[str, Any],
    session: AsyncSession,
    checkpoint: Callable[[str, list[WorkflowStep]], Awaitable[None]] | None = None,
) -> WorkflowExecution:
    nodes = {node.id: node for node in definition.nodes}
    current = execution.current_node or definition.entry_node
    steps = list(execution.steps)
    visited = 0
    while current and visited < MAX_STEPS:
        visited += 1
        if checkpoint is not None:
            await checkpoint(current, steps)
        node = nodes.get(current)
        if node is None:
            steps.append(WorkflowStep(current, "failed", "unknown node", at=_now()))
            return replace(
                execution,
                steps=tuple(steps),
                status=ExecutionStatus.FAILED,
                error="unknown node",
                current_node=current,
                finished_at=_now(),
            )
        if node.type is NodeType.TERMINAL:
            steps.append(WorkflowStep(current, "executed", "terminal reached", at=_now()))
            return replace(
                execution,
                steps=tuple(steps),
                status=ExecutionStatus.COMPLETED,
                current_node=current,
                finished_at=_now(),
            )
        if node.type is NodeType.TRIGGER:
            steps.append(WorkflowStep(current, "executed", "trigger accepted", at=_now()))
            current = node.next
            continue
        if node.type is NodeType.CONDITION:
            target, detail = _resolve_condition(node, payload)
            steps.append(WorkflowStep(current, "executed", detail, at=_now()))
            if target is None:
                return replace(
                    execution,
                    steps=tuple(steps),
                    status=ExecutionStatus.FAILED,
                    error=detail,
                    current_node=current,
                    finished_at=_now(),
                )
            current = target
            continue
        if node.type is NodeType.ACTION:
            status, detail = (
                await _execute_action(tenant_id, node.action, payload, session)
                if node.action
                else ("unsupported", "no action defined")
            )
            steps.append(WorkflowStep(current, status, detail, at=_now()))
            if status == "failed":
                current = node.default_next or ""
                if not current:
                    return replace(
                        execution,
                        steps=tuple(steps),
                        status=ExecutionStatus.FAILED,
                        error=detail,
                        current_node="",
                        finished_at=_now(),
                    )
            else:
                current = node.next
            continue
        if node.type is NodeType.DELAY:
            steps.append(
                WorkflowStep(current, "scheduled", f"delay {node.delay_seconds}s", at=_now())
            )
            current = node.next
            continue
        if node.type is NodeType.RETRY:
            last = steps[-1] if steps else None
            if (
                last is not None
                and last.status in ("failed", "unsupported")
                and last.attempt < node.retry_limit
            ):
                steps.append(
                    WorkflowStep(
                        current,
                        "scheduled",
                        f"retry {last.attempt + 1}/{node.retry_limit}",
                        attempt=last.attempt + 1,
                        at=_now(),
                    )
                )
                current = node.next
            else:
                steps.append(
                    WorkflowStep(
                        current,
                        "executed",
                        f"retry policy limit {node.retry_limit}",
                        at=_now(),
                    )
                )
                current = node.default_next or node.next
            continue
        if node.type is NodeType.TIMEOUT:
            steps.append(
                WorkflowStep(
                    current, "executed", f"within timeout {node.timeout_seconds}s", at=_now()
                )
            )
            current = node.next
            continue
        if node.type is NodeType.APPROVAL:
            steps.append(
                WorkflowStep(
                    current, "scheduled", f"awaiting approval by {node.approver_role}", at=_now()
                )
            )
            return replace(
                execution,
                steps=tuple(steps),
                status=ExecutionStatus.WAITING_APPROVAL,
                current_node=current,
            )
        if node.type is NodeType.HANDOFF:
            steps.append(
                WorkflowStep(
                    current, "scheduled_intent", "human handoff requested (no dial)", at=_now()
                )
            )
            current = node.next
            continue
        steps.append(
            WorkflowStep(current, "failed", f"unsupported node type {node.type.value}", at=_now())
        )
        return replace(
            execution,
            steps=tuple(steps),
            status=ExecutionStatus.FAILED,
            error=f"unsupported node type {node.type.value}",
            current_node=current,
            finished_at=_now(),
        )
    if current:
        return replace(
            execution,
            steps=tuple(steps),
            status=ExecutionStatus.TIMED_OUT,
            error="step limit exceeded (possible cycle)",
            current_node=current,
            finished_at=_now(),
        )
    return replace(
        execution,
        steps=tuple(steps),
        status=ExecutionStatus.FAILED,
        error="workflow ended without reaching a terminal node",
        current_node="",
        finished_at=_now(),
    )


def _resolve_condition(node: WorkflowNode, payload: dict[str, Any]) -> tuple[str | None, str]:
    if node.branches:
        for condition, target in node.branches:
            if condition.matches(payload):
                return target, f"branch matched field={condition.field}"
        if node.default_next:
            return node.default_next, "no branch matched; default taken"
        return None, "no branch matched and no default"
    if node.condition is not None:
        if node.condition.matches(payload):
            return node.next, f"condition matched field={node.condition.field}"
        if node.default_next:
            return node.default_next, "condition failed; default taken"
        return None, "condition failed and no default"
    return node.next, "no condition defined"


# --------------------------------------------------------- execution reads ---


async def inspect_execution(
    tenant_id: str,
    execution_id: str,
    *,
    session: AsyncSession | None = None,
) -> WorkflowExecution:
    session = _require_session(session)
    row = await WorkflowRepository(session).get_execution(
        execution_id, tenant_id=_tenant_uuid(tenant_id)
    )
    if row is None:
        raise NotFoundError("execution not found")
    workflow = await WorkflowRepository(session).get_workflow(
        _tenant_uuid(tenant_id), row["workflow_db_id"]
    )
    if workflow is None:
        raise NotFoundError("workflow not found")
    return _execution_from_row(row, workflow["id"])


async def execution_history(
    tenant_id: str,
    workflow_id: str = "",
    *,
    session: AsyncSession | None = None,
) -> list[WorkflowExecution]:
    session = _require_session(session)
    repo = WorkflowRepository(session)
    rows = await repo.list_executions(_tenant_uuid(tenant_id), workflow_id or None)
    workflow = (
        await repo.get_workflow(_tenant_uuid(tenant_id), workflow_id) if workflow_id else None
    )
    if workflow_id and workflow is None:
        raise NotFoundError("workflow not found")
    resolved_workflow_id = workflow["id"] if workflow else workflow_id
    return [_execution_from_row(row, resolved_workflow_id) for row in rows]


async def cancel_execution(
    tenant_id: str,
    execution_id: str,
    *,
    session: AsyncSession | None = None,
) -> WorkflowExecution:
    session = _require_session(session)
    execution = await inspect_execution(tenant_id, execution_id, session=session)
    if execution_is_terminal(execution.status):
        return execution
    try:
        row = await WorkflowRepository(session).transition_execution(
            execution_id,
            ExecutionStatus.CANCELLED.value,
            current_node_id=execution.current_node,
            tenant_id=_tenant_uuid(tenant_id),
        )
        await _commit(session, tenant_id)
    except Exception:
        await _rollback(session, tenant_id)
        raise
    return _execution_from_row(row, execution.workflow_id)


async def approve_execution(
    tenant_id: str,
    execution_id: str,
    *,
    session: AsyncSession | None = None,
) -> WorkflowExecution:
    session = _require_session(session)
    tenant_uuid = _tenant_uuid(tenant_id)
    repo = WorkflowRepository(session)
    row = await repo.get_execution(execution_id, tenant_id=tenant_uuid)
    if row is None:
        raise NotFoundError("execution not found")
    execution = _execution_from_row(row, "")
    if execution.status is not ExecutionStatus.WAITING_APPROVAL:
        raise BadRequestError("execution is not waiting for approval")
    definition = await _definition_for_execution(
        repo, tenant_uuid, row["workflow_db_id"], row["workflow_version_id"]
    )
    nodes = {node.id: node for node in definition.nodes}
    approval_node = nodes.get(execution.current_node)
    if approval_node is None or approval_node.type is not NodeType.APPROVAL:
        raise ConflictError("approval checkpoint is no longer valid")
    steps = list(execution.steps)
    if steps and steps[-1].node_id == approval_node.id and steps[-1].status == "scheduled":
        waiting = steps[-1]
        steps[-1] = WorkflowStep(
            node_id=waiting.node_id,
            status="executed",
            detail="approval granted",
            attempt=waiting.attempt,
            at=_now(),
        )
    checkpoint = {"steps": [_step_payload(step) for step in steps]}
    try:
        await repo.transition_execution(
            execution_id,
            ExecutionStatus.RUNNING.value,
            current_node_id=approval_node.next,
            checkpoint=checkpoint,
            tenant_id=tenant_uuid,
            expected_version=row["concurrency_version"],
        )
        await _commit(session, tenant_id)
        resumed = replace(
            execution,
            status=ExecutionStatus.RUNNING,
            current_node=approval_node.next,
            steps=tuple(steps),
            error="",
            finished_at="",
        )
        resumed = await _walk(
            tenant_id,
            definition,
            resumed,
            _json_dict(row.get("input_payload")).get("payload", {}),
            session,
            checkpoint=lambda current, current_steps: _checkpoint(
                session, repo, execution_id, tenant_uuid, current, current_steps
            ),
        )
        output = {"steps": [_step_payload(step) for step in resumed.steps]}
        final_row = await repo.transition_execution(
            execution_id,
            resumed.status.value,
            current_node_id=resumed.current_node,
            checkpoint=output,
            tenant_id=tenant_uuid,
            error_metadata={"message": resumed.error} if resumed.error else {},
            output_metadata=output,
        )
        await _commit(session, tenant_id)
    except Exception as exc:
        await _rollback(session, tenant_id)
        try:
            final_row = await repo.transition_execution(
                execution_id,
                ExecutionStatus.FAILED.value,
                current_node_id=execution.current_node,
                checkpoint=checkpoint,
                tenant_id=tenant_uuid,
                error_metadata={"message": str(exc)[:500]},
            )
            await _commit(session, tenant_id)
        except Exception:
            await _rollback(session, tenant_id)
        raise
    return _execution_from_row(final_row, definition.id)


async def retry_execution(
    tenant_id: str,
    execution_id: str,
    payload: dict[str, Any],
    *,
    session: AsyncSession | None = None,
) -> WorkflowExecution:
    session = _require_session(session)
    previous = await inspect_execution(tenant_id, execution_id, session=session)
    if not execution_is_terminal(previous.status):
        raise BadRequestError("only a terminal execution may be retried")
    retry_key = f"retry:{previous.id}:{request_fingerprint(payload)}"
    return await execute_workflow(
        tenant_id,
        previous.workflow_id,
        payload,
        session=session,
        idempotency_key=retry_key,
    )


async def recover_executions(
    tenant_id: str,
    *,
    session: AsyncSession | None = None,
    stale_after_seconds: int = 60,
) -> list[WorkflowExecution]:
    session = _require_session(session)
    tenant_uuid = _tenant_uuid(tenant_id)
    repo = WorkflowRepository(session)
    rows = await repo.recover_executions(tenant_uuid, stale_after_seconds=stale_after_seconds)
    # Publish the recovery leases before running graph work. A second worker
    # must observe the recovering state and cannot claim the same rows.
    await _commit(session, tenant_id)
    result: list[WorkflowExecution] = []
    for row in rows:
        try:
            workflow = await repo.get_workflow(tenant_uuid, row["workflow_db_id"])
            if workflow is None:
                raise NotFoundError("workflow not found for recovered execution")
            definition = await _definition_for_execution(
                repo, tenant_uuid, row["workflow_db_id"], row["workflow_version_id"]
            )
            execution = _execution_from_checkpoint(row, workflow["id"])
            checkpoint = _json_dict(row.get("checkpoint"))
            await repo.transition_execution(
                row["id"],
                ExecutionStatus.RUNNING.value,
                current_node_id=execution.current_node,
                checkpoint=checkpoint,
                tenant_id=tenant_uuid,
                expected_version=row["concurrency_version"],
            )
            await _commit(session, tenant_id)
            execution = await _walk(
                tenant_id,
                definition,
                execution,
                _json_dict(row.get("input_payload")).get("payload", {}),
                session,
                checkpoint=lambda current, steps: _checkpoint(
                    session, repo, execution.id, tenant_uuid, current, steps
                ),
            )
            output = {"steps": [_step_payload(step) for step in execution.steps]}
            final_row = await repo.transition_execution(
                execution.id,
                execution.status.value,
                current_node_id=execution.current_node,
                checkpoint=output,
                tenant_id=tenant_uuid,
                error_metadata={"message": execution.error} if execution.error else {},
                output_metadata=output,
            )
            await _commit(session, tenant_id)
            result.append(_execution_from_row(final_row, workflow["id"]))
        except Exception as exc:
            await _rollback(session, tenant_id)
            try:
                failed = await repo.transition_execution(
                    row["id"],
                    ExecutionStatus.FAILED.value,
                    current_node_id=row.get("current_node_id"),
                    checkpoint=row.get("checkpoint") or {},
                    tenant_id=tenant_uuid,
                    error_metadata={"message": str(exc)[:500]},
                )
                await _commit(session, tenant_id)
                result.append(_execution_from_row(failed, str(row["workflow_db_id"])))
            except Exception:
                await _rollback(session, tenant_id)
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
