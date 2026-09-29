"""Compatibility schemas for durable workflow repository callers.

The HTTP workflow contract lives in ``app.api.workflow_routes``. These
schemas remain available to builder callers and describe the same persisted
workflow/version/execution records without introducing another storage layer.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WorkflowCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    slug: str | None = None
    description: str | None = None


class WorkflowVersionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version_number: int
    status: str
    checksum: str


class ExecutionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_version_id: str
    input_payload: dict[str, Any] = Field(default_factory=dict)


class ExecutionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: str
    current_node_id: str | None
