"""The numbers behind "show me this one, in full".

The list reports summarise, so a rounding slip there costs a pixel. These are
the screens an accountant opens to check a figure against Tally itself, which
makes them the place a wrong number is most expensive and most believable. The
sides are pinned explicitly for the same reason they are in ``test_analytics``:
a sign convention error reads as entirely plausible output.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from tally_core.domain.masters import VoucherTypeKind

from tally_backend.services import analytics as an
from tally_backend.services.voucher_store import record_key

TODAY = date(2026, 7, 23)
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture
def vouchers(samples):
    return an.effective(an.parse_vouchers(samples.vouchers(TODAY)))


def _money(amount: str, side: str) -> dict[str, str]:
    return {"amount": amount, "side": side, "currency": "INR"}


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------


def test_voucher_key_matches_the_key_the_store_files_it_under(samples) -> None:
    """The app taps a row and asks for that key back.

    Two implementations of one identity would open a different voucher from the
    one that was tapped, and nothing on screen would say so -- the detail would
    simply be somebody else's invoice.
    """
    raw = samples.vouchers(TODAY)
    parsed = an.parse_vouchers(raw)

    for payload, voucher in zip(raw, parsed, strict=True):
        assert an.voucher_key(voucher) == record_key(payload)


def test_voucher_key_prefers_the_guid_over_the_fallback_hash() -> None:
    """Identity has to survive an edit. A number, a date and a party are all
    things an operator can change; the GUID is not."""
    with_guid = an.parse_vouchers(
        [
            {
                "voucher_type": "Sales",
                "date": TODAY.isoformat(),
                "guid": "abc-123",
                "voucher_number": "7",
                "party_name": "Reliance Retail",
            }
        ]
    )[0]
    assert an.voucher_key(with_guid) == "abc-123"

    renumbered = with_guid.model_copy(update={"voucher_number": "8"})
    assert an.voucher_key(renumbered) == an.voucher_key(with_guid)


def test_every_listed_row_carries_a_key(vouchers) -> None:
    """A row the user can see but not follow is a dead end."""
    rows = an.recent_transactions(vouchers, limit=10)
    assert rows
    assert all(row["key"] for row in rows)


# --------------------------------------------------------------------------
# Voucher detail
# --------------------------------------------------------------------------


def test_voucher_detail_keeps_every_line_and_its_side(vouchers) -> None:
    sale = next(v for v in vouchers if v.voucher_number == "1")
    detail = an.voucher_detail(sale)

    assert [e["ledger"] for e in detail["ledger_entries"]] == [
        "Reliance Retail",
        "Sales",
        "Output GST",
    ]
    # The party is debited, income and tax are credited. Flattening these to
    # magnitudes would make the voucher unreadable as accounting.
    assert [e["amount"]["side"] for e in detail["ledger_entries"]] == [
        "debit",
        "credit",
        "credit",
    ]
    assert detail["ledger_entries"][2]["amount"]["amount"] == "1800.00"


def test_voucher_detail_reports_both_sides_so_a_reader_can_see_it_balances(
    vouchers,
) -> None:
    """A voucher whose sides disagree means the read dropped a line. Without
    both totals on screen that is indistinguishable from a one-entry voucher."""
    sale = next(v for v in vouchers if v.voucher_number == "1")
    detail = an.voucher_detail(sale)

    assert detail["debit_total"]["amount"] == "11800.00"
    assert detail["credit_total"]["amount"] == "11800.00"


def test_voucher_detail_carries_the_stock_lines(vouchers) -> None:
    sale = next(v for v in vouchers if v.voucher_number == "1")
    items = an.voucher_detail(sale)["inventory_entries"]

    assert len(items) == 1
    assert items[0]["item"] == "Rice"
    assert items[0]["quantity"] == 100.0
    assert items[0]["unit"] == "KG"


def test_a_cancelled_voucher_says_so(samples) -> None:
    """It is excluded from every total in the product, so a detail screen that
    showed it silently would be explaining a figure that is in none of them."""
    cancelled = next(
        v for v in an.parse_vouchers(samples.vouchers(TODAY)) if v.is_cancelled
    )
    assert an.voucher_detail(cancelled)["is_cancelled"] is True


def test_find_voucher_returns_none_rather_than_guessing(vouchers) -> None:
    assert an.find_voucher(vouchers, "no-such-key") is None


# --------------------------------------------------------------------------
# Ledger statement
# --------------------------------------------------------------------------


def test_ledger_statement_runs_oldest_to_newest(vouchers) -> None:
    """A running total that counts down the page is not a running total."""
    statement = an.ledger_statement(
        vouchers, ledger="Sales", since=YESTERDAY, until=TODAY
    )
    dates = [row["date"] for row in statement["entries"]]
    assert dates == sorted(dates)


def test_ledger_statement_accumulates_the_movement(vouchers) -> None:
    statement = an.ledger_statement(
        vouchers, ledger="Sales", since=YESTERDAY, until=TODAY
    )

    # Yesterday's 5,000 then today's 10,000, both credits to income.
    assert [row["movement"]["amount"] for row in statement["entries"]] == [
        "5000.00",
        "10000.00",
    ]
    assert [row["running"]["amount"] for row in statement["entries"]] == [
        "5000.00",
        "15000.00",
    ]
    assert statement["entries"][-1]["running"]["side"] == "credit"
    assert statement["credit_total"]["amount"] == "15000.00"
    assert statement["debit_total"]["amount"] == "0.00"


def test_ledger_statement_excludes_the_cancelled_sale(samples) -> None:
    """₹9,99,999 of it. Callers pass ``effective`` vouchers; this pins that the
    fixture's trap would be caught if one ever forgot."""
    live = an.effective(an.parse_vouchers(samples.vouchers(TODAY)))
    statement = an.ledger_statement(live, ledger="Sales", since=YESTERDAY, until=TODAY)
    assert statement["voucher_count"] == 2


def test_ledger_statement_nets_two_lines_hitting_the_same_ledger() -> None:
    """An invoice split across cost centres posts to one ledger twice. Two rows
    for one voucher would make the running column step twice for a single
    transaction."""
    voucher = an.parse_vouchers(
        [
            {
                "voucher_type": "Sales",
                "kind": "sales",
                "date": TODAY.isoformat(),
                "voucher_number": "9",
                "ledger_entries": [
                    {"ledger_name": "Sales", "amount": _money("6000.00", "credit")},
                    {"ledger_name": "Sales", "amount": _money("4000.00", "credit")},
                    {"ledger_name": "Debtors", "amount": _money("10000.00", "debit")},
                ],
            }
        ]
    )

    statement = an.ledger_statement(voucher, ledger="Sales", since=TODAY, until=TODAY)
    assert len(statement["entries"]) == 1
    assert statement["entries"][0]["movement"]["amount"] == "10000.00"


def test_ledger_statement_matches_a_name_regardless_of_case_and_spacing(
    vouchers,
) -> None:
    """Ledger names reach us from a tapped row, a report and a master list, and
    Tally is not consistent about spacing between them."""
    statement = an.ledger_statement(
        vouchers, ledger="  sales ", since=YESTERDAY, until=TODAY
    )
    assert statement["voucher_count"] == 2


def test_ledger_statement_ignores_vouchers_outside_the_window(vouchers) -> None:
    statement = an.ledger_statement(vouchers, ledger="Sales", since=TODAY, until=TODAY)
    assert statement["voucher_count"] == 1


# --------------------------------------------------------------------------
# Registers
# --------------------------------------------------------------------------


def test_sales_register_totals_and_buckets_by_month(vouchers) -> None:
    report = an.register(vouchers, VoucherTypeKind.SALES, since=YESTERDAY, until=TODAY)

    assert report["voucher_count"] == 2
    assert report["total"]["amount"] == "16800.00"
    assert len(report["months"]) == 1
    assert report["months"][0]["label"] == "Jul 2026"
    assert report["months"][0]["voucher_count"] == 2


def test_purchase_register_does_not_pick_up_sales(vouchers) -> None:
    report = an.register(
        vouchers, VoucherTypeKind.PURCHASE, since=YESTERDAY, until=TODAY
    )
    assert report["voucher_count"] == 1
    assert report["total"]["amount"] == "212600.00"


def test_register_ranks_parties_over_the_whole_window_not_the_capped_list(
    vouchers,
) -> None:
    report = an.register(
        vouchers, VoucherTypeKind.SALES, since=YESTERDAY, until=TODAY, limit=1
    )

    # The row list is capped...
    assert len(report["vouchers"]) == 1
    assert report["truncated"] is True
    # ...but the count, the total and the cuts describe the real register, or
    # the chart would disagree with the figure printed above it.
    assert report["voucher_count"] == 2
    assert report["total"]["amount"] == "16800.00"
    assert {party["name"] for party in report["by_party"]} == {
        "Reliance Retail",
        "Local Kirana",
    }


def test_register_rows_are_newest_first(vouchers) -> None:
    report = an.register(vouchers, VoucherTypeKind.SALES, since=YESTERDAY, until=TODAY)
    dates = [row["date"] for row in report["vouchers"]]
    assert dates == sorted(dates, reverse=True)


# --------------------------------------------------------------------------
# Stock movement
# --------------------------------------------------------------------------


def test_item_movement_reads_direction_from_the_voucher_class() -> None:
    """Tally signs inventory quantities inconsistently between voucher types.
    A purchase counted as an outward movement turns a stock report into fiction,
    so direction comes from the accounting class and never from the sign.
    """
    moves = an.parse_vouchers(
        [
            {
                "voucher_type": "Purchase",
                "kind": "purchase",
                "date": YESTERDAY.isoformat(),
                "voucher_number": "P1",
                "inventory_entries": [
                    {
                        "item_name": "Rice",
                        # Positive here, negative on the sale below: the sign is
                        # exactly what must not be trusted.
                        "quantity": 150.0,
                        "unit": "KG",
                        "amount": _money("12000.00", "debit"),
                    }
                ],
            },
            {
                "voucher_type": "Sales",
                "kind": "sales",
                "date": TODAY.isoformat(),
                "voucher_number": "S1",
                "inventory_entries": [
                    {
                        "item_name": "Rice",
                        "quantity": -40.0,
                        "unit": "KG",
                        "amount": _money("4000.00", "credit"),
                    }
                ],
            },
        ]
    )

    report = an.item_movement(moves, item="Rice", since=YESTERDAY, until=TODAY)

    assert report["quantity_in"] == 150.0
    assert report["quantity_out"] == 40.0
    assert report["net_quantity"] == 110.0
    assert report["unit"] == "KG"
    assert {row["direction"] for row in report["movements"]} == {"in", "out"}


def test_item_movement_leaves_an_unclassifiable_voucher_out_of_both_totals() -> None:
    """A stock journal is neither a purchase nor a sale. Guessing it into one
    would quietly move a figure an owner is reconciling a shortfall against, so
    the row stays visible and the arithmetic stays honest about excluding it."""
    moves = an.parse_vouchers(
        [
            {
                "voucher_type": "Stock Journal",
                "kind": "stock_journal",
                "date": TODAY.isoformat(),
                "voucher_number": "J1",
                "inventory_entries": [
                    {
                        "item_name": "Rice",
                        "quantity": 12.0,
                        "unit": "KG",
                        "amount": _money("900.00", "debit"),
                    }
                ],
            }
        ]
    )

    report = an.item_movement(moves, item="Rice", since=TODAY, until=TODAY)

    assert report["voucher_count"] == 1
    assert report["movements"][0]["direction"] == "other"
    assert report["quantity_in"] == 0.0
    assert report["quantity_out"] == 0.0


def test_item_movement_ignores_other_items(vouchers) -> None:
    report = an.item_movement(vouchers, item="Wheat", since=YESTERDAY, until=TODAY)
    assert report["voucher_count"] == 0
    # Never a zero presented as a reading: no rows means no quantity, and the
    # app renders "nothing moved" rather than "0 KG".
    assert report["movements"] == []


def test_item_movement_values_the_outward_side(vouchers) -> None:
    report = an.item_movement(vouchers, item="Rice", since=YESTERDAY, until=TODAY)
    assert report["quantity_out"] == 100.0
    assert Decimal(report["value_out"]["amount"]) == Decimal("10000.00")


def test_an_old_voucher_says_its_stock_lines_were_not_kept(vouchers) -> None:
    """The sync drops inventory from slices older than ``sync_inventory_days``.

    So an empty item list on an old invoice means "not kept", not "sold
    nothing" -- and drawing it as the latter is the same class of mistake as
    rendering an unread figure as a zero.
    """
    sale = next(v for v in vouchers if v.voucher_number == "1")
    assert sale.inventory_entries, "the fixture invoice has stock lines"

    kept = an.voucher_detail(sale, inventory_kept=True)
    assert kept["inventory_omitted"] is False

    # A voucher that really does have lines is never flagged, however old it is.
    old = an.voucher_detail(sale, inventory_kept=False)
    assert old["inventory_omitted"] is False

    stripped = sale.model_copy(update={"inventory_entries": []})
    assert an.voucher_detail(stripped, inventory_kept=False)["inventory_omitted"] is True
    # An accounting-only voucher inside the window has nothing to report.
    assert an.voucher_detail(stripped, inventory_kept=True)["inventory_omitted"] is False
