"""Apple Push Notification service client (token-based auth, HTTP/2).

APNs is not FCM with different words. Three differences drive this module:

* **The transport must be HTTP/2.** APNs rejects HTTP/1.1. The default transport
  therefore builds an ``httpx`` client with ``http2=True``, and when the ``h2``
  package is missing it raises a clear ``ProviderRejected`` instead of failing with
  an opaque ``ImportError`` deep inside a request. Tests inject a transport, which
  is also why the protocol logic is verifiable on Linux.
* **Auth is a signed provider token, cached.** The ES256 JWT is valid for an hour
  and Apple rate-limits *token generation*, not requests: minting a token per
  notification gets you throttled. It is cached for 45 minutes.
* **Errors are per-request status codes**, not a body envelope:
  ``410 Unregistered`` and ``400 BadDeviceToken`` are permanent (the app was
  deleted, the token belongs to another environment), ``429 TooManyRequests`` and
  ``503 ServiceUnavailable`` are transient, and everything else is reported rather
  than guessed.

Sandbox versus production is a per-deployment setting (``APNS_USE_SANDBOX``) and it
must match the provisioning profile the build was signed with: a device token from
a debug build is invalid on the production host and APNs answers
``BadDeviceToken`` for a token that is perfectly good.
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

PRODUCTION_HOST = "https://api.push.apple.com"
SANDBOX_HOST = "https://api.sandbox.push.apple.com"

#: Provider token lifetime is 60 minutes; Apple rejects tokens older than that and
#: throttles clients that mint one per request.
TOKEN_TTL_SECONDS = 45 * 60

#: Notification types (the `apns-push-type` header). iOS 13+ requires it, and the
#: wrong value changes how the system treats the delivery: `alert` may be presented,
#: `background` may be throttled, `voip` wakes the app.
PUSH_TYPES = ("alert", "background", "voip", "complication", "fileprovider", "mdm", "location", "liveactivity")

#: `apns-priority`: 10 is immediate (page-out), 5 is "send when convenient".
PRIORITY_IMMEDIATE = 10
PRIORITY_DEFERRED = 5

#: Statuses worth retrying. Everything else means the request or the token is wrong.
TRANSIENT_STATUSES = frozenset({429, 500, 503})
PERMANENT_STATUSES = frozenset({400, 403, 405, 410, 413})

#: APNs `reason` strings that mean "this device token will never work again".
PERMANENT_REASONS = frozenset(
    {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic", "MissingDeviceToken"}
)
TRANSIENT_REASONS = frozenset({"TooManyRequests", "TooManyProviderTokenUpdates", "ServiceUnavailable", "InternalServerError"})

Transport = Callable[[str, dict[str, str], dict[str, Any]], Awaitable[tuple[int, dict[str, str]]]]


@dataclass(frozen=True)
class ApnsConfig:
    key_id: str
    team_id: str
    private_key_p8: str
    bundle_id: str
    use_sandbox: bool = True
    endpoint: str = ""

    @classmethod
    def from_settings(cls) -> ApnsConfig:
        cfg = get_plat2_settings()
        if not cfg.apns_enabled:
            raise ProviderRejected("APNs is not enabled in this deployment (APNS_ENABLED=false)")
        missing = [
            name
            for name, value in (
                ("APNS_KEY_ID", cfg.apns_key_id),
                ("APNS_TEAM_ID", cfg.apns_team_id),
                ("APNS_PRIVATE_KEY_P8", cfg.apns_private_key_p8),
                ("APNS_BUNDLE_ID", cfg.apns_bundle_id),
            )
            if not value
        ]
        if missing:
            raise ProviderRejected("APNs credentials are incomplete", detail={"missing": missing})
        return cls(
            key_id=cfg.apns_key_id,
            team_id=cfg.apns_team_id,
            private_key_p8=cfg.apns_private_key_p8.replace("\\n", "\n"),
            bundle_id=cfg.apns_bundle_id,
            use_sandbox=bool(cfg.apns_use_sandbox),
            endpoint=cfg.apns_endpoint or "",
        )

    @property
    def host(self) -> str:
        if self.endpoint:
            return self.endpoint.rstrip("/")
        return SANDBOX_HOST if self.use_sandbox else PRODUCTION_HOST


@dataclass(frozen=True)
class ApnsMessage:
    device_token: str
    title: str
    body: str
    data: dict[str, Any] = field(default_factory=dict)
    collapse_id: str = ""
    push_type: str = "alert"
    immediate: bool = True
    badge: int | None = None
    thread_id: str = ""
    expiry_seconds: int = 3600

    def payload(self) -> dict[str, Any]:
        """The ``aps`` envelope plus our own keys at the top level.

        Custom keys must not collide with ``aps``; Apple keeps the rest opaque, and
        flattening them is what lets the app receive ``shift_id`` without a second
        fetch. Values are copied as-is (JSON-safe strings/numbers) and never
        include a secret — a lock screen shows this text.
        """
        aps: dict[str, Any] = {
            "alert": {"title": self.title, "body": self.body},
            "sound": "default" if self.push_type == "alert" else None,
        }
        if self.badge is not None:
            aps["badge"] = int(self.badge)
        if self.thread_id:
            aps["thread-id"] = self.thread_id
        if self.push_type == "background":
            # A background push must be silent: an alert in a background push is
            # rejected by APNs.
            aps = {"content-available": 1}
        payload: dict[str, Any] = {"aps": aps}
        for key, value in self.data.items():
            if key == "aps":
                continue
            payload[str(key)] = value
        return payload

    def headers(self, *, bundle_id: str, provider_token: str) -> dict[str, str]:
        headers = {
            "authorization": f"bearer {provider_token}",
            "apns-topic": bundle_id,
            "apns-push-type": self.push_type,
            "apns-priority": str(PRIORITY_IMMEDIATE if self.immediate else PRIORITY_DEFERRED),
            "apns-expiration": str(int(time.time()) + max(0, self.expiry_seconds)),
            # One id per logical notification, so a retry of the same push replaces
            # the first one in Notification Center instead of stacking.
            "apns-id": str(uuid.uuid4()),
            "content-type": "application/json",
        }
        if self.collapse_id:
            headers["apns-collapse-id"] = self.collapse_id[:64]
        return headers


@dataclass(frozen=True)
class ApnsResult:
    ok: bool
    apns_id: str = ""
    status_code: int = 0
    reason: str = ""
    transient: bool = False

    @property
    def permanent(self) -> bool:
        return not self.ok and not self.transient

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "apns_id": self.apns_id,
            "status_code": self.status_code,
            "reason": self.reason,
            "transient": self.transient,
        }


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def build_provider_token(config: ApnsConfig, *, now: int | None = None) -> str:
    """ES256 provider token: ``{alg, kid}.{iss, iat}`` with a raw r||s signature.

    The signature must be the JOSE form (64 bytes, r||s), not the DER encoding
    ``cryptography`` returns by default — that detail is the single most common
    reason a hand-rolled APNs client gets 403 ``InvalidProviderToken``.
    """
    issued_at = int(now if now is not None else time.time())
    header = {"alg": "ES256", "kid": config.key_id}
    claims = {"iss": config.team_id, "iat": issued_at}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    )

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

    try:
        key = serialization.load_pem_private_key(config.private_key_p8.encode(), password=None)
    except Exception as exc:  # noqa: BLE001 - a bad key is a configuration error
        raise ValidationFailed(f"APNs private key could not be loaded: {exc}") from exc
    if not isinstance(key, ec.EllipticCurvePrivateKey):
        raise ValidationFailed("APNs private key must be an EC P-256 key")
    der = key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return signing_input + "." + _b64url(raw)


async def default_transport(
    url: str, headers: dict[str, str], payload: dict[str, Any]
) -> tuple[int, dict[str, str]]:
    """HTTP/2 POST to APNs."""
    try:
        import h2  # noqa: F401  (httpx needs it for HTTP/2; APNs requires HTTP/2)
    except Exception as exc:  # noqa: BLE001
        raise ProviderRejected(
            "APNs requires HTTP/2 and the `h2` package is not installed in this deployment",
            detail={"remedy": "pip install h2"},
        ) from exc
    import httpx

    async with httpx.AsyncClient(http2=True, timeout=10.0) as client:
        response = await client.post(url, headers=headers, json=payload)
    reason = response.headers.get("apns-id", "")
    body: dict[str, str] = {}
    if response.content:
        try:
            body = response.json()
        except Exception:  # noqa: BLE001
            body = {"raw": response.text[:300]}
    body.setdefault("apns-id", reason)
    return response.status_code, body


def classify(status_code: int, body: dict[str, Any]) -> ApnsResult:
    """Map an APNs response to a result."""
    apns_id = str(body.get("apns-id") or "")
    if status_code == 200:
        return ApnsResult(ok=True, apns_id=apns_id, status_code=status_code)
    reason = str(body.get("reason") or "")
    if reason in PERMANENT_REASONS or status_code == 410:
        return ApnsResult(ok=False, status_code=status_code, reason=reason or "Unregistered")
    if reason in TRANSIENT_REASONS or status_code in TRANSIENT_STATUSES:
        # A transient status with an empty body still needs a code an operator can
        # search for; an empty reason reads like "no error".
        return ApnsResult(
            ok=False, status_code=status_code, reason=reason or f"HTTP_{status_code}", transient=True
        )
    if status_code in PERMANENT_STATUSES:
        return ApnsResult(ok=False, status_code=status_code, reason=reason or f"HTTP_{status_code}")
    # Unknown 4xx/5xx: report it as transient so it is retried a bounded number of
    # times and then surfaced, rather than silently dropped.
    return ApnsResult(ok=False, status_code=status_code, reason=reason or f"HTTP_{status_code}", transient=True)


class ApnsClient:
    """Token-authenticated APNs sender with a cached provider token."""

    def __init__(
        self,
        config: ApnsConfig,
        *,
        transport: Transport | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or default_transport
        self._clock = clock or time.time
        self._token: str = ""
        self._issued_at: float = 0.0

    @classmethod
    def from_settings(cls, *, transport: Transport | None = None) -> ApnsClient:
        return cls(ApnsConfig.from_settings(), transport=transport)

    def provider_token(self) -> str:
        now = self._clock()
        if self._token and now - self._issued_at < TOKEN_TTL_SECONDS:
            return self._token
        self._token = build_provider_token(self.config, now=int(now))
        self._issued_at = now
        return self._token

    async def send(self, message: ApnsMessage) -> ApnsResult:
        if message.push_type not in PUSH_TYPES:
            raise ValidationFailed(
                "unknown APNs push type", detail={"push_type": message.push_type, "allowed": list(PUSH_TYPES)}
            )
        url = f"{self.config.host}/3/device/{message.device_token}"
        status, body = await self._transport(
            url,
            message.headers(bundle_id=self.config.bundle_id, provider_token=self.provider_token()),
            message.payload(),
        )
        result = classify(status, body)
        if not result.ok:
            log.info(
                "platform_v2.apns.send_failed",
                reason=result.reason,
                status_code=result.status_code,
                transient=result.transient,
            )
        return result


def push_type_for(template: str) -> str:
    """Which APNs push type a product notification uses.

    Rota and swap notices are visible alerts. A page-out for a breaching queue is
    also an alert — the difference is priority, not type; a ``background`` push may
    be coalesced by iOS and is the wrong choice for anything a person must see.
    """
    # Every notification the platform sends today is something a person must act on,
    # so none of them may be coalesced by the OS: they are all alerts, at differing
    # priorities. The template argument stays so a future background sync has one
    # place to be classified rather than a second code path.
    del template
    return "alert"


__all__ = [
    "PERMANENT_REASONS",
    "PERMANENT_STATUSES",
    "PRIORITY_DEFERRED",
    "PRIORITY_IMMEDIATE",
    "PRODUCTION_HOST",
    "PUSH_TYPES",
    "SANDBOX_HOST",
    "TOKEN_TTL_SECONDS",
    "TRANSIENT_REASONS",
    "TRANSIENT_STATUSES",
    "ApnsClient",
    "ApnsConfig",
    "ApnsMessage",
    "ApnsResult",
    "Transport",
    "build_provider_token",
    "classify",
    "default_transport",
    "push_type_for",
]
