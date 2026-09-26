"""Organization is the parent of one or more tenants.

This package does not replace authentication, RBAC, billing, or the existing
``Tenant`` row. A user still belongs to exactly one tenant. Organization
membership is a binding on that user and uses the existing role enum.
"""

from app.organization.models import OrganizationStatus

__all__ = ["OrganizationStatus"]
