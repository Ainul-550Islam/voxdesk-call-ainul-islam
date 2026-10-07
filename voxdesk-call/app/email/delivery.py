"""Production email adapter.

The provider comes from settings when one is already configured. A log
transport is refused in production. Credentials are never logged. Tests inject
their own provider; that adapter is not the production path.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

from app.core.config import settings
from app.core.logging import log


@dataclass(frozen=True)
class EmailMessage:
    tenant_id: str
    recipient: str
    subject: str
    body: str
    template_name: str = ""
    category: str = "operational"


@dataclass(frozen=True)
class EmailSendResult:
    outcome: str
    category: str = ""


class EmailProvider(Protocol):
    async def send(self, message: EmailMessage) -> EmailSendResult: ...


def recipient_hash(recipient: str) -> str:
    return hashlib.sha256(recipient.strip().lower().encode()).hexdigest()


def validate_recipient(recipient: str) -> bool:
    if "@" not in recipient:
        return False
    local, _, domain = recipient.partition("@")
    return bool(local) and "." in domain and " " not in recipient


class SmtpEmailProvider:
    """Uses the existing SMTP settings. The password is never interpolated into a log."""

    async def send(self, message: EmailMessage) -> EmailSendResult:
        if not validate_recipient(message.recipient):
            return EmailSendResult("permanent_failure", "invalid_recipient")
        if not settings.smtp_host:
            return EmailSendResult("permanent_failure", "invalid_configuration")
        try:
            from app.auth.identity.email import RenderedEmail, _send_smtp
            import asyncio

            rendered = RenderedEmail(
                to=message.recipient,
                subject=message.subject,
                text_body=message.body,
                html_body="",
            )
            await asyncio.to_thread(_send_smtp, rendered)
        except Exception as exc:
            name = type(exc).__name__
            log.info("email.provider_failed", error_type=name, template=message.template_name)
            if name in {"SMTPRecipientsRefused", "SMTPSenderRefused"}:
                return EmailSendResult("permanent_failure", "invalid_recipient")
            return EmailSendResult("retryable_failure", "provider_5xx")
        log.info(
            "email.accepted",
            template=message.template_name,
            recipient_hash=recipient_hash(message.recipient)[:12],
        )
        return EmailSendResult("accepted")

    async def send_raw(self, *, recipient: str, body: str) -> EmailSendResult:
        return await self.send(
            EmailMessage(tenant_id="", recipient=recipient, subject="", body=body)
        )


class ConfiguredEmailProvider:
    """Production path. ``log`` transport is not a provider in production."""

    def __init__(self) -> None:
        if settings.is_production and settings.email_transport == "log":
            raise RuntimeError("production email cannot use the log transport")
        self._smtp = SmtpEmailProvider()

    async def send(self, message: EmailMessage) -> EmailSendResult:
        if settings.email_transport == "smtp":
            return await self._smtp.send(message)
        if settings.is_production:
            return EmailSendResult("permanent_failure", "invalid_configuration")
        log.info(
            "email.log_transport",
            template=message.template_name,
            recipient_hash=recipient_hash(message.recipient)[:12],
        )
        return EmailSendResult("accepted")


def classify_provider_error(category: str) -> str:
    if category in {"timeout", "network", "provider_5xx", "rate_limit"}:
        return "retryable"
    return "permanent"


async def record(
    session,
    *,
    tenant_id,
    recipient: str,
    status: str,
    template_name: str = "",
    category: str = "",
    environment_id=None,
    attempt_count: int = 1,
):
    """Store a delivery fact. The recipient is hashed; the address is not a column."""
    from app.db.models import EmailDelivery

    row = EmailDelivery(
        tenant_id=tenant_id,
        environment_id=environment_id,
        recipient_hash=recipient_hash(recipient),
        template_name=template_name[:120],
        status=status,
        attempt_count=attempt_count,
        error_category=category[:64],
    )
    session.add(row)
    await session.flush()
    return row
