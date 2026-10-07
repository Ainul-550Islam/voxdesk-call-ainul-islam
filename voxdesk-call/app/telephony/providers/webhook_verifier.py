"""Provider webhook authentication.

Twilio verification is the existing ``verify_twilio_request``. This module
calls it and does not reimplement the HMAC. Telnyx and Vonage fail closed
when their secret or signature is missing. A verification error is never
turned into a valid verdict.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from app.core.config import settings
from app.telephony.providers.base import WebhookVerdict

_MAX_SKEW_SECONDS = 300
_TELNYX_PATHS = ("/telephony", "/api/telephony")
_VONAGE_PATHS = ("/telephony", "/api/telephony")


def _path(request) -> str:
    url = getattr(request, "url", None)
    path = getattr(url, "path", "") if url is not None else ""
    return str(path or "")


def _header(request, name: str) -> str:
    headers = getattr(request, "headers", {}) or {}
    getter = getattr(headers, "get", None)
    if getter is None:
        return ""
    return str(getter(name) or getter(name.lower()) or "")


async def _body(request) -> bytes:
    raw = getattr(request, "body", None)
    if raw is None:
        return b""
    if callable(raw):
        result = raw()
        if hasattr(result, "__await__"):
            result = await result
        if isinstance(result, str):
            return result.encode()
        return bytes(result or b"")
    if isinstance(raw, str):
        return raw.encode()
    return bytes(raw)


def _path_allowed(path: str, prefixes: tuple[str, ...]) -> bool:
    return any(path == prefix or path.startswith(prefix + "/") for prefix in prefixes)


async def verify(provider: str, request) -> WebhookVerdict:
    name = (provider or "").strip().lower()
    if name == "twilio":
        return await _twilio(request)
    if name == "telnyx":
        return await _telnyx(request)
    if name == "vonage":
        return await _vonage(request)
    return WebhookVerdict(False, "unknown_provider")


async def _twilio(request) -> WebhookVerdict:
    """Delegate to the existing verifier. Do not accept on an import or runtime failure."""
    from app.telephony.stream_auth import verify_twilio_request

    try:
        ok = await verify_twilio_request(request)
    except ImportError:
        return WebhookVerdict(False, "twilio_verifier_unavailable")
    if ok:
        return WebhookVerdict(True, "twilio_signature")
    return WebhookVerdict(False, "twilio_rejected")


async def _telnyx(request) -> WebhookVerdict:
    if not _path_allowed(_path(request), _TELNYX_PATHS):
        return WebhookVerdict(False, "endpoint_mismatch")
    public_key = (settings.telnyx_public_key or "").strip()
    if not public_key:
        return WebhookVerdict(False, "configuration_missing")
    signature = _header(request, "telnyx-signature-ed25519")
    timestamp = _header(request, "telnyx-timestamp")
    if not signature or not timestamp:
        return WebhookVerdict(False, "signature_missing")
    if not _timestamp_fresh(timestamp):
        return WebhookVerdict(False, "timestamp_rejected")
    body = await _body(request)
    signed = timestamp.encode() + b"|" + body
    if not _ed25519_ok(public_key, signature, signed):
        return WebhookVerdict(False, "signature_rejected")
    return WebhookVerdict(True, "telnyx_signature")


async def _vonage(request) -> WebhookVerdict:
    if not _path_allowed(_path(request), _VONAGE_PATHS):
        return WebhookVerdict(False, "endpoint_mismatch")
    secret = (settings.vonage_signature_secret or "").strip()
    if not secret:
        return WebhookVerdict(False, "configuration_missing")
    authorization = _header(request, "authorization")
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        return _vonage_jwt(token, secret)
    signature = _header(request, "x-vonage-signature") or _query_sig(request)
    if not signature:
        return WebhookVerdict(False, "signature_missing")
    body = await _body(request)
    if (
        _hmac_sha256(secret, body) == signature
        or _sorted_param_sig(secret, request, body) == signature
    ):
        return WebhookVerdict(True, "vonage_signature")
    return WebhookVerdict(False, "signature_rejected")


def _timestamp_fresh(raw: str, *, now: int | None = None) -> bool:
    try:
        stamp = int(raw)
    except ValueError:
        return False
    current = int(now if now is not None else time.time())
    return abs(current - stamp) <= _MAX_SKEW_SECONDS


def _ed25519_ok(public_key: str, signature: str, payload: bytes) -> bool:
    """Verify with PyNaCl when it is installed. Absent library rejects, never accepts."""
    try:
        from nacl.exceptions import BadSignatureError
        from nacl.signing import VerifyKey
    except ImportError:
        return False
    try:
        key_bytes = base64.b64decode(public_key)
        sig_bytes = base64.b64decode(signature)
        VerifyKey(key_bytes).verify(payload, sig_bytes)
    except (BadSignatureError, ValueError, TypeError):
        return False
    return True


def _vonage_jwt(token: str, secret: str) -> WebhookVerdict:
    parts = token.split(".")
    if len(parts) != 3:
        return WebhookVerdict(False, "signature_rejected")
    signing_input = f"{parts[0]}.{parts[1]}".encode()
    expected = _b64(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    if not hmac.compare_digest(expected, parts[2]):
        return WebhookVerdict(False, "signature_rejected")
    try:
        header = json.loads(_b64decode(parts[0]))
        claims = json.loads(_b64decode(parts[1]))
    except (ValueError, json.JSONDecodeError):
        return WebhookVerdict(False, "signature_rejected")
    if header.get("alg") != "HS256":
        return WebhookVerdict(False, "signature_rejected")
    now = int(time.time())
    exp = claims.get("exp")
    iat = claims.get("iat")
    if exp is not None and int(exp) < now:
        return WebhookVerdict(False, "timestamp_rejected")
    if iat is not None and abs(now - int(iat)) > _MAX_SKEW_SECONDS:
        return WebhookVerdict(False, "timestamp_rejected")
    return WebhookVerdict(True, "vonage_jwt")


def _hmac_sha256(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _sorted_param_sig(secret: str, request, body: bytes) -> str:
    pairs = []
    url = getattr(request, "url", None)
    query = getattr(url, "query", b"") if url is not None else b""
    if isinstance(query, str):
        query = query.encode()
    pairs.extend(parse_qsl(query.decode() if query else "", keep_blank_values=True))
    if body:
        pairs.extend(parse_qsl(body.decode(errors="ignore"), keep_blank_values=True))
    filtered = [(key, value) for key, value in pairs if key.lower() != "sig"]
    material = "&".join(f"{key}={value}" for key, value in sorted(filtered))
    digest = hmac.new(secret.encode(), (material + secret).encode(), hashlib.sha256).hexdigest()
    return digest


def _query_sig(request) -> str:
    url = getattr(request, "url", None)
    query = getattr(url, "query", "") if url is not None else ""
    if isinstance(query, bytes):
        query = query.decode()
    for key, value in parse_qsl(str(query), keep_blank_values=True):
        if key.lower() == "sig":
            return value
    return ""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)
