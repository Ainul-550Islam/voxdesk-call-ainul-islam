"""Environment foundation: development, staging and production metadata.

No deployment is executed from here. Selected business resources carry
``environment_id`` in addition to ``tenant_id``. Billing, SSO, API keys and
service accounts do not.
"""

from app.environments.models import EnvironmentKind, EnvironmentStatus

__all__ = ["EnvironmentKind", "EnvironmentStatus"]
