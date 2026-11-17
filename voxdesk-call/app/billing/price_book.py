"""Versioned per-component price book (PART 18, P18-05 / D-04 / R-91).

Why this exists when ``app/billing/plans.py`` already has prices
------------------------------------------------------------------------
``plans.py`` prices *plans*: a monthly fee, included units, one overage rate per
metric. That cannot produce an honest invoice for the product customers actually
buy, because a voice minute is not one cost — it is a telephony leg, an STT
stream, an LLM conversation and a TTS stream, each from a different vendor at a
different rate, and the LLM rate depends on the model. A single blended
"voice minute" price hides the only number a reseller needs: margin.

So this module prices **components**, and every invoice line names one:

======  =================  ==============================================
kind    component          priced by
======  =================  ==============================================
voice   ``voice_engine``   per call-minute (the media pipeline)
stt     ``stt``            per transcribed minute
llm     ``llm:<model>``    per 1,000 tokens, in and out priced separately
tts     ``tts``            per 1,000 characters
telephony ``telephony:<cc>`` per call-minute *by destination country*
addon   ``addon:<code>``   a flat monthly or per-unit charge
======  =================  ==============================================

Rules that are load-bearing:

* **Prices are versioned, never edited.** A published version is immutable
  (``PriceBook.frozen``); a change publishes a new version with a new
  ``effective_from``. An invoice always cites the version it used, so
  re-deriving last quarter's invoice produces last quarter's numbers.
* **A version is looked up by time, not by "latest".** ``for_moment`` picks the
  newest version whose ``effective_from`` has passed, which is what makes a
  mid-month price change billable on both sides of the change.
* **Integers, not floats, in the ledger.** Prices are millicents (1/100,000 of a
  dollar, the same unit ``app/billing/cost.py`` uses) and line amounts are
  computed in integer millicents then rounded once, at the line. Summing floats
  is how an invoice stops adding up.
* **A missing price is UNKNOWN, never zero.** ``PriceBook.price_for`` returns
  ``None``; callers must render that as "not priced" rather than free, because a
  free line is a silent revenue leak and an operator can only fix what they can
  see.

The book is data (dicts) rather than a database table on purpose: it ships with
the release, the release is signed, and a price change is a deploy — which is
exactly the audit trail a finance team wants.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping

MILLICENTS_PER_CENT = 1_000
MILLICENTS_PER_DOLLAR = 100_000

#: Component kinds, in invoice display order.
COMPONENT_KINDS = ("voice", "stt", "llm", "tts", "telephony", "addon")


class PriceBookError(Exception):
    """A price book that would produce a wrong invoice."""


# ---------------------------------------------------------------------------
# One component price
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComponentPrice:
    """A single priced component.

    ``unit`` is the natural billing unit: ``minute``, ``1k_tokens``,
    ``1k_chars``, ``month``, ``unit``. ``millicents`` is the *cost to us*;
    ``list_millicents`` is what we charge, and the two are separate fields
    because a price book that only stores one of them cannot answer the margin
    question — which is the whole point of the module.
    """

    kind: str
    component: str
    unit: str
    millicents: int
    list_millicents: int | None = None
    provider: str = ""
    #: Free-text note shown on the invoice, e.g. "inbound US", "gpt-4.1 output".
    label: str = ""
    #: 1000 tokens, 1000 characters: the divisor applied to the raw quantity.
    divisor: int = 1

    def __post_init__(self) -> None:
        if self.kind not in COMPONENT_KINDS:
            raise PriceBookError(f"unknown component kind {self.kind!r}")
        if self.millicents < 0:
            raise PriceBookError(f"{self.component}: a negative cost price is not a discount")
        if self.divisor <= 0:
            raise PriceBookError(f"{self.component}: divisor must be positive")

    @property
    def margin_millicents(self) -> int | None:
        if self.list_millicents is None:
            return None
        return self.list_millicents - self.millicents

    @property
    def margin_pct(self) -> float | None:
        if not self.list_millicents:
            return None
        return round((self.margin_millicents or 0) / self.list_millicents * 100, 2)

    def amount_millicents(self, quantity: float, *, list_price: bool = False) -> int:
        """Integer millicents for a raw quantity (seconds, tokens, characters…).

        Rounded half-up, once, at the line. ``quantity`` may be fractional (a
        42-second call is 0.7 minutes) but the result is an integer.
        """
        rate = self.list_millicents if (list_price and self.list_millicents is not None) else self.millicents
        exact = quantity * rate / self.divisor
        return int(exact + 0.5)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "component": self.component,
            "provider": self.provider,
            "unit": self.unit,
            "divisor": self.divisor,
            "millicents": self.millicents,
            "list_millicents": self.list_millicents,
            "label": self.label,
            "margin_millicents": self.margin_millicents,
            "margin_pct": self.margin_pct,
        }


# ---------------------------------------------------------------------------
# A version of the book
# ---------------------------------------------------------------------------

#: The shipped default, in millicents.
#:
#: **These are indicative, not contracted.** They are order-of-magnitude list
#: rates for the providers named, included so the product is usable on day one
#: and so the margin view has something to compute. A real deployment replaces
#: them with the rates in its signed provider agreements, which is why every
#: version carries an ``indicated`` flag and every invoice states whether it was
#: priced against unconfirmed numbers. An invoice that implies precision it
#: cannot support is worse than one that admits its inputs.
_DEFAULT_PRICES: tuple[ComponentPrice, ...] = (
    # --- media pipeline -----------------------------------------------------
    ComponentPrice("voice", "voice_engine", "minute", 120, 300, "voxdesk", "media pipeline", divisor=1),
    # STT is billed on transcribed audio; Deepgram nova at ~$0.0043/min.
    ComponentPrice("stt", "stt", "minute", 430, 800, "deepgram", "nova", divisor=1),
    # --- LLM, in and out priced separately because vendors do -------
    ComponentPrice("llm", "llm:gpt-4.1", "1k_tokens", 200, 450, "openai", "input", divisor=1000),
    ComponentPrice("llm", "llm:gpt-4.1:output", "1k_tokens", 800, 1500, "openai", "output", divisor=1000),
    ComponentPrice("llm", "llm:gpt-4.1-mini", "1k_tokens", 40, 110, "openai", "input", divisor=1000),
    ComponentPrice("llm", "llm:gpt-4.1-mini:output", "1k_tokens", 160, 340, "openai", "output", divisor=1000),
    ComponentPrice("llm", "llm:claude-sonnet-4", "1k_tokens", 300, 620, "anthropic", "input", divisor=1000),
    ComponentPrice("llm", "llm:claude-sonnet-4:output", "1k_tokens", 1500, 2400, "anthropic", "output", divisor=1000),
    # --- TTS ----------------------------------------------------------------
    # ElevenLabs-style pricing: ~$0.0001-0.0003 per character depending on tier.
    ComponentPrice("tts", "tts", "1k_chars", 8000, 18_000, "elevenlabs", "multilingual v2", divisor=1000),
    # --- telephony, by destination country ---------------------------------
    # Outbound termination. A single blended rate would lose money on every
    # call to a high-cost destination and overcharge every domestic one.
    ComponentPrice("telephony", "telephony:US", "minute", 1300, 2200, "twilio", "US/CA termination"),
    ComponentPrice("telephony", "telephony:GB", "minute", 1750, 2900, "twilio", "UK termination"),
    ComponentPrice("telephony", "telephony:DE", "minute", 2100, 3400, "twilio", "Germany termination"),
    ComponentPrice("telephony", "telephony:BD", "minute", 9000, 13_000, "twilio", "Bangladesh termination"),
    ComponentPrice("telephony", "telephony:IN", "minute", 2300, 3600, "twilio", "India termination"),
    ComponentPrice("telephony", "telephony:NG", "minute", 20_000, 26_000, "twilio", "Nigeria termination"),
    ComponentPrice("telephony", "telephony:ZZ", "minute", 5000, 7500, "twilio", "rest of world (fallback)"),
    # --- add-ons ------------------------------------------------------------
    ComponentPrice("addon", "addon:extra_concurrency", "unit", 5000, 9000, "voxdesk", "per concurrent channel per month"),
    ComponentPrice("addon", "addon:extra_phone_number", "unit", 100_000, 150_000, "twilio", "per number per month"),
    ComponentPrice("addon", "addon:recording_storage", "unit", 2500, 5000, "voxdesk", "per 100 GB-month"),
    ComponentPrice("addon", "addon:sso", "month", 0, 500_000, "voxdesk", "flat monthly"),
)


@dataclass(frozen=True)
class PriceBook:
    """One immutable version of the whole catalogue."""

    version: str
    effective_from: date
    prices: tuple[ComponentPrice, ...]
    currency: str = "usd"
    notes: str = ""
    #: True when the rates have NOT been confirmed against signed provider
    #: agreements. Every invoice states this so a finance team knows whether it
    #: is looking at contracted numbers or at a placeholder.
    indicated: bool = True
    extra: Mapping[str, Any] = field(default_factory=dict)

    # -- lookup ------------------------------------------------------------
    def _index(self) -> dict[str, ComponentPrice]:
        return {price.component: price for price in self.prices}

    def price_for(self, component: str) -> ComponentPrice | None:
        """The price for one component, or None when it is not in this version.

        None is a *finding*, not a default: an unpriced component must surface
        as "not priced" on the invoice, never as zero.
        """
        return self._index().get(component)

    def require(self, component: str) -> ComponentPrice:
        price = self.price_for(component)
        if price is None:
            raise PriceBookError(
                f"price book {self.version} has no price for {component!r}; "
                "publish a version that includes it rather than billing zero"
            )
        return price

    def telephony_for(self, country_code: str | None) -> ComponentPrice:
        """Destination rate, falling back to the documented rest-of-world rate."""
        code = (country_code or "").upper().lstrip("+") or "ZZ"
        return self.price_for(f"telephony:{code}") or self.require("telephony:ZZ")

    def llm_for(self, model: str, *, output: bool = False) -> ComponentPrice | None:
        key = f"llm:{model}" + (":output" if output else "")
        return self.price_for(key) or self.price_for(f"llm:{model}")

    def of_kind(self, kind: str) -> list[ComponentPrice]:
        return [p for p in self.prices if p.kind == kind]

    # -- integrity ---------------------------------------------------------
    def validate(self) -> list[str]:
        """Problems that would make an invoice wrong. Empty means good."""
        problems: list[str] = []
        seen: set[str] = set()
        for price in self.prices:
            if price.component in seen:
                problems.append(f"{price.component}: priced twice in one version")
            seen.add(price.component)
            if price.list_millicents is not None and price.list_millicents < price.millicents:
                problems.append(
                    f"{price.component}: sells below cost ({price.list_millicents} < {price.millicents})"
                )
        for kind in COMPONENT_KINDS:
            if kind != "addon" and not any(p.kind == kind for p in self.prices):
                problems.append(f"no {kind} price in this version")
        if "telephony:ZZ" not in seen:
            problems.append("no telephony fallback rate (telephony:ZZ)")
        return problems

    def digest(self) -> str:
        """Content hash, so an invoice can cite an exact book."""
        body = json.dumps(
            [
                {
                    "component": p.component,
                    "kind": p.kind,
                    "unit": p.unit,
                    "divisor": p.divisor,
                    "millicents": p.millicents,
                    "list_millicents": p.list_millicents,
                }
                for p in sorted(self.prices, key=lambda x: x.component)
            ],
            sort_keys=True,
        )
        return hashlib.sha256(f"{self.version}:{body}".encode()).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "effective_from": self.effective_from.isoformat(),
            "currency": self.currency,
            "notes": self.notes,
            "indicated": self.indicated,
            "digest": self.digest(),
            "prices": [p.as_dict() for p in self.prices],
        }


# ---------------------------------------------------------------------------
# The catalogue: an ordered set of versions
# ---------------------------------------------------------------------------


class PriceBookCatalogue:
    """Every published version, oldest first. Lookup is by *time*, never latest.

    A mid-month price change must bill the days before the change at the old
    rate. That is only possible if the lookup answers "which version was in
    force at this moment", so ``for_moment`` takes a datetime and every invoice
    line carries the moment it was priced.
    """

    def __init__(self, versions: Iterable[PriceBook]) -> None:
        ordered = sorted(versions, key=lambda book: (book.effective_from, book.version))
        if not ordered:
            raise PriceBookError("a catalogue needs at least one version")
        for book in ordered:
            problems = book.validate()
            if problems:
                raise PriceBookError(f"price book {book.version}: " + "; ".join(problems))
        for earlier, later in zip(ordered, ordered[1:]):
            if earlier.effective_from == later.effective_from:
                raise PriceBookError(
                    f"{earlier.version} and {later.version} take effect on the same day; "
                    "the lookup would be ambiguous"
                )
        self._versions = tuple(ordered)

    # -- introspection -----------------------------------------------------
    @property
    def versions(self) -> tuple[PriceBook, ...]:
        return self._versions

    def latest(self) -> PriceBook:
        return self._versions[-1]

    def get(self, version: str) -> PriceBook:
        for book in self._versions:
            if book.version == version:
                return book
        raise PriceBookError(f"unknown price book version {version!r}")

    def for_moment(self, moment: datetime | date | None = None) -> PriceBook:
        """The version in force at ``moment`` (default: now)."""
        if moment is None:
            day = datetime.now(timezone.utc).date()
        elif isinstance(moment, datetime):
            day = moment.astimezone(timezone.utc).date() if moment.tzinfo else moment.date()
        else:
            day = moment
        in_force = [book for book in self._versions if book.effective_from <= day]
        if not in_force:
            raise PriceBookError(
                f"no price book was in force on {day.isoformat()}; the earliest version "
                f"starts {self._versions[0].effective_from.isoformat()}"
            )
        return in_force[-1]

    # -- publishing --------------------------------------------------------
    def publish(
        self,
        *,
        version: str,
        effective_from: date,
        prices: Iterable[ComponentPrice],
        currency: str = "usd",
        notes: str = "",
        indicated: bool = True,
    ) -> "PriceBookCatalogue":
        """Return a new catalogue with one more version. Never mutates ``self``.

        ``indicated=False`` is the claim "these are our contracted rates". Set it
        only when that is true: it is printed on every invoice the version
        prices.
        """
        new_book = PriceBook(
            version=version,
            effective_from=effective_from,
            prices=tuple(prices),
            currency=currency,
            notes=notes,
            indicated=indicated,
        )
        if any(book.version == version for book in self._versions):
            raise PriceBookError(f"version {version!r} already exists; versions are immutable")
        return PriceBookCatalogue((*self._versions, new_book))

    def with_change(
        self,
        *,
        version: str,
        effective_from: date,
        changes: Mapping[str, Mapping[str, Any]],
        notes: str = "",
    ) -> "PriceBookCatalogue":
        """Publish a version that differs from the latest only where stated.

        This is how a price actually changes in practice — one rate moves, not
        the whole table — so the API makes that the cheap operation and the full
        rewrite the expensive one.
        """
        base = self.latest()
        by_name = {price.component: price for price in base.prices}
        for component, patch in changes.items():
            current = by_name.get(component)
            if current is None:
                raise PriceBookError(f"{component!r} is not in {base.version}; add it with publish()")
            merged = {
                "kind": current.kind,
                "component": current.component,
                "unit": current.unit,
                "millicents": current.millicents,
                "list_millicents": current.list_millicents,
                "provider": current.provider,
                "label": current.label,
                "divisor": current.divisor,
                **patch,
            }
            by_name[component] = ComponentPrice(**merged)
        return self.publish(
            version=version,
            effective_from=effective_from,
            prices=by_name.values(),
            currency=base.currency,
            notes=notes or f"change from {base.version}",
            indicated=base.indicated,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "versions": [book.as_dict() for book in self._versions],
            "latest": self.latest().version,
        }


def default_catalogue() -> PriceBookCatalogue:
    """The shipped book: one version, in force from 2026-01-01."""
    return PriceBookCatalogue(
        (
            PriceBook(
                version="2026-01",
                effective_from=date(2026, 1, 1),
                prices=_DEFAULT_PRICES,
                notes="Shipped with the product: indicative list rates, not contracted.",
                indicated=True,
            ),
        )
    )


# ---------------------------------------------------------------------------
# Itemised invoice lines
# ---------------------------------------------------------------------------

#: The unit a raw usage quantity is expressed in, per pricing unit.
_UNIT_TO_RAW = {
    "minute": "seconds",
    "1k_tokens": "tokens",
    "1k_chars": "characters",
    "month": "count",
    "unit": "count",
}


@dataclass(frozen=True)
class UsageComponent:
    """One measured fact, ready to be priced.

    ``quantity`` is in the component's **raw** unit — minutes for a per-minute
    price, tokens for a per-1,000-token price, characters for text. The price's
    ``divisor`` is what converts raw units into billed units, exactly once, so
    that a rate quoted as "per 1,000 tokens" cannot be divided by 1,000 twice by
    two different callers.

    It is a float because 42 seconds is 0.7 minutes, and rounding before pricing
    is how an invoice loses a cent per call.
    """

    component: str
    quantity: float
    source: str = ""
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InvoiceLine:
    """One priced row. Integer millicents, traceable to a component and a book."""

    component: str
    kind: str
    label: str
    quantity: float
    unit: str
    unit_cost_millicents: int
    unit_list_millicents: int | None
    cost_millicents: int
    charge_millicents: int
    price_book_version: str
    sources: tuple[str, ...] = ()

    @property
    def margin_millicents(self) -> int:
        return self.charge_millicents - self.cost_millicents

    @property
    def margin_pct(self) -> float | None:
        if not self.charge_millicents:
            return None
        return round(self.margin_millicents / self.charge_millicents * 100, 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "kind": self.kind,
            "label": self.label,
            "quantity": round(self.quantity, 4),
            "unit": self.unit,
            "unit_cost_millicents": self.unit_cost_millicents,
            "unit_list_millicents": self.unit_list_millicents,
            "cost_millicents": self.cost_millicents,
            "charge_millicents": self.charge_millicents,
            "margin_millicents": self.margin_millicents,
            "margin_pct": self.margin_pct,
            "price_book_version": self.price_book_version,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class Invoice:
    """A priced period: itemised lines plus totals that equal their sum."""

    invoice_id: str
    tenant_id: str
    period_start: date
    period_end: date
    price_book_version: str
    price_book_digest: str
    #: Whether the book this invoice used carried confirmed rates.
    prices_indicated: bool
    currency: str
    lines: tuple[InvoiceLine, ...]
    unpriced: tuple[str, ...] = ()
    proration: "Proration | None" = None

    @property
    def cost_millicents(self) -> int:
        return sum(line.cost_millicents for line in self.lines)

    @property
    def charge_millicents(self) -> int:
        return sum(line.charge_millicents for line in self.lines)

    @property
    def margin_millicents(self) -> int:
        return self.charge_millicents - self.cost_millicents

    @property
    def margin_pct(self) -> float | None:
        if not self.charge_millicents:
            return None
        return round(self.margin_millicents / self.charge_millicents * 100, 2)

    def totals(self) -> dict[str, Any]:
        """The numbers printed at the bottom, derived — never stored twice."""
        subscription = self.proration.charge_millicents if self.proration else 0
        subscription_cost = self.proration.cost_millicents if self.proration else 0
        charge = self.charge_millicents + subscription
        cost = self.cost_millicents + subscription_cost
        return {
            "lines": len(self.lines),
            "usage_cost_millicents": self.cost_millicents,
            "usage_charge_millicents": self.charge_millicents,
            "subscription_charge_millicents": subscription,
            "subscription_cost_millicents": subscription_cost,
            "total_cost_millicents": cost,
            "total_charge_millicents": charge,
            "margin_millicents": charge - cost,
            "margin_pct": round((charge - cost) / charge * 100, 2) if charge else None,
            # A zero-cost subscription would read as 100% margin. Say when the
            # cost side is incomplete instead of printing a flattering number.
            "cost_complete": not (self.proration and not self.proration.cost_millicents),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "tenant_id": self.tenant_id,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "price_book_version": self.price_book_version,
            "price_book_digest": self.price_book_digest,
            "prices_indicated": self.prices_indicated,
            "currency": self.currency,
            "lines": [line.as_dict() for line in self.lines],
            "unpriced": list(self.unpriced),
            "proration": self.proration.as_dict() if self.proration else None,
            "totals": self.totals(),
        }


@dataclass(frozen=True)
class Proration:
    """A mid-cycle plan change, split at the change.

    Concurrency (the number of simultaneous channels) is the thing customers
    actually change mid-month, so it is prorated by *day*, not by month: 10 days
    at 2 channels and 20 days at 4 channels is 10*2 + 20*4 = 100 channel-days,
    and the monthly rate is charged on that fraction. Charging a whole extra
    month for a change on the 21st is the single most common billing complaint
    in this category, which is why it is modelled explicitly rather than
    approximated.
    """

    period_start: date
    period_end: date
    segments: tuple[Mapping[str, Any], ...]
    monthly_cost_millicents_per_channel: int = 0

    @property
    def days(self) -> int:
        return max((self.period_end - self.period_start).days, 1)

    @property
    def weighted_channel_days(self) -> float:
        return sum(float(s["days"]) * float(s["channels"]) for s in self.segments)

    @property
    def equivalent_channels(self) -> float:
        return self.weighted_channel_days / self.days

    @property
    def plan_millicents(self) -> int:
        return int(sum(int(s["monthly_millicents"]) * float(s["days"]) / self.days for s in self.segments) + 0.5)

    @property
    def charge_millicents(self) -> int:
        return self.plan_millicents

    @property
    def cost_millicents(self) -> int:
        """What the same prorated channels cost us.

        Zero when no cost per channel is configured — which reads as 100% margin,
        so ``Invoice.totals`` treats a zero-cost proration as "unknown" and says
        so rather than printing a number it cannot support.
        """
        if not self.monthly_cost_millicents_per_channel:
            return 0
        days = self.days
        return int(
            sum(
                float(s["channels"]) * self.monthly_cost_millicents_per_channel * float(s["days"]) / days
                for s in self.segments
            )
            + 0.5
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "days": self.days,
            "segments": [dict(s) for s in self.segments],
            "weighted_channel_days": round(self.weighted_channel_days, 2),
            "equivalent_channels": round(self.equivalent_channels, 3),
            "plan_millicents": self.plan_millicents,
            "plan_cost_millicents": self.cost_millicents,
        }


def prorate_concurrency(
    *,
    period_start: date,
    period_end: date,
    changes: Iterable[Mapping[str, Any]],
    monthly_millicents_per_channel: int,
) -> Proration:
    """Split a period at each concurrency change and price the segments.

    ``changes`` is an iterable of ``{"effective": date, "channels": int}``, at
    least one of which takes effect on ``period_start``.
    """
    ordered = sorted(changes, key=lambda c: c["effective"])
    if not ordered:
        raise PriceBookError("proration needs at least one segment")
    if ordered[0]["effective"] > period_start:
        raise PriceBookError(
            f"the first segment starts {ordered[0]['effective']} but the period starts "
            f"{period_start}; the days before it would be unpriced"
        )
    segments: list[dict[str, Any]] = []
    total_days = max((period_end - period_start).days, 1)
    for index, change in enumerate(ordered):
        start = period_start if index == 0 else max(change["effective"], period_start)
        end = period_end if index == len(ordered) - 1 else ordered[index + 1]["effective"]
        days = max((end - start).days, 0)
        if days == 0:
            continue
        channels = int(change["channels"])
        segments.append(
            {
                "from": start.isoformat(),
                "to": end.isoformat(),
                "days": days,
                "channels": channels,
                "monthly_millicents": channels * monthly_millicents_per_channel,
            }
        )
    covered = sum(s["days"] for s in segments)
    if covered != total_days:
        raise PriceBookError(
            f"segments cover {covered} of {total_days} days; a gap bills nothing"
        )
    return Proration(period_start=period_start, period_end=period_end, segments=tuple(segments))


def price_usage(
    *,
    book: PriceBook,
    components: Iterable[UsageComponent],
    tenant_id: str,
    invoice_id: str,
    period_start: date,
    period_end: date,
    proration: Proration | None = None,
) -> Invoice:
    """Price a period's usage into itemised lines.

    Components are merged by name first, so ten thousand calls produce a handful
    of lines rather than ten thousand. Merging happens *before* pricing, which
    keeps the rounding to one place.
    """
    merged: dict[str, dict[str, Any]] = {}
    for item in components:
        bucket = merged.setdefault(
            item.component, {"quantity": 0.0, "sources": set()}
        )
        bucket["quantity"] += float(item.quantity)
        if item.source:
            bucket["sources"].add(item.source)

    lines: list[InvoiceLine] = []
    unpriced: list[str] = []
    for component in sorted(merged):
        price = book.price_for(component)
        bucket = merged[component]
        if price is None:
            unpriced.append(component)
            continue
        quantity = bucket["quantity"]
        lines.append(
            InvoiceLine(
                component=component,
                kind=price.kind,
                label=price.label or component,
                quantity=quantity,
                unit=price.unit,
                unit_cost_millicents=price.millicents,
                unit_list_millicents=price.list_millicents,
                cost_millicents=price.amount_millicents(quantity),
                charge_millicents=price.amount_millicents(quantity, list_price=True),
                price_book_version=book.version,
                sources=tuple(sorted(bucket["sources"])),
            )
        )

    kind_order = {kind: index for index, kind in enumerate(COMPONENT_KINDS)}
    lines.sort(key=lambda line: (kind_order.get(line.kind, 99), line.component))

    return Invoice(
        invoice_id=invoice_id,
        tenant_id=tenant_id,
        period_start=period_start,
        period_end=period_end,
        price_book_version=book.version,
        price_book_digest=book.digest(),
        prices_indicated=book.indicated,
        currency=book.currency,
        lines=tuple(lines),
        unpriced=tuple(unpriced),
        proration=proration,
    )


def usage_from_events(rows: Iterable[Mapping[str, Any]]) -> list[UsageComponent]:
    """Turn metered usage rows into priceable components.

    ``rows`` are dicts with ``metric``, ``quantity`` (the metric's smallest
    unit), ``provider``, ``model``, ``country`` and ``source``. The mapping is
    the only place the metric vocabulary meets the price-book vocabulary, and it
    is stated once so it cannot drift:

    ==============  ==============================  ==============================
    metric          component                       quantity (raw)
    ==============  ==============================  ==============================
    voice_minute    ``voice_engine``                seconds / 60 → minutes
    voice_minute    ``telephony:<CC>``              seconds / 60 (when country known)
    stt_minute      ``stt``                         seconds / 60 → minutes
    llm_token       ``llm:<model>[:output]``        tokens (divisor 1,000)
    tts_character   ``tts``                         characters (divisor 1,000)
    ==============  ==============================  ==============================
    """
    out: list[UsageComponent] = []
    for row in rows:
        metric = str(row.get("metric") or "")
        quantity = float(row.get("quantity") or 0)
        source = str(row.get("source") or "")
        if quantity == 0:
            continue
        if metric == "voice_minute":
            minutes = quantity / 60.0
            out.append(UsageComponent("voice_engine", minutes, source))
            country = row.get("country")
            if country:
                out.append(UsageComponent(f"telephony:{str(country).upper()}", minutes, source))
        elif metric == "stt_minute":
            out.append(UsageComponent("stt", quantity / 60.0, source))
        elif metric == "llm_token":
            model = str(row.get("model") or "gpt-4.1-mini")
            direction = ":output" if row.get("direction") == "output" else ""
            # Raw tokens: the per-1,000 divisor lives on the price, once.
            out.append(UsageComponent(f"llm:{model}{direction}", quantity, source))
        elif metric == "tts_character":
            out.append(UsageComponent("tts", quantity, source))
        else:
            # An unknown metric is surfaced by price_usage as unpriced rather
            # than silently dropped — the caller sees it in `invoice.unpriced`.
            out.append(UsageComponent(metric, quantity, source))
    return out
