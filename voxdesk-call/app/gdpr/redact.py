"""Deterministic PII redaction for transcripts, LLM inputs, webhooks, and exports.

Single canonical redactor across the platform:
* email addresses -> ``[EMAIL]``
* US Social Security Numbers (``NNN-NN-NNNN``) -> ``[SSN]``
* credit-card numbers (13–19 digits passing the **Luhn checksum**) -> ``[CARD]``
* phone numbers (US and E.164 formats) -> ``[PHONE]``

Non-Luhn 13–19 digit sequences (such as innocent 16-digit order or account IDs)
are left untouched and are never mistaken for phone numbers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

TOKEN_EMAIL = "[EMAIL]"
TOKEN_PHONE = "[PHONE]"
TOKEN_CARD = "[CARD]"
TOKEN_SSN = "[SSN]"

_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# 13-19 digits, optionally separated by single spaces or hyphens, bounded by
# non-digits on either side. Candidates are filtered through `luhn_valid`.
_CARD_CANDIDATE_RE = re.compile(
    r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"
)

# Matches E.164 (+14155550123) and common North American formats
# (+1 (555) 123-4567, 555-123-4567, (555) 123 4567) without matching inside a
# longer digit run such as a 16-digit non-Luhn account/order identifier.
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+[1-9]\d{6,14}|(?:\+?\d{1,3}[\s.-])?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4})(?!\d)"
)


def luhn_valid(number: str) -> bool:
    """Return True iff the digit string passes the Luhn checksum (13-19 digits)."""
    digits = [int(ch) for ch in number if ch.isdigit()]
    if not (13 <= len(digits) <= 19):
        return False
    if len(set(digits)) == 1 and digits[0] == 0:
        return False
    checksum = 0
    parity = len(digits) % 2
    for idx, d in enumerate(digits):
        if idx % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


@dataclass(frozen=True)
class RedactionResult:
    text: str
    emails: int = 0
    phones: int = 0
    cards: int = 0
    ssns: int = 0

    @property
    def total(self) -> int:
        return self.emails + self.phones + self.cards + self.ssns

    @property
    def kinds(self) -> list[str]:
        found: list[str] = []
        if self.emails:
            found.append("email")
        if self.phones:
            found.append("phone")
        if self.cards:
            found.append("card")
        if self.ssns:
            found.append("ssn")
        return sorted(found)


def redact_emails(text: str) -> tuple[str, int]:
    count = 0

    def _sub(_match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return TOKEN_EMAIL

    return _EMAIL_RE.sub(_sub, text), count


def redact_ssns(text: str) -> tuple[str, int]:
    count = 0

    def _sub(_match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return TOKEN_SSN

    return _SSN_RE.sub(_sub, text), count


def redact_cards(text: str) -> tuple[str, int]:
    count = 0

    def _sub(match: re.Match[str]) -> str:
        nonlocal count
        candidate = match.group(0)
        if luhn_valid(candidate):
            count += 1
            return TOKEN_CARD
        return candidate

    return _CARD_CANDIDATE_RE.sub(_sub, text), count


def redact_phones(text: str) -> tuple[str, int]:
    count = 0

    def _sub(match: re.Match[str]) -> str:
        nonlocal count
        candidate = match.group(0)
        digits = "".join(ch for ch in candidate if ch.isdigit())
        if len(digits) < 10 or len(digits) > 15:
            return candidate
        count += 1
        return TOKEN_PHONE

    return _PHONE_RE.sub(_sub, text), count


def redact(
    text: str,
    *,
    emails: bool = True,
    phones: bool = True,
    cards: bool = True,
    ssns: bool = True,
) -> RedactionResult:
    """Apply enabled redactors in order (emails -> SSNs -> cards -> phones)."""
    if not text:
        return RedactionResult(text=text or "")
    email_count = phone_count = card_count = ssn_count = 0
    out = text
    if emails:
        out, email_count = redact_emails(out)
    if ssns:
        out, ssn_count = redact_ssns(out)
    if cards:
        out, card_count = redact_cards(out)
    if phones:
        out, phone_count = redact_phones(out)
    return RedactionResult(
        text=out,
        emails=email_count,
        phones=phone_count,
        cards=card_count,
        ssns=ssn_count,
    )
