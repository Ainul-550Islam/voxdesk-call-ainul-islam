"""Boundary checks for the organization hierarchy.

A mismatch is not a permission error. Confirming that some other organization,
tenant or environment exists is itself a leak, so every cross-boundary result
is rendered as 404 with the same body as a missing row.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, Request

from app.db.models import Tenant


class HierarchyError(Exception):
    """A hierarchy rule failed. Routes translate this; they do not parse it."""

    status_code = 409
    code = "hierarchy_error"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class NotFound(HierarchyError):
    status_code = 404
    code = "not_found"

    def __init__(self, message: str = "Not found") -> None:
        super().__init__(message, code="not_found", status_code=404)


class BoundaryDenied(NotFound):
    """The principal is outside this organization, tenant or environment."""


class Conflict(HierarchyError):
    status_code = 409
    code = "conflict"

    def __init__(self, message: str) -> None:
        super().__init__(message, code="conflict", status_code=409)


class LifecycleDenied(HierarchyError):
    status_code = 409
    code = "lifecycle_denied"

    def __init__(self, message: str) -> None:
        super().__init__(message, code="lifecycle_denied", status_code=409)


class ValidationFailed(HierarchyError):
    status_code = 422
    code = "invalid"

    def __init__(self, message: str) -> None:
        super().__init__(message, code="invalid", status_code=422)


class Forbidden(HierarchyError):
    """The caller is inside the scope and the action is still refused.

    Cross-scope misses stay ``BoundaryDenied`` (404). This is only for a
    principal who already belongs here and lacks the permission or the role.
    """

    status_code = 403
    code = "forbidden"

    def __init__(self, message: str = "Insufficient permissions") -> None:
        super().__init__(message, code="forbidden", status_code=403)


def require_same_tenant(actor: Tenant, tenant_id: uuid.UUID) -> None:
    if actor.id != tenant_id:
        raise BoundaryDenied()


def require_same_organization(actor: Tenant, organization_id: uuid.UUID) -> None:
    if actor.organization_id != organization_id:
        raise BoundaryDenied()


def require_environment_tenant(environment_tenant_id: uuid.UUID, actor: Tenant) -> None:
    if environment_tenant_id != actor.id:
        raise BoundaryDenied()


def to_http(exc: HierarchyError) -> HTTPException:
    """Public body. 404 never explains which check failed."""
    if exc.status_code == 404:
        return HTTPException(status_code=404, detail={"code": "not_found", "message": "Not found"})
    return HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code, "message": exc.message},
    )


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]
