"""Provider-neutral email transport with only real HTTP/SMTP adapters."""

from __future__ import annotations
from dataclasses import dataclass
import httpx
from app.core.ssrf import validate_resolved_outbound_url
from app.core.tracing import span
from app.security.secret_store import get_secret_store, SecretStoreError
from .delivery import EmailMessage, EmailSendResult, SmtpEmailProvider, validate_recipient


class EmailConfigurationError(RuntimeError):
    """Email provider configuration is incomplete or unsafe."""


@dataclass(frozen=True)
class EmailConfig:
    provider: str
    secret_ref: str | None
    from_address: str
    domain: str = ""
    endpoint: str = ""


class HttpEmailProvider:
    def __init__(self, config: EmailConfig):
        self.config = config
        if config.provider not in {"resend", "sendgrid", "mailgun"}:
            raise EmailConfigurationError("email adapter is not implemented")
        if not validate_recipient(config.from_address):
            raise EmailConfigurationError("sender address is invalid")
        if config.secret_ref is None:
            raise EmailConfigurationError("email provider secret reference is required")

    def _secret(self, tenant_id: str) -> str:
        try:
            return get_secret_store().get(
                tenant_id, f"email:{self.config.provider}", self.config.secret_ref or ""
            )
        except SecretStoreError as exc:
            raise EmailConfigurationError("email provider secret is unavailable") from exc

    async def send(self, message: EmailMessage) -> EmailSendResult:
        if not validate_recipient(message.recipient):
            return EmailSendResult("permanent_failure", "invalid_recipient")
        key = self._secret(message.tenant_id)
        if self.config.provider == "resend":
            url = self.config.endpoint or "https://api.resend.com/emails"
            await validate_resolved_outbound_url(url, require_https=True)
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {
                "from": self.config.from_address,
                "to": [message.recipient],
                "subject": message.subject,
                "text": message.body,
            }
            auth = None
        elif self.config.provider == "sendgrid":
            url = self.config.endpoint or "https://api.sendgrid.com/v3/mail/send"
            await validate_resolved_outbound_url(url, require_https=True)
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            payload = {
                "personalizations": [{"to": [{"email": message.recipient}]}],
                "from": {"email": self.config.from_address},
                "subject": message.subject,
                "content": [{"type": "text/plain", "value": message.body}],
            }
            auth = None
        else:
            if not self.config.domain or any(ch in self.config.domain for ch in "/:@"):
                raise EmailConfigurationError("mailgun domain is invalid")
            url = (
                self.config.endpoint or f"https://api.mailgun.net/v3/{self.config.domain}/messages"
            )
            await validate_resolved_outbound_url(url, require_https=True)
            headers, payload, auth = (
                {},
                {
                    "from": self.config.from_address,
                    "to": message.recipient,
                    "subject": message.subject,
                    "text": message.body,
                },
                ("api", key),
            )
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                with span(
                    "voxdesk.email.send", tenant_id=message.tenant_id, provider=self.config.provider
                ):
                    response = await client.post(
                        url,
                        headers=headers,
                        json=payload if self.config.provider != "mailgun" else None,
                        data=payload if self.config.provider == "mailgun" else None,
                        auth=auth,
                    )
        except (httpx.TimeoutException, httpx.TransportError):
            return EmailSendResult("retryable_failure", "network")
        if response.status_code in {408, 429} or response.status_code >= 500:
            return EmailSendResult("retryable_failure", f"provider_http_{response.status_code}")
        if response.status_code >= 400:
            return EmailSendResult("permanent_failure", f"provider_http_{response.status_code}")
        return EmailSendResult("accepted")


def build_provider(config: EmailConfig):
    if config.provider == "smtp":
        return SmtpEmailProvider()
    return HttpEmailProvider(config)
