"""Price book and itemised invoices (PART 18, P18-05 / D-04 / R-91).

Four properties are being defended here, and none of them is "the happy path
renders":

1. **Lines sum to the total.** Every total on an invoice is derived by adding
   its own lines, so a rounding bug shows up as a mismatch rather than as a
   plausible-looking number.
2. **Versions are immutable and time-addressed.** A mid-month price change
   prices the days before it at the old rate; the old version still answers for
   itself afterwards.
3. **Proration is by day, not by month.** A concurrency change on the 21st
   charges 10/30 of the month, not a whole second month.
4. **A missing price is never zero.** An unpriced component is listed as
   unpriced, because a silent zero is a revenue leak nobody notices for a
   quarter.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.billing import price_book as pb

JAN = date(2026, 1, 1)
FEB = date(2026, 2, 1)


@pytest.fixture()
def catalogue() -> pb.PriceBookCatalogue:
    return pb.default_catalogue()


@pytest.fixture()
def book(catalogue: pb.PriceBookCatalogue) -> pb.PriceBook:
    return catalogue.for_moment(datetime(2026, 6, 15, tzinfo=timezone.utc))


# ---------------------------------------------------------------------------
# The shipped book
# ---------------------------------------------------------------------------


def test_the_shipped_book_is_internally_consistent(book: pb.PriceBook):
    assert book.validate() == []
    # Every non-add-on kind is priced, and telephony has a fallback, so no
    # destination can ever be silently free.
    for kind in ("voice", "stt", "llm", "tts", "telephony"):
        assert book.of_kind(kind), f"no {kind} price shipped"
    assert book.price_for("telephony:ZZ") is not None


def test_the_shipped_prices_are_marked_indicated(book: pb.PriceBook):
    """Rates that have not been confirmed against signed agreements say so.

    An invoice that implies contract precision would be a lie in a deal room.
    """
    assert book.indicated is True
    invoice = pb.price_usage(
        book=book,
        components=[pb.UsageComponent("voice_engine", 1.0)],
        tenant_id="t",
        invoice_id="i",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 7, 1),
    )
    assert invoice.as_dict()["prices_indicated"] is True


def test_no_component_sells_below_cost(book: pb.PriceBook):
    for price in book.prices:
        if price.list_millicents is not None:
            assert price.list_millicents >= price.millicents, price.component


def test_a_version_that_sells_below_cost_is_refused():
    with pytest.raises(pb.PriceBookError):
        pb.PriceBookCatalogue(
            (
                pb.PriceBook(
                    version="bad",
                    effective_from=JAN,
                    prices=(
                        *pb.default_catalogue().latest().prices,
                        pb.ComponentPrice("voice", "voice_engine", "minute", 500, 400),
                    ),
                ),
            )
        )


# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------


def test_lookup_is_by_time_not_by_latest(catalogue: pb.PriceBookCatalogue):
    updated = catalogue.with_change(
        version="2026-02",
        effective_from=FEB,
        changes={"telephony:US": {"millicents": 1500, "list_millicents": 2400}},
    )
    assert updated.for_moment(date(2026, 1, 15)).version == "2026-01"
    assert updated.for_moment(date(2026, 2, 1)).version == "2026-02"
    # The earlier version still answers for itself: an old invoice re-derives
    # to the numbers it was issued with.
    assert updated.get("2026-01").price_for("telephony:US").millicents == 1300
    assert updated.get("2026-02").price_for("telephony:US").millicents == 1500


def test_a_mid_month_change_bills_both_sides(catalogue: pb.PriceBookCatalogue):
    updated = catalogue.with_change(
        version="2026-02",
        effective_from=date(2026, 1, 15),
        changes={"telephony:US": {"millicents": 1500, "list_millicents": 2400}},
    )
    before = updated.for_moment(date(2026, 1, 10)).price_for("telephony:US")
    after = updated.for_moment(date(2026, 1, 20)).price_for("telephony:US")
    assert (before.millicents, after.millicents) == (1300, 1500)


def test_versions_cannot_be_reused(catalogue: pb.PriceBookCatalogue):
    with pytest.raises(pb.PriceBookError):
        catalogue.publish(
            version="2026-01",
            effective_from=FEB,
            prices=catalogue.latest().prices,
        )


def test_publishing_never_mutates_the_existing_catalogue(catalogue: pb.PriceBookCatalogue):
    before = [book.version for book in catalogue.versions]
    catalogue.with_change(version="2026-03", effective_from=FEB, changes={})
    assert [book.version for book in catalogue.versions] == before


def test_two_versions_on_the_same_day_are_refused(catalogue: pb.PriceBookCatalogue):
    with pytest.raises(pb.PriceBookError):
        catalogue.publish(version="same-day", effective_from=JAN, prices=catalogue.latest().prices)


def test_a_version_that_drops_a_component_is_refused(catalogue: pb.PriceBookCatalogue):
    kept = [p for p in catalogue.latest().prices if p.component != "telephony:ZZ"]
    with pytest.raises(pb.PriceBookError) as exc:
        catalogue.publish(version="no-fallback", effective_from=FEB, prices=kept)
    assert "fallback" in str(exc.value)


def test_a_moment_before_the_first_version_is_refused(catalogue: pb.PriceBookCatalogue):
    with pytest.raises(pb.PriceBookError):
        catalogue.for_moment(date(2025, 6, 1))


# ---------------------------------------------------------------------------
# Pricing one line
# ---------------------------------------------------------------------------


def test_llm_tokens_are_divided_by_one_thousand_exactly_once(book: pb.PriceBook):
    price = book.require("llm:gpt-4.1-mini")
    # 45,000 tokens at 110 millicents per 1,000 = 4,950 millicents = 4.95 cents.
    assert price.amount_millicents(45_000, list_price=True) == 4_950
    assert price.amount_millicents(45_000) == 1_800


def test_a_partial_minute_rounds_once_at_the_line(book: pb.PriceBook):
    price = book.require("telephony:US")
    # 42 seconds = 0.7 minutes at 2,200 millicents/min = 1,540 millicents.
    assert price.amount_millicents(0.7, list_price=True) == 1_540


def test_an_unknown_destination_uses_the_documented_fallback(book: pb.PriceBook):
    assert book.telephony_for("XV").component == "telephony:ZZ"
    assert book.telephony_for(None).component == "telephony:ZZ"
    assert book.telephony_for("+bd").component == "telephony:BD"


# ---------------------------------------------------------------------------
# The invoice
# ---------------------------------------------------------------------------

_ROWS = [
    {"metric": "voice_minute", "quantity": 600, "country": "US", "source": "call_1"},
    {"metric": "stt_minute", "quantity": 600, "source": "call_1"},
    {"metric": "llm_token", "quantity": 45_000, "model": "gpt-4.1-mini", "source": "call_1"},
    {
        "metric": "llm_token",
        "quantity": 12_000,
        "model": "gpt-4.1-mini",
        "direction": "output",
        "source": "call_1",
    },
    {"metric": "tts_character", "quantity": 9_000, "source": "call_1"},
]


def _invoice(book: pb.PriceBook, rows=None, **kwargs) -> pb.Invoice:
    return pb.price_usage(
        book=book,
        components=pb.usage_from_events(rows if rows is not None else _ROWS),
        tenant_id="tenant-1",
        invoice_id="inv-1",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 7, 1),
        **kwargs,
    )


def test_lines_sum_to_the_total(book: pb.PriceBook):
    invoice = _invoice(book)
    totals = invoice.totals()
    assert totals["usage_cost_millicents"] == sum(line.cost_millicents for line in invoice.lines)
    assert totals["usage_charge_millicents"] == sum(
        line.charge_millicents for line in invoice.lines
    )
    assert totals["total_charge_millicents"] == totals["usage_charge_millicents"]
    assert totals["margin_millicents"] == (
        totals["total_charge_millicents"] - totals["total_cost_millicents"]
    )


def test_one_call_produces_one_line_per_component_not_per_call(book: pb.PriceBook):
    many = [
        {"metric": "voice_minute", "quantity": 60, "country": "US", "source": f"call_{i}"}
        for i in range(50)
    ]
    invoice = _invoice(book, many)
    assert {line.component for line in invoice.lines} == {
        "voice_engine",
        "telephony:US",
    }
    voice = next(line for line in invoice.lines if line.component == "voice_engine")
    assert voice.quantity == pytest.approx(50.0)
    assert len(voice.sources) == 50


def test_every_line_cites_the_price_book_version(book: pb.PriceBook):
    invoice = _invoice(book)
    assert all(line.price_book_version == book.version for line in invoice.lines)
    assert invoice.as_dict()["price_book_digest"] == book.digest()


def test_an_unpriced_component_is_named_not_zeroed(book: pb.PriceBook):
    invoice = _invoice(book, [{"metric": "quantum_token", "quantity": 10, "source": "x"}])
    assert invoice.unpriced == ("quantum_token",)
    assert all(line.component != "quantum_token" for line in invoice.lines)
    # And it does not quietly contribute to the total.
    assert invoice.totals()["usage_charge_millicents"] == 0


def test_margin_is_computed_from_cost_and_charge(book: pb.PriceBook):
    invoice = _invoice(book)
    for line in invoice.lines:
        assert line.margin_millicents == line.charge_millicents - line.cost_millicents
    assert invoice.margin_pct is not None
    assert 0 < invoice.margin_pct < 100


def test_the_invoice_is_json_serialisable_for_a_pdf_pipeline(book: pb.PriceBook):
    import json

    payload = _invoice(book).as_dict()
    json.dumps(payload)
    assert set(payload["totals"]) >= {
        "usage_cost_millicents",
        "usage_charge_millicents",
        "total_charge_millicents",
        "margin_pct",
    }


def test_tenant_id_is_carried_and_never_derived_from_input_rows(book: pb.PriceBook):
    invoice = _invoice(book)
    assert invoice.tenant_id == "tenant-1"
    assert all("tenant" not in line.component for line in invoice.lines)


# ---------------------------------------------------------------------------
# Proration
# ---------------------------------------------------------------------------


def test_concurrency_is_prorated_by_day(catalogue: pb.PriceBookCatalogue):
    proration = pb.prorate_concurrency(
        period_start=date(2026, 6, 1),
        period_end=date(2026, 7, 1),
        changes=[
            {"effective": date(2026, 6, 1), "channels": 2},
            {"effective": date(2026, 6, 21), "channels": 4},
        ],
        monthly_millicents_per_channel=9000,
    )
    # 30-day month: 20 days at 2 channels + 10 days at 4.
    assert proration.days == 30
    assert proration.weighted_channel_days == 80
    assert proration.equivalent_channels == pytest.approx(80 / 30, rel=1e-3)
    # 20/30 * 2 * 9000 + 10/30 * 4 * 9000 = 12,000 + 12,000 = 24,000
    assert proration.plan_millicents == 24_000


def test_an_upgrade_on_the_last_day_does_not_bill_a_second_month(catalogue):
    proration = pb.prorate_concurrency(
        period_start=date(2026, 6, 1),
        period_end=date(2026, 7, 1),
        changes=[
            {"effective": date(2026, 6, 1), "channels": 2},
            {"effective": date(2026, 6, 29), "channels": 4},
        ],
        monthly_millicents_per_channel=9000,
    )
    # 30-day month, change on the 29th: 28 days at 2 channels, 2 days at 4.
    # (28*2 + 2*4)/30 * 9,000 = 64/30 * 9,000 = 19,200 millicents.
    assert proration.weighted_channel_days == 64
    assert proration.plan_millicents == 19_200
    assert proration.plan_millicents < 36_000  # never two full months


def test_proration_with_a_gap_is_refused(catalogue):
    with pytest.raises(pb.PriceBookError):
        pb.prorate_concurrency(
            period_start=date(2026, 6, 1),
            period_end=date(2026, 7, 1),
            changes=[
                {"effective": date(2026, 6, 2), "channels": 2},
                {"effective": date(2026, 6, 20), "channels": 3},
            ],
            monthly_millicents_per_channel=9000,
        )


def test_proration_appears_on_the_invoice_and_in_the_total(book: pb.PriceBook):
    proration = pb.prorate_concurrency(
        period_start=date(2026, 6, 1),
        period_end=date(2026, 7, 1),
        changes=[
            {"effective": date(2026, 6, 1), "channels": 2},
            {"effective": date(2026, 6, 21), "channels": 4},
        ],
        monthly_millicents_per_channel=9000,
    )
    invoice = _invoice(book, proration=proration)
    totals = invoice.totals()
    assert totals["subscription_charge_millicents"] == 24_000
    assert totals["total_charge_millicents"] == totals["usage_charge_millicents"] + 24_000


def test_a_proration_without_a_cost_side_says_so(catalogue):
    proration = pb.prorate_concurrency(
        period_start=date(2026, 6, 1),
        period_end=date(2026, 7, 1),
        changes=[{"effective": date(2026, 6, 1), "channels": 2}],
        monthly_millicents_per_channel=9000,
    )
    invoice = pb.price_usage(
        book=catalogue.latest(),
        components=[pb.UsageComponent("voice_engine", 10.0)],
        tenant_id="t",
        invoice_id="i",
        period_start=date(2026, 6, 1),
        period_end=date(2026, 7, 1),
        proration=proration,
    )
    # 100% margin would be a flattering lie; the invoice admits the cost is unknown.
    assert invoice.totals()["cost_complete"] is False


# ---------------------------------------------------------------------------
# Add-ons
# ---------------------------------------------------------------------------


def test_add_ons_price_as_flat_units(book: pb.PriceBook):
    invoice = _invoice(
        book,
        [
            {"metric": "addon:extra_concurrency", "quantity": 2, "source": "account"},
            {"metric": "addon:sso", "quantity": 1, "source": "account"},
        ],
    )
    by_component = {line.component: line for line in invoice.lines}
    assert by_component["addon:extra_concurrency"].charge_millicents == 18_000
    assert by_component["addon:sso"].charge_millicents == 500_000
    assert all(line.kind == "addon" for line in invoice.lines)


def test_add_ons_sort_after_usage_on_the_invoice(book: pb.PriceBook):
    invoice = _invoice(
        book,
        [
            {"metric": "addon:sso", "quantity": 1, "source": "a"},
            {"metric": "voice_minute", "quantity": 60, "country": "US", "source": "b"},
        ],
    )
    kinds = [line.kind for line in invoice.lines]
    assert kinds.index("addon") > kinds.index("voice")
