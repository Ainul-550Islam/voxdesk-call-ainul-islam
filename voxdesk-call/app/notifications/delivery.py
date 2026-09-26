"""Channel dispatch. Adapters return a normalized outcome. No giant branch owns I/O."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class DispatchResult:
    outcome: str
    category: str = ""
    provider: str = ""


class ChannelAdapter(Protocol):
    name: str

    async def send(self, *, recipient: str, body: str) -> DispatchResult: ...


class InAppAdapter:
    name = "in_app"

    async def send(self, *, recipient: str, body: str) -> DispatchResult:
        return DispatchResult("accepted", provider=self.name)


class InternalAlertAdapter:
    name = "internal_alert"

    async def send(self, *, recipient: str, body: str) -> DispatchResult:
        return DispatchResult("accepted", provider=self.name)


class SmsAdapter:
    name = "sms"

    async def send(self, *, recipient: str, body: str) -> DispatchResult:
        if not recipient.startswith("+") or not recipient[1:].isdigit():
            return DispatchResult("permanent_failure", "invalid_recipient", self.name)
        return DispatchResult("accepted", provider=self.name)


class WebhookChannelAdapter:
    name = "webhook"

    async def send(self, *, recipient: str, body: str) -> DispatchResult:
        from app.core.ssrf import OutboundUrlError, validate_outbound_url

        try:
            validate_outbound_url(recipient, require_https=True)
        except OutboundUrlError:
            return DispatchResult("permanent_failure", "invalid_configuration", self.name)
        return DispatchResult("accepted", provider=self.name)


class EmailChannelAdapter:
    name = "email"

    def __init__(self, provider=None) -> None:
        self.provider = provider

    async def send(self, *, recipient: str, body: str) -> DispatchResult:
        if "@" not in recipient or recipient.startswith("@") or recipient.endswith("@"):
            return DispatchResult("permanent_failure", "invalid_recipient", self.name)
        if self.provider is None:
            return DispatchResult("permanent_failure", "invalid_configuration", self.name)
        result = await self.provider.send_raw(recipient=recipient, body=body)
        return DispatchResult(result.outcome, result.category, "email")


def default_adapters() -> dict[str, ChannelAdapter]:
    return {
        "in_app": InAppAdapter(),
        "internal_alert": InternalAlertAdapter(),
        "sms": SmsAdapter(),
        "webhook": WebhookChannelAdapter(),
        "email": EmailChannelAdapter(),
    }


async def dispatch(
    channel: str, *, recipient: str, body: str, adapters: dict | None = None
) -> DispatchResult:
    chosen = (adapters or default_adapters()).get(channel)
    if chosen is None:
        return DispatchResult("permanent_failure", "unsupported_channel", channel)
    return await chosen.send(recipient=recipient, body=body)
