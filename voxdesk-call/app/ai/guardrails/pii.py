"""Thin guardrail wrapper delegating all PII detection and redaction to app.gdpr.redact."""

from __future__ import annotations

from app.gdpr.redact import redact as _gdpr_redact


class RedactedText(str):
    """String result that also supports ``redacted_text, findings = redact(text)`` unpacking."""

    findings: list[str]
    text: str

    def __new__(cls, text: str, findings: list[str]) -> "RedactedText":
        obj = super().__new__(cls, text)
        obj.findings = list(findings)
        obj.text = str(text)
        return obj

    def __iter__(self):
        return iter((str(self), list(self.findings)))


def detect(text: str) -> list[str]:
    """Return sorted PII kinds detected in ``text`` using the canonical GDPR redactor."""
    return _gdpr_redact(text or "").kinds


def has_pii(text: str) -> bool:
    """Return True when ``text`` contains any canonical PII pattern."""
    return bool(detect(text))


def redact(text: str) -> RedactedText:
    """Redact PII via ``app.gdpr.redact.redact``.

    Returns a ``RedactedText`` (a ``str`` subclass) that behaves as the redacted
    ``str`` and also unpacks as ``(redacted_text, findings)``.
    """
    result = _gdpr_redact(text or "")
    return RedactedText(result.text, result.kinds)


def enforce(text: str) -> dict[str, object]:
    """Return redaction metadata and scrubbed text for AI guardrail callers."""
    result = _gdpr_redact(text or "")
    kinds = result.kinds
    return {
        "redacted": result.text,
        "kinds": kinds,
        "redacted_any": bool(kinds),
        "guaranteed_detection": False,
    }
