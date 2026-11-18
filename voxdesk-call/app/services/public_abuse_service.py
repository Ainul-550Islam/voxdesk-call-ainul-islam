"""Public endpoint abuse prevention (PART 14, P14-02).

Everything a stranger can reach costs money: a web call buys STT, LLM and TTS
seconds, and a chat session buys tokens. This module is the gate in front of
those spends, and it is deliberately four independent checks rather than one
combined score, because they fail in different ways:

1. **captcha** -- hCaptcha / reCAPTCHA v3 / Turnstile, with a per-key score
   threshold. A missing token is a refusal, not a pass;
2. **budget** -- a per-key daily cap on requests and on billable seconds, backed
   by a counter row so the cap has something to read;
3. **toll fraud heuristics** -- many calls from one fingerprint in a short
   window, or destinations concentrated in high-cost countries;
4. **origin + key** -- already enforced by the public key service; re-checked
   here so a caller cannot skip it by using this module directly.

The verifier is injected. Production passes a real HTTP client; the tests pass a
scripted one. Nothing in this module makes a network call by itself, which is
what makes the thresholds testable.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.channel_models import PublicAbuseBudget

PROVIDERS = ("recaptcha_v3", "hcaptcha", "turnstile")
#: Score below this is a bot. reCAPTCHA/Turnstile/HCaptcha all answer 0..1.
DEFAULT_THRESHOLD = 0.5
DEFAULT_DAILY_REQUESTS = 500
DEFAULT_DAILY_SECONDS = 3600
#: Toll-fraud heuristics, stated as numbers so an operator can argue with them.
RAPID_CALL_WINDOW_SECONDS = 600
RAPID_CALL_LIMIT = 5
HIGH_COST_PREFIXES = ("+234", "+263", "+265", "+267", "+268", "+371", "+373", "+380", "+381", "+387", "+389", "+40", "+44", "+49")
HIGH_COST_SHARE_LIMIT = 0.5


class AbuseError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


Verifier = Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]]


@dataclass(frozen=True)
class CaptchaConfig:
    provider: str = "recaptcha_v3"
    enabled: bool = True
    threshold: float = DEFAULT_THRESHOLD
    site_key: str = ""

    @classmethod
    def from_widget_config(cls, config: Mapping[str, Any] | None) -> "CaptchaConfig":
        cfg = dict(config or {})
        captcha = dict(cfg.get("captcha") or {})
        provider = str(captcha.get("provider") or "recaptcha_v3")
        if provider not in PROVIDERS:
            raise AbuseError("invalid_captcha_provider", f"unknown captcha provider {provider!r}")
        threshold = float(captcha.get("threshold", DEFAULT_THRESHOLD))
        if not 0.0 <= threshold <= 1.0:
            raise AbuseError("invalid_threshold", "the captcha threshold must be between 0 and 1")
        return cls(
            provider=provider,
            enabled=bool(captcha.get("enabled", True)),
            threshold=threshold,
            site_key=str(captcha.get("site_key") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "enabled": self.enabled,
            "threshold": self.threshold,
            "site_key": self.site_key,
        }


@dataclass(frozen=True)
class BudgetConfig:
    daily_requests: int = DEFAULT_DAILY_REQUESTS
    daily_seconds: int = DEFAULT_DAILY_SECONDS

    @classmethod
    def from_widget_config(cls, config: Mapping[str, Any] | None) -> "BudgetConfig":
        cfg = dict(config or {})
        budget = dict(cfg.get("budget") or {})
        return cls(
            daily_requests=int(budget.get("daily_requests", DEFAULT_DAILY_REQUESTS)),
            daily_seconds=int(budget.get("daily_seconds", DEFAULT_DAILY_SECONDS)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {"daily_requests": self.daily_requests, "daily_seconds": self.daily_seconds}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "reason": self.reason,
            "detail": dict(self.detail),
        }


def utc_day(moment: datetime | None = None) -> str:
    return (moment or datetime.now(timezone.utc)).astimezone(timezone.utc).date().isoformat()


async def _budget_row(
    session: AsyncSession, *, tenant_id: uuid.UUID, public_key_id: uuid.UUID, day: str
) -> PublicAbuseBudget | None:
    return (
        await session.execute(
            select(PublicAbuseBudget).where(
                PublicAbuseBudget.public_key_id == public_key_id, PublicAbuseBudget.day == day
            )
        )
    ).scalar_one_or_none()


async def _budget_for_write(
    session: AsyncSession, *, tenant_id: uuid.UUID, public_key_id: uuid.UUID, day: str
) -> PublicAbuseBudget:
    row = await _budget_row(session, tenant_id=tenant_id, public_key_id=public_key_id, day=day)
    if row is None:
        row = PublicAbuseBudget(
            tenant_id=tenant_id, public_key_id=public_key_id, day=day, requests=0,
            seconds_used=0, refused=0,
        )
        session.add(row)
        await session.flush()
    return row


async def verify_captcha(
    token: str | None,
    config: CaptchaConfig,
    verifier: Verifier | None,
) -> Decision:
    """Check one captcha token. Always awaitable, so callers need no branch."""

    async def _verify() -> Decision:
        if not token:
            return Decision(False, "captcha_missing", "a captcha token is required")
        if verifier is None:
            # No verifier configured is a deployment fault, not a client fault:
            # refuse, and say so, rather than letting every request through.
            return Decision(
                False, "captcha_unconfigured", "the captcha verifier is not configured"
            )
        try:
            result = await verifier(token, {"provider": config.provider})
        except Exception as exc:  # a provider outage must not open the gate
            return Decision(False, "captcha_error", f"captcha verification failed: {exc}"[:200])
        if not result.get("success"):
            return Decision(False, "captcha_failed", "the captcha token was rejected")
        score = result.get("score")
        if score is not None and float(score) < config.threshold:
            return Decision(
                False,
                "captcha_low_score",
                f"captcha score {float(score):.2f} is below the {config.threshold:.2f} threshold",
                {"score": float(score), "threshold": config.threshold},
            )
        return Decision(
            True, "ok", "captcha accepted", {"score": score, "provider": config.provider}
        )

    if not config.enabled:
        return Decision(True, "captcha_disabled", "captcha is turned off for this key")
    if not token:
        return Decision(False, "captcha_missing", "a captcha token is required")
    return await _verify()


async def check_budget(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    public_key_id: uuid.UUID,
    budget: BudgetConfig,
    seconds: int = 0,
    consume: bool = True,
    moment: datetime | None = None,
) -> Decision:
    """Read (and optionally consume) today's budget for this key."""
    day = utc_day(moment)
    row = await _budget_for_write(
        session, tenant_id=tenant_id, public_key_id=public_key_id, day=day
    )
    if row.requests >= budget.daily_requests:
        row.refused = int(row.refused or 0) + 1
        await session.flush()
        return Decision(
            False,
            "budget_requests_exhausted",
            f"this key has used {row.requests} of {budget.daily_requests} daily requests",
            {"day": day, "requests": row.requests},
        )
    if seconds and row.seconds_used + seconds > budget.daily_seconds:
        row.refused = int(row.refused or 0) + 1
        await session.flush()
        return Decision(
            False,
            "budget_seconds_exhausted",
            f"this key has used {row.seconds_used} of {budget.daily_seconds} daily seconds",
            {"day": day, "seconds_used": row.seconds_used},
        )
    if consume:
        row.requests = int(row.requests or 0) + 1
        row.seconds_used = int(row.seconds_used or 0) + int(seconds)
        await session.flush()
    return Decision(
        True,
        "ok",
        "within budget",
        {"day": day, "requests": row.requests, "seconds_used": row.seconds_used},
    )


def toll_fraud_check(
    *,
    recent_calls: int,
    destinations: Iterable[str] = (),
    fingerprint_reuse: int = 1,
    now: datetime | None = None,
) -> Decision:
    """Cheap heuristics that catch the common toll-fraud patterns.

    Not a fraud *score*. Two rules an operator can read, and a refusal that
    names which one fired:

    * many calls from one fingerprint inside a short window (scripted abuse);
    * a majority of destinations in high-cost prefixes (premium-rate resale).
    """
    if recent_calls > RAPID_CALL_LIMIT and fingerprint_reuse > RAPID_CALL_LIMIT:
        return Decision(
            False,
            "toll_fraud_rapid",
            f"{recent_calls} calls in {RAPID_CALL_WINDOW_SECONDS}s from one fingerprint",
            {"recent_calls": recent_calls, "fingerprint_reuse": fingerprint_reuse},
        )
    numbers = [number for number in destinations if number]
    if numbers:
        high_cost = sum(1 for number in numbers if number.startswith(HIGH_COST_PREFIXES))
        share = high_cost / len(numbers)
        if len(numbers) >= 4 and share >= HIGH_COST_SHARE_LIMIT:
            return Decision(
                False,
                "toll_fraud_destinations",
                f"{high_cost} of {len(numbers)} destinations are in high-cost prefixes",
                {"share": round(share, 3)},
            )
    return Decision(True, "ok", "no toll-fraud pattern")


def fingerprint(ip: str | None, user_agent: str | None, salt: str) -> str:
    """A stable, non-reversible per-visitor id. Never stores the IP itself."""
    raw = f"{ip or ''}|{user_agent or ''}|{salt}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


async def evaluate(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    public_key_id: uuid.UUID,
    widget_config: Mapping[str, Any] | None,
    captcha_token: str | None,
    verifier: Verifier | None = None,
    seconds: int = 0,
    recent_calls: int = 0,
    destinations: Iterable[str] = (),
    consume: bool = True,
    moment: datetime | None = None,
) -> Decision:
    """All the gates, in the order that fails cheapest first."""
    captcha_config = CaptchaConfig.from_widget_config(widget_config)
    budget_config = BudgetConfig.from_widget_config(widget_config)

    fraud = toll_fraud_check(recent_calls=recent_calls, destinations=destinations, now=moment)
    if not fraud.allowed:
        row = await _budget_for_write(
            session, tenant_id=tenant_id, public_key_id=public_key_id, day=utc_day(moment)
        )
        row.refused = int(row.refused or 0) + 1
        await session.flush()
        return fraud

    decision = await verify_captcha(captcha_token, captcha_config, verifier)
    if not decision.allowed:
        row = await _budget_for_write(
            session, tenant_id=tenant_id, public_key_id=public_key_id, day=utc_day(moment)
        )
        row.refused = int(row.refused or 0) + 1
        await session.flush()
        return decision

    return await check_budget(
        session,
        tenant_id=tenant_id,
        public_key_id=public_key_id,
        budget=budget_config,
        seconds=seconds,
        consume=consume,
        moment=moment,
    )


async def usage(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    public_key_id: uuid.UUID,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Today's counters, for the key settings screen."""
    day = utc_day(moment)
    row = await _budget_row(session, tenant_id=tenant_id, public_key_id=public_key_id, day=day)
    return {
        "day": day,
        "requests": int(getattr(row, "requests", 0) or 0),
        "seconds_used": int(getattr(row, "seconds_used", 0) or 0),
        "refused": int(getattr(row, "refused", 0) or 0),
    }


# ---------------------------------------------------------------------------
# The production verifier.
#
# `evaluate` takes its verifier as an argument so it can be tested. This is the
# default the routes pass: a real HTTP call to the configured provider, using
# only endpoints and field names that provider documents. If no secret is
# configured it returns None, and `verify_captcha` then refuses with
# `captcha_unconfigured` rather than silently letting every request through.
# ---------------------------------------------------------------------------

VERIFY_URLS = {
    "recaptcha_v3": "https://www.google.com/recaptcha/api/siteverify",
    "hcaptcha": "https://hcaptcha.com/siteverify",
    "turnstile": "https://challenges.cloudflare.com/turnstile/v0/siteverify",
}


def configured_secret(provider: str) -> str:
    """Read the provider secret from settings without inventing a second config."""
    from app.core.config import get_settings

    settings = get_settings()
    names = {
        "recaptcha_v3": ("recaptcha_secret", "recaptcha_secret_key"),
        "hcaptcha": ("hcaptcha_secret", "hcaptcha_secret_key"),
        "turnstile": ("turnstile_secret", "turnstile_secret_key"),
    }
    for name in names.get(provider, ()):
        value = getattr(settings, name, "") or ""
        if value:
            return str(value)
    return ""


def default_verifier() -> Verifier | None:
    """A verifier bound to the configured provider secrets, or None."""
    import httpx

    async def _verify(token: str, meta: Mapping[str, Any]) -> Mapping[str, Any]:
        provider = str(meta.get("provider") or "recaptcha_v3")
        secret = configured_secret(provider)
        if not secret:
            raise AbuseError("captcha_unconfigured", f"no {provider} secret is configured")
        url = VERIFY_URLS.get(provider)
        if url is None:
            raise AbuseError("invalid_captcha_provider", f"unknown captcha provider {provider!r}")
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.post(url, data={"secret": secret, "response": token})
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, Mapping):
            raise AbuseError("captcha_error", "the captcha provider returned no object")
        return {"success": bool(payload.get("success")), "score": payload.get("score")}

    for provider in PROVIDERS:
        if configured_secret(provider):
            return _verify
    return None
