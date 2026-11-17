"""Slack request signature verification (`v0=` scheme)."""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Mapping

#: Slack's own recommendation. Older than this and a captured request replays.
MAX_SKEW_SECONDS = 300


class SignatureError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def sign(secret: str, timestamp: str | int, body: str) -> str:
    """The `v0=` signature Slack would send for this body."""
    base = f"v0:{timestamp}:{body}".encode()
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def verify(
    *,
    secret: str,
    body: str,
    headers: Mapping[str, str],
    now: float | None = None,
    max_skew: int = MAX_SKEW_SECONDS,
) -> None:
    """Raise :class:`SignatureError` unless the request is genuinely from Slack."""
    lowered = {key.lower(): value for key, value in headers.items()}
    timestamp = lowered.get("x-slack-request-timestamp")
    provided = lowered.get("x-slack-signature")
    if not timestamp or not provided:
        raise SignatureError("missing_signature", "the request carries Slack signature headers")
    try:
        ts_value = int(timestamp)
    except ValueError:
        raise SignatureError("bad_timestamp", "the Slack timestamp is not an integer") from None
    current = time.time() if now is None else now
    if abs(current - ts_value) > max_skew:
        # Replay defence: a valid signature on a ten-minute-old body is still a
        # forgery if it has been replayed.
        raise SignatureError(
            "stale_timestamp",
            f"the Slack timestamp is {int(abs(current - ts_value))}s old (limit {max_skew}s)",
        )
    if not secret:
        raise SignatureError("no_secret", "no Slack signing secret is configured")
    expected = sign(secret, timestamp, body)
    if not hmac.compare_digest(expected, provided):
        raise SignatureError("bad_signature", "the Slack signature does not match the body")
