"""Closed vocabularies for the durable governance subsystem.

These are strings on purpose. Governance rows are versioned records whose
values must remain readable across deployments; transitions are validated in
``model_registry`` and ``service`` rather than being implicit enum casts.
"""

from __future__ import annotations

import enum


class GovernancePolicyStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    RETIRED = "retired"


class GovernancePolicyType(str, enum.Enum):
    RUNTIME_ACCESS = "runtime_access"
    MODEL_APPROVAL = "model_approval"
    TOOL_ACCESS = "tool_access"
    DATA_USAGE = "data_usage"
    RETENTION = "retention"
    RESIDENCY = "residency"


class DecisionType(str, enum.Enum):
    ALLOW = "allow"
    DENY = "deny"
    CONDITION = "condition"


class RiskTier(str, enum.Enum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class RiskAssessmentStatus(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    RETIRED = "retired"


class ModelRegistryStatus(str, enum.Enum):
    REGISTERED = "registered"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class ModelVersionStatus(str, enum.Enum):
    REGISTERED = "registered"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    SUSPENDED = "suspended"
    RETIRED = "retired"


class ModelEvaluationStatus(str, enum.Enum):
    NOT_EVALUATED = "not_evaluated"
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class EvidenceActorType(str, enum.Enum):
    USER = "user"
    SERVICE = "service"
    SYSTEM = "system"


class EvidenceEventType(str, enum.Enum):
    POLICY_DECISION = "policy_decision"
    POLICY_PUBLISHED = "policy_published"
    RISK_ASSESSED = "risk_assessed"
    RISK_APPROVED = "risk_approved"
    MODEL_REGISTERED = "model_registered"
    MODEL_VERSION_REGISTERED = "model_version_registered"
    MODEL_APPROVED = "model_approved"
    MODEL_SUSPENDED = "model_suspended"
    MODEL_RETIRED = "model_retired"
    LINEAGE_CAPTURED = "lineage_captured"
    RESIDENCY_REQUESTED = "residency_requested"
    RETENTION_CHANGED = "retention_changed"
    ATTESTATION_ISSUED = "attestation_issued"
    EVIDENCE_PACKAGE_CREATED = "evidence_package_created"


class AttestationStatus(str, enum.Enum):
    DRAFT = "draft"
    ISSUED = "issued"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ResidencyIntentStatus(str, enum.Enum):
    REQUESTED = "requested"
    CONFIGURED = "configured"
    VERIFIED = "verified"
    REJECTED = "rejected"


class RetentionRuleStatus(str, enum.Enum):
    ACTIVE = "active"
    RETIRED = "retired"


class EvidencePackageStatus(str, enum.Enum):
    GENERATED = "generated"
    INVALID = "invalid"


MODEL_REGISTRY_TRANSITIONS: dict[ModelRegistryStatus, frozenset[ModelRegistryStatus]] = {
    ModelRegistryStatus.REGISTERED: frozenset(
        {ModelRegistryStatus.ACTIVE, ModelRegistryStatus.SUSPENDED, ModelRegistryStatus.RETIRED}
    ),
    ModelRegistryStatus.ACTIVE: frozenset(
        {ModelRegistryStatus.SUSPENDED, ModelRegistryStatus.RETIRED}
    ),
    ModelRegistryStatus.SUSPENDED: frozenset(
        {ModelRegistryStatus.ACTIVE, ModelRegistryStatus.RETIRED}
    ),
    ModelRegistryStatus.RETIRED: frozenset(),
}

MODEL_VERSION_TRANSITIONS: dict[ModelVersionStatus, frozenset[ModelVersionStatus]] = {
    ModelVersionStatus.REGISTERED: frozenset(
        {ModelVersionStatus.SUBMITTED, ModelVersionStatus.SUSPENDED, ModelVersionStatus.RETIRED}
    ),
    ModelVersionStatus.SUBMITTED: frozenset(
        {ModelVersionStatus.APPROVED, ModelVersionStatus.SUSPENDED, ModelVersionStatus.RETIRED}
    ),
    ModelVersionStatus.APPROVED: frozenset(
        {ModelVersionStatus.SUSPENDED, ModelVersionStatus.RETIRED}
    ),
    ModelVersionStatus.SUSPENDED: frozenset(
        {ModelVersionStatus.APPROVED, ModelVersionStatus.RETIRED}
    ),
    ModelVersionStatus.RETIRED: frozenset(),
}


TERMINAL_EVIDENCE_STATUSES = frozenset(
    {AttestationStatus.REVOKED.value, AttestationStatus.EXPIRED.value}
)
