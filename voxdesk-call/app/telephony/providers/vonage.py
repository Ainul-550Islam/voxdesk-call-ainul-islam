"""Vonage number API, Voice API and media-streaming boundary.

Number search, purchase and cancel use the REST number API when the API key
and secret are configured. Voice calls use the Voice API v1 with a JWT
signed RS256 by the application's private key.

Two rules govern this adapter:

* **A capability is claimed only when it is real.** ``supports_ai_media_stream``
  is true only when *every* credential the media path needs is present; the
  media path otherwise raises ``UnsupportedCapability`` instead of pretending
  to stream.
* **A retry never turns a rejection into a success.** 5xx and transport
  failures are retried with backoff; 4xx answers are returned to the caller
  immediately, because retrying a rejected request only wastes the
  provider's rate limit.
"""

from __future__ import annotations

import time
import uuid

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
_VOICE_BASE = "https://api.nexmo.com"

#: Retry budget for the Voice API: three attempts, exponential backoff.
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (0.2, 0.5)
#: How long a completed idempotent operation is remembered.
_IDEMPOTENCY_TTL_SECONDS = 300.0


def _carrier_settings():
    """The Batch 02 carrier settings, resolved lazily."""
    from app.carriers_v2.settings import get_carrier_v2_settings

    return get_carrier_v2_settings()


class VonageAdapter(TelephonyAdapter):
    name = "vonage"

    def __init__(self, transport=None) -> None:
        self._transport = transport
        self._idempotent_results: dict[str, tuple[float, HttpResult]] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------
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
        carrier = _carrier_settings()
        return bool(
            (settings.vonage_application_id or "").strip()
            or (carrier.vonage_application_id or "").strip()
        ) and self._private_key_available()

    def _private_key(self) -> str:
        """The RS256 private key, from the carrier settings or the main ones."""
        carrier = _carrier_settings()
        resolved = carrier.resolved_vonage_private_key()
        if resolved:
            return resolved
        return (settings.vonage_private_key or "").strip()

    def _private_key_available(self) -> bool:
        return bool(self._private_key())

    def _application_id(self) -> str:
        carrier = _carrier_settings()
        return (
            (carrier.vonage_application_id or "").strip()
            or (settings.vonage_application_id or "").strip()
        )

    def _signature_secret(self) -> str:
        carrier = _carrier_settings()
        return (
            (carrier.vonage_signature_secret or "").strip()
            or (settings.vonage_signature_secret or "").strip()
        )

    @property
    def supports_ai_media_stream(self) -> bool:
        """True only when the complete Vonage media credential set exists."""
        carrier = _carrier_settings()
        return bool(
            (carrier.vonage_api_key or settings.vonage_api_key or "").strip()
            and (carrier.vonage_api_secret or settings.vonage_api_secret or "").strip()
            and self._application_id()
            and self._private_key_available()
            and self._signature_secret()
        )

    def require_media_streaming(self) -> None:
        """Raise ``UnsupportedCapability`` when the media path is unavailable."""
        if not self.supports_ai_media_stream:
            raise UnsupportedCapability(
                "Vonage AI media streaming requires a complete credential set "
                "(API key, API secret, application id, RS256 private key and "
                "signature secret)",
                provider=self.name,
            )

    def health(self) -> HealthResult:
        if not self.configured():
            return HealthResult(self.name, False, "credentials_missing", probed=False)
        if self.supports_ai_media_stream:
            return HealthResult(self.name, True, "media_credentials_present", probed=False)
        return HealthResult(self.name, True, "credentials_present", probed=False)

    def capabilities(self) -> CapabilitySet:
        """What this deployment has confirmed, not what the vendor sells."""
        voice = bool(self.configured() and self.voice_configured())
        return CapabilitySet(
            voice=voice,
            sms=False,
            mms=False,
            whatsapp=False,
            recording=False,
            transcription=False,
            source="provider_config" if voice else "unconfirmed",
        )

    # ------------------------------------------------------------------
    # Voice API
    # ------------------------------------------------------------------
    def application_jwt(self) -> str:
        """A short-lived RS256 JWT for the Voice API."""
        application_id = self._application_id()
        private_key = self._private_key()
        if not application_id:
            raise ProviderConfigurationError(
                "Vonage application id is required for voice calls", provider=self.name
            )
        if not private_key:
            raise ProviderConfigurationError(
                "Vonage RS256 private key is required for voice calls", provider=self.name
            )
        try:
            import jwt
        except ImportError as exc:  # pragma: no cover - PyJWT is a hard dependency
            raise ProviderConfigurationError(
                "Vonage voice signing library is not installed", provider=self.name
            ) from exc
        now = int(time.time())
        claims = {
            "application_id": application_id,
            "iat": now,
            "exp": now + 900,
            "jti": str(uuid.uuid4()),
        }
        try:
            token = jwt.encode(claims, private_key, algorithm="RS256")
        except Exception as exc:
            raise ProviderConfigurationError(
                "Vonage private key could not be used to sign the application JWT",
                provider=self.name,
            ) from exc
        return token if isinstance(token, str) else token.decode("utf-8")

    async def create_outbound(
        self,
        *,
        to_number: str,
        from_number: str,
        answer_url: str = "",
        ncco: list | None = None,
        idempotency_key: str = "",
    ) -> CallResult:
        """Place an outbound call through the Vonage Voice API."""
        self.require_configured()
        if not self.voice_configured():
            raise ProviderConfigurationError(
                "Vonage application id and private key are required for voice calls",
                provider=self.name,
            )
        dest = normalize(to_number)
        caller = normalize(from_number)
        payload: dict = {
            "to": [{"type": "phone", "number": dest}],
            "from": {"type": "phone", "number": caller},
        }
        if ncco:
            payload["ncco"] = ncco
        elif answer_url:
            payload["answer_url"] = [answer_url]
            payload["answer_method"] = "GET"
        else:
            raise ProviderValidationError(
                "An answer URL or an NCCO document is required to place a Vonage call",
                provider=self.name,
            )
        body = await self._voice_request(
            "POST", "/v1/calls", payload, idempotency_key=idempotency_key
        )
        external_id = str(body.get("uuid") or "").strip()
        if not external_id:
            raise ProviderUnavailableError(
                "Vonage did not return a call identifier", provider=self.name
            )
        return CallResult(
            provider=self.name,
            external_id=external_id,
            status=str(body.get("status") or "started"),
            to_number=dest,
            from_number=caller,
        )

    async def hangup(self, external_id: str) -> CallResult:
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        await self._voice_request("DELETE", f"/v1/calls/{external_id}", None)
        return CallResult(
            provider=self.name,
            external_id=external_id,
            status="completed",
        )

    async def transfer(self, external_id: str, destination: str) -> CallResult:
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        target = normalize(destination)
        payload = {
            "action": "transfer",
            "destination": {"type": "ncco", "ncco": [{"action": "connect", "from": target}]},
        }
        await self._voice_request("PUT", f"/v1/calls/{external_id}", payload)
        return CallResult(
            provider=self.name,
            external_id=external_id,
            status="transfer_requested",
            to_number=target,
        )

    async def send_dtmf(self, external_id: str, digits: str) -> dict:
        """Send DTMF tones into a live Vonage call."""
        import re

        self.require_configured()
        if not external_id or not digits:
            raise ProviderValidationError("Call id and digits are required", provider=self.name)
        if not re.fullmatch(r"[0-9*#pP]+", digits):
            raise ProviderValidationError(
                f"Invalid DTMF sequence {digits!r}; allowed characters are 0-9, *, #, p.",
                provider=self.name,
            )
        await self._voice_request(
            "PUT", f"/v1/calls/{external_id}/dtmf", {"digits": digits}
        )
        return {
            "provider": self.name,
            "external_id": external_id,
            "digits": digits,
            "status": "sent",
        }

    async def start_playback(
        self, external_id: str, text: str, *, language: str = "en-US"
    ) -> dict:
        """Play synthesised speech into a live Vonage call."""
        self.require_configured()
        if not external_id or not text.strip():
            raise ProviderValidationError(
                "Call id and text are required to play audio", provider=self.name
            )
        await self._voice_request(
            "PUT",
            f"/v1/calls/{external_id}/talk",
            {"text": text, "language": language, "style": 0, "premium": False},
        )
        return {
            "provider": self.name,
            "external_id": external_id,
            "status": "playing",
            "bytes": len(text.encode("utf-8")),
        }

    async def start_stream(
        self, external_id: str, stream_url: str, *, loop: int = 1, level: str = "0.4"
    ) -> dict:
        """Stream an audio file into a live Vonage call."""
        self.require_configured()
        if not external_id or not stream_url.strip():
            raise ProviderValidationError(
                "Call id and stream URL are required to stream audio", provider=self.name
            )
        await self._voice_request(
            "PUT",
            f"/v1/calls/{external_id}/stream",
            {"stream_url": [stream_url], "loop": max(1, int(loop)), "level": level},
        )
        return {
            "provider": self.name,
            "external_id": external_id,
            "status": "streaming",
            "stream_url": stream_url,
        }

    async def call_status(self, external_id: str) -> dict:
        """Read the live state of a Vonage call."""
        self.require_configured()
        if not external_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        return await self._voice_request("GET", f"/v1/calls/{external_id}", None)

    # ------------------------------------------------------------------
    # Numbers
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------
    async def start_recording(self, external_call_id: str) -> RecordingResult:
        """Start recording a live Vonage call."""
        self.require_configured()
        if not external_call_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        body = await self._voice_request(
            "PUT", f"/v1/calls/{external_call_id}/recording", {"action": "start"}
        )
        recording_url = str(body.get("recording_url") or body.get("recording_id") or "")
        return RecordingResult(self.name, recording_url or external_call_id, "recording")

    async def stop_recording(
        self, external_call_id: str, recording_id: str = ""
    ) -> RecordingResult:
        """Stop the recording of a live Vonage call."""
        self.require_configured()
        if not external_call_id:
            raise ProviderValidationError("Call id is required", provider=self.name)
        await self._voice_request(
            "PUT", f"/v1/calls/{external_call_id}/recording", {"action": "stop"}
        )
        return RecordingResult(self.name, recording_id or external_call_id, "stopped")

    async def verify_webhook(self, request) -> WebhookVerdict:
        return await verify_provider_webhook(self.name, request)

    def normalize_webhook_event(self, payload):
        return normalize_generic_webhook_payload(self.name, payload)

    async def send_whatsapp(self, *_args, **_kwargs) -> None:
        """This adapter speaks the Vonage Voice API; WhatsApp is not a channel it covers."""
        raise UnsupportedCapability(
            "This Vonage adapter carries voice calls; WhatsApp messages are not a capability it offers.",
            provider=self.name,
        )

    # ------------------------------------------------------------------
    # Transport
    # ------------------------------------------------------------------
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

    async def _voice_request(
        self,
        method: str,
        path: str,
        payload: dict | None,
        *,
        idempotency_key: str = "",
    ) -> dict:
        """Call the Voice API with retries and idempotency."""
        cached = self._replay_idempotent(idempotency_key)
        if cached is not None:
            return cached
        token = self.application_jwt()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key[:128]
        url = _VOICE_BASE + path
        timeout = float(_carrier_settings().vonage_http_timeout_seconds or 8)

        last_error: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                result = await self._send(method, url, payload, headers=headers, timeout=timeout)
            except (ProviderTimeoutError, ProviderUnavailableError) as exc:
                last_error = exc
                if attempt + 1 >= _MAX_ATTEMPTS:
                    raise
                await self._sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
                continue
            if result.status_code >= 500 and attempt + 1 < _MAX_ATTEMPTS:
                await self._sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
                continue
            body = require_body(result, provider=self.name)
            self._remember_idempotent(idempotency_key, body)
            return body
        raise ProviderUnavailableError(
            "Vonage voice request failed", provider=self.name
        ) from last_error

    def _replay_idempotent(self, key: str) -> dict | None:
        if not key:
            return None
        entry = self._idempotent_results.get(key)
        if entry is None:
            return None
        created, body = entry
        if time.monotonic() - created > _IDEMPOTENCY_TTL_SECONDS:
            self._idempotent_results.pop(key, None)
            return None
        return dict(body)

    def _remember_idempotent(self, key: str, body: dict) -> None:
        if not key:
            return
        if len(self._idempotent_results) > 512:
            self._idempotent_results.clear()
        self._idempotent_results[key] = (time.monotonic(), dict(body))

    @staticmethod
    async def _sleep(seconds: float) -> None:
        import asyncio

        await asyncio.sleep(seconds)

    async def _send(
        self,
        method: str,
        url: str,
        payload: dict | None,
        *,
        headers: dict | None = None,
        timeout: float | None = None,
    ) -> HttpResult:
        request_headers = headers or {"Content-Type": "application/x-www-form-urlencoded"}
        effective_timeout = float(timeout if timeout is not None else (settings.telephony_http_timeout_seconds or 8))
        # The number API is form-encoded; the Voice API is JSON.
        form_encoded = "x-www-form-urlencoded" in str(
            request_headers.get("Content-Type", "")
        ).lower()
        if self._transport is not None:
            # The injected transport keeps the original call shape
            # (``method, url, headers=, json=, timeout=``) so existing test
            # doubles stay valid.
            return await self._transport(
                method,
                url,
                headers=request_headers,
                json=payload,
                timeout=effective_timeout,
            )
        import httpx

        try:
            async with httpx.AsyncClient(timeout=effective_timeout) as client:
                if method == "GET":
                    response = await client.get(url, headers=request_headers, params=payload)
                elif method == "DELETE":
                    response = await client.delete(url, headers=request_headers)
                elif form_encoded:
                    response = await client.post(url, headers=request_headers, data=payload or {})
                elif method == "POST":
                    response = await client.post(
                        url, headers=request_headers, json=payload or {}
                    )
                else:
                    response = await client.put(
                        url, headers=request_headers, json=payload or {}
                    )
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


def vonage_media_capabilities() -> dict[str, dict]:
    """The Vonage media capability row for the carrier capability table."""
    adapter = VonageAdapter()
    ready = adapter.supports_ai_media_stream
    return {
        "vonage": {
            "supported": ready,
            "serializer": "VonageFrameSerializer" if ready else None,
            "encodings": ["audio/l16;rate=16000"] if ready else [],
            "sample_rates": [16000] if ready else [],
            "reason": (
                "Vonage Voice API credentials are present."
                if ready
                else "Vonage media streaming is not enabled: the credential set is incomplete."
            ),
        }
    }


def _signer_available() -> bool:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return False
    return True
