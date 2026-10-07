"""Authentication and RBAC dependencies for review actions.

Permissions are the shared project Permission enum and ``require_permission``
implementation; this module does not create a reviewer role or role table.
"""
from app.auth.dependencies import TenantContext, require_permission
from app.auth.permissions import Permission

review_reader = require_permission(Permission.GOVERNANCE_READ)
review_assigner = require_permission(Permission.GOVERNANCE_WRITE)
review_starter = require_permission(Permission.GOVERNANCE_WRITE)
review_decider = require_permission(Permission.GOVERNANCE_APPROVE)
review_canceller = require_permission(Permission.GOVERNANCE_WRITE)
review_expirer = require_permission(Permission.GOVERNANCE_WRITE)

__all__ = [
    "TenantContext", "review_reader", "review_assigner", "review_starter",
    "review_decider", "review_canceller", "review_expirer",
]
