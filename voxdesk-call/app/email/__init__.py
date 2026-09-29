"""Outbound email interfaces."""

from .delivery import EmailMessage, EmailProvider, EmailSendResult
from .providers import EmailConfig, EmailConfigurationError, build_provider
from .service import send

__all__ = [
    "EmailMessage",
    "EmailProvider",
    "EmailSendResult",
    "EmailConfig",
    "EmailConfigurationError",
    "build_provider",
    "send",
]
