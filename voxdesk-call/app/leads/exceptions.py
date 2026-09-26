"""Lead-domain failures. Routes translate them; they do not parse messages."""

from __future__ import annotations

from app.tenancy.isolation import HierarchyError, NotFound


class LeadError(HierarchyError):
    """A lead rule failed inside the caller's tenant."""

    def __init__(self, message: str, *, code: str, status_code: int = 409) -> None:
        super().__init__(message, code=code, status_code=status_code)


class LeadNotFound(NotFound):
    """Missing in this tenant and environment. Same public body as a cross-tenant miss."""

    def __init__(self, message: str = "Not found") -> None:
        super().__init__(message)


class InvalidTransition(LeadError):
    def __init__(self, message: str = "That status change is not allowed") -> None:
        super().__init__(message, code="invalid_transition", status_code=409)


class DuplicateLead(LeadError):
    def __init__(self, message: str = "A lead with that phone or email already exists") -> None:
        super().__init__(message, code="duplicate_lead", status_code=409)


class MergeRejected(LeadError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="merge_rejected", status_code=409)


class ImportTooLarge(LeadError):
    def __init__(self, message: str = "Import exceeds the configured bound") -> None:
        super().__init__(message, code="import_too_large", status_code=413)


class InvalidSegment(LeadError):
    def __init__(self, message: str = "Segment definition is not allowed") -> None:
        super().__init__(message, code="invalid_segment", status_code=422)


class ClaimConflict(LeadError):
    def __init__(self, message: str = "The lead changed concurrently") -> None:
        super().__init__(message, code="claim_conflict", status_code=409)
