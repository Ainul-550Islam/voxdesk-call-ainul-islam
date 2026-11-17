"""Deterministic provider-cost accounting for operators (Step 7).

This is **operational analytics**, not billing. It answers "what did this
call / this tenant's usage cost *us* in provider fees", using an
operator-configured price table. It never touches customer invoices, plan
overage, Stripe, or any metering row — those stay the property of
``app.billing.metering`` / ``app.billing.plans``.

The honesty rules, stated once:

* A price comes only from ``COST_UNIT_PRICES`` (settings). A missing or zero
  price means **UNKNOWN**; the volume is still counted, the dollars are not,
  and nothing is ever guessed.
* Revenue (what tenants pay) is a different number owned by the plan
  catalogue. Cost and revenue are reported separately; this module never
  conflates them.
* Consumption is never double-counted: cost is recorded exactly where the
  corresponding usage event is written (voice minutes and SMS in
  ``billing.hooks``), and for LLM/TTS exactly where the bytes/tokens are
  measured (the pipeline usage tracker). Each site keys on the same
  idempotency boundary as the usage itself.

Prices are expressed in **millicents per unit** (1/100,000 of a dollar) so a
$0.013/minute Twilio rate is ``1300`` and a $0.00015/token LLM rate is ``15``
per 1,000 tokens.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.billing import price_book as price_book_module
from app.core import observability
from app.core.config import settings
from app.core.logging import log

#: The fixed key vocabulary understood in ``COST_UNIT_PRICES``. Anything else
#: is ignored, so a typo degrades to UNKNOWN rather than silently pricing.
_PRICE_KEYS = frozenset({
    "voice_minute",        # millicents per minute (Twilio)
    "sms_segment",         # millicents per SMS segment
    "llm_1k_tokens",       # millicents per 1,000 tokens (any provider)
    "tts_1k_chars",        # millicents per 1,000 characters (ElevenLabs)
})

#: LLM providers that may carry a provider-specific price key suffix.
_LLM_PROVIDERS = frozenset({"openai", "anthropic", "google"})


@dataclass(frozen=True)
class CostLine:
    """The deterministic cost of some measured consumption."""

    resource: str
    units: float
    price_millicents: float | None     # per natural unit, or None if UNKNOWN
    known: bool

    @property
    def cost_usd(self) -> float | None:
        """Dollars, or None when the price is UNKNOWN."""
        if not self.known or self.price_millicents is None:
            return None
        return round(self.units * self.price_millicents / 100_000.0, 6)


def _prices() -> dict[str, int]:
    return settings.cost_unit_prices


def price_millicents(resource: str, *, provider: str | None = None) -> float | None:
    """The configured price for one *natural* unit, in millicents, or None.

    ``resource`` is one of ``voice_minute``, ``sms_segment``, ``llm_token``,
    ``tts_character`` (the counter vocabulary). LLM/TTS prices are configured
    per 1,000 units and divided down here so every caller works in natural
    units.
    """
    prices = _prices()
    if resource == "voice_minute":
        raw = prices.get("voice_minute")
        return float(raw) if raw and raw > 0 else None
    if resource == "sms_segment":
        raw = prices.get("sms_segment")
        return float(raw) if raw and raw > 0 else None
    if resource == "llm_token":
        key = f"llm_1k_tokens:{provider}" if provider in _LLM_PROVIDERS else None
        raw = prices.get(key) if key else None
        if raw is None or raw <= 0:
            raw = prices.get("llm_1k_tokens")
        return (float(raw) / 1000.0) if raw and raw > 0 else None
    if resource == "tts_character":
        raw = prices.get("tts_1k_chars")
        return (float(raw) / 1000.0) if raw and raw > 0 else None
    return None


def cost_line(resource: str, units: float, *, provider: str | None = None) -> CostLine:
    """Compute a deterministic cost line from measured units."""
    price = price_millicents(resource, provider=provider)
    return CostLine(
        resource=resource,
        units=float(units),
        price_millicents=price,
        known=price is not None,
    )


def record_cost(resource: str, units: float, *, provider: str | None = None) -> CostLine:
    """Emit a cost counter update and return the line for logging.

    Idempotency is the *caller's* concern (this is a pure metric emit); call
    it only at the same place the usage event is written so it cannot run
    twice for one fact.
    """
    line = cost_line(resource, units, provider=provider)
    observability.record_cost(
        resource, line.units, unit_price_millicents=line.price_millicents
    )
    if not line.known:
        log.debug(
            "cost.unknown", resource=resource, units=round(line.units, 2),
            provider=provider,
        )
    return line


# ------------------------------------------------- per-call reconstruction ---

async def call_cost_breakdown(session, call) -> dict[str, Any]:
    """Reconstruct one call's provider cost from its usage events.

    Reads only; never writes, never estimates. Voice minutes come from the
    idempotent usage event written at finalization; LLM/TTS consumption is not
    stored as usage events (it is operational-only), so it is reported as
    ``unavailable`` here and is visible in the call's log lines instead.
    Returns a plain dict safe to log and safe to show an operator.
    """
    from sqlalchemy import select

    from app.db.models import UsageEvent, UsageMetric

    rows = (
        await session.execute(
            select(UsageEvent).where(UsageEvent.source_entity_id == call.id)
        )
    ).scalars().all()

    voice_seconds = 0
    for row in rows:
        if row.metric is UsageMetric.VOICE_MINUTE:
            voice_seconds += int(row.quantity or 0)

    minutes = voice_seconds / 60.0
    line = cost_line("voice_minute", minutes)
    return {
        "call_id": str(call.id),
        "voice_minutes": round(minutes, 2),
        "voice_cost_usd": line.cost_usd,
        "voice_cost_known": line.known,
        "llm_tokens": "unavailable",   # operational metric, not a usage event
        "tts_characters": "unavailable",
    }


# ------------------------------------------- itemised, by component (P18-05) ---


def _event_rows(usage_events, *, provider_hint: str | None = None) -> list[dict[str, Any]]:
    """Shape usage rows into what ``price_book.usage_from_events`` expects.

    The metering rows carry the metric and the quantity in the metric's smallest
    unit; the country and model live in ``event_metadata`` because they are
    context, not billing authority. This adapter is deliberately the only place
    that knows both vocabularies.
    """
    rows: list[dict[str, Any]] = []
    for event in usage_events:
        metric = getattr(event.metric, "value", str(event.metric))
        meta = dict(getattr(event, "event_metadata", {}) or {})
        row: dict[str, Any] = {
            "metric": metric,
            "quantity": int(getattr(event, "quantity", 0) or 0),
            "source": str(getattr(event, "source_reference", "") or ""),
        }
        if metric == "voice_minute":
            row["country"] = meta.get("country") or meta.get("to_country")
        if metric == "llm_token":
            row["model"] = meta.get("model") or provider_hint
            row["direction"] = meta.get("direction") or "input"
        if "stt" in metric or metric == "stt_minute":
            row["metric"] = "stt_minute"
        rows.append(row)
    return rows


async def itemised_usage_cost(
    session,
    *,
    tenant_id,
    period,
    price_book=None,
) -> dict[str, Any]:
    """Usage for a period, priced per component by the price book.

    This is the number the operator wants and the plan overage rate cannot
    produce: voice minutes split into media, STT and telephony; LLM tokens split
    by model and direction; TTS by characters. Each line carries both cost and
    charge, so the margin falls out of the same rows.
    """
    from sqlalchemy import select

    from app.db.models import UsageEvent

    events = (
        await session.execute(
            select(UsageEvent).where(
                UsageEvent.tenant_id == tenant_id,
                UsageEvent.billing_period == period.label,
            )
        )
    ).scalars().all()

    book = price_book or price_book_module.default_catalogue().for_moment(period.start)
    components = price_book_module.usage_from_events(_event_rows(events))
    invoice = price_book_module.price_usage(
        book=book,
        components=components,
        tenant_id=str(tenant_id),
        invoice_id=f"usage-{period.label}",
        period_start=period.start,
        period_end=period.end,
    )
    return invoice.as_dict()


def usage_by_provider_and_day(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pivot usage into provider totals and a day-by-day series.

    Both views come from the same rows, so the totals always equal the sum of
    the series — the usual failure of a "usage by day" screen is that it is
    computed by a second query with a different filter.
    """
    by_provider: dict[str, dict[str, float]] = {}
    by_day: dict[str, dict[str, float]] = {}
    for row in rows:
        provider = str(row.get("provider") or "unknown")
        day = str(row.get("day") or "")
        metric = str(row.get("metric") or "")
        quantity = float(row.get("quantity") or 0)
        entry = by_provider.setdefault(provider, {})
        entry[metric] = entry.get(metric, 0.0) + quantity
        if day:
            bucket = by_day.setdefault(day, {})
            bucket[metric] = bucket.get(metric, 0.0) + quantity
    return {
        "by_provider": {
            provider: {metric: round(value, 3) for metric, value in sorted(metrics.items())}
            for provider, metrics in sorted(by_provider.items())
        },
        "by_day": {
            day: {metric: round(value, 3) for metric, value in sorted(metrics.items())}
            for day, metrics in sorted(by_day.items())
        },
    }
