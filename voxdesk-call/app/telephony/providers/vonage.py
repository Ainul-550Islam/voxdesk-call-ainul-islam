"""Vonage number API and voice boundary.

Number search, purchase and cancel use the REST number API when the API key
and secret are configured. Voice calls need an application id and a private
key signed as RS256. This runtime does not pretend a call was placed when
that signer is unavailable.
"""

from __future__ import annotations

from app.core.value_types import list_value

from app.core.config import settings
from app.telephony.capabilities import CapabilitySet, from_provider_features
from app.telephony.phone import normalize
from app.telephony.provider_errors import (
    ProviderConfigurationError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    UnsupportedCapability,
)
from app.telephony.providers.base import (
    CallResult,
    HealthResult,
    HttpResult,
    NumberResult,
    RecordingResult,
    TelephonyAdapter,
    WebhookVerdict,
    normalize_generic_webhook_payload,
    raise_if_error,
    require_body,
)
from app.telephony.providers.webhook_verifier import verify as verify_provider_webhook

_NUMBER_BASE = "https://rest.nexmo.com"


class VonageAdapter(TelephonyAdapter):
    name = "vonage"

    def __init__(self, transport=None) -> None:
        self._transport = transport

    def configured(self) -> bool:
        return bool(
            (settings.vonage_api_key or "").strip() and (settings.vonage_api_secret or "").strip()
        )

    def require_configured(self) -> None:
        if not self.configured():
            raise ProviderConfigurationError(
                "Vonage API key and secret are not configured",
                provider=self.name,
            )

    def voice_configured(self) -> bool:
        return bool(
            (settings.vonage_application_id or "").strip()
            and (settings.vonage_private_key or "").strip()
        )

    def health(self) -> HealthResult:
        if not self.configured():
            return HealthResult(self.name, False, "credentials_missing", probed=False)
        return HealthResult(self.name, True, "credentials_present", probed=False)

    def capabilities(self) -> CapabilitySet:
        # Sub-Phase 2F: Honest capability declaration — Vonage voice/streaming
        # is disabled (voice=False) until RS256 NCCO WebSocket media streaming is deployed.
        return CapabilitySet(
            voice=False,
            sms=False,
            mms=False,
            whatsapp=False,
            recording=False,
            transcription=False,
            source="unconfirmed",
        )

    async def create_outbound(
        self, *, to_number: str, from_number: str, answer_url: str = ""
    ) -> CallResult:
        self.require_configured()
        normalize(to_number)
        normalize(from_number)
        if not self.voice_configured():
            raise ProviderConfigurationError(
                "Vonage application id and private key are required for voice calls",
                provider=self.name,
            )
        if not _signer_available():
            raise ProviderUnavailableError(
                "Vonage voice signing library is not installed",
                provider=self.name,
            )
        raise UnsupportedCapability(
            "Vonage voice call signing is not implemented in this adapter",
            provider=self.name,
        )

    async def hangup(self, external_id: str) -> CallResult:
        return await self._voice_unavailable(external_id)

    async def transfer(self, external_id: str, destination: str) -> CallResult:
        normalize(destination)
        return await self._voice_unavailable(external_id)

    async def search_numbers(self, *, country: str, limit: int = 10) -> list[NumberResult]:
        self.require_configured()
        code = (country or "").strip().upper()
        if len(code) != 2:
            raise ProviderValidationError("Country must be a 2-letter code", provider=self.name)
        body = await self._numbers(
            "GET",
            "/number/search",
            {"country": code, "size": str(max(1, min(limit, 20)))},
        )
        rows = list_value(body.get("numbers"))
        found = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            msisdn = str(row.get("msisdn") or "")
            if not msisdn:
                continue
            features = [str(item).lower() for item in (row.get("features") or [])]
            e164 = msisdn if msisdn.startswith("+") else f"+{msisdn}"
            found.append(
                NumberResult(
                    self.name,
                    e164,
                    msisdn,
                    capabilities=from_provider_features(
                        features, country=code, source="provider_api"
                    ),
                    country=code,
                )
            )
        return found

    async def provision_number(self, e164: str, *, country: str = "") -> NumberResult:
        self.require_configured()
        number = normalize(e164)
        code = (country or "").strip().upper()
        if len(code) != 2:
            raise ProviderValidationError(
                "Country is required to purchase a Vonage number", provider=self.name
            )
        msisdn = number.lstrip("+")
        body = await self._numbers("POST", "/number/buy", {"country": code, "msisdn": msisdn})
        if str(body.get("error-code") or body.get("error_code") or "200") not in {"200", ""}:
            raise ProviderUnavailableError(
                "Vonage did not accept the number purchase", provider=self.name
            )
        return NumberResult(self.name, number, msisdn, country=code)

    async def release_number(self, external_id: str) -> NumberResult:
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Number id is required", provider=self.name)
        await self._numbers("POST", "/number/cancel", {"msisdn": external_id.lstrip("+")})
        return NumberResult(self.name, "", external_id)

    async def start_recording(self, external_call_id: str) -> RecordingResult:
        self.require_configured()
        if not external_call_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        raise UnsupportedCapability("Vonage recording is not implemented", provider=self.name)

    async def stop_recording(
        self, external_call_id: str, recording_id: str = ""
    ) -> RecordingResult:
        self.require_configured()
        if not external_call_id and not recording_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        raise UnsupportedCapability("Vonage recording is not implemented", provider=self.name)

    async def verify_webhook(self, request) -> WebhookVerdict:
        return await verify_provider_webhook(self.name, request)

    def normalize_webhook_event(self, payload):
        return normalize_generic_webhook_payload(self.name, payload)

    async def send_whatsapp(self, *_args, **_kwargs) -> None:
        raise UnsupportedCapability("Vonage WhatsApp is not implemented", provider=self.name)

    async def _voice_unavailable(self, external_id: str) -> CallResult:
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        if not self.voice_configured():
            raise ProviderConfigurationError(
                "Vonage application id and private key are required for voice calls",
                provider=self.name,
            )
        raise UnsupportedCapability("Vonage voice control is not implemented", provider=self.name)

    async def _numbers(self, method: str, path: str, params: dict) -> dict:
        # The secret is a request parameter, never a log field and never a result field.
        payload = {
            "api_key": settings.vonage_api_key,
            "api_secret": settings.vonage_api_secret,
            **params,
        }
        url = _NUMBER_BASE + path
        try:
            result = await self._send(method, url, payload)
        except (ProviderTimeoutError, ProviderUnavailableError):
            raise
        except Exception as exc:
            raise raise_if_error(exc, provider=self.name) from exc
        body = require_body(result, provider=self.name)
        error_code = str(body.get("error-code") or "")
        if error_code and error_code not in {"200", "0"}:
            raise ProviderUnavailableError(
                "Vonage rejected the number operation", provider=self.name
            )
        return body

    async def _send(self, method: str, url: str, payload: dict) -> HttpResult:
        timeout = float(settings.telephony_http_timeout_seconds or 8)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if self._transport is not None:
            return await self._transport(
                method, url, headers=headers, json=payload, timeout=timeout
            )
        import httpx

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    response = await client.get(url, params=payload)
                else:
                    response = await client.post(url, data=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Vonage timed out", provider=self.name) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("Vonage request failed", provider=self.name) from exc
        try:
            parsed = response.json()
            body = parsed if isinstance(parsed, dict) else {}
        except ValueError:
            body = {}
        return HttpResult(response.status_code, body)


def _signer_available() -> bool:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return False
    return True
