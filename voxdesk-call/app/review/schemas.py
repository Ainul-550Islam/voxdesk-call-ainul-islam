"""Strict HTTP and service contracts for human review."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import ReviewCaseStatus, ReviewDecisionType, ReviewPriority


class ReviewModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ReviewCaseCreate(ReviewModel):
    environment_id: uuid.UUID
    execution_id: uuid.UUID | None = None
    case_type: str = Field(min_length=1, max_length=64)
    agent_type: str = Field(min_length=1, max_length=32)
    subject_type: str = Field(default="specialized_execution", min_length=1, max_length=80)
    subject_id: str | None = Field(default=None, max_length=200)
    priority: ReviewPriority = ReviewPriority.NORMAL
    reason: str = Field(min_length=1, max_length=2000)
    requested_controls: list[str] = Field(default_factory=list, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReviewAssignmentRequest(ReviewModel):
    reviewer_id: uuid.UUID
    expires_at: dt.datetime | None = None


class ReviewDecisionRequest(ReviewModel):
    decision: ReviewDecisionType
    rationale: str = Field(min_length=1, max_length=10_000)
    evidence: dict[str, Any] = Field(default_factory=dict)


class RequestChangesRequest(ReviewModel):
    rationale: str = Field(min_length=1, max_length=10_000)
    evidence: dict[str, Any] = Field(default_factory=dict)


class ReviewCaseOut(ReviewModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    organization_id: uuid.UUID
    environment_id: uuid.UUID
    execution_id: uuid.UUID | None
    case_type: str
    agent_type: str
    subject_type: str
    subject_id: str
    status: str
    priority: str
    reason: str
    requested_controls: list[str]
    requested_by: uuid.UUID | None
    version: int
    metadata_json: dict[str, Any]
    created_at: dt.datetime
    updated_at: dt.datetime
    completed_at: dt.datetime | None


class ReviewAssignmentOut(ReviewModel):
    id: uuid.UUID
    review_case_id: uuid.UUID
    reviewer_id: uuid.UUID
    assigned_by: uuid.UUID | None
    status: str
    assignment_version: int
    assigned_at: dt.datetime
    completed_at: dt.datetime | None
    expires_at: dt.datetime | None


class ReviewDecisionOut(ReviewModel):
    id: uuid.UUID
    review_case_id: uuid.UUID
    reviewer_id: uuid.UUID
    decision: str
    rationale: str
    evidence: dict[str, Any]
    policy_decision_id: uuid.UUID | None
    evidence_event_id: uuid.UUID | None
    decision_version: int
    created_at: dt.datetime


class ReviewDetailOut(ReviewCaseOut):
    assignment: ReviewAssignmentOut | None = None
    decisions: list[ReviewDecisionOut] = Field(default_factory=list)


class ReviewQueueFilters(ReviewModel):
    environment_id: uuid.UUID | None = None
    status: ReviewCaseStatus | None = None
    priority: ReviewPriority | None = None
    case_type: str | None = Field(default=None, max_length=64)
    assigned_to: uuid.UUID | None = None
