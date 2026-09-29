"""Protocol for the project's durable workflow repository.

The service uses ``WorkflowRepository`` directly, with one injected
``AsyncSession`` and one authenticated tenant context. This protocol exists for
callers that need structural typing; it does not introduce another store or
session implementation.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession


@runtime_checkable
class WorkflowStore(Protocol):
    def __init__(self, session: AsyncSession) -> None: ...

    async def create_workflow(
        self,
        tenant_id: uuid.UUID,
        name: str,
        definition: dict[str, Any],
        slug: str | None = None,
        created_by: uuid.UUID | None = None,
    ) -> dict[str, Any]: ...

    async def get_workflow(
        self, tenant_id: uuid.UUID, workflow_id: uuid.UUID | str
    ) -> dict[str, Any] | None: ...

    async def list_workflows(
        self, tenant_id: uuid.UUID, status: str | None = None
    ) -> list[dict[str, Any]]: ...

    async def update_workflow(
        self,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID | str,
        *,
        definition: dict[str, Any],
        current_version_id: uuid.UUID | None = None,
    ) -> dict[str, Any]: ...

    async def create_version(
        self,
        workflow_id: uuid.UUID | str,
        graph_config: dict[str, Any],
        version_number: int,
        checksum: str = "",
        *,
        tenant_id: uuid.UUID,
        created_by: uuid.UUID | None = None,
    ) -> dict[str, Any]: ...

    async def get_version(
        self, version_id: uuid.UUID | str, tenant_id: uuid.UUID
    ) -> dict[str, Any] | None: ...

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
    ) -> dict[str, Any]: ...

    async def get_execution(
        self, execution_id: uuid.UUID | str, *, tenant_id: uuid.UUID
    ) -> dict[str, Any] | None: ...

    async def transition_execution(
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
    ) -> dict[str, Any]: ...

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
    ) -> dict[str, Any]: ...

    async def heartbeat_execution(
        self,
        execution_id: uuid.UUID | str,
        *,
        tenant_id: uuid.UUID,
        expected_version: int,
    ) -> dict[str, Any]: ...

    async def recover_executions(
        self,
        tenant_id: uuid.UUID,
        *,
        stale_after_seconds: int = 60,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    async def list_executions(
        self,
        tenant_id: uuid.UUID,
        workflow_id: uuid.UUID | str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]: ...
