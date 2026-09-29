"""Durable human-review lifecycle vocabulary.

The enum values are persisted as strings so state transitions remain explicit
and migration-compatible.  A case is never inferred from an assignment or a
provider response; the review service owns the state machine.
"""
from __future__ import annotations

import enum


class ReviewCaseStatus(str, enum.Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ReviewAssignmentStatus(str, enum.Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class ReviewDecisionType(str, enum.Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"


class ReviewPriority(str, enum.Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


TERMINAL_CASE_STATES = frozenset({
    ReviewCaseStatus.APPROVED.value,
    ReviewCaseStatus.REJECTED.value,
    ReviewCaseStatus.CANCELLED.value,
    ReviewCaseStatus.EXPIRED.value,
})
