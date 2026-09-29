"""Pydantic contracts shared by specialized-agent APIs and services."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .enums import RiskTier


class SpecializedModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SourceReferenceIn(BaseModel):
    document_id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=200)
    source_title: str = Field(min_length=1, max_length=500)
    content_fingerprint: str = Field(pattern=r"^[0-9a-fA-F]{64}$")
    page_number: int | None = Field(default=None, ge=1)
    character_start: int | None = Field(default=None, ge=0)
    character_end: int | None = Field(default=None, ge=0)
    retrieval_timestamp: dt.datetime | None = None


class EvidenceReference(SpecializedModel):
    event_id: uuid.UUID | None
    event_hash: str | None
    status: str
    event_type: str


class SpecializedExecutionRequest(BaseModel):
    environment_id: uuid.UUID
    model_version_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=200)
    risk_tier: str = Field(default=RiskTier.MODERATE.value, min_length=1, max_length=24)
    agent_version: str = Field(default="1.0.0", min_length=1, max_length=100)
    locale: str | None = Field(default=None, max_length=32)
    language: str | None = Field(default=None, max_length=32)
    source_references: list[SourceReferenceIn] = Field(default_factory=list, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class SpecializedExecutionResponse(SpecializedModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    environment_id: uuid.UUID
    request_id: str
    trace_id: str
    agent_type: str
    agent_version: str
    model_version_id: uuid.UUID
    risk_tier: str
    status: str
    input_fingerprint: str
    output_fingerprint: str | None
    policy_decision_id: uuid.UUID | None
    lineage_root_id: uuid.UUID | None
    evidence_root_hash: str | None
    review_required: bool
    review_state: str
    result: dict[str, Any]
    failure_code: str | None
    started_at: dt.datetime | None
    completed_at: dt.datetime | None
    created_at: dt.datetime
    updated_at: dt.datetime


class AgentCapabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    type: str
    name: str
    version: str
    status: str
    risk_tier: str
    capabilities: list[str]
    supported_inputs: list[str]
    supported_outputs: list[str]
    required_controls: list[str]


class ExecutionContextMetadata(BaseModel):
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    environment_id: uuid.UUID
    actor_id: uuid.UUID
    request_id: str
    trace_id: str
    agent_type: str
    agent_version: str
    policy_version: int | None
    model_version_id: uuid.UUID
    risk_tier: str
    locale: str | None = None
    language: str | None = None
    created_at: dt.datetime


class ExecutionOutput(BaseModel):
    status: str
    review_required: bool
    review_state: str
    result: dict[str, Any]
    evidence: EvidenceReference | None = None
    lineage_root_id: uuid.UUID | None = None


class IdempotencyKey(BaseModel):
    key: str = Field(min_length=8, max_length=200)

    @field_validator("key")
    @classmethod
    def no_control_chars(cls, value: str) -> str:
        if any(ord(char) < 32 for char in value):
            raise ValueError("idempotency key contains a control character")
        return value.strip()
