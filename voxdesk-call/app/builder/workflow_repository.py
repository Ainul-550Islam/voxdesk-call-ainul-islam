"""Database repository for durable, tenant-scoped workflows.

The repository is the only persistence boundary for workflow definitions,
versions, executions, checkpoints and execution idempotency. It deliberately
returns plain dictionaries so the domain dataclasses remain independent of
SQLAlchemy while every database operation remains explicit and testable.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError, ConflictError, NotFoundError
from app.db.rls import set_tenant_context
from app.db.models import (
    ExecutionCheckpoint,
    Workflow,
    WorkflowExecution as WorkflowExecutionRow,
    WorkflowIdempotency,
    WorkflowVersion,
)


from app.builder.workflow_state import can_transition as can_execution_transition


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid(value: uuid.UUID | str) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _checksum(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class WorkflowRepository:
    """Async SQLAlchemy repository. No process-local workflow state exists here."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def _rollback(self, tenant_id: uuid.UUID) -> None:
        await self.session.rollback()
        await set_tenant_context(self.session, tenant_id)

    async def _workflow(
        self,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID | str,
        *,
        for_update: bool = False,
    ) -> Workflow | None:
        identifier = _uuid(workflow_id)
        predicate = [Workflow.tenant_id == tenant_id]
        if identifier is not None:
            predicate.append(Workflow.id == identifier)
        else:
            predicate.append(Workflow.slug == str(workflow_id))
        statement = select(Workflow).where(*predicate)
        if for_update:
            statement = statement.with_for_update()
        return (await self.session.execute(statement)).scalar_one_or_none()

    @staticmethod
    def _workflow_dict(row: Workflow) -> dict[str, Any]:
        return {
            "db_id": row.id,
            "id": row.slug or str(row.id),
            "tenant_id": str(row.tenant_id),
            "name": row.name,
            "slug": row.slug,
            "description": row.description or "",
            "status": row.status,
            "definition": _json(row.workflow_metadata),
            "current_version_id": row.current_version_id,
            "published_version_id": row.published_version_id,
            "created_by": row.created_by,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _version_dict(row: WorkflowVersion, workflow: Workflow | None = None) -> dict[str, Any]:
        return {
            "id": row.id,
            "workflow_db_id": row.workflow_id,
            "workflow_id": (
                workflow.slug if workflow is not None and workflow.slug else str(row.workflow_id)
            ),
            "tenant_id": str(row.tenant_id),
            "version_number": row.version_number,
            "graph_config": _json(row.graph_config),
            "checksum": row.checksum,
            "status": row.status,
            "created_by": row.created_by,
            "created_at": row.created_at,
            "published_at": row.published_at,
        }

    @staticmethod
    def _execution_dict(row: WorkflowExecutionRow) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "db_id": row.id,
            "workflow_db_id": row.workflow_id,
            "workflow_version_id": row.workflow_version_id,
            "tenant_id": str(row.tenant_id),
            "status": row.status,
            "current_node_id": row.current_node_id or "",
            "input_payload": _json(row.input_payload),
            "output_metadata": _json(row.output_metadata),
            "error_metadata": _json(row.error_metadata),
            "attempt_count": row.attempt_count,
            "checkpoint": _json(row.checkpoint_data),
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "last_heartbeat_at": row.last_heartbeat_at,
            "recovery_count": row.recovery_count,
            "concurrency_version": row.concurrency_version,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    async def create_workflow(
        self,
        tenant_id: uuid.UUID,
        name: str,
        definition: dict[str, Any],
        slug: str | None = None,
        created_by: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        existing = None
        if slug:
            existing = (
                await self.session.execute(
                    select(Workflow).where(Workflow.tenant_id == tenant_id, Workflow.slug == slug)
                )
            ).scalar_one_or_none()
        if existing is not None:
            return self._workflow_dict(existing)
        row = Workflow(
            tenant_id=tenant_id,
            name=name,
            slug=slug,
            description=str(definition.get("description", ""))[:4000],
            status=str(definition.get("status", "draft")),
            workflow_metadata=definition,
            created_by=created_by,
        )
        self.session.add(row)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self._rollback(tenant_id)
            if slug:
                existing = (
                    await self.session.execute(
                        select(Workflow).where(
                            Workflow.tenant_id == tenant_id, Workflow.slug == slug
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return self._workflow_dict(existing)
            raise ConflictError("workflow already exists") from exc
        return self._workflow_dict(row)

    async def get_workflow(
        self, tenant_id: uuid.UUID, workflow_id: uuid.UUID | str
    ) -> dict[str, Any] | None:
        row = await self._workflow(tenant_id, workflow_id)
        return self._workflow_dict(row) if row is not None else None

    async def list_workflows(
        self, tenant_id: uuid.UUID, status: str | None = None
    ) -> list[dict[str, Any]]:
        statement = select(Workflow).where(Workflow.tenant_id == tenant_id).order_by(Workflow.name)
        if status:
            statement = statement.where(Workflow.status == status)
        rows = (await self.session.execute(statement)).scalars().all()
        return [self._workflow_dict(row) for row in rows]

    async def update_workflow(
        self,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID | str,
        *,
        definition: dict[str, Any],
        current_version_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        row = await self._workflow(tenant_id, workflow_id, for_update=True)
        if row is None:
            raise NotFoundError("workflow not found")
        row.name = str(definition.get("name", row.name))[:255]
        row.description = str(definition.get("description", row.description or ""))[:4000]
        row.workflow_metadata = definition
        if current_version_id is not None:
            row.current_version_id = current_version_id
        row.updated_at = _now()
        await self.session.flush()
        return self._workflow_dict(row)

    async def transition_workflow(
        self,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID | str,
        *,
        new_status: str,
        current_version_id: uuid.UUID | None = None,
        published_version_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        row = await self._workflow(tenant_id, workflow_id, for_update=True)
        if row is None:
            raise NotFoundError("workflow not found")
        allowed = {
            "draft": {"active", "archived"},
            "active": {"paused", "archived"},
            "paused": {"active", "archived"},
            "archived": set(),
        }
        if new_status not in allowed.get(row.status, set()):
            raise BadRequestError(f"cannot move workflow {row.status} -> {new_status}")
        row.status = new_status
        if current_version_id is not None:
            row.current_version_id = current_version_id
        if published_version_id is not None:
            row.published_version_id = published_version_id
        row.updated_at = _now()
        await self.session.flush()
        return self._workflow_dict(row)

    async def create_version(
        self,
        workflow_id: uuid.UUID | str,
        graph_config: dict[str, Any],
        version_number: int,
        checksum: str = "",
        *,
        tenant_id: uuid.UUID | None = None,
        created_by: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        if tenant_id is None:
            raise BadRequestError("tenant context is required for workflow version persistence")
        workflow = await self._workflow(tenant_id, workflow_id, for_update=True)
        if workflow is None:
            raise NotFoundError("workflow not found")
        existing = (
            await self.session.execute(
                select(WorkflowVersion).where(
                    WorkflowVersion.workflow_id == workflow.id,
                    WorkflowVersion.version_number == version_number,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return self._version_dict(existing, workflow)
        row = WorkflowVersion(
            workflow_id=workflow.id,
            tenant_id=workflow.tenant_id,
            version_number=version_number,
            graph_config=graph_config,
            checksum=checksum or _checksum(graph_config),
            status="draft",
            created_by=created_by,
        )
        self.session.add(row)
        await self.session.flush()
        return self._version_dict(row, workflow)

    async def get_version(
        self,
        version_id: uuid.UUID | str,
        tenant_id: uuid.UUID | None = None,
    ) -> dict[str, Any] | None:
        if tenant_id is None:
            raise BadRequestError("tenant context is required for workflow version lookup")
        identifier = _uuid(version_id)
        if identifier is None:
            return None
        predicate = [WorkflowVersion.id == identifier]
        if tenant_id is not None:
            predicate.append(WorkflowVersion.tenant_id == tenant_id)
        row = (
            await self.session.execute(select(WorkflowVersion).where(*predicate))
        ).scalar_one_or_none()
        if row is None:
            return None
        workflow = await self.session.get(Workflow, row.workflow_id)
        if workflow is None or (tenant_id is not None and workflow.tenant_id != tenant_id):
            return None
        return self._version_dict(row, workflow)

    async def list_versions(
        self, tenant_id: uuid.UUID, workflow_id: uuid.UUID | str
    ) -> list[dict[str, Any]]:
        workflow = await self._workflow(tenant_id, workflow_id)
        if workflow is None:
            return []
        rows = (
            (
                await self.session.execute(
                    select(WorkflowVersion)
                    .where(
                        WorkflowVersion.workflow_id == workflow.id,
                        WorkflowVersion.tenant_id == tenant_id,
                    )
                    .order_by(WorkflowVersion.version_number.desc())
                )
            )
            .scalars()
            .all()
        )
        return [self._version_dict(row, workflow) for row in rows]

    async def publish_version(
        self,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID | str,
        version_id: uuid.UUID,
    ) -> dict[str, Any]:
        workflow = await self._workflow(tenant_id, workflow_id, for_update=True)
        if workflow is None:
            raise NotFoundError("workflow not found")
        version = (
            await self.session.execute(
                select(WorkflowVersion)
                .where(
                    WorkflowVersion.id == version_id,
                    WorkflowVersion.workflow_id == workflow.id,
                    WorkflowVersion.tenant_id == tenant_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if version is None:
            raise NotFoundError("workflow version not found")
        if version.status == "published":
            return self._workflow_dict(workflow)
        if version.status != "draft":
            raise ConflictError("only draft workflow versions can be published")
        version.status = "published"
        version.published_at = _now()
        workflow.status = "active"
        workflow.current_version_id = version.id
        workflow.published_version_id = version.id
        workflow.updated_at = _now()
        await self.session.flush()
        return self._workflow_dict(workflow)

    async def _resolve_idempotency_race(
        self,
        tenant_id: uuid.UUID,
        operation: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> dict[str, Any] | None:
        """Read the committed winner after a unique-key race.

        PostgreSQL normally blocks the losing insert until the winner commits.
        The short bounded retry also makes the same reservation behavior
        deterministic for SQLite-based test fixtures and transaction pools.
        """
        for _ in range(40):
            row = (
                await self.session.execute(
                    select(WorkflowIdempotency).where(
                        WorkflowIdempotency.tenant_id == tenant_id,
                        WorkflowIdempotency.operation == operation,
                        WorkflowIdempotency.idempotency_key == idempotency_key,
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                if row.request_fingerprint != request_fingerprint:
                    raise ConflictError("idempotency key was used with a different request")
                if row.result_ref:
                    found = await self.get_execution(row.result_ref, tenant_id=tenant_id)
                    if found is not None:
                        found["_idempotent_existing"] = True
                        return found
            await asyncio.sleep(0.005)
        return None

    async def create_execution(
        self,
        workflow_id: uuid.UUID,
        version_id: uuid.UUID,
        tenant_id: uuid.UUID,
        input_payload: dict[str, Any],
        *,
        idempotency_key: str,
        request_fingerprint: str,
        current_node_id: str | None = None,
    ) -> dict[str, Any]:
        workflow = await self._workflow(tenant_id, workflow_id, for_update=False)
        if workflow is None:
            raise NotFoundError("workflow not found")
        version = (
            await self.session.execute(
                select(WorkflowVersion).where(
                    WorkflowVersion.id == version_id,
                    WorkflowVersion.workflow_id == workflow.id,
                    WorkflowVersion.tenant_id == tenant_id,
                )
            )
        ).scalar_one_or_none()
        if version is None:
            raise NotFoundError("workflow version not found")
        operation = "execution_create"
        existing = (
            await self.session.execute(
                select(WorkflowIdempotency)
                .where(
                    WorkflowIdempotency.tenant_id == tenant_id,
                    WorkflowIdempotency.operation == operation,
                    WorkflowIdempotency.idempotency_key == idempotency_key,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise ConflictError("idempotency key was used with a different request")
            if existing.result_ref:
                found = await self.get_execution(existing.result_ref, tenant_id=tenant_id)
                if found is not None:
                    found["_idempotent_existing"] = True
                    return found
            resolved = await self._resolve_idempotency_race(
                tenant_id, operation, idempotency_key, request_fingerprint
            )
            if resolved is not None:
                return resolved
            raise ConflictError("execution idempotency reservation is incomplete")
        else:
            idem = WorkflowIdempotency(
                tenant_id=tenant_id,
                operation=operation,
                idempotency_key=idempotency_key,
                request_fingerprint=request_fingerprint,
                status="in_progress",
            )
            self.session.add(idem)
            try:
                await self.session.flush()
                existing = idem
            except IntegrityError as exc:
                await self._rollback(tenant_id)
                resolved = await self._resolve_idempotency_race(
                    tenant_id, operation, idempotency_key, request_fingerprint
                )
                if resolved is not None:
                    return resolved
                raise ConflictError("could not reserve execution idempotency key") from exc
        execution = WorkflowExecutionRow(
            id=uuid.uuid4(),
            workflow_id=workflow.id,
            workflow_version_id=version.id,
            tenant_id=tenant_id,
            status="running",
            current_node_id=current_node_id,
            input_payload=input_payload,
            output_metadata={},
            error_metadata=None,
            attempt_count=1,
            checkpoint_data={},
            started_at=_now(),
            last_heartbeat_at=_now(),
            concurrency_version=1,
        )
        try:
            self.session.add(execution)
            await self.session.flush()
            existing.result_ref = str(execution.id)
            existing.status = "completed"
            await self.session.flush()
        except (IntegrityError, StaleDataError) as exc:
            # A concurrent reservation may win between the initial lookup and
            # the execution insert. Roll back this transaction, then resolve
            # the committed winner by the same tenant/operation/key tuple.
            await self._rollback(tenant_id)
            resolved = await self._resolve_idempotency_race(
                tenant_id, operation, idempotency_key, request_fingerprint
            )
            if resolved is not None:
                return resolved
            raise ConflictError("could not reserve execution idempotency key") from exc
        return self._execution_dict(execution)

    async def get_execution(
        self, execution_id: uuid.UUID | str, *, tenant_id: uuid.UUID | None = None
    ) -> dict[str, Any] | None:
        if tenant_id is None:
            raise BadRequestError("tenant context is required for execution lookup")
        identifier = _uuid(execution_id)
        if identifier is None:
            return None
        predicate = [WorkflowExecutionRow.id == identifier]
        if tenant_id is not None:
            predicate.append(WorkflowExecutionRow.tenant_id == tenant_id)
        row = (
            await self.session.execute(select(WorkflowExecutionRow).where(*predicate))
        ).scalar_one_or_none()
        return self._execution_dict(row) if row is not None else None

    async def list_executions(
        self,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID | str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        predicate = [WorkflowExecutionRow.tenant_id == tenant_id]
        if workflow_id is not None:
            workflow = await self._workflow(tenant_id, workflow_id)
            if workflow is None:
                return []
            predicate.append(WorkflowExecutionRow.workflow_id == workflow.id)
        if status:
            predicate.append(WorkflowExecutionRow.status == status)
        rows = (
            (
                await self.session.execute(
                    select(WorkflowExecutionRow)
                    .where(*predicate)
                    .order_by(WorkflowExecutionRow.created_at.desc())
                )
            )
            .scalars()
            .all()
        )
        return [self._execution_dict(row) for row in rows]

    async def transition_execution(
        self,
        execution_id: uuid.UUID | str,
        new_status: str,
        current_node_id: str | None = None,
        checkpoint: dict[str, Any] | None = None,
        *,
        tenant_id: uuid.UUID | None = None,
        expected_version: int | None = None,
        error_metadata: dict[str, Any] | None = None,
        output_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if tenant_id is None:
            raise BadRequestError("tenant context is required for execution transition")
        identifier = _uuid(execution_id)
        if identifier is None:
            raise NotFoundError("execution not found")
        row = (
            await self.session.execute(
                select(WorkflowExecutionRow)
                .where(
                    WorkflowExecutionRow.id == identifier,
                    *([WorkflowExecutionRow.tenant_id == tenant_id] if tenant_id else []),
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise NotFoundError("execution not found")
        if expected_version is not None and row.concurrency_version != expected_version:
            raise ConflictError("execution was modified by another worker")
        if new_status != row.status and not can_execution_transition(row.status, new_status):
            raise BadRequestError(f"cannot move execution {row.status} -> {new_status}")
        values: dict[str, Any] = {
            "status": new_status,
            "concurrency_version": row.concurrency_version + 1,
            "updated_at": _now(),
            "last_heartbeat_at": _now(),
        }
        if current_node_id is not None:
            values["current_node_id"] = current_node_id
        if checkpoint is not None:
            values["checkpoint_data"] = checkpoint
        if error_metadata is not None:
            values["error_metadata"] = error_metadata
        if output_metadata is not None:
            values["output_metadata"] = output_metadata
        if new_status in {"completed", "failed", "cancelled", "timed_out"}:
            values["finished_at"] = _now()
        result = await self.session.execute(
            update(WorkflowExecutionRow)
            .where(
                WorkflowExecutionRow.id == identifier,
                WorkflowExecutionRow.concurrency_version == row.concurrency_version,
                *([WorkflowExecutionRow.tenant_id == tenant_id] if tenant_id else []),
            )
            .values(**values)
        )
        if result.rowcount != 1:
            raise ConflictError("execution was modified by another worker")
        if checkpoint is not None:
            self.session.add(
                ExecutionCheckpoint(
                    execution_id=row.id,
                    node_id=current_node_id or row.current_node_id or "",
                    checkpoint_data=checkpoint,
                )
            )
        await self.session.flush()
        updated = await self.get_execution(identifier, tenant_id=tenant_id)
        if updated is None:
            raise NotFoundError("execution not found")
        return updated

    async def update_execution_state(
        self,
        execution_id: uuid.UUID | str,
        new_status: str,
        current_node_id: str | None = None,
        checkpoint: dict[str, Any] | None = None,
        *,
        tenant_id: uuid.UUID,
        expected_version: int | None = None,
        error_metadata: dict[str, Any] | None = None,
        output_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Compatibility name for the conditional transition operation."""
        return await self.transition_execution(
            execution_id,
            new_status,
            current_node_id=current_node_id,
            checkpoint=checkpoint,
            tenant_id=tenant_id,
            expected_version=expected_version,
            error_metadata=error_metadata,
            output_metadata=output_metadata,
        )

    async def heartbeat_execution(
        self,
        execution_id: uuid.UUID | str,
        *,
        tenant_id: uuid.UUID,
        expected_version: int,
    ) -> dict[str, Any]:
        identifier = _uuid(execution_id)
        if identifier is None:
            raise NotFoundError("execution not found")
        result = await self.session.execute(
            update(WorkflowExecutionRow)
            .where(
                WorkflowExecutionRow.id == identifier,
                WorkflowExecutionRow.tenant_id == tenant_id,
                WorkflowExecutionRow.status.in_({"running", "recovering", "waiting_approval"}),
                WorkflowExecutionRow.concurrency_version == expected_version,
            )
            .values(
                last_heartbeat_at=_now(),
                updated_at=_now(),
                concurrency_version=expected_version + 1,
            )
        )
        if result.rowcount != 1:
            raise ConflictError("execution heartbeat lost its lease")
        await self.session.flush()
        updated = await self.get_execution(identifier, tenant_id=tenant_id)
        if updated is None:
            raise NotFoundError("execution not found")
        return updated

    async def recover_executions(
        self,
        tenant_id: uuid.UUID,
        *,
        stale_after_seconds: int = 60,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        cutoff = _now() - timedelta(seconds=max(1, stale_after_seconds))
        rows = (
            (
                await self.session.execute(
                    select(WorkflowExecutionRow)
                    .where(
                        WorkflowExecutionRow.tenant_id == tenant_id,
                        WorkflowExecutionRow.status.in_({"running", "recovering"}),
                        WorkflowExecutionRow.last_heartbeat_at < cutoff,
                    )
                    .order_by(WorkflowExecutionRow.last_heartbeat_at)
                    .limit(max(1, min(limit, 1000)))
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            row.status = "recovering"
            row.recovery_count += 1
            row.attempt_count += 1
            row.concurrency_version += 1
            row.last_heartbeat_at = _now()
            row.updated_at = _now()
        await self.session.flush()
        return [self._execution_dict(row) for row in rows]
