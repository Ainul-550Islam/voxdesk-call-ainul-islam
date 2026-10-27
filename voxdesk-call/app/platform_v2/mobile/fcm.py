"""Firebase Cloud Messaging client for Android push.

Auth is the service-account flow: sign an assertion with the account's RSA key,
exchange it for a short-lived access token, send with it. The token is cached and
refreshed comfortably before expiry — a per-message OAuth round trip would triple
the latency of a page-out and hammer Google's token endpoint during an incident,
which is exactly when notifications matter.

Error handling is the part worth reading. FCM answers 200 for a request it accepted
and carries the per-token outcome in the body's ``error.status``, so a naive client
treats a rejected token as a success. ``classify`` maps those statuses:

* ``UNREGISTERED``, ``INVALID_ARGUMENT``, ``SENDER_ID_MISMATCH`` — permanent. The
  token is dead or belongs to another project; retrying forever is how a token
  table fills with garbage.
* ``UNAVAILABLE``, ``INTERNAL``, ``QUOTA_EXCEEDED``, HTTP 429/5xx — transient. The
  push retry job picks these up again.

The HTTP layer is injectable (``transport``) precisely so the protocol logic —
assertion, headers, classification — is tested without a network or a real
Firebase project: the tests in ``tests/platform_v2/test_push_fcm.py`` assert the
classification table against recorded error bodies, and nothing in this repository
has ever sent a real notification in CI.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.platform_v2.errors import ProviderRejected, ValidationFailed
from app.platform_v2.settings import get_plat2_settings

log = structlog.get_logger(__name__)

FCM_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"

#: Token endpoint used by the service-account flow.
TOKEN_URI = "https://oauth2.googleapis.com/token"

#: Refresh the access token this many seconds before it expires.
REFRESH_SKEW_SECONDS = 120

#: Timeout for one HTTP call. A push that has not reached Google in ten seconds
#: has not reached the phone either.
HTTP_TIMEOUT_SECONDS = 10.0

PERMANENT_ERRORS = frozenset(
    {
        "UNREGISTERED",
        "INVALID_ARGUMENT",
        "SENDER_ID_MISMATCH",
        "THIRD_PARTY_AUTH_ERROR",
        "INVALID_RECIPIENT",
    }
)
TRANSIENT_ERRORS = frozenset(
    {
        "UNAVAILABLE",
        "INTERNAL",
        "QUOTA_EXCEEDED",
        "RESOURCE_EXHAUSTED",
        "TIMEOUT",
    }
)

Transport = Callable[[str, str, dict[str, str], dict[str, Any] | None], Awaitable[tuple[int, dict[str, Any]]]]


@dataclass(frozen=True)
class ServiceAccount:
    """The subset of a service-account JSON this client needs."""

    project_id: str
    client_email: str
    private_key_pem: str
    token_uri: str = TOKEN_URI

    @classmethod
    def from_json(cls, raw: str) -> ServiceAccount:
        if not raw or not raw.strip():
            raise ProviderRejected("FCM service account JSON is not configured")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValidationFailed(f"FCM service account JSON is not valid JSON: {exc}") from exc
        missing = [key for key in ("project_id", "client_email", "private_key") if not payload.get(key)]
        if missing:
            raise ValidationFailed(
                "FCM service account JSON is missing required fields",
                detail={"missing": missing},
            )
        return cls(
            project_id=str(payload["project_id"]),
            client_email=str(payload["client_email"]),
            private_key_pem=str(payload["private_key"]).replace("\\n", "\n"),
            token_uri=str(payload.get("token_uri") or TOKEN_URI),
        )


@dataclass(frozen=True)
class PushMessage:
    """One Android notification."""

    token: str
    title: str
    body: str
    data: dict[str, str] = field(default_factory=dict)
    collapse_key: str = ""
    high_priority: bool = False
    tag: str = ""

    def as_payload(self) -> dict[str, Any]:
        """FCM v1 message envelope.

        ``data`` values must be strings — FCM rejects numbers, and a rota id that
        silently disappears because it was an int is a bug that only shows up on a
        device. They must also not carry secrets: this payload crosses Google's
        infrastructure and the device's lock screen.
        """
        message: dict[str, Any] = {
            "token": self.token,
            "notification": {"title": self.title, "body": self.body},
            "android": {
                "priority": "HIGH" if self.high_priority else "NORMAL",
            },
        }
        if self.collapse_key:
            message["android"]["collapse_key"] = self.collapse_key
            # `tag` is the Android-side collapse key: it replaces an older
            # notification in the shade instead of stacking five rota alerts.
            message["android"]["notification"] = {"tag": self.tag or self.collapse_key}
        if self.data:
            message["data"] = {str(key): str(value) for key, value in self.data.items()}
        return {"message": message}


@dataclass(frozen=True)
class SendResult:
    ok: bool
    message_id: str = ""
    error_code: str = ""
    error_reason: str = ""
    transient: bool = False
    status_code: int = 0

    @property
    def permanent(self) -> bool:
        return not self.ok and not self.transient

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "message_id": self.message_id,
            "error_code": self.error_code,
            "error_reason": self.error_reason,
            "transient": self.transient,
            "status_code": self.status_code,
        }


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def build_assertion(account: ServiceAccount, *, now: int | None = None, scope: str = FCM_SCOPE) -> str:
    """Signed JWT for the service-account token exchange (RS256)."""
    issued_at = int(now if now is not None else time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    claims = {
        "iss": account.client_email,
        "scope": scope,
        "aud": account.token_uri,
        "iat": issued_at,
        "exp": issued_at + 3600,
    }
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    )

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    try:
        key = serialization.load_pem_private_key(account.private_key_pem.encode(), password=None)
    except Exception as exc:  # noqa: BLE001 - a bad key is a configuration error
        raise ValidationFailed(f"FCM private key could not be loaded: {exc}") from exc
    signature = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + _b64url(signature)


async def default_transport(
    method: str, url: str, headers: dict[str, str], payload: dict[str, Any] | None
) -> tuple[int, dict[str, Any]]:
    """Real HTTP, used when no transport is injected."""
    import httpx

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = await client.request(method, url, headers=headers, json=payload)
    try:
        body = response.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is still a status code
        body = {"raw": response.text[:500]}
    return response.status_code, body


def classify(status_code: int, body: dict[str, Any]) -> SendResult:
    """Map an FCM response to a result, with retryability attached.

    A 200 with an error status is a failure: that is the shape FCM uses for a
    rejected token, and treating it as success loses the token invalidation signal.
    """
    if status_code == 200 and not body.get("error"):
        return SendResult(ok=True, message_id=str(body.get("name") or ""), status_code=status_code)

    error = body.get("error") or {}
    status = ""
    if isinstance(error, dict):
        # `details[].errorCode` is the specific reason (`UNREGISTERED`) while
        # `status` is the gRPC class (`NOT_FOUND`). The specific one decides
        # retryability, so it is read first and kept as the recorded error code.
        details = error.get("details") or []
        for detail in details:
            if isinstance(detail, dict) and detail.get("errorCode"):
                status = str(detail["errorCode"])
                break
        if not status:
            status = str(error.get("status") or "")
    status = status or str(body.get("raw") or "")[:40] or f"HTTP_{status_code}"
    reason = ""
    if isinstance(error, dict) and error.get("message"):
        reason = str(error["message"])[:300]

    if status in PERMANENT_ERRORS or status_code == 404:
        return SendResult(ok=False, error_code=status, error_reason=reason, transient=False, status_code=status_code)
    if status in TRANSIENT_ERRORS or status_code in (429, 500, 502, 503, 504):
        return SendResult(ok=False, error_code=status, error_reason=reason, transient=True, status_code=status_code)
    if status_code == 401 or status_code == 403:
        # Our own credentials, not the device's: transient from the device's point
        # of view but it will not fix itself, so it must not silently invalidate a
        # good token either. Reported as a transient failure with the code kept.
        return SendResult(
            ok=False,
            error_code=status or f"HTTP_{status_code}",
            error_reason=reason or "provider rejected our credentials",
            transient=True,
            status_code=status_code,
        )
    return SendResult(ok=False, error_code=status, error_reason=reason, transient=False, status_code=status_code)


class FcmClient:
    """Minimal FCM v1 sender."""

    def __init__(
        self,
        account: ServiceAccount,
        *,
        endpoint: str,
        transport: Transport | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.account = account
        self.endpoint = endpoint.rstrip("/")
        self._transport = transport or default_transport
        self._clock = clock or time.time
        self._access_token: str = ""
        self._expires_at: float = 0.0

    @classmethod
    def from_settings(cls, *, transport: Transport | None = None) -> FcmClient:
        cfg = get_plat2_settings()
        if not cfg.fcm_enabled:
            raise ProviderRejected("FCM is not enabled in this deployment (FCM_ENABLED=false)")
        account = ServiceAccount.from_json(cfg.fcm_service_account_json)
        return cls(account, endpoint=cfg.fcm_endpoint, transport=transport)

    def _project(self) -> str:
        project = self.account.project_id or get_plat2_settings().fcm_project_id
        if not project:
            raise ProviderRejected("FCM project id is not configured")
        return project

    async def access_token(self) -> str:
        now = self._clock()
        if self._access_token and now < self._expires_at - REFRESH_SKEW_SECONDS:
            return self._access_token
        assertion = build_assertion(self.account, now=int(now))
        status, body = await self._transport(
            "POST",
            self.account.token_uri,
            {"Content-Type": "application/x-www-form-urlencoded"},
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        token = str(body.get("access_token") or "")
        if status != 200 or not token:
            raise ProviderRejected(
                "FCM access token exchange failed",
                detail={"status_code": status, "error": str(body.get("error") or "")[:80]},
            )
        self._access_token = token
        self._expires_at = now + float(body.get("expires_in") or 3600)
        return token

    async def send(self, message: PushMessage) -> SendResult:
        token = await self.access_token()
        url = f"{self.endpoint}/v1/projects/{self._project()}/messages:send"
        status, body = await self._transport(
            "POST",
            url,
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                # A message id makes a duplicate send visible in the provider's
                # logs instead of looking like two real page-outs.
                "X-Request-Id": str(uuid.uuid4()),
            },
            message.as_payload(),
        )
        result = classify(status, body)
        if not result.ok:
            log.info(
                "platform_v2.fcm.send_failed",
                error_code=result.error_code,
                transient=result.transient,
                status_code=result.status_code,
            )
        return result


__all__ = [
    "FCM_SCOPE",
    "HTTP_TIMEOUT_SECONDS",
    "PERMANENT_ERRORS",
    "TRANSIENT_ERRORS",
    "FcmClient",
    "PushMessage",
    "SendResult",
    "ServiceAccount",
    "Transport",
    "build_assertion",
    "classify",
    "default_transport",
]
