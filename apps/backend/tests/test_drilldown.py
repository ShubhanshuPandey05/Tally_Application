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


def test_ledger_statement_runs_from_its_opening_balance(vouchers) -> None:
    """With an opening balance the running column is the account's balance, and
    it ends on the closing balance -- Tally's Ledger Vouchers, line for line."""
    opening = an.Money.from_tally("2000.00 Cr")
    statement = an.ledger_statement(
        vouchers, ledger="Sales", since=YESTERDAY, until=TODAY, opening=opening
    )

    assert statement["opening_balance"]["display"] == "2,000.00 Cr"
    assert [row["running"]["amount"] for row in statement["entries"]] == [
        "7000.00",
        "17000.00",
    ]
    assert statement["period_closing_balance"]["display"] == "17,000.00 Cr"
    # The movement is still the movement: an opening balance changes where the
    # account stood, not what went through it.
    assert statement["net_movement"]["amount"] == "15000.00"


def test_an_opening_balance_on_the_other_side_is_worked_off_first(vouchers) -> None:
    """An income account that opened in debit climbs out of it. Adding the
    magnitudes instead of the signed figures reads 15,000 as 18,000."""
    statement = an.ledger_statement(
        vouchers,
        ledger="Sales",
        since=YESTERDAY,
        until=TODAY,
        opening=an.Money.from_tally("3000.00 Dr"),
    )

    assert [row["running"]["display"] for row in statement["entries"]] == [
        "2,000.00 Cr",
        "12,000.00 Cr",
    ]


def test_a_statement_with_no_opening_balance_says_so(vouchers) -> None:
    """Null, never nil: an account whose opening could not be worked out did
    not start from zero, and the app draws the two differently."""
    statement = an.ledger_statement(vouchers, ledger="Sales", since=YESTERDAY, until=TODAY)

    assert statement["opening_balance"] is None
    assert statement["period_closing_balance"] is None


def test_an_order_is_not_a_ledger_entry() -> None:
    """Verified live 2026-10-11: a sales order names the customer and posts
    nothing to them. Listed on a statement it shows a customer owing for goods
    nobody has billed, and the running balance leaves Tally's by that amount."""
    lines = [
        {"ledger_name": "Ram Traders", "amount": _money("1180.00", "debit")},
        {"ledger_name": "Sales", "amount": _money("1180.00", "credit")},
    ]
    parsed = an.parse_vouchers(
        [
            {"voucher_type": "Sales", "kind": "sales", "date": TODAY.isoformat(),
             "voucher_number": "1", "ledger_entries": lines},
            {"voucher_type": "Sales Order", "kind": "sales_order", "date": TODAY.isoformat(),
             "voucher_number": "SO-1", "ledger_entries": lines},
            {"voucher_type": "Delivery Note", "kind": "delivery_note", "date": TODAY.isoformat(),
             "voucher_number": "DN-1", "ledger_entries": lines},
        ]
    )

    statement = an.ledger_statement(parsed, ledger="Ram Traders", since=TODAY, until=TODAY)

    assert [row["voucher_number"] for row in statement["entries"]] == ["1"]
    assert statement["net_movement"]["amount"] == "1180.00"
    assert an.ledger_movement(parsed, ledger="ram  traders").amount == Decimal("1180.00")


def test_an_unknown_group_is_not_assumed_to_be_a_balance_sheet_account() -> None:
    groups = an.parse_groups(
        [
            {"name": "Sales Accounts", "is_revenue": True},
            {"name": "Sundry Debtors", "is_revenue": False},
        ]
    )

    assert an.is_revenue_group(groups, "sales accounts") is True
    assert an.is_revenue_group(groups, "Sundry Debtors") is False
    # Which way the opening balance is worked out hangs on this answer, so
    # "not in the read" must not come back as either.
    assert an.is_revenue_group(groups, "North Zone Debtors") is None
    assert an.is_revenue_group(groups, None) is None


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


def test_register_months_carry_quantity_tax_and_cancelled_entries() -> None:
    def sale(number: str, *, lines: bool, cancelled: bool = False) -> dict:
        return {
            "voucher_number": number,
            "voucher_type": "Sales",
            "kind": "sales",
            "date": TODAY.isoformat(),
            "is_cancelled": cancelled,
            "ledger_entries": [
                {"ledger_name": "Ram Traders", "amount": _money("1180.00", "debit")},
                {"ledger_name": "Sales", "amount": _money("1000.00", "credit")},
                {"ledger_name": "Output CGST 9%", "amount": _money("90.00", "credit")},
                {"ledger_name": "SGST Payable", "amount": _money("90.00", "credit")},
            ],
            "inventory_entries": [
                {"item_name": "Rice", "quantity": -10.0, "amount": _money("1000.00", "credit")}
            ]
            if lines
            else [],
        }

    everything = an.parse_vouchers(
        [sale("1", lines=True), sale("2", lines=True), sale("3", lines=True, cancelled=True)]
    )
    report = an.register(
        an.effective(everything),
        VoucherTypeKind.SALES,
        since=TODAY,
        until=TODAY,
        cancelled=[v for v in everything if v.is_cancelled],
    )
    month = report["months"][0]

    # The cancelled sale is counted, and adds to nothing.
    assert month["voucher_count"] == 2
    assert month["cancelled_count"] == 1
    assert month["total"]["amount"] == "2360.00"
    assert month["quantity"] == 20.0
    assert month["item_value"]["amount"] == "2000.00"
    assert month["cgst"]["amount"] == "180.00"
    assert month["sgst"]["amount"] == "180.00"
    assert month["igst"]["amount"] == "0.00"


def test_a_month_synced_without_stock_lines_reports_no_quantity() -> None:
    """Old slices are stored without their lines. That is "not known", and a
    quantity of zero beside real sales would say nothing was sold."""
    voucher = {
        "voucher_number": "1",
        "voucher_type": "Sales",
        "kind": "sales",
        "date": TODAY.isoformat(),
        "ledger_entries": [
            {"ledger_name": "Ram Traders", "amount": _money("500.00", "debit")},
            {"ledger_name": "Sales", "amount": _money("500.00", "credit")},
        ],
    }
    report = an.register(
        an.parse_vouchers([voucher]), VoucherTypeKind.SALES, since=TODAY, until=TODAY
    )

    assert report["months"][0]["quantity"] is None
    assert report["months"][0]["item_value"] is None


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


def test_an_invoice_line_keeps_its_own_hsn_and_rate_over_the_masters(vouchers) -> None:
    """What the bill was taxed at, not what the item is taxed at today.

    Tally works out the HSN and the rate when the invoice is saved and keeps
    them on the line. The stock master only stands in for a voucher stored
    before those were read -- it used to be the only source, and on a real
    company it held an HSN for barely half the items and a rate for fewer.
    """
    from tally_core.domain.masters import StockItem

    sale = next(v for v in vouchers if v.voucher_number == "1")
    line = sale.inventory_entries[0]
    master = {
        line.item_name.strip().lower(): StockItem(
            name=line.item_name, hsn_code="1006", gst_rate=5.0
        )
    }

    from_master = an.voucher_detail(sale, items=master)["inventory_entries"][0]
    assert (from_master["hsn_code"], from_master["gst_rate"]) == ("1006", 5.0)

    recorded = sale.model_copy(
        update={
            "inventory_entries": [
                line.model_copy(update={"hsn_code": "10063020", "gst_rate": 0.0}),
                *sale.inventory_entries[1:],
            ]
        }
    )
    from_line = an.voucher_detail(recorded, items=master)["inventory_entries"][0]
    # A recorded nil rate is a rate. It must not fall through to the master's.
    assert (from_line["hsn_code"], from_line["gst_rate"]) == ("10063020", 0.0)

    neither = an.voucher_detail(sale)["inventory_entries"][0]
    assert neither["hsn_code"] is None and neither["gst_rate"] is None


def test_a_voucher_carries_its_invoice_header_only_when_it_has_one(vouchers) -> None:
    from datetime import date as day

    from tally_core.domain.transactions import InvoiceDetails, VoucherParty

    sale = next(v for v in vouchers if v.voucher_number == "1")
    assert an.voucher_detail(sale)["invoice"] is None

    written = sale.model_copy(
        update={
            "invoice": InvoiceDetails(
                consignee=VoucherParty(name="Ram Traders", address=["Ring Road"], state="Bihar"),
                dispatched_through="Anchal Logistics",
                bill_of_lading_no="771522771058",
                bill_of_lading_date=day(2025, 4, 26),
            )
        }
    )
    invoice = an.voucher_detail(written)["invoice"]
    assert invoice["consignee"]["name"] == "Ram Traders"
    assert invoice["buyer"] is None
    assert invoice["dispatched_through"] == "Anchal Logistics"
    assert invoice["bill_of_lading_date"] == "2025-04-26"
    assert invoice["terms_of_delivery"] == []
