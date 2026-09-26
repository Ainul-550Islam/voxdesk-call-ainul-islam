"""Environment-scoped business resources.

``tenant_id`` remains the authoritative isolation key. ``environment_id`` is
an additional boundary on the eight resources registered here.
"""

from app.environments.resource_types import EnvironmentResourceType

__all__ = ["EnvironmentResourceType"]
