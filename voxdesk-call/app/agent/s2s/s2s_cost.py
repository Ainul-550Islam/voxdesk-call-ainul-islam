"""Speech-to-speech metering and budget enforcement.

A realtime session burns money in two currencies: tokens and audio-seconds.
Both are metered here, and the meter enforces the two ceilings that keep a
single call from becoming an unbounded bill:

* ``S2S_MAX_SESSION_SECONDS`` -- the wall-clock length of one session;
* the per-session and per-tenant micro-USD budgets.

Token usage is written into the **existing** usage ledger
(``app.billing.metering``) with a deterministic idempotency key, so a
retried or duplicated finalisation can never bill twice. The audio-second
counters live on the session record this batch adds
(``s2s_sessions``), because the existing ledger has no audio-second metric.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from app.agent.s2s.s2s_session import S2SBudgetExceeded
from app.core.logging import log

#: Micro-USD per million tokens (input / output). Zero means "the operator
#: has not configured a price", and the cost stays unknown rather than
#: being invented.
DEFAULT_INPUT_MICRO_USD_PER_MILLION = 0
DEFAULT_OUTPUT_MICRO_USD_PER_MILLION = 0
#: Micro-USD per second of audio (input / output).
DEFAULT_INPUT_MICRO_USD_PER_SECOND = 0
DEFAULT_OUTPUT_MICRO_USD_PER_SECOND = 0


@dataclass
class S2SUsage:
    """The measured usage of one session."""

    input_audio_seconds: float = 0.0
    output_audio_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micro_usd: int = 0
    cost_known: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_audio_seconds": round(self.input_audio_seconds, 4),
            "output_audio_seconds": round(self.output_audio_seconds, 4),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_micro_usd": self.cost_micro_usd,
            "cost_known": self.cost_known,
        }


@dataclass
class S2SCostMeter:
    """Meters one speech-to-speech session and enforces its ceilings."""

    provider: str = ""
    model: str = ""
    max_session_seconds: int = 600
    session_budget_micro_usd: int = 500_000
    tenant_budget_micro_usd: int = 5_000_000
    input_micro_usd_per_million: int = DEFAULT_INPUT_MICRO_USD_PER_MILLION
    output_micro_usd_per_million: int = DEFAULT_OUTPUT_MICRO_USD_PER_MILLION
    input_micro_usd_per_second: int = DEFAULT_INPUT_MICRO_USD_PER_SECOND
    output_micro_usd_per_second: int = DEFAULT_OUTPUT_MICRO_USD_PER_SECOND
    #: What the tenant has already spent on speech-to-speech, in micro-USD.
    #: The meter adds its own cost to this figure when it checks the
    #: tenant-wide ceiling, so one call can never push a tenant over it.
    tenant_spend_micro_usd: int = 0
    started_at: float = field(default_factory=time.monotonic)
    usage: S2SUsage = field(default_factory=S2SUsage)

    # -- measurement -----------------------------------------------------
    def record_input_audio(self, seconds: float) -> None:
        self.usage.input_audio_seconds += max(0.0, float(seconds))

    def record_output_audio(self, seconds: float) -> None:
        self.usage.output_audio_seconds += max(0.0, float(seconds))

    def record_audio_bytes(self, *, raw: bytes, output: bool, sample_rate: int) -> None:
        """Record audio from a raw byte count."""
        if sample_rate <= 0:
            return
        seconds = (len(raw) / 2.0) / sample_rate
        if output:
            self.record_output_audio(seconds)
        else:
            self.record_input_audio(seconds)

    def record_tokens(self, *, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.usage.input_tokens += max(0, int(input_tokens))
        self.usage.output_tokens += max(0, int(output_tokens))

    # -- ceilings --------------------------------------------------------
    def elapsed_seconds(self) -> float:
        return max(0.0, time.monotonic() - self.started_at)

    def session_limit_exceeded(self) -> bool:
        if self.max_session_seconds <= 0:
            return False
        return self.elapsed_seconds() > self.max_session_seconds

    def budget_exceeded(self) -> bool:
        if self.session_budget_micro_usd <= 0:
            return False
        # The cost is recomputed here so the check never reads a stale value.
        self.compute_cost()
        return self.usage.cost_micro_usd > self.session_budget_micro_usd

    def tenant_budget_exceeded(self, *, spent_micro_usd: int | None = None) -> bool:
        """True when the tenant-wide speech-to-speech ceiling is crossed.

        The tenant's own spend is added to this session's cost, so a call
        that starts just below the ceiling still cannot cross it.
        """
        if self.tenant_budget_micro_usd <= 0:
            return False
        self.compute_cost()
        spent = (
            self.tenant_spend_micro_usd
            if spent_micro_usd is None
            else max(0, int(spent_micro_usd))
        )
        return spent + self.usage.cost_micro_usd > self.tenant_budget_micro_usd

    def check_tenant_budget(self, *, spent_micro_usd: int | None = None) -> None:
        """Raise when the tenant-wide ceiling is crossed."""
        if self.tenant_budget_exceeded(spent_micro_usd=spent_micro_usd):
            raise S2SBudgetExceeded(
                "The tenant exceeded its speech-to-speech budget",
                provider=self.provider,
                category="budget_exceeded",
            )

    def check_limits(self) -> None:
        """Raise when a ceiling has been crossed."""
        if self.session_limit_exceeded():
            raise S2SBudgetExceeded(
                "The speech-to-speech session exceeded its maximum duration",
                provider=self.provider,
                category="timeout",
            )
        if self.budget_exceeded():
            raise S2SBudgetExceeded(
                "The speech-to-speech session exceeded its cost budget",
                provider=self.provider,
                category="budget_exceeded",
            )
        self.check_tenant_budget()

    # -- costing ---------------------------------------------------------
    def compute_cost(self) -> int:
        """Recompute the cost in micro-USD; zero when no price is configured."""
        cost = 0
        known = False
        if self.input_micro_usd_per_million > 0:
            cost += int(
                self.usage.input_tokens * self.input_micro_usd_per_million / 1_000_000
            )
            known = True
        if self.output_micro_usd_per_million > 0:
            cost += int(
                self.usage.output_tokens * self.output_micro_usd_per_million / 1_000_000
            )
            known = True
        if self.input_micro_usd_per_second > 0:
            cost += int(self.usage.input_audio_seconds * self.input_micro_usd_per_second)
            known = True
        if self.output_micro_usd_per_second > 0:
            cost += int(self.usage.output_audio_seconds * self.output_micro_usd_per_second)
            known = True
        self.usage.cost_micro_usd = cost
        self.usage.cost_known = known
        return cost

    def snapshot(self) -> S2SUsage:
        self.compute_cost()
        return self.usage


def build_cost_meter(
    *,
    provider: str = "",
    model: str = "",
    max_session_seconds: int | None = None,
    session_budget_micro_usd: int | None = None,
    tenant_budget_micro_usd: int | None = None,
) -> S2SCostMeter:
    """Build a meter from the Batch 02 settings."""
    from app.carriers_v2.settings import get_carrier_v2_settings

    carrier = get_carrier_v2_settings()
    return S2SCostMeter(
        provider=provider,
        model=model,
        max_session_seconds=(
            max_session_seconds
            if max_session_seconds is not None
            else carrier.s2s_max_session_seconds
        ),
        session_budget_micro_usd=(
            session_budget_micro_usd
            if session_budget_micro_usd is not None
            else carrier.s2s_session_budget_micro_usd
        ),
        tenant_budget_micro_usd=(
            tenant_budget_micro_usd
            if tenant_budget_micro_usd is not None
            else carrier.s2s_tenant_budget_micro_usd
        ),
    )


async def persist_token_usage(
    session,
    tenant,
    *,
    call_id,
    input_tokens: int,
    output_tokens: int,
    provider: str = "",
) -> bool:
    """Write the measured token usage into the existing usage ledger.

    The idempotency key is derived from the call and the discriminator
    ``s2s``, so a session that is finalised twice bills once.
    """
    from sqlalchemy import select

    from app.billing import metering
    from app.billing.periods import period_for
    from app.db.models import Subscription, UsageMetric

    total = max(0, int(input_tokens)) + max(0, int(output_tokens))
    if total <= 0:
        return False
    try:
        subscription = (
            await session.execute(
                select(Subscription).where(Subscription.tenant_id == tenant.id)
            )
        ).scalar_one_or_none()
        period = period_for(subscription)
        result = await metering.record_usage(
            session,
            tenant_id=tenant.id,
            metric=UsageMetric.LLM_TOKEN,
            quantity=total,
            idempotency_key=metering.usage_idempotency_key(
                UsageMetric.LLM_TOKEN, call_id, discriminator="s2s"
            ),
            period=period,
            source_entity_id=call_id,
            metadata={
                "runtime": "s2s",
                "provider": provider or "",
                "input_tokens": max(0, int(input_tokens)),
                "output_tokens": max(0, int(output_tokens)),
            },
        )
    except Exception as exc:
        log.warning("s2s.cost.usage_persist_failed", error=type(exc).__name__)
        return False
    return bool(getattr(result, "created", False))


async def tenant_spend_micro_usd(session, tenant_id) -> int:
    """The micro-USD this tenant has spent on speech-to-speech so far."""
    from sqlalchemy import func, select

    from app.db.carrier_s2s_models import S2SSessionRecord

    total = (
        await session.execute(
            select(func.coalesce(func.sum(S2SSessionRecord.cost_micro_usd), 0)).where(
                S2SSessionRecord.tenant_id == tenant_id
            )
        )
    ).scalar_one()
    return int(total or 0)


__all__ = [
    "S2SCostMeter",
    "S2SUsage",
    "build_cost_meter",
    "persist_token_usage",
    "tenant_spend_micro_usd",
]
