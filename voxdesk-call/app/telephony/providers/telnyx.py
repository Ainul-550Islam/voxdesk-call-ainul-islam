"""Telnyx Call Control and number API.

Requests go to the documented v2 API. A missing key, connection id or HTTP
error is a typed failure. This adapter never invents a call id or a number id.
WhatsApp is not implemented.

Batch 01 additions, all in the same adapter so there is one Telnyx credential
surface to review:

* :attr:`TelnyxAdapter.supports_ai_media_stream` -- the capability flag. It is
  true only when the API key, the webhook public key *and* the connection id are
  present. Anything less keeps it false and every media-stream entry point
  answers ``501 UNSUPPORTED_CAPABILITY``; a half-configured carrier streaming
  silence to a real caller is the failure mode this flag exists to prevent.
* Call Control v2 commands (:meth:`answer`, :meth:`transfer_call`,
  :meth:`hangup_call`, :meth:`send_dtmf`) with bounded retries on 5xx/timeout,
  **no** retry on 4xx, and a stable ``Idempotency-Key`` reused across attempts so
  a retried command cannot answer, transfer or hang up a call twice.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

from app.core.value_types import dictionary_value, list_value

from app.core.config import settings
from app.telephony.capabilities import CapabilitySet, from_provider_features
from app.telephony.phone import normalize
from app.telephony.provider_errors import (
    ProviderAuthenticationError,
    ProviderAuthorizationError,
    ProviderConfigurationError,
    ProviderRateLimitedError,
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


class TelnyxAdapter(TelephonyAdapter):
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

    # ------------------------------------------------- Telnyx AI media stream --

    @property
    def missing_media_stream_settings(self) -> tuple[str, ...]:
        """The credential names still missing for AI media streaming, in order."""
        missing = []
        if not (settings.telnyx_api_key or "").strip():
            missing.append("TELNYX_API_KEY")
        if not (settings.telnyx_public_key or "").strip():
            missing.append("TELNYX_PUBLIC_KEY")
        if not (settings.telnyx_connection_id or "").strip():
            missing.append("TELNYX_CONNECTION_ID")
        return tuple(missing)

    @property
    def supports_ai_media_stream(self) -> bool:
        """True only when the whole media-stream credential set is configured.

        Deliberately not a vendor claim: Telnyx does stream AI media, but this
        deployment can only do it when it holds the key that controls the call,
        the public key that proves a webhook came from Telnyx, and the connection
        id the stream is attached to.
        """
        return not self.missing_media_stream_settings

    def media_stream_capability(self) -> dict:
        """Machine-readable capability row. ``state`` is READY or NOT_CONFIGURED."""
        missing = self.missing_media_stream_settings
        return {
            "provider": self.name,
            "supports_ai_media_stream": not missing,
            "state": "NOT_CONFIGURED" if missing else "READY",
            "missing": list(missing),
            "serializer": "TelnyxMediaStream",
            "encodings": ["PCMU", "PCMA", "L16"],
            "sample_rates": [8000],
            "pipeline_sample_rate": 16000,
            "stream_endpoint": "/telephony/telnyx/ws",
            "call_control_api": "v2",
        }

    def require_ai_media_stream(self) -> None:
        """Raise ``UnsupportedCapability`` (UNSUPPORTED_CAPABILITY) when absent."""
        missing = self.missing_media_stream_settings
        if missing:
            raise UnsupportedCapability(
                "Telnyx AI media streaming is not configured; missing "
                + ", ".join(missing),
                provider=self.name,
            )

    # ------------------------------------------------- Telnyx Call Control v2 --

    def _call_control_idempotency_key(
        self, command: str, call_control_id: str, payload: dict | None
    ) -> str:
        """Stable, bounded key: retrying a command must not apply it twice."""
        material = json.dumps(
            {"command": command, "call": call_control_id, "payload": payload or {}},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:40]
        return f"voxdesk-telnyx-{command}-{digest}"

    async def answer(self, external_id: str) -> CallResult:
        """Answer a ringing inbound call (``call.answer``)."""
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        await self.call_control("answer", external_id, {})
        return CallResult(self.name, external_id, "in_progress")

    async def send_dtmf(self, external_id: str, digits: str) -> CallResult:
        """Send DTMF digits on a live call (``call.dtmf``)."""
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        cleaned = (digits or "").strip()
        if not cleaned:
            raise ProviderValidationError("Digits are required", provider=self.name)
        if len(cleaned) > 32 or any(character not in "0123456789*#ABCDabcd" for character in cleaned):
            raise ProviderValidationError("Digits must be DTMF characters only", provider=self.name)
        await self.call_control("send_dtmf", external_id, {"digits": cleaned})
        return CallResult(self.name, external_id, "dtmf_sent")

    async def hangup_call(self, external_id: str) -> CallResult:
        """Hang up a live call (``call.hangup``). Retried only when allowed."""
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        await self.call_control("hangup", external_id, {})
        return CallResult(self.name, external_id, "completed")

    async def transfer_call(
        self, external_id: str, destination: str, *, from_number: str = ""
    ) -> CallResult:
        """Warm transfer to another E.164 destination (``call.transfer``)."""
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        target = normalize(destination)
        payload: dict[str, str] = {"to": target}
        if from_number:
            payload["from"] = normalize(from_number)
        await self.call_control("transfer", external_id, payload)
        return CallResult(self.name, external_id, "transfer_requested", to_number=target)

    async def call_control(
        self,
        command: str,
        external_id: str,
        payload: dict,
        *,
        idempotency_key: str | None = None,
    ) -> dict:
        """Execute one Call Control v2 action with retries and idempotency.

        Retry policy: transport failures, timeouts, 429 and 5xx are retried up to
        ``TELNYX_CALL_CONTROL_MAX_RETRIES`` times with exponential backoff and
        the *same* idempotency key. 4xx (other than 429) means the carrier
        understood and refused the request; retrying cannot change the answer,
        so it surfaces immediately as a typed error.
        """
        self.require_configured()
        name = (command or "").strip().lower()
        if name not in {
            "answer",
            "hangup",
            "transfer",
            "send_dtmf",
            "record_start",
            "record_stop",
            "streaming_start",
            "streaming_stop",
        }:
            raise ProviderValidationError("Unsupported call-control command", provider=self.name)
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)

        key = idempotency_key or self._call_control_idempotency_key(name, external_id, payload)
        attempts = max(0, int(settings.telnyx_call_control_max_retries)) + 1
        backoff = float(settings.telnyx_call_control_retry_backoff_seconds or 0.0)
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                return await self._request(
                    "POST",
                    f"/calls/{external_id}/actions/{name}",
                    payload,
                    headers={"Idempotency-Key": key},
                    timeout=float(settings.telnyx_call_control_timeout_seconds or 8.0),
                )
            except (ProviderUnavailableError, ProviderTimeoutError, ProviderRateLimitedError) as exc:
                last_error = exc
                if attempt + 1 >= attempts:
                    break
                if backoff > 0:
                    await asyncio.sleep(backoff * (2**attempt))
            except (ProviderAuthenticationError, ProviderAuthorizationError) as exc:
                # A credential problem is never fixed by retrying.
                raise exc
        assert last_error is not None
        raise last_error

    async def start_media_stream(
        self, external_id: str, *, stream_url: str, client_state: str = ""
    ) -> dict:
        """Attach a bidirectional media stream to a live call (``streaming_start``).

        Refused with ``UNSUPPORTED_CAPABILITY`` unless the full credential set is
        present, so a call is never answered into a stream that cannot be
        authenticated.
        """
        self.require_ai_media_stream()
        if not (stream_url or "").startswith("wss://"):
            raise ProviderValidationError("A wss:// stream URL is required", provider=self.name)
        payload = {
            "stream_url": stream_url,
            "stream_bidirectional_mode": "rtp",
            "stream_bidirectional_codec": "PCMU",
        }
        if client_state:
            payload["client_state"] = client_state
        return await self.call_control("streaming_start", external_id, payload)

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
        """Abstract-adapter entry point; shares the retrying call-control path."""
        return await self.hangup_call(external_id)

    async def transfer(self, external_id: str, destination: str) -> CallResult:
        """Abstract-adapter entry point; shares the retrying call-control path."""
        return await self.transfer_call(external_id, destination)

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
        rows = list_value(body.get("data"))
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
        data = dictionary_value(body.get("data"))
        external = str(data.get("id") or "")
        if not external:
            raise ProviderUnavailableError(
                "Telnyx did not return a number order id", provider=self.name
            )
        features = []
        numbers = list_value(data.get("phone_numbers"))
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
        data = dictionary_value(body.get("data"))
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

    def normalize_webhook_event(self, payload):
        return normalize_generic_webhook_payload(self.name, payload)

    async def send_whatsapp(self, *_args, **_kwargs) -> None:
        raise UnsupportedCapability("Telnyx WhatsApp is not implemented", provider=self.name)

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict | None,
        query: str = "",
        *,
        headers: dict | None = None,
        timeout: float | None = None,
    ) -> dict:
        url = settings.telnyx_api_base.rstrip("/") + path
        if query:
            url = f"{url}?{query}"
        request_headers = {
            "Authorization": f"Bearer {settings.telnyx_api_key}",
            "Content-Type": "application/json",
        }
        if headers:
            request_headers.update(headers)
        try:
            result = await self._send(
                method, url, headers=request_headers, payload=payload, timeout=timeout
            )
        except ProviderTimeoutError:
            raise
        except ProviderUnavailableError:
            raise
        except Exception as exc:
            raise raise_if_error(exc, provider=self.name) from exc
        return require_body(result, provider=self.name)

    async def _send(
        self,
        method: str,
        url: str,
        *,
        headers: dict,
        payload: dict | None,
        timeout: float | None = None,
    ) -> HttpResult:
        timeout = float(timeout or settings.telephony_http_timeout_seconds or 8)
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
