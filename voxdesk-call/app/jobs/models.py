"""Durable job domain types.

SQLAlchemy mappings live in ``app.db.models``. These objects are the worker's
view of a row and the errors a handler is allowed to raise.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime


class JobState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTER = "dead_letter"
    CANCELLED = "cancelled"


class AttemptState(str, enum.Enum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


class PermanentJobError(Exception):
    """Do not retry. The job moves to the dead-letter state."""

    def __init__(self, category: str, message: str = "") -> None:
        super().__init__(message or category)
        self.category = category


class RetryableJobError(Exception):
    """Safe to retry until the attempt budget is exhausted."""

    def __init__(self, category: str, message: str = "") -> None:
        super().__init__(message or category)
        self.category = category


@dataclass(frozen=True)
class JobView:
    id: uuid.UUID
    organization_id: uuid.UUID | None
    tenant_id: uuid.UUID
    environment_id: uuid.UUID | None
    job_type: str
    payload: dict
    status: str
    attempt_count: int
    max_attempts: int
    worker_id: str
    idempotency_key: str
    available_at: datetime
    leased_until: datetime | None
