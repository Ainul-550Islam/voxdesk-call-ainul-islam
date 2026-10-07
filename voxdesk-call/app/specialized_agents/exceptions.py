"""Typed failures for the specialized-agent boundary."""

from __future__ import annotations

from app.tenancy.isolation import HierarchyError


class SpecializedAgentError(HierarchyError):
    """Base error translated by API routes without exposing internals."""

    status_code = 409
    code = "specialized_agent_error"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None):
        super().__init__(message, code=code or self.code, status_code=status_code or self.status_code)


class UnknownAgentError(SpecializedAgentError):
    code = "unknown_agent"
    status_code = 422

    def __init__(self, agent_type: str):
        super().__init__(f"Unknown specialized agent type: {agent_type}", code=self.code, status_code=self.status_code)


class AgentExecutionError(SpecializedAgentError):
    code = "agent_execution_failed"
    status_code = 502


class SourceValidationError(SpecializedAgentError):
    code = "invalid_source"
    status_code = 422


class ReviewRequiredError(SpecializedAgentError):
    code = "human_review_required"
    status_code = 409


class ConfidenceValidationError(SpecializedAgentError):
    code = "invalid_confidence"
    status_code = 422


class SpecializedPolicyDenied(SpecializedAgentError):
    code = "policy_denied"
    status_code = 403


class ModelAdmissionError(SpecializedAgentError):
    code = "model_admission_denied"
    status_code = 403


class UnsupportedFeatureError(SpecializedAgentError):
    code = "unsupported_feature"
    status_code = 422


class InputLimitError(SpecializedAgentError):
    code = "input_limit_exceeded"
    status_code = 413


class IdempotencyConflictError(SpecializedAgentError):
    code = "idempotency_conflict"
    status_code = 409


class DataValidationError(SpecializedAgentError):
    code = "invalid_analytical_input"
    status_code = 422
