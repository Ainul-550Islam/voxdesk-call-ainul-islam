# File: app/builder/workflow_executor.py — Non-voice background workflow automation coordinator (distinct from real-time voice FlowRunner in app/builder/flow_runner.py)
"""Durable non-voice workflow automation execution coordinator.

Note: This module executes asynchronous, non-voice workflow automations over the
durable job queue (`app/jobs/`). For real-time voice/chat conversation flows
wired into the Pipecat pipeline, see ``app.builder.flow_runner.FlowRunner`` and
``app.agent.flow_processor.FlowProcessor``.

Loads durable definition/version, validates state transitions via the workflow
state machine, persists checkpoints and execution state when a repository/store
is provided, and supports deterministic fallback in unit tests.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.builder.workflow_state import can_transition, is_terminal


class WorkflowExecutor:
    """Coordinates workflow execution, checkpoint persistence, and crash recovery."""

    def __init__(self, store: Any = None, repository: Any = None, state_machine: Any = None) -> None:
        self.store = store
        self.repository = repository or store
        self.state_machine = state_machine
        self._local_checkpoints: dict[str, list[dict[str, Any]]] = {}
        self._local_states: dict[str, dict[str, Any]] = {}

    def _can_transition(self, from_state: str, to_state: str) -> bool:
        if self.state_machine is not None and hasattr(self.state_machine, "can_transition"):
            return bool(self.state_machine.can_transition(from_state, to_state))
        return can_transition(from_state, to_state)

    async def execute(
        self,
        execution_id: str,
        tenant_id: str,
        *,
        initial_node: str = "start",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Start or advance a durable workflow execution."""
        if self.repository is not None and hasattr(self.repository, "transition_execution"):
            try:
                t_uuid = uuid.UUID(str(tenant_id))
                updated = await self.repository.transition_execution(
                    execution_id,
                    "running",
                    current_node_id=initial_node,
                    checkpoint={"node": initial_node, "payload": payload or {}},
                    tenant_id=t_uuid,
                )
                return {
                    "execution_id": str(updated.get("id", execution_id)),
                    "status": updated.get("status", "running"),
                    "node": updated.get("current_node_id") or initial_node,
                    "recovered": False,
                    "attempt_count": updated.get("attempt_count", 1),
                }
            except ValueError:
                pass

        state = {
            "execution_id": execution_id,
            "tenant_id": tenant_id,
            "status": "running",
            "node": initial_node,
            "recovered": False,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._local_states[execution_id] = state
        return {
            "execution_id": execution_id,
            "status": "running",
            "node": initial_node,
            "recovered": False,
        }

    async def resume(
        self,
        execution_id: str,
        *,
        tenant_id: str | None = None,
        target_status: str = "recovering",
    ) -> dict[str, Any]:
        """Resume a paused, failed, or stale execution from its latest checkpoint."""
        current = self._local_states.get(execution_id, {"status": "running", "node": "start"})
        from_status = str(current.get("status", "running"))
        if is_terminal(from_status) and from_status != "failed":
            raise ValueError(f"cannot resume terminal execution in state '{from_status}'")
        if not self._can_transition(from_status, target_status):
            raise ValueError(f"invalid resume transition: {from_status} -> {target_status}")

        if (
            self.repository is not None
            and tenant_id is not None
            and hasattr(self.repository, "transition_execution")
        ):
            try:
                t_uuid = uuid.UUID(str(tenant_id))
                updated = await self.repository.transition_execution(
                    execution_id,
                    target_status,
                    tenant_id=t_uuid,
                )
                return {
                    "execution_id": str(updated.get("id", execution_id)),
                    "status": updated.get("status", target_status),
                    "resumed": True,
                    "node": updated.get("current_node_id") or current.get("node", "start"),
                }
            except ValueError:
                pass

        checkpoints = self._local_checkpoints.get(execution_id, [])
        last_node = checkpoints[-1]["node_id"] if checkpoints else current.get("node", "start")
        self._local_states[execution_id] = {
            **current,
            "execution_id": execution_id,
            "status": target_status,
            "node": last_node,
            "recovered": True,
        }
        return {
            "execution_id": execution_id,
            "status": target_status,
            "resumed": True,
        }

    async def checkpoint(
        self,
        execution_id: str,
        node_id: str,
        data: dict[str, Any],
        *,
        tenant_id: str | None = None,
    ) -> bool:
        """Persist an execution checkpoint at a specific node boundary."""
        if not execution_id or not node_id:
            return False

        if (
            self.repository is not None
            and tenant_id is not None
            and hasattr(self.repository, "transition_execution")
        ):
            try:
                t_uuid = uuid.UUID(str(tenant_id))
                await self.repository.transition_execution(
                    execution_id,
                    "running",
                    current_node_id=node_id,
                    checkpoint=dict(data),
                    tenant_id=t_uuid,
                )
                return True
            except ValueError:
                pass

        records = self._local_checkpoints.setdefault(execution_id, [])
        records.append(
            {
                "execution_id": execution_id,
                "node_id": node_id,
                "data": dict(data),
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if execution_id in self._local_states:
            self._local_states[execution_id]["node"] = node_id
        return True

    def get_checkpoints(self, execution_id: str) -> list[dict[str, Any]]:
        """Return recorded checkpoints for an execution."""
        return list(self._local_checkpoints.get(execution_id, []))
