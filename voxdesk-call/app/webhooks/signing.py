"""HMAC signatures for outbound webhooks.

Format: ``t=<unix>,v1=<hex>``. Customers can reject an old timestamp. The raw
secret is never logged by this module.
"""

from __future__ import annotations

import hashlib
import hmac
import time


def sign(secret: str, body: bytes, *, timestamp: int | None = None) -> str:
    moment = int(time.time()) if timestamp is None else int(timestamp)
    signed = f"{moment}.".encode() + body
    digest = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return f"t={moment},v1={digest}"


def verify(
    secret: str, body: bytes, header: str, *, tolerance_seconds: int = 300, now: int | None = None
) -> bool:
    parts = {}
    for item in header.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts[key.strip()] = value.strip()
    if "t" not in parts or "v1" not in parts:
        return False
    try:
        moment = int(parts["t"])
    except ValueError:
        return False
    current = int(time.time()) if now is None else int(now)
    if abs(current - moment) > tolerance_seconds:
        return False
    expected = sign(secret, body, timestamp=moment)
    return hmac.compare_digest(expected, f"t={moment},v1={parts['v1']}")
