"""Twilio adapter over the existing client, transfer TwiML and webhook check.

This does not replace ``twilio_handler``, ``outbound.place_call`` or
``provider.TwilioProvider``. Those remain the live call path. This class is
the normalized boundary used by number, recording and provider selection.
"""

from __future__ import annotations

from app.core.config import settings
from app.telephony.capabilities import CapabilitySet
from app.telephony.phone import normalize
from app.telephony.provider_errors import (
    ProviderConfigurationError,
    ProviderNotFoundError,
    ProviderStateError,
    ProviderUnavailableError,
    ProviderValidationError,
    from_http_status,
)
from app.telephony.providers.base import (
    CallResult,
    HealthResult,
    NumberResult,
    RecordingResult,
    WebhookVerdict,
)
from app.telephony.providers.webhook_verifier import verify as verify_provider_webhook


class TwilioAdapter:
    name = "twilio"

    def configured(self) -> bool:
        return bool(
            (settings.twilio_account_sid or "").strip()
            and (settings.twilio_auth_token or "").strip()
        )

    def require_configured(self) -> None:
        if not self.configured():
            raise ProviderConfigurationError(
                "Twilio account SID and auth token are not configured",
                provider=self.name,
            )

    def health(self) -> HealthResult:
        if not self.configured():
            return HealthResult(self.name, False, "credentials_missing", probed=False)
        return HealthResult(self.name, True, "credentials_present", probed=False)

    def capabilities(self) -> CapabilitySet:
        """Confirmed by this deployment's configuration, not by a live account probe."""
        voice = self.configured()
        return CapabilitySet(
            voice=voice,
            sms=voice and bool((settings.twilio_phone_number or "").strip()),
            mms=False,
            whatsapp=bool((settings.twilio_whatsapp_number or "").strip()),
            recording=voice,
            transcription=False,
            source="configuration" if voice else "unconfirmed",
        )

    async def create_outbound(
        self, *, to_number: str, from_number: str, answer_url: str = ""
    ) -> CallResult:
        self.require_configured()
        destination = normalize(to_number)
        caller = normalize(from_number)
        client = self._client()
        url = answer_url or f"{settings.public_base_url.rstrip('/')}/telephony/outbound-answer"
        callback = f"{settings.public_base_url.rstrip('/')}/telephony/status"
        try:
            created = client.calls.create(
                to=destination,
                from_=caller,
                url=url,
                status_callback=callback,
                status_callback_event=["completed", "no-answer", "busy", "failed"],
                timeout=25,
            )
        except Exception as exc:
            raise self._map(exc) from exc
        sid = getattr(created, "sid", "") or ""
        if not sid:
            raise ProviderUnavailableError("Twilio did not return a call id", provider=self.name)
        return CallResult(self.name, sid, "queued", to_number=destination, from_number=caller)

    async def hangup(self, external_id: str) -> CallResult:
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        client = self._client()
        try:
            updated = client.calls(external_id).update(status="completed")
        except Exception as exc:
            raise self._map(exc) from exc
        return CallResult(
            self.name, getattr(updated, "sid", external_id) or external_id, "completed"
        )

    async def transfer(self, external_id: str, destination: str) -> CallResult:
        """Reuse the existing redirect client and transfer TwiML. Do not dial a second way."""
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        dest = normalize(destination)
        try:
            from app.telephony.provider import TwilioProvider
            from app.telephony.transfer import transfer_twiml
        except ImportError as exc:
            raise ProviderUnavailableError(
                "Twilio SDK is not installed", provider=self.name
            ) from exc

        result = await TwilioProvider().redirect_call(external_id, transfer_twiml(dest))
        if not result.ok:
            if result.error_code == "20404":
                raise ProviderNotFoundError("Call is not in progress", provider=self.name)
            if result.error_code == "sdk_missing":
                raise ProviderUnavailableError("Twilio SDK is not installed", provider=self.name)
            raise ProviderStateError("Twilio refused the transfer", provider=self.name)
        return CallResult(self.name, external_id, "transfer_requested", to_number=dest)

    async def search_numbers(self, *, country: str, limit: int = 10) -> list[NumberResult]:
        self.require_configured()
        code = (country or "").strip().upper()
        if len(code) != 2:
            raise ProviderValidationError("Country must be a 2-letter code", provider=self.name)
        client = self._client()
        try:
            rows = client.available_phone_numbers(code).local.list(limit=max(1, min(limit, 20)))
        except Exception as exc:
            raise self._map(exc) from exc
        found = []
        for row in rows or []:
            number = getattr(row, "phone_number", "") or ""
            if not number:
                continue
            features = []
            if getattr(row, "capabilities", None):
                caps = row.capabilities
                if isinstance(caps, dict):
                    features = [name for name, enabled in caps.items() if enabled]
            found.append(
                NumberResult(
                    self.name,
                    number,
                    "",
                    capabilities=_features(features, code),
                    country=code,
                )
            )
        return found

    async def provision_number(self, e164: str, *, country: str = "") -> NumberResult:
        self.require_configured()
        number = normalize(e164)
        client = self._client()
        try:
            created = client.incoming_phone_numbers.create(phone_number=number)
        except Exception as exc:
            raise self._map(exc) from exc
        sid = getattr(created, "sid", "") or ""
        if not sid:
            raise ProviderUnavailableError("Twilio did not return a number id", provider=self.name)
        return NumberResult(
            self.name, number, sid, capabilities=self.capabilities(), country=country
        )

    async def release_number(self, external_id: str) -> NumberResult:
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Number id is required", provider=self.name)
        client = self._client()
        try:
            client.incoming_phone_numbers(external_id).delete()
        except Exception as exc:
            raise self._map(exc) from exc
        return NumberResult(self.name, "", external_id)

    async def start_recording(self, external_call_id: str) -> RecordingResult:
        self.require_configured()
        if not external_call_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        client = self._client()
        try:
            created = client.calls(external_call_id).recordings.create()
        except Exception as exc:
            raise self._map(exc) from exc
        sid = getattr(created, "sid", "") or ""
        if not sid:
            raise ProviderUnavailableError(
                "Twilio did not return a recording id", provider=self.name
            )
        return RecordingResult(self.name, sid, "recording")

    async def stop_recording(
        self, external_call_id: str, recording_id: str = ""
    ) -> RecordingResult:
        self.require_configured()
        if not external_call_id or not recording_id:
            raise ProviderValidationError(
                "Call id and recording id are required", provider=self.name
            )
        client = self._client()
        try:
            client.calls(external_call_id).recordings(recording_id).update(status="stopped")
        except Exception as exc:
            raise self._map(exc) from exc
        return RecordingResult(self.name, recording_id, "processing")

    async def verify_webhook(self, request) -> WebhookVerdict:
        return await verify_provider_webhook(self.name, request)

    def _client(self):
        try:
            from twilio.rest import Client
        except ImportError as exc:
            raise ProviderUnavailableError(
                "Twilio SDK is not installed", provider=self.name
            ) from exc
        return Client(settings.twilio_account_sid, settings.twilio_auth_token)

    def _map(self, exc: Exception):
        code = getattr(exc, "status", None) or getattr(exc, "code", None)
        try:
            status = int(code)
        except (TypeError, ValueError):
            status = 0
        if status:
            return from_http_status(status, provider=self.name)
        if type(exc).__name__ == "TwilioRestException":
            return ProviderStateError("Twilio rejected the operation", provider=self.name)
        return ProviderUnavailableError("Twilio request failed", provider=self.name)


def _features(names: list[str], country: str):
    from app.telephony.capabilities import from_provider_features

    lowered = [name.lower() for name in names]
    # Twilio's available-number payload uses voice/SMS/MMS, not recording.
    return from_provider_features(lowered, country=country, source="provider_api")
