from __future__ import annotations
from datetime import datetime
from typing import Any, Literal
import uuid
from pydantic import BaseModel, ConfigDict, Field

class ControlInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    control_key: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=300)
    description: str = ""
    severity: Literal["low","medium","high","critical"] = "medium"
    source_reference: str | None = None
    required_evidence: list[str] = Field(default_factory=list)
    rules: dict[str, Any] = Field(default_factory=dict)
    active: bool = True

class FrameworkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: uuid.UUID
    framework_type: str
    name: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=100)
    configuration: dict[str, Any] = Field(default_factory=dict)
    controls: list[ControlInput] = Field(default_factory=list, max_length=300)

class CheckInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: uuid.UUID
    framework_id: uuid.UUID
    subject_type: str = Field(min_length=1,max_length=100)
    subject_id: str = Field(min_length=1,max_length=200)
    observed: dict[str, Any] = Field(max_length=300)
    evidence_references: list[str] = Field(default_factory=list,max_length=100)

class RemediationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner_id: uuid.UUID | None = None
    due_at: datetime | None = None

class RemediationTransitionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: uuid.UUID
    status: str = Field(pattern="^(open|in_progress|resolved|cancelled)$")
    owner_id: uuid.UUID | None = None
    due_at: datetime | None = None
    resolution_reference: str | None = None
