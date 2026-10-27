"""Domain errors for the platform surfaces, and their HTTP translation.

Every failure a route can produce is one of these, with a machine-readable
``code`` and a message written for an operator. The point is not tidiness: a
WHM forecast that refuses because there are 9 days of history, a domain that is
still ``pending_dns`` and a solve that cannot satisfy the labour rules are three
different conversations, and a generic 400 would start all of them from zero.

Nothing here ever carries a secret, a provider payload or another tenant's
identifier — the translation to HTTP is a straight copy of ``code``/``message``
with no repr of the exception.
"""

from __future__ import annotations

from typing import Any


class PlatformV2Error(Exception):
    """Base class: a caller mistake or a refused operation, never a crash."""

    status_code = 400
    code = "platform_v2_error"

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}

    def as_body(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


class ValidationFailed(PlatformV2Error):
    status_code = 422
    code = "validation_failed"


class PermissionDenied(PlatformV2Error):
    status_code = 403
    code = "permission_denied"


class NotFound(PlatformV2Error):
    status_code = 404
    code = "not_found"


class Conflict(PlatformV2Error):
    status_code = 409
    code = "conflict"


class InsufficientHistory(PlatformV2Error):
    """A forecast was asked for with too little history to be honest about it."""

    status_code = 422
    code = "insufficient_history"


class InfeasibleSchedule(PlatformV2Error):
    """The labour rules, coverage targets and available people cannot coexist.

    Carries the report from ``wfm.scheduler`` in ``detail`` — including what was
    infeasible — because "no schedule" without a reason is what makes people
    override constraints by hand.
    """

    status_code = 422
    code = "schedule_infeasible"


class SolverUnavailable(PlatformV2Error):
    """OR-Tools is not installed in this deployment."""

    status_code = 503
    code = "solver_unavailable"


class DomainNotVerified(PlatformV2Error):
    status_code = 409
    code = "domain_not_verified"


class DomainTaken(PlatformV2Error):
    """The domain belongs to another tenant.

    Deliberately the same message whether the domain is active, pending or
    merely claimed: a distinct message per state would let anyone enumerate the
    platform's customers by trying hostnames.
    """

    status_code = 409
    code = "domain_unavailable"


class ProviderRejected(PlatformV2Error):
    """A push/ACME/DNS provider refused the request; the reason is safe to show."""

    status_code = 502
    code = "provider_rejected"


def to_http(exc: PlatformV2Error):
    """Translate a domain error into the API's documented error envelope."""
    from fastapi import HTTPException

    return HTTPException(status_code=exc.status_code, detail=exc.as_body())


__all__ = [
    "Conflict",
    "DomainNotVerified",
    "DomainTaken",
    "InfeasibleSchedule",
    "InsufficientHistory",
    "NotFound",
    "PermissionDenied",
    "PlatformV2Error",
    "ProviderRejected",
    "SolverUnavailable",
    "ValidationFailed",
    "to_http",
]
