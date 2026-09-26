"""Resource-scope errors. Routes render them with the existing hierarchy mapper."""

from __future__ import annotations

from app.tenancy.isolation import BoundaryDenied, Forbidden, LifecycleDenied, ValidationFailed


class ResourceScopeError(ValidationFailed):
    def __init__(self, message: str = "Invalid resource scope") -> None:
        super().__init__(message)
        self.code = "invalid_resource_scope"


class EnvironmentMismatch(BoundaryDenied):
    def __init__(self) -> None:
        super().__init__("Not found")
        self.code = "not_found"


class TenantMismatch(BoundaryDenied):
    def __init__(self) -> None:
        super().__init__("Not found")
        self.code = "not_found"


class EnvironmentSuspended(LifecycleDenied):
    def __init__(self) -> None:
        super().__init__("This environment is suspended")
        self.code = "environment_suspended"


class EnvironmentArchived(LifecycleDenied):
    def __init__(self) -> None:
        super().__init__("This environment is archived")
        self.code = "environment_archived"


class ResourceUnauthorized(Forbidden):
    def __init__(self, message: str = "Insufficient permissions") -> None:
        super().__init__(message)
        self.code = "forbidden"


class InvalidResourceTransition(LifecycleDenied):
    def __init__(self, message: str = "That resource transition is not allowed") -> None:
        super().__init__(message)
        self.code = "invalid_transition"
