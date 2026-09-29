"""Structured, fail-closed errors for governance operations."""

from __future__ import annotations

from app.tenancy.isolation import Conflict, HierarchyError, NotFound, ValidationFailed


class GovernanceError(HierarchyError):
    code = "governance_error"
    status_code = 409


class GovernanceNotFound(NotFound):
    code = "governance_not_found"


class GovernanceConflict(Conflict):
    code = "governance_conflict"


class GovernanceValidation(ValidationFailed):
    code = "governance_validation_failed"


class PolicyDenied(HierarchyError):
    code = "policy_denied"
    status_code = 403


class MissingGovernanceConfiguration(PolicyDenied):
    code = "governance_configuration_missing"


class InvalidTransition(GovernanceError):
    code = "invalid_governance_transition"


class EvidenceIntegrityError(GovernanceError):
    code = "evidence_integrity_error"


class EvidenceVerificationFailed(EvidenceIntegrityError):
    code = "evidence_verification_failed"


class VerificationRequired(PolicyDenied):
    code = "authoritative_verification_required"


class RetentionBlocked(GovernanceError):
    code = "retention_blocked"


class LegalHoldActive(RetentionBlocked):
    code = "legal_hold_active"


class ResidencyVerificationRequired(PolicyDenied):
    code = "residency_verification_required"
