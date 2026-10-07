"""Pydantic domain schemas, state machine, and validators for Conductor (Prompt 4).

Enforces strict schema validation on all Conductor requests, proposed change
operations, evidence links, human approval actions, and apply payloads.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import BadRequestError, ConflictError
from app.security.policy import (
    MAX_PROPOSAL_CHANGES,
    classify_change_risk,
    classify_path_section as classify_path_section,
    validate_mutation_operation,
    validate_mutation_path,
    validate_natural_language_request,
    validate_value_safety,
)


class ConductorSessionStatus(str, Enum):
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    EXPIRED = "EXPIRED"
    ARCHIVED = "ARCHIVED"


class ConductorProposalStatus(str, Enum):
    DRAFT = "DRAFT"
    GENERATING = "GENERATING"
    PROPOSED = "PROPOSED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    SIMULATING = "SIMULATING"
    SIMULATED = "SIMULATED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    APPROVED = "APPROVED"
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    STALE = "STALE"


class ConductorOperationType(str, Enum):
    SET = "set"
    REPLACE = "replace"
    ADD = "add"
    REMOVE = "remove"
    APPEND = "append"
    DELETE = "delete"
    MOVE = "move"


class ConductorApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ConductorRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ConductorEvidenceSource(str, Enum):
    AGENT_VERSION = "agent_version"
    CALL = "call"
    QA_SCORECARD = "qa_scorecard"
    TEST_SUITE = "test_suite"
    TEST_CASE = "test_case"
    TEST_RUN = "test_run"
    EVALUATION_RESULT = "evaluation_result"
    VALIDATION = "validation"
    TOOL_REGISTRY = "tool_registry"
    KNOWLEDGE_BASE = "knowledge_base"
    WORKFLOW = "workflow"


ALLOWED_PROPOSAL_TRANSITIONS: dict[ConductorProposalStatus, frozenset[ConductorProposalStatus]] = {
    ConductorProposalStatus.DRAFT: frozenset(
        {
            ConductorProposalStatus.GENERATING,
            ConductorProposalStatus.PROPOSED,
            ConductorProposalStatus.FAILED,
            ConductorProposalStatus.REJECTED,
        }
    ),
    ConductorProposalStatus.GENERATING: frozenset(
        {
            ConductorProposalStatus.PROPOSED,
            ConductorProposalStatus.FAILED,
        }
    ),
    ConductorProposalStatus.PROPOSED: frozenset(
        {
            ConductorProposalStatus.VALIDATING,
            ConductorProposalStatus.VALIDATED,
            ConductorProposalStatus.SIMULATING,
            ConductorProposalStatus.READY_FOR_REVIEW,
            ConductorProposalStatus.PARTIALLY_APPROVED,
            ConductorProposalStatus.APPROVED,
            ConductorProposalStatus.REJECTED,
            ConductorProposalStatus.STALE,
            ConductorProposalStatus.FAILED,
        }
    ),
    ConductorProposalStatus.VALIDATING: frozenset(
        {
            ConductorProposalStatus.VALIDATED,
            ConductorProposalStatus.READY_FOR_REVIEW,
            ConductorProposalStatus.FAILED,
            ConductorProposalStatus.STALE,
        }
    ),
    ConductorProposalStatus.VALIDATED: frozenset(
        {
            ConductorProposalStatus.SIMULATING,
            ConductorProposalStatus.SIMULATED,
            ConductorProposalStatus.READY_FOR_REVIEW,
            ConductorProposalStatus.PARTIALLY_APPROVED,
            ConductorProposalStatus.APPROVED,
            ConductorProposalStatus.REJECTED,
            ConductorProposalStatus.STALE,
            ConductorProposalStatus.FAILED,
        }
    ),
    ConductorProposalStatus.SIMULATING: frozenset(
        {
            ConductorProposalStatus.SIMULATED,
            ConductorProposalStatus.READY_FOR_REVIEW,
            ConductorProposalStatus.FAILED,
            ConductorProposalStatus.STALE,
        }
    ),
    ConductorProposalStatus.SIMULATED: frozenset(
        {
            ConductorProposalStatus.READY_FOR_REVIEW,
            ConductorProposalStatus.PARTIALLY_APPROVED,
            ConductorProposalStatus.APPROVED,
            ConductorProposalStatus.REJECTED,
            ConductorProposalStatus.STALE,
            ConductorProposalStatus.FAILED,
        }
    ),
    ConductorProposalStatus.READY_FOR_REVIEW: frozenset(
        {
            ConductorProposalStatus.VALIDATING,
            ConductorProposalStatus.VALIDATED,
            ConductorProposalStatus.SIMULATING,
            ConductorProposalStatus.SIMULATED,
            ConductorProposalStatus.PARTIALLY_APPROVED,
            ConductorProposalStatus.APPROVED,
            ConductorProposalStatus.REJECTED,
            ConductorProposalStatus.STALE,
            ConductorProposalStatus.FAILED,
        }
    ),
    ConductorProposalStatus.PARTIALLY_APPROVED: frozenset(
        {
            ConductorProposalStatus.READY_FOR_REVIEW,
            ConductorProposalStatus.PARTIALLY_APPROVED,
            ConductorProposalStatus.APPROVED,
            ConductorProposalStatus.REJECTED,
            ConductorProposalStatus.APPLYING,
            ConductorProposalStatus.STALE,
            ConductorProposalStatus.FAILED,
        }
    ),
    ConductorProposalStatus.APPROVED: frozenset(
        {
            ConductorProposalStatus.READY_FOR_REVIEW,
            ConductorProposalStatus.PARTIALLY_APPROVED,
            ConductorProposalStatus.APPROVED,
            ConductorProposalStatus.REJECTED,
            ConductorProposalStatus.APPLYING,
            ConductorProposalStatus.STALE,
            ConductorProposalStatus.FAILED,
        }
    ),
    ConductorProposalStatus.APPLYING: frozenset(
        {
            ConductorProposalStatus.APPLIED,
            ConductorProposalStatus.STALE,
            ConductorProposalStatus.FAILED,
        }
    ),
    ConductorProposalStatus.REJECTED: frozenset(
        {
            ConductorProposalStatus.READY_FOR_REVIEW,
            ConductorProposalStatus.PARTIALLY_APPROVED,
            ConductorProposalStatus.APPROVED,
            ConductorProposalStatus.REJECTED,
            ConductorProposalStatus.STALE,
        }
    ),
    ConductorProposalStatus.APPLIED: frozenset(),
    ConductorProposalStatus.FAILED: frozenset(
        {
            ConductorProposalStatus.VALIDATING,
            ConductorProposalStatus.READY_FOR_REVIEW,
            ConductorProposalStatus.STALE,
        }
    ),
    ConductorProposalStatus.STALE: frozenset(
        {
            ConductorProposalStatus.REJECTED,
        }
    ),
}


def coerce_proposal_status(value: ConductorProposalStatus | str) -> ConductorProposalStatus:
    if isinstance(value, ConductorProposalStatus):
        return value
    if hasattr(value, "value"):
        return ConductorProposalStatus(str(value.value))
    s = str(value).strip()
    if s.startswith("ConductorProposalStatus."):
        s = s.split(".", 1)[1]
    return ConductorProposalStatus(s.upper())


def enforce_proposal_transition(
    current: ConductorProposalStatus | str,
    target: ConductorProposalStatus | str,
) -> ConductorProposalStatus:
    """Validate and return target proposal status or raise ConflictError (HTTP 409)."""
    cur = coerce_proposal_status(current)
    tgt = coerce_proposal_status(target)
    if cur == tgt and cur != ConductorProposalStatus.APPLIED:
        return tgt
    allowed = ALLOWED_PROPOSAL_TRANSITIONS.get(cur, frozenset())
    if tgt not in allowed:
        raise ConflictError(
            f"Invalid ConductorProposal state transition from {cur.value} to {tgt.value}."
        )
    return tgt


# ------------------------------------------------------------ Request Schemas


class ConductorContextPolicySpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    include_calls: bool = True
    include_qa_scorecards: bool = True
    include_test_runs: bool = True
    include_tools: bool = True
    include_knowledge_bases: bool = True
    include_workflows: bool = True
    call_ids: list[str] = Field(default_factory=list, max_length=20)
    test_run_ids: list[str] = Field(default_factory=list, max_length=20)
    suite_ids: list[str] = Field(default_factory=list, max_length=10)


class ConductorSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str = Field(..., min_length=1, max_length=80)
    agent_kind: str = Field(default="voice", pattern="^(voice|chat)$")
    base_version_number: int | None = Field(default=None, ge=1)
    environment_id: uuid.UUID | None = None
    origin_surface: str = Field(
        default="agent_builder",
        min_length=1,
        max_length=64,
    )
    request_text: str = Field(default="", max_length=8000)
    context_policy: ConductorContextPolicySpec = Field(
        default_factory=ConductorContextPolicySpec
    )

    @field_validator("request_text")
    @classmethod
    def _check_optional_request_text(cls, v: str) -> str:
        clean = str(v or "").strip()
        if clean:
            validate_natural_language_request(clean)
        return clean


class ProposedChangeOperationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=200)
    operation: ConductorOperationType = Field(default=ConductorOperationType.SET)
    old_value: Any = None
    new_value: Any = None
    from_path: str | None = Field(default=None, max_length=200)
    reason: str = Field(default="", max_length=2000)
    risk_level: ConductorRiskLevel | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _validate_change_operation(self) -> "ProposedChangeOperationInput":
        clean_path = validate_mutation_path(self.path)
        op_val = (
            self.operation.value
            if hasattr(self.operation, "value")
            else str(self.operation).split(".")[-1].lower()
        )
        validate_mutation_operation(op_val)
        if op_val == "move":
            if not self.from_path:
                raise BadRequestError("Operation 'move' requires 'from_path'.")
            validate_mutation_path(self.from_path)
        validate_value_safety(self.new_value, path=clean_path)
        if self.reason:
            validate_value_safety(self.reason, path=f"{clean_path}.reason")
        if self.risk_level is None:
            inferred = classify_change_risk(clean_path, op_val, self.new_value)
            self.risk_level = ConductorRiskLevel(inferred)
        self.path = clean_path
        return self


class ConductorPromptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: uuid.UUID | None = None
    agent_id: str | None = Field(default=None, max_length=80)
    agent_kind: str = Field(default="voice", pattern="^(voice|chat)$")
    base_version_number: int | None = Field(default=None, ge=1)
    request_text: str = Field(..., min_length=1, max_length=8000)
    origin_surface: str = Field(default="agent_builder", max_length=64)
    call_ids: list[str] = Field(default_factory=list, max_length=20)
    test_run_ids: list[str] = Field(default_factory=list, max_length=20)
    explicit_operations: list[ProposedChangeOperationInput] = Field(
        default_factory=list,
        max_length=MAX_PROPOSAL_CHANGES,
    )
    auto_validate: bool = True
    auto_simulate: bool = False
    auto_deploy: bool = False
    auto_publish: bool = False
    idempotency_key: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def _check_safety(self) -> "ConductorPromptRequest":
        self.request_text = validate_natural_language_request(
            self.request_text,
            auto_deploy=self.auto_deploy,
            auto_publish=self.auto_publish,
        )
        return self


class ConductorSimulateProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_messages: list[str] = Field(
        default_factory=lambda: [
            "Hello, how can you help me today?",
            "Can you confirm my appointment details?",
        ],
        max_length=20,
    )
    dynamic_variables: dict[str, Any] = Field(default_factory=dict)
    evaluation_rules: list[dict[str, Any]] = Field(default_factory=list, max_length=25)
    persist_reproduction_test_case: bool = True
    test_case_name: str | None = Field(default=None, max_length=200)
    suite_id: uuid.UUID | None = None
    allow_mock_fallback: bool = True


class ConductorChangeReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=1000)


class ConductorProposalReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(default="", max_length=1000)
    safe_only: bool = False


class ConductorApplyProposalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str | None = Field(default=None, max_length=128)
    expected_base_version_number: int | None = Field(default=None, ge=1)
    expected_base_config_hash: str | None = Field(default=None, max_length=96)
    version_notes: str = Field(default="", max_length=1000)
    auto_deploy: bool = False
    auto_publish: bool = False

    @model_validator(mode="after")
    def _reject_auto_production_flags(self) -> "ConductorApplyProposalRequest":
        if self.auto_deploy or self.auto_publish:
            raise BadRequestError(
                "Conductor apply creates a new immutable AgentVersion only and never auto-publishes to production."
            )
        return self


# ----------------------------------------------------------- Response Schemas


class ConductorEvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    proposal_id: uuid.UUID
    tenant_id: uuid.UUID
    source_type: str
    source_id: str
    evidence_summary: str
    evidence_payload: dict[str, Any]
    created_at: datetime


class ConductorApprovalEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    proposal_id: uuid.UUID
    change_id: uuid.UUID | None
    tenant_id: uuid.UUID
    actor_user_id: uuid.UUID | None
    action: str
    reason: str
    previous_state: str
    new_state: str
    created_at: datetime


class ConductorChangeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    proposal_id: uuid.UUID
    tenant_id: uuid.UUID
    sequence: int
    section: str
    path: str
    operation: str
    old_value: Any
    new_value: Any
    reason: str
    evidence_ids: list[str]
    risk_level: str
    validation_state: str
    validation_messages: list[str]
    simulation_state: str
    approval_state: str
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ConductorFieldDiffItem(BaseModel):
    change_id: str
    sequence: int
    section: str
    json_pointer: str
    path: str
    operation: str
    old_value: Any
    new_value: Any
    reason: str
    risk_level: str
    approval_state: str
    validation_state: str
    simulation_state: str
    is_text_diff: bool = False
    line_diff: list[str] = Field(default_factory=list)


class ConductorProposalDiffResponse(BaseModel):
    proposal_id: uuid.UUID
    agent_id: str
    base_version_number: int
    base_config_hash: str
    candidate_config_hash: str
    approved_candidate_config_hash: str
    diff_hash: str
    total_changes: int
    approved_changes: int
    rejected_changes: int
    pending_changes: int
    grouped_by_section: dict[str, list[ConductorFieldDiffItem]]
    changes: list[ConductorFieldDiffItem]
    side_by_side: dict[str, Any]


class ConductorProposalResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    tenant_id: uuid.UUID
    environment_id: uuid.UUID | None
    agent_id: str
    agent_kind: str
    base_agent_version_id: uuid.UUID | None
    base_version_number: int
    base_config_hash: str
    base_draft_etag: str
    base_config_snapshot: dict[str, Any]
    request_text: str
    summary: str
    rationale: str
    status: str
    validation_status: str
    validation_report: dict[str, Any]
    simulation_status: str
    simulation_summary: dict[str, Any]
    risk_summary: dict[str, Any]
    candidate_config_snapshot: dict[str, Any]
    final_candidate_hash: str
    resulting_agent_version_id: uuid.UUID | None
    resulting_version_number: int | None
    apply_idempotency_key: str | None
    approved_change_hash: str | None
    is_mock_provider: bool
    provider: str
    model: str
    correlation_id: str
    error_code: str | None
    error_message: str | None
    production_published: bool = False
    ready_to_publish: bool = False
    changes: list[ConductorChangeResponse] = Field(default_factory=list)
    evidence: list[ConductorEvidenceResponse] = Field(default_factory=list)
    approvals: list[ConductorApprovalEntryResponse] = Field(default_factory=list)
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    applied_at: datetime | None


class ConductorSessionResponse(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    environment_id: uuid.UUID | None
    agent_id: str
    agent_kind: str
    starting_agent_version_id: uuid.UUID | None
    starting_version_number: int
    caller_user_id: uuid.UUID | None
    origin_surface: str
    status: str
    request_text: str
    context_policy: dict[str, Any]
    context_snapshot: dict[str, Any]
    correlation_id: str
    proposals: list[ConductorProposalResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None


class ConductorApplyResultResponse(BaseModel):
    proposal: ConductorProposalResponse
    resulting_version: dict[str, Any]
    applied_change_ids: list[str]
    skipped_change_ids: list[str]
    base_version_number: int
    resulting_version_number: int
    base_config_hash: str
    resulting_config_hash: str
    production_published: bool = False
    ready_to_publish: bool = True
    idempotent_replay: bool = False
