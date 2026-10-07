"""Pydantic contracts for the additive governance API surfaces."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GovernanceModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class PolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    policy_type: str = Field(min_length=1, max_length=40)
    rules: dict[str, Any] = Field(default_factory=dict)
    rationale: str | None = Field(default=None, max_length=4000)
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    organization_id: uuid.UUID | None = None
    environment_id: uuid.UUID | None = None


class PolicyOut(GovernanceModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    organization_id: uuid.UUID | None
    environment_id: uuid.UUID | None
    name: str
    policy_type: str
    status: str
    version: int
    rules: dict[str, Any]
    rationale: str | None
    effective_from: datetime | None
    effective_to: datetime | None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None


class PolicyDecisionRequest(BaseModel):
    policy_type: str = Field(min_length=1, max_length=40)
    input_payload: dict[str, Any] = Field(default_factory=dict)
    environment_id: uuid.UUID | None = None
    correlation_id: str | None = Field(default=None, max_length=128)


class PolicyDecisionOut(GovernanceModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    environment_id: uuid.UUID | None
    decision: str
    reason_code: str
    input_fingerprint: str
    policy_id: uuid.UUID | None
    policy_version: int | None
    correlation_id: str | None
    created_at: datetime


class RiskAssessmentCreate(BaseModel):
    subject_type: str = Field(min_length=1, max_length=60)
    subject_id: str = Field(min_length=1, max_length=200)
    tier: str | None = Field(default=None, min_length=1, max_length=24)
    risk_tier: str | None = Field(default=None, min_length=1, max_length=24)
    factors: dict[str, Any] = Field(default_factory=dict)
    required_controls: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=4000)
    environment_id: uuid.UUID | None = None


class RiskAssessmentOut(GovernanceModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    environment_id: uuid.UUID | None
    subject_type: str
    subject_id: str
    version: int
    tier: str
    risk_tier: str
    status: str
    factors: dict[str, Any]
    required_controls: list[str]
    rationale: str
    assessed_by: uuid.UUID | None
    approved_by: uuid.UUID | None
    assessed_at: datetime
    approved_at: datetime | None


class ModelRegistryCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=100)
    model_name: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    family: str | None = Field(default=None, max_length=200)
    purpose: str | None = Field(default=None, max_length=4000)
    risk_tier: str = Field(min_length=1, max_length=24)
    intended_use: str = Field(min_length=1, max_length=4000)
    data_classes: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    approved_for_channels: list[str] = Field(default_factory=list)
    approved_for_environments: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    organization_id: uuid.UUID | None = None


class ModelRegistryOut(GovernanceModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    organization_id: uuid.UUID | None
    provider: str
    model_name: str
    display_name: str
    family: str | None
    purpose: str | None
    status: str
    risk_tier: str
    intended_use: str
    data_classes: list[str]
    capabilities: list[str]
    approved_for_channels: list[str]
    approved_for_environments: list[str]
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ModelVersionCreate(BaseModel):
    version: str = Field(min_length=1, max_length=100)
    fingerprint: str | None = Field(default=None, min_length=32, max_length=128)
    artifact_uri: str = Field(min_length=1, max_length=2000)
    artifact_digest: str = Field(min_length=32, max_length=128)
    capabilities: list[str] = Field(default_factory=list)
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)
    evaluation_summary: dict[str, Any] = Field(default_factory=dict)


class ModelEvaluationRequest(BaseModel):
    verifier: str = Field(min_length=1, max_length=200)
    verification_reference: str = Field(min_length=1, max_length=200)
    passed: bool


class ModelVersionOut(GovernanceModel):
    id: uuid.UUID
    registry_id: uuid.UUID
    tenant_id: uuid.UUID | None
    organization_id: uuid.UUID | None
    provider: str | None
    model_name: str | None
    version: str
    fingerprint: str | None
    artifact_uri: str
    artifact_digest: str
    capabilities: list[str]
    input_modalities: list[str]
    output_modalities: list[str]
    status: str
    evaluation_status: str
    approval_reference: str | None
    evaluation_summary: dict[str, Any]
    created_at: datetime
    approved_at: datetime | None
    retired_at: datetime | None


class LineageCreate(BaseModel):
    correlation_id: str = Field(min_length=1, max_length=128)
    trace_id: str | None = Field(default=None, max_length=128)
    request_id: str | None = Field(default=None, max_length=128)
    subject_type: str | None = Field(default=None, max_length=80)
    subject_id: str | None = Field(default=None, max_length=200)
    source_type: str | None = Field(default=None, max_length=80)
    source_id: str | None = Field(default=None, max_length=200)
    parent_lineage_id: uuid.UUID | None = None
    tool_name: str | None = Field(default=None, max_length=200)
    input_payload: Any
    output_payload: Any | None = None
    tool_fingerprints: list[str] = Field(default_factory=list)
    source_references: list[str] = Field(default_factory=list)
    model_registry_id: uuid.UUID | None = None
    model_version_id: uuid.UUID | None = None
    decision_id: uuid.UUID | None = None
    environment_id: uuid.UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LineageOut(GovernanceModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    environment_id: uuid.UUID | None
    correlation_id: str
    trace_id: str | None
    request_id: str | None
    subject_type: str | None
    subject_id: str | None
    source_type: str | None
    source_id: str | None
    parent_lineage_id: uuid.UUID | None
    tool_name: str | None
    input_fingerprint: str
    output_fingerprint: str | None
    tool_fingerprints: list[str]
    source_references: list[str]
    model_registry_id: uuid.UUID | None
    model_version_id: uuid.UUID | None
    decision_id: uuid.UUID | None
    created_at: datetime


class EvidenceEventOut(GovernanceModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    environment_id: uuid.UUID | None
    chain_scope: str
    sequence: int
    sequence_number: int
    event_type: str
    actor_type: str
    actor_id: uuid.UUID | None
    subject_type: str | None
    subject_id: str | None
    correlation_id: str | None
    payload: dict[str, Any]
    payload_hash: str
    previous_hash: str | None
    event_hash: str
    chain_hash: str
    occurred_at: datetime
    created_at: datetime


class EvidenceQuery(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
    from_sequence: int | None = Field(default=None, ge=0)
    to_sequence: int | None = Field(default=None, ge=0)
    environment_id: uuid.UUID | None = None


class RetentionRuleCreate(BaseModel):
    evidence_type: str = Field(min_length=1, max_length=80)
    retention_days: int = Field(ge=1, le=36500)
    rationale: str = Field(min_length=1, max_length=4000)
    legal_hold: bool = False


class RetentionRuleOut(GovernanceModel):
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    organization_id: uuid.UUID | None
    evidence_type: str
    retention_days: int
    version: int
    active: bool
    legal_hold: bool
    rationale: str
    created_at: datetime


class ResidencyIntentCreate(BaseModel):
    region: str = Field(min_length=1, max_length=64)
    environment_id: uuid.UUID | None = None


class ResidencyVerification(BaseModel):
    verifier: str = Field(min_length=1, max_length=200)
    verification_reference: str = Field(min_length=1, max_length=200)
    verification_metadata: dict[str, Any] = Field(default_factory=dict)


class ResidencyIntentOut(GovernanceModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    environment_id: uuid.UUID | None
    requested_region: str
    status: str
    version: int
    physical_residency_proven: bool
    authoritative_verifier: str | None
    verification_reference: str | None
    verification_metadata: dict[str, Any]
    verified_at: datetime | None
    created_at: datetime


class AttestationCreate(BaseModel):
    attestation_type: str = Field(default="evidence_attestation", min_length=1, max_length=80)
    subject_type: str = Field(min_length=1, max_length=80)
    subject_id: str = Field(min_length=1, max_length=200)
    issuer: str = Field(min_length=1, max_length=200)
    period_start: datetime | None = None
    period_end: datetime | None = None
    claims: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None


class AttestationOut(GovernanceModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    environment_id: uuid.UUID | None
    attestation_type: str
    subject_type: str
    subject_id: str
    period_start: datetime | None
    period_end: datetime | None
    status: str
    issuer: str
    issued_by: uuid.UUID | None
    claims: dict[str, Any]
    metadata_json: dict[str, Any]
    evidence_root: str
    from_sequence: int | None
    to_sequence: int | None
    issued_at: datetime | None
    expires_at: datetime | None


class EvidencePackageRequest(BaseModel):
    from_sequence: int | None = Field(default=None, ge=0)
    to_sequence: int | None = Field(default=None, ge=0)
    environment_id: uuid.UUID | None = None


class EvidencePackageOut(GovernanceModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    environment_id: uuid.UUID | None
    status: str
    from_sequence: int | None
    to_sequence: int | None
    event_count: int
    evidence_root: str
    package_hash: str
    manifest: dict[str, Any]
    created_at: datetime
