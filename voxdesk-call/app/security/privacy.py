"""Policy-driven PII masking and deterministic tokenization."""

from __future__ import annotations
import hashlib
import hmac
import re
from dataclasses import dataclass
from app.core.config import settings

_PATTERNS = {
    "email": re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    "phone": re.compile(r"(?<!\d)(?:\+?\d[\d ().-]{7,}\d)(?!\d)"),
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "card": re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
}


@dataclass(frozen=True)
class PrivacyPolicy:
    mode: str = "mask"
    categories: tuple[str, ...] = ("email", "phone", "ssn", "card")
    replacement: str = "[REDACTED]"


def _token(category: str, value: str) -> str:
    digest = hmac.new(
        settings.jwt_secret.encode(), f"{category}:{value}".encode(), hashlib.sha256
    ).hexdigest()[:20]
    return f"<{category}_{digest}>"


def transform(text: str, policy: PrivacyPolicy) -> str:
    if policy.mode not in {"mask", "tokenize"}:
        raise ValueError("privacy mode must be mask or tokenize")
    output = text
    for category in policy.categories:
        pattern = _PATTERNS.get(category)
        if pattern is None:
            continue
        output = pattern.sub(
            lambda match: _token(category, match.group(0))
            if policy.mode == "tokenize"
            else policy.replacement,
            output,
        )
    return output


def detect(
    text: str, categories: tuple[str, ...] = ("email", "phone", "ssn", "card")
) -> dict[str, int]:
    return {
        category: len(_PATTERNS[category].findall(text))
        for category in categories
        if category in _PATTERNS
    }
