"""Typed tenancy failures.

These are the hierarchy errors routes already translate. A new module must
not invent a second status vocabulary or a second tenant type.
"""

from __future__ import annotations

from app.tenancy.isolation import (
    BoundaryDenied,
    Conflict,
    Forbidden,
    HierarchyError,
    LifecycleDenied,
    NotFound,
    ValidationFailed,
)


class ResidencyDenied(HierarchyError):
    """A region label was refused. Nothing was stored."""

    def __init__(self, message: str = "Region is not supported") -> None:
        super().__init__(message, code="residency_denied", status_code=422)


class FlagDenied(HierarchyError):
    """A feature-flag write was refused. Nothing was stored."""

    def __init__(self, message: str = "Feature flag change is not allowed") -> None:
        super().__init__(message, code="flag_denied", status_code=403)


class LimitDenied(HierarchyError):
    """A quota value was refused before the billing resolver ran."""

    def __init__(self, message: str = "Quota value is invalid") -> None:
        super().__init__(message, code="limit_denied", status_code=422)


__all__ = [
    "BoundaryDenied",
    "Conflict",
    "FlagDenied",
    "Forbidden",
    "HierarchyError",
    "LifecycleDenied",
    "LimitDenied",
    "NotFound",
    "ResidencyDenied",
    "ValidationFailed",
]
