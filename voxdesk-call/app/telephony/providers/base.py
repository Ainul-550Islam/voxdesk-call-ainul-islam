"""Provider-neutral telephony boundary.

Business code talks to these results, not to a Twilio, Telnyx or Vonage object.
An adapter that cannot perform an operation raises a typed error. It does not
return a successful empty result.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.telephony.capabilities import CapabilitySet
from app.telephony.provider_errors import TelephonyError


@dataclass(frozen=True)
class CallResult:
    provider: str
    external_id: str
    status: str
    to_number: str = ""
    from_number: str = ""

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "external_id": self.external_id,
            "status": self.status,
            "to_number": self.to_number,
            "from_number": self.from_number,
        }


@dataclass(frozen=True)
class NumberResult:
    provider: str
    e164: str
    external_id: str = ""
    capabilities: CapabilitySet = field(default_factory=CapabilitySet)
    country: str = ""
    region: str = ""

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "e164": self.e164,
            "external_id": self.external_id,
            "capabilities": self.capabilities.as_dict(),
            "country": self.country,
            "region": self.region,
        }


@dataclass(frozen=True)
class RecordingResult:
    provider: str
    external_id: str
    status: str

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "external_id": self.external_id,
            "status": self.status,
        }


@dataclass(frozen=True)
class HealthResult:
    provider: str
    ok: bool
    reason: str
    probed: bool = False

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "ok": self.ok,
            "reason": self.reason,
            "probed": self.probed,
        }


@dataclass(frozen=True)
class WebhookVerdict:
    valid: bool
    reason: str

    def as_dict(self) -> dict:
        return {"valid": self.valid, "reason": self.reason}


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    body: dict


class TelephonyAdapter(ABC):
    """Operations the product actually uses. Subclasses do not invent extras."""

    name: str

    @abstractmethod
    def configured(self) -> bool:
        """True only when the credentials this adapter needs are present."""

    @abstractmethod
    def require_configured(self) -> None:
        """Raise ``ProviderConfigurationError`` when credentials are absent."""

    @abstractmethod
    def health(self) -> HealthResult:
        """Configuration health. Not a live probe unless ``probed`` is true."""

    @abstractmethod
    def capabilities(self) -> CapabilitySet:
        """What this deployment has confirmed, not what the vendor sells."""

    @abstractmethod
    async def create_outbound(
        self, *, to_number: str, from_number: str, answer_url: str = ""
    ) -> CallResult:
        """Place an outbound call. Must not return an id the provider did not issue."""

    @abstractmethod
    async def hangup(self, external_id: str) -> CallResult:
        """Ask the provider to end a live call."""

    @abstractmethod
    async def transfer(self, external_id: str, destination: str) -> CallResult:
        """Move a live call. Reuse existing transfer TwiML for Twilio."""

    @abstractmethod
    async def search_numbers(self, *, country: str, limit: int = 10) -> list[NumberResult]:
        """Search provider inventory. Does not persist and does not purchase."""

    @abstractmethod
    async def provision_number(self, e164: str, *, country: str = "") -> NumberResult:
        """Reserve or purchase. Fail if the provider did not accept the number."""

    @abstractmethod
    async def release_number(self, external_id: str) -> NumberResult:
        """Release a provider-owned number. An empty id is a validation error."""

    @abstractmethod
    async def start_recording(self, external_call_id: str) -> RecordingResult:
        """Start a provider recording. Does not invent a recording id."""

    @abstractmethod
    async def stop_recording(
        self, external_call_id: str, recording_id: str = ""
    ) -> RecordingResult:
        """Stop a provider recording."""

    @abstractmethod
    async def verify_webhook(self, request) -> WebhookVerdict:
        """Provider signature check. Invalid is the only failure result."""


def require_body(result: HttpResult, *, provider: str) -> dict:
    """Return a JSON object from a 2xx response, or raise the mapped error."""
    from app.telephony.provider_errors import ProviderUnavailableError, from_http_status

    if result.status_code < 200 or result.status_code >= 300:
        raise from_http_status(result.status_code, provider=provider)
    if not isinstance(result.body, dict):
        raise ProviderUnavailableError("Provider response was not an object", provider=provider)
    return result.body


def raise_if_error(exc: Exception, *, provider: str) -> TelephonyError:
    """Normalize transport failures. Callers re-raise the returned error."""
    from app.telephony.provider_errors import ProviderTimeoutError, ProviderUnavailableError

    name = type(exc).__name__
    if "Timeout" in name:
        return ProviderTimeoutError("Provider timed out", provider=provider)
    return ProviderUnavailableError("Provider request failed", provider=provider)
