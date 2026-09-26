"""Telnyx Call Control and number API.

Requests go to the documented v2 API. A missing key, connection id or HTTP
error is a typed failure. This adapter never invents a call id or a number id.
WhatsApp is not implemented.
"""

from __future__ import annotations

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
    WebhookVerdict,
    raise_if_error,
    require_body,
)
from app.telephony.providers.webhook_verifier import verify as verify_provider_webhook


class TelnyxAdapter:
    name = "telnyx"

    def __init__(self, transport=None) -> None:
        self._transport = transport

    def configured(self) -> bool:
        return bool((settings.telnyx_api_key or "").strip())

    def require_configured(self) -> None:
        if not self.configured():
            raise ProviderConfigurationError("Telnyx API key is not configured", provider=self.name)

    def health(self) -> HealthResult:
        if not self.configured():
            return HealthResult(self.name, False, "credentials_missing", probed=False)
        return HealthResult(self.name, True, "credentials_present", probed=False)

    def capabilities(self) -> CapabilitySet:
        # Voice and recording are confirmed only after a number payload says so.
        # A configured key alone does not mean every country supports every channel.
        return CapabilitySet(source="unconfirmed")

    async def create_outbound(
        self, *, to_number: str, from_number: str, answer_url: str = ""
    ) -> CallResult:
        self.require_configured()
        connection = (settings.telnyx_connection_id or "").strip()
        if not connection:
            raise ProviderConfigurationError(
                "Telnyx connection id is required for outbound calls",
                provider=self.name,
            )
        destination = normalize(to_number)
        caller = normalize(from_number)
        body = await self._request(
            "POST",
            "/calls",
            {
                "to": destination,
                "from": caller,
                "connection_id": connection,
                "webhook_url": answer_url
                or f"{settings.public_base_url.rstrip('/')}/telephony/telnyx",
            },
        )
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        external = str(data.get("call_control_id") or "")
        if not external:
            raise ProviderUnavailableError("Telnyx did not return a call id", provider=self.name)
        return CallResult(self.name, external, "queued", to_number=destination, from_number=caller)

    async def hangup(self, external_id: str) -> CallResult:
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        await self._request("POST", f"/calls/{external_id}/actions/hangup", {})
        return CallResult(self.name, external_id, "completed")

    async def transfer(self, external_id: str, destination: str) -> CallResult:
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        dest = normalize(destination)
        await self._request("POST", f"/calls/{external_id}/actions/transfer", {"to": dest})
        return CallResult(self.name, external_id, "transfer_requested", to_number=dest)

    async def search_numbers(self, *, country: str, limit: int = 10) -> list[NumberResult]:
        self.require_configured()
        code = (country or "").strip().upper()
        if len(code) != 2:
            raise ProviderValidationError("Country must be a 2-letter code", provider=self.name)
        body = await self._request(
            "GET",
            "/available_phone_numbers",
            None,
            query=f"filter[country_code]={code}&filter[limit]={max(1, min(limit, 20))}",
        )
        rows = body.get("data") if isinstance(body.get("data"), list) else []
        found = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            number = str(row.get("phone_number") or "")
            if not number:
                continue
            features = row.get("features") or row.get("capabilities") or []
            if isinstance(features, dict):
                features = [name for name, enabled in features.items() if enabled]
            found.append(
                NumberResult(
                    self.name,
                    number,
                    str(row.get("id") or ""),
                    capabilities=from_provider_features(
                        features, country=code, source="provider_api"
                    ),
                    country=code,
                    region=str(row.get("region_information") or "")[:32]
                    if isinstance(row.get("region_information"), str)
                    else "",
                )
            )
        return found

    async def provision_number(self, e164: str, *, country: str = "") -> NumberResult:
        self.require_configured()
        number = normalize(e164)
        body = await self._request(
            "POST",
            "/number_orders",
            {"phone_numbers": [{"phone_number": number}]},
        )
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        external = str(data.get("id") or "")
        if not external:
            raise ProviderUnavailableError(
                "Telnyx did not return a number order id", provider=self.name
            )
        features = []
        numbers = data.get("phone_numbers") if isinstance(data.get("phone_numbers"), list) else []
        if numbers and isinstance(numbers[0], dict):
            raw = numbers[0].get("features") or []
            features = raw if isinstance(raw, list) else []
        return NumberResult(
            self.name,
            number,
            external,
            capabilities=from_provider_features(features, country=country, source="provider_api"),
            country=country,
        )

    async def release_number(self, external_id: str) -> NumberResult:
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Number id is required", provider=self.name)
        await self._request("DELETE", f"/phone_numbers/{external_id}", None)
        return NumberResult(self.name, "", external_id)

    async def start_recording(self, external_call_id: str) -> RecordingResult:
        self.require_configured()
        if not external_call_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        body = await self._request(
            "POST",
            f"/calls/{external_call_id}/actions/record_start",
            {"format": "mp3", "channels": "single"},
        )
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        external = str(data.get("recording_id") or data.get("id") or "")
        if not external:
            raise ProviderUnavailableError(
                "Telnyx did not return a recording id", provider=self.name
            )
        return RecordingResult(self.name, external, "recording")

    async def stop_recording(
        self, external_call_id: str, recording_id: str = ""
    ) -> RecordingResult:
        self.require_configured()
        if not external_call_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        await self._request("POST", f"/calls/{external_call_id}/actions/record_stop", {})
        return RecordingResult(self.name, recording_id, "processing")

    async def verify_webhook(self, request) -> WebhookVerdict:
        return await verify_provider_webhook(self.name, request)

    async def send_whatsapp(self, *_args, **_kwargs) -> None:
        raise UnsupportedCapability("Telnyx WhatsApp is not implemented", provider=self.name)

    async def _request(self, method: str, path: str, payload: dict | None, query: str = "") -> dict:
        url = settings.telnyx_api_base.rstrip("/") + path
        if query:
            url = f"{url}?{query}"
        headers = {
            "Authorization": f"Bearer {settings.telnyx_api_key}",
            "Content-Type": "application/json",
        }
        try:
            result = await self._send(method, url, headers=headers, payload=payload)
        except ProviderTimeoutError:
            raise
        except ProviderUnavailableError:
            raise
        except Exception as exc:
            raise raise_if_error(exc, provider=self.name) from exc
        return require_body(result, provider=self.name)

    async def _send(
        self, method: str, url: str, *, headers: dict, payload: dict | None
    ) -> HttpResult:
        timeout = float(settings.telephony_http_timeout_seconds or 8)
        if self._transport is not None:
            return await self._transport(
                method, url, headers=headers, json=payload, timeout=timeout
            )
        import httpx

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(method, url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError("Telnyx timed out", provider=self.name) from exc
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError("Telnyx request failed", provider=self.name) from exc
        body = {}
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                body = parsed
        except ValueError:
            body = {}
        return HttpResult(response.status_code, body)
