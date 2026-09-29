"""Durable job queue public contracts.

The queue, persisted models, and handler map remain owned by their existing
modules. This package only re-exports stable vocabulary for domain callers.
"""
from app.jobs.types import (
    ExecutionResult,
    FailureClass,
    JobPayloadRejected,
    JobPriority,
    JobType,
    RetryDecision,
    ensure_payload_safe,
    handler_for,
    register_handler,
    register_handler_function,
    registered_job_types,
)

__all__ = [
    "ExecutionResult", "FailureClass", "JobPayloadRejected", "JobPriority",
    "JobType", "RetryDecision", "ensure_payload_safe", "handler_for",
    "register_handler", "register_handler_function", "registered_job_types",
]
