"""Environment foundation: development, staging and production metadata.

No deployment is executed from here. Selected business resources carry
``environment_id`` in addition to ``tenant_id``. API keys may optionally bind
one environment. Billing, SSO and service accounts remain tenant-scoped.
"""

from app.environments.models import EnvironmentKind, EnvironmentStatus

__all__ = ["EnvironmentKind", "EnvironmentStatus"]
