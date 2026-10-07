"""Shared reliability primitives.

Job-specific retry and durable job semantics remain in ``app.jobs``. This
package adds request idempotency that uses the same PostgreSQL database as the
business resources, rather than process-local caches.
"""
from app.resilience.idempotency import (
    IdempotencyConflict,
    IdempotencyInProgress,
    IdempotencyPreviousFailure,
    RequestClaim,
    claim_request,
    complete_request,
    fail_request,
    request_digest,
)

__all__ = [
    "IdempotencyConflict",
    "IdempotencyInProgress",
    "IdempotencyPreviousFailure",
    "RequestClaim",
    "claim_request",
    "complete_request",
    "fail_request",
    "request_digest",
]
