"""Deterministic redaction for telemetry. Not a complete PII detector.

Email, phone, SSN-shaped and card-shaped strings are masked. Anything else is
left alone. Authorization still has to reject the action; this only keeps
those patterns out of logs.
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{8,}\d)")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_CARD = re.compile(r"\b(?:\d[ -]*?){13,19}\b")


def enforce(text: str) -> dict:
    """Redact a copy for telemetry. The caller still has to authorize the action."""
    raw = text or ""
    return {
        "kinds": detect(raw),
        "redacted": redact(raw),
        "guaranteed_detection": False,
    }


def detect(text: str) -> tuple[str, ...]:
    found = []
    if _EMAIL.search(text or ""):
        found.append("email")
    if _PHONE.search(text or ""):
        found.append("phone")
    if _SSN.search(text or ""):
        found.append("ssn")
    if _CARD.search(text or ""):
        found.append("card")
    return tuple(found)


def redact(text: str) -> str:
    value = text or ""
    value = _EMAIL.sub("[email]", value)
    value = _SSN.sub("[ssn]", value)
    value = _CARD.sub("[card]", value)
    value = _PHONE.sub("[phone]", value)
    return value
