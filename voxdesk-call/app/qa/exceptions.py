"""Typed QA failures.

Cross-tenant misses stay ``BoundaryDenied`` (404) in the tenancy package.
These errors are for a caller who is already inside the tenant.
"""

from __future__ import annotations


class QaError(Exception):
    code = "qa_error"
    status_code = 409

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message}


class ReviewNotFound(QaError):
    code = "review_not_found"
    status_code = 404


class ScorecardNotFound(QaError):
    code = "scorecard_not_found"
    status_code = 404


class InvalidTransition(QaError):
    code = "invalid_transition"
    status_code = 409


class ReviewerNotAuthorized(QaError):
    code = "reviewer_not_authorized"
    status_code = 403


class InvalidRubric(QaError):
    code = "invalid_rubric"
    status_code = 422


class InvalidEvidence(QaError):
    code = "invalid_evidence"
    status_code = 422


class InvalidScore(QaError):
    code = "invalid_score"
    status_code = 422


class CalibrationConflict(QaError):
    code = "calibration_conflict"
    status_code = 409


class DuplicateAutoReview(QaError):
    code = "duplicate_auto_review"
    status_code = 409


class ExportNotAuthorized(QaError):
    code = "export_not_authorized"
    status_code = 403


class StaleReview(QaError):
    code = "stale_review"
    status_code = 409
