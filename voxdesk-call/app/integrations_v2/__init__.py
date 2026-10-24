"""Enterprise Helpdesk, CRM v2, Collaboration, Sync, and Agent Tools subsystem (Batch 05)."""

from app.integrations_v2.core.errors import (
    AuthExpired,
    CapabilityUnsupported,
    IntegrationV2Error,
    ProviderRejected,
    RateLimited,
    SSRFBlocked,
    WebhookSignatureError,
)
from app.integrations_v2.core.provider_base import (
    AuthKind,
    Capability,
    HealthStatus,
    ProviderV2,
    VerificationStatus,
)

__all__ = [
    "AuthExpired",
    "AuthKind",
    "Capability",
    "CapabilityUnsupported",
    "HealthStatus",
    "IntegrationV2Error",
    "ProviderRejected",
    "ProviderV2",
    "RateLimited",
    "SSRFBlocked",
    "VerificationStatus",
    "WebhookSignatureError",
]
