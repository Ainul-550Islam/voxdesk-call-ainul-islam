"""Shared redaction for structured logs and durable audit detail.

Redaction is deliberately applied before persistence/rendering, not only in the
Dashboard. The primary guard is key-based; value patterns are a second line of
defence for credentials copied into an innocently named ``error`` or ``detail``
field. Audit identifiers such as user IDs, request IDs and resource IDs remain
visible; bearer credentials and common direct identifiers in free text do not.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REDACTED = "[REDACTED]"
TRUNCATED = "[TRUNCATED]"
MAX_STRING_LENGTH = 4096
MAX_COLLECTION_ITEMS = 100
MAX_DEPTH = 10

_SENSITIVE_SEGMENTS = frozenset(
    {
        "password", "passwd", "pwd", "secret", "secrets", "token", "tokens",
        "authorization", "cookie", "credential", "credentials", "private_key",
        "client_secret", "api_key", "apikey", "access_token", "refresh_token",
        "id_token", "jwt", "bearer", "signature", "webhook_secret", "otp",
        "mfa_code", "totp", "totp_secret", "verification_code", "recovery_code", "assertion", "session_secret",
        "csrf_token", "refresh_tokens", "access_tokens", "session_tokens",
        "service_account_secret", "scim_token", "password_hash",
        "secret_hash", "token_hash", "credential_hash", "salt",
    }
)

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bBearer\s+[^\s,;]+"),
    re.compile(r"(?i)\bBasic\s+[A-Za-z0-9+/=]{8,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:vdk|vdsa|vdscim)_[0-9a-fA-F]{8,32}_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk_(?:live|test)_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bwhsec_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\brk_(?:live|test)_[A-Za-z0-9]{12,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.DOTALL),
    re.compile(r"(?i)([?&;](?:access_token|refresh_token|token|api_key|secret|password)=)[^&#\s]+"),
    re.compile(r"(?i)(://[^/:@\s]+:)[^@/\s]+(@)"),
)
_EMAIL = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_PHONE = re.compile(r"(?<![\w.-])\+?\d[\d ().-]{7,}\d(?![\w.-])")


def is_sensitive_key(key: str) -> bool:
    """Whether a structured field name denotes a credential or secret."""
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).strip().lower()).strip("_")
    if not normalized:
        return False
    if normalized in _SENSITIVE_SEGMENTS:
        return True
    segments = set(normalized.split("_"))
    if segments & {"password", "passwd", "pwd", "secret", "secrets", "authorization", "cookie", "credential", "credentials", "jwt", "bearer", "otp", "assertion"}:
        return True
    if normalized.endswith(("_private_key", "_api_key", "_apikey", "_secret", "_token", "_credential", "_credentials")):
        return True
    return normalized.startswith(("authorization_", "cookie_", "set_cookie"))


def redact_text(
    value: str,
    *,
    known_secrets: tuple[str, ...] | list[str] = (),
    redact_pii: bool = True,
) -> str:
    """Redact credential-shaped values and, optionally, direct PII in text."""
    text = value
    for secret in sorted({str(item) for item in known_secrets if item and len(str(item)) >= 4}, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda match: "".join((match.group(1) or "", REDACTED, match.group(2) or "")), text)
        else:
            text = pattern.sub(REDACTED, text)
    if redact_pii:
        text = _EMAIL.sub("[REDACTED_EMAIL]", text)
        text = _PHONE.sub("[REDACTED_PHONE]", text)
    if len(text) > MAX_STRING_LENGTH:
        text = text[:MAX_STRING_LENGTH] + TRUNCATED
    return text


def redact_tree(
    value: Any,
    *,
    known_secrets: tuple[str, ...] | list[str] = (),
    redact_pii: bool = True,
    _depth: int = 0,
    _key: str = "",
) -> Any:
    """Return a bounded JSON-safe structure with secrets scrubbed recursively."""
    if is_sensitive_key(_key):
        return REDACTED
    if _depth > MAX_DEPTH:
        return "[MAX_DEPTH]"
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_COLLECTION_ITEMS:
                cleaned["_truncated_items"] = len(value) - MAX_COLLECTION_ITEMS
                break
            key_text = str(key)[:128]
            cleaned[key_text] = redact_tree(
                item,
                known_secrets=known_secrets,
                redact_pii=redact_pii,
                _depth=_depth + 1,
                _key=key_text,
            )
        return cleaned
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        cleaned = [
            redact_tree(
                item,
                known_secrets=known_secrets,
                redact_pii=redact_pii,
                _depth=_depth + 1,
                _key=_key,
            )
            for item in items[:MAX_COLLECTION_ITEMS]
        ]
        if len(items) > MAX_COLLECTION_ITEMS:
            cleaned.append({"_truncated_items": len(items) - MAX_COLLECTION_ITEMS})
        return cleaned
    if isinstance(value, str):
        return redact_text(value, known_secrets=known_secrets, redact_pii=redact_pii)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if hasattr(value, "value") and isinstance(getattr(value, "value"), (str, int)):
        return redact_tree(
            value.value,
            known_secrets=known_secrets,
            redact_pii=redact_pii,
            _depth=_depth + 1,
            _key=_key,
        )
    return redact_text(str(value), known_secrets=known_secrets, redact_pii=redact_pii)


def configured_secret_values() -> tuple[str, ...]:
    """Read configured secret values lazily for value-based log/audit redaction."""
    try:
        from app.core.config import settings
    except Exception:  # importing settings must never break logging/auditing
        __import__("logging").getLogger(__name__).debug("suppressed_exception", exc_info=True)
        return ()
    names = (
        "secret_key", "jwt_secret", "twilio_auth_token", "twilio_account_sid", "openai_api_key",
        "anthropic_api_key", "google_api_key", "deepgram_api_key",
        "elevenlabs_api_key", "stripe_secret_key", "stripe_webhook_secret",
        "metrics_token", "license_secret", "identity_encryption_keys",
        "crm_encryption_keys", "telnyx_api_key", "telnyx_public_key",
        "vonage_api_key", "vonage_api_secret", "vonage_signature_secret",
        "vonage_private_key", "smtp_password", "conductor_webhook_secret",
        "realtime_gateway_ingest_secret", "artifact_registry_bearer_token",
    )
    return tuple(
        value for name in names
        if isinstance((value := getattr(settings, name, None)), str) and value
    )
