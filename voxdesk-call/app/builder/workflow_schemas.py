"""Workflow schemas — Pydantic-style contracts."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict
from typing import Any

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
    input_payload: dict = {}

class ExecutionRead(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    status: str
    current_node_id: str | None
