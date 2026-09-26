"""Tenant hierarchy helpers.

Tenancy here means the existing ``Tenant`` row plus the organization and
environment that now surround it. It is not a second authentication system
and not a many-to-many membership model.
"""

from app.tenancy.exceptions import (
    BoundaryDenied,
    Conflict,
    FlagDenied,
    Forbidden,
    HierarchyError,
    LifecycleDenied,
    LimitDenied,
    NotFound,
    ResidencyDenied,
    ValidationFailed,
)

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
