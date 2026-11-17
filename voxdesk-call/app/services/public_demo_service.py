"""Request-a-call demo gate (PART 18, P18-03).

The public demo is the one unauthenticated feature in the product that dials a
real telephone number on the vendor's account. Everything here exists because
that sentence is dangerous:

1. **captcha** — delegated to ``public_abuse_service.verify_captcha``, which is
   already reviewed and provider-agnostic. A missing token is a refusal.
2. **consent** — the caller must tick a box whose exact wording is stored with
   the request. If the consent text changes, old requests keep the text they
   actually agreed to; we never rewrite history.
3. **rate limit** — per IP *and* per destination number, both sliding windows,
   because the two attacks are different: one IP enumerating numbers, or one
   number being dialled from many IPs.
4. **verified destination only** — a demo will call a number only if a human has
   proved they control it (`demo_destination_verifications`). This is the rule
   that stops the demo being turned into a free robocall cannon.
5. **hard cost cap** — a per-day budget in billed seconds. Checked before the
   dial and consumed on the dial, so the cap is a real ceiling rather than an
   average.

Nothing here makes a network call: the captcha verifier and the dialler are both
injected, which is what makes the thresholds testable without a phone or a
provider account.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.demo_models import DemoCallRequest, DemoDestinationVerification
from app.services import public_abuse_service

#: The exact text a visitor agrees to. Stored per request; changing it here does
#: not change what an existing request says it agreed to.
CONSENT_TEXT_VERSION = "2026-11-08"
CONSENT_TEXT = (
    "I consent to VoxDesk placing one automated demonstration call to the number "
    "I have verified. The call is recorded and transcribed for the purpose of "
    "demonstrating the product and is deleted after 30 days. I confirm I control "
    "this number or have the permission of the person who does, and that I am "
    "18 or older. I can ask for the recording to be deleted at any time."
)

#: Cost cap. Stated as numbers so an operator can argue with them rather than
#: guessing what "capped" means.
DAILY_CALL_CAP = 25
DAILY_SECONDS_CAP = 600  # 25 calls x 24 s, plus headroom
MAX_CALL_SECONDS = 60
DEFAULT_CALL_SECONDS = 25

#: Rate limits. Independent windows, checked before any spend.
PER_IP_HOURLY_LIMIT = 3
PER_NUMBER_DAILY_LIMIT = 2
IP_WINDOW = timedelta(hours=1)
NUMBER_WINDOW = timedelta(days=1)

#: A verification code is short-lived and single-use.
VERIFICATION_TTL = timedelta(minutes=15)
VERIFICATION_CODE_LENGTH = 6

_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


class DemoError(Exception):
    """A refusal with a stable code, so the page can explain itself."""

    def __init__(self, code: str, message: str, status: int = 400, detail: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "detail": self.detail}


@dataclass(frozen=True)
class DemoConfig:
    """Every knob the demo has, in one object, so it can be frozen in a test."""

    enabled: bool = True
    daily_calls: int = DAILY_CALL_CAP
    daily_seconds: int = DAILY_SECONDS_CAP
    max_call_seconds: int = MAX_CALL_SECONDS
    default_call_seconds: int = DEFAULT_CALL_SECONDS
    per_ip_hourly: int = PER_IP_HOURLY_LIMIT
    per_number_daily: int = PER_NUMBER_DAILY_LIMIT
    captcha: public_abuse_service.CaptchaConfig = public_abuse_service.CaptchaConfig()

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any] | None = None) -> "DemoConfig":
        from app.core.config import get_settings

        values = settings if settings is not None else get_settings()
        get = (
            (lambda name, default: values.get(name, default))
            if isinstance(values, Mapping)
            else (lambda name, default: getattr(values, name, default))
        )
        provider = str(get("demo_captcha_provider", "recaptcha_v3") or "recaptcha_v3")
        captcha = public_abuse_service.CaptchaConfig(
            provider=provider,
            enabled=bool(get("demo_captcha_enabled", True)),
            threshold=float(get("demo_captcha_threshold", public_abuse_service.DEFAULT_THRESHOLD)),
            site_key=str(get("demo_captcha_site_key", "") or ""),
        )
        return cls(
            enabled=bool(get("demo_enabled", True)),
            daily_calls=int(get("demo_daily_calls", DAILY_CALL_CAP)),
            daily_seconds=int(get("demo_daily_seconds", DAILY_SECONDS_CAP)),
            max_call_seconds=int(get("demo_max_call_seconds", MAX_CALL_SECONDS)),
            default_call_seconds=int(
                get("demo_default_call_seconds", DEFAULT_CALL_SECONDS)
            ),
            per_ip_hourly=int(get("demo_per_ip_hourly", PER_IP_HOURLY_LIMIT)),
            per_number_daily=int(get("demo_per_number_daily", PER_NUMBER_DAILY_LIMIT)),
            captcha=captcha,
        )


# ---------------------------------------------------------------------------
# Destination normalisation and verification
# ---------------------------------------------------------------------------


def normalise_destination(raw: str) -> str:
    """Keep only E.164. A demo never dials something it cannot name exactly."""
    cleaned = re.sub(r"[\s()\-.]+", "", str(raw or "").strip())
    if not cleaned.startswith("+"):
        raise DemoError(
            "destination_not_e164",
            "enter the number in international format, starting with '+' and the country code",
        )
    if not _E164_RE.match(cleaned):
        raise DemoError("destination_not_e164", "that is not a valid international number")
    return cleaned


def mask_destination(number: str) -> str:
    """A number that can be shown in a UI or a log without being dialable.

    Keeps the first four characters (the ``+`` and country/area prefix, which is
    what a human uses to recognise their own number) and the last two, and stars
    everything between. Short numbers are not partially shown at all.
    """
    if len(number) <= 8:
        return "***"
    return f"{number[:4]}{'*' * (len(number) - 6)}{number[-2:]}"


def hash_destination(number: str, salt: str) -> str:
    """Indexable form for rate limiting without storing the number in the clear."""
    return hashlib.sha256(f"{salt}:{number}".encode()).hexdigest()


class VerificationCodeSender:
    """Sends the verification code. Replaced in tests; prod sends an SMS."""

    async def __call__(self, number: str, code: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


async def request_verification(
    session: AsyncSession,
    *,
    number: str,
    send_code: Callable[[str, str], Awaitable[None]],
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Create or refresh a verification row and hand the code to the sender.

    The code is stored only as a hash: a dump of this table must not let anyone
    verify control of a number they do not own.
    """
    normalised = normalise_destination(number)
    now = moment or datetime.now(timezone.utc)
    code = "".join(secrets.choice("0123456789") for _ in range(VERIFICATION_CODE_LENGTH))

    existing = await session.scalar(
        select(DemoDestinationVerification).where(
            DemoDestinationVerification.number == normalised
        )
    )
    row = existing or DemoDestinationVerification(number=normalised)
    row.code_hash = _hash_code(normalised, code)
    row.expires_at = now + VERIFICATION_TTL
    row.attempts = 0
    if existing is None:
        session.add(row)
    await session.flush()

    await send_code(normalised, code)
    return {
        "destination": mask_destination(normalised),
        "expires_in_seconds": int(VERIFICATION_TTL.total_seconds()),
        "code_length": VERIFICATION_CODE_LENGTH,
    }


def _hash_code(number: str, code: str) -> str:
    return hashlib.sha256(f"{number}:{code}".encode()).hexdigest()


async def confirm_verification(
    session: AsyncSession,
    *,
    number: str,
    code: str,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Consume a code. Single use, expiring, and attempt-limited."""
    normalised = normalise_destination(number)
    now = moment or datetime.now(timezone.utc)
    row = await session.scalar(
        select(DemoDestinationVerification).where(
            DemoDestinationVerification.number == normalised
        )
    )
    if row is None or not row.code_hash:
        raise DemoError(
            "verification_not_requested", "request a code before confirming one", 409
        )
    if row.verified_at is not None:
        return {"destination": mask_destination(normalised), "already_verified": True}
    if row.expires_at is not None and row.expires_at <= now:
        raise DemoError("verification_expired", "that code has expired; request a new one", 410)
    if int(row.attempts or 0) >= 5:
        raise DemoError(
            "verification_attempts_exhausted",
            "too many wrong codes for this number; request a new one in 15 minutes",
            429,
        )
    if not secrets.compare_digest(row.code_hash, _hash_code(normalised, str(code or "").strip())):
        row.attempts = int(row.attempts or 0) + 1
        await session.flush()
        raise DemoError("verification_code_wrong", "that code is not correct", 403)

    row.verified_at = now
    row.code_hash = ""  # single use: consumed on success
    await session.flush()
    return {"destination": mask_destination(normalised), "verified": True}


async def is_verified(session: AsyncSession, number: str) -> bool:
    normalised = normalise_destination(number)
    row = await session.scalar(
        select(DemoDestinationVerification).where(
            DemoDestinationVerification.number == normalised
        )
    )
    return bool(row and row.verified_at is not None)


# ---------------------------------------------------------------------------
# Rate limiting and the cost cap
# ---------------------------------------------------------------------------


async def _count_recent(
    session: AsyncSession,
    *,
    since: datetime,
    ip_hash: str | None = None,
    number_hash: str | None = None,
) -> int:
    stmt = select(func.count()).select_from(DemoCallRequest).where(
        DemoCallRequest.created_at >= since
    )
    if ip_hash is not None:
        stmt = stmt.where(DemoCallRequest.ip_hash == ip_hash)
    if number_hash is not None:
        stmt = stmt.where(DemoCallRequest.number_hash == number_hash)
    return int(await session.scalar(stmt) or 0)


async def check_rate_limits(
    session: AsyncSession,
    *,
    ip_hash: str,
    number_hash: str,
    config: DemoConfig,
    moment: datetime | None = None,
) -> dict[str, int]:
    """Both windows, checked before anything else that costs money."""
    now = moment or datetime.now(timezone.utc)
    per_ip = await _count_recent(
        session, since=now - IP_WINDOW, ip_hash=ip_hash
    )
    if per_ip >= config.per_ip_hourly:
        raise DemoError(
            "rate_limited_ip",
            f"this connection has requested {per_ip} demo calls in the last hour",
            429,
            {"limit": config.per_ip_hourly, "window": "1h"},
        )
    per_number = await _count_recent(
        session, since=now - NUMBER_WINDOW, number_hash=number_hash
    )
    if per_number >= config.per_number_daily:
        raise DemoError(
            "rate_limited_number",
            "this number has already had its demo calls for today",
            429,
            {"limit": config.per_number_daily, "window": "24h"},
        )
    return {"ip_used": per_ip, "number_used": per_number}


async def check_cost_cap(
    session: AsyncSession,
    *,
    config: DemoConfig,
    seconds: int,
    moment: datetime | None = None,
) -> dict[str, int]:
    """Today's global demo spend, in calls and billed seconds.

    Counted across all visitors: the cap protects the vendor's wallet, so it
    cannot be per-IP or one attacker with many addresses defeats it.
    """
    now = moment or datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    calls = await _count_recent(session, since=day_start)
    seconds_used = int(
        await session.scalar(
            select(func.coalesce(func.sum(DemoCallRequest.max_seconds), 0)).where(
                DemoCallRequest.created_at >= day_start
            )
        )
        or 0
    )
    if calls >= config.daily_calls:
        raise DemoError(
            "demo_daily_cap_reached",
            "the public demo has used its calls for today — request a walkthrough instead",
            429,
            {"calls": calls, "limit": config.daily_calls, "retry_after": "midnight UTC"},
        )
    if seconds_used + seconds > config.daily_seconds:
        raise DemoError(
            "demo_seconds_cap_reached",
            "the public demo has used its call minutes for today — request a walkthrough instead",
            429,
            {"seconds_used": seconds_used, "limit": config.daily_seconds},
        )
    return {"calls": calls, "seconds_used": seconds_used}


# ---------------------------------------------------------------------------
# The request itself
# ---------------------------------------------------------------------------


async def create_demo_call(
    session: AsyncSession,
    *,
    number: str,
    ip: str,
    user_agent: str | None,
    consent: bool,
    consent_version: str,
    captcha_token: str | None,
    config: DemoConfig,
    salt: str,
    verifier: public_abuse_service.Verifier | None = None,
    dialler: Callable[[str, int], Awaitable[str]] | None = None,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Run every gate in cost order, then dial once. Returns the record.

    Order matters and is deliberate: the cheapest checks reject first, so a bot
    cannot make the server do expensive work (captcha verification is an HTTP
    round trip, a dial costs real money).
    """
    if not config.enabled:
        raise DemoError("demo_disabled", "the public demo is closed right now", 503)

    destination = normalise_destination(number)

    if not consent:
        raise DemoError(
            "consent_required",
            "the consent box must be ticked — we do not place a demo call without it",
            422,
        )
    if consent_version != CONSENT_TEXT_VERSION:
        raise DemoError(
            "consent_text_changed",
            "the consent wording changed while this page was open; reload and read it again",
            409,
            {"current_version": CONSENT_TEXT_VERSION},
        )

    # 1. captcha (network round trip, but no spend)
    decision = await public_abuse_service.verify_captcha(
        captcha_token, config.captcha, verifier
    )
    if not decision.allowed:
        raise DemoError(decision.code, decision.reason, 400 if "missing" in decision.code else 403)

    # 2. verified destination — the rule that stops this being a robocall cannon
    if not await is_verified(session, destination):
        raise DemoError(
            "destination_not_verified",
            "verify this number first — we only place demo calls to numbers someone "
            "has proved they control",
            403,
            {"destination": mask_destination(destination)},
        )

    # 3. rate limits and 4. the cost cap
    ip_hash = hash_destination(ip, salt)
    number_hash = hash_destination(destination, salt)
    await check_rate_limits(
        session, ip_hash=ip_hash, number_hash=number_hash, config=config, moment=moment
    )
    await check_cost_cap(
        session, config=config, seconds=config.default_call_seconds, moment=moment
    )

    row = DemoCallRequest(
        created_at=moment or datetime.now(timezone.utc),
        number=destination,
        number_hash=number_hash,
        ip_hash=ip_hash,
        user_agent=(user_agent or "")[:255] or None,
        consent_text=CONSENT_TEXT,
        consent_version=CONSENT_TEXT_VERSION,
        max_seconds=min(config.default_call_seconds, config.max_call_seconds),
        status="queued",
    )
    session.add(row)
    await session.flush()

    if dialler is not None:
        try:
            provider_ref = await dialler(destination, row.max_seconds)
        except Exception as exc:  # a failed dial is recorded, not hidden
            row.status = "dial_failed"
            row.failure_reason = f"{type(exc).__name__}: {exc}"[:400]
            await session.flush()
            raise DemoError(
                "dial_failed",
                "the demo call could not be placed; nothing has been charged to you",
                502,
                {"request_id": str(row.id)},
            ) from None
        row.status = "dialled"
        row.provider_ref = str(provider_ref)[:255]
        await session.flush()

    return row.as_dict()


async def demo_usage(
    session: AsyncSession, *, config: DemoConfig, moment: datetime | None = None
) -> dict[str, Any]:
    """What the public page shows above the form: is the demo open today?"""
    now = moment or datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    calls = await _count_recent(session, since=day_start)
    seconds_used = int(
        await session.scalar(
            select(func.coalesce(func.sum(DemoCallRequest.max_seconds), 0)).where(
                DemoCallRequest.created_at >= day_start
            )
        )
        or 0
    )
    return {
        "enabled": config.enabled,
        "calls_today": calls,
        "calls_remaining": max(config.daily_calls - calls, 0),
        "seconds_used": seconds_used,
        "seconds_remaining": max(config.daily_seconds - seconds_used, 0),
        "max_call_seconds": config.max_call_seconds,
        "captcha": {
            "provider": config.captcha.provider,
            "site_key": config.captcha.site_key,
            "enabled": config.captcha.enabled,
        },
        "consent_text": CONSENT_TEXT,
        "consent_version": CONSENT_TEXT_VERSION,
    }
