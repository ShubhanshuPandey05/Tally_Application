"""Top Prime Reports: the rankings, the totals and the rules they share.

Each report was specified against a TallyPrime TDL module a customer already
reads, so these tests pin the specification's rules -- what counts, how it is
summed, which row comes first -- rather than whatever the code happens to do.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from tally_core.domain.masters import VoucherTypeKind

from tally_backend.services import analytics as an
from tally_backend.services import top_reports as top

SINCE = date(2026, 4, 1)
UNTIL = date(2026, 6, 30)


def _m(amount: str, side: str) -> dict[str, str]:
    return {"amount": amount, "side": side, "currency": "INR"}


def _sale(
    number: str,
    on: date,
    party: str | None,
    lines: list[tuple[str, float, str]],
    *,
    gst: str = "0",
    kind: VoucherTypeKind = VoucherTypeKind.SALES,
    voucher_type: str = "Sales",
    **extra: Any,
) -> dict[str, Any]:
    """A sale (or any invoice-shaped voucher): party Dr, goods and tax Cr."""
    goods = sum(Decimal(amount) for _, _, amount in lines)
    total = goods + Decimal(gst)
    entries = [
        {"ledger_name": party or "Cash", "amount": _m(str(total), "debit"), "is_party": True},
        {"ledger_name": "Sales", "amount": _m(str(goods), "credit")},
    ]
    if Decimal(gst):
        entries.append({"ledger_name": "Output IGST", "amount": _m(gst, "credit")})
    return {
        "voucher_number": number,
        "voucher_type": voucher_type,
        "kind": str(kind),
        "date": on.isoformat(),
        "party_name": party,
        "ledger_entries": entries,
        "inventory_entries": [
            {"item_name": item, "quantity": qty, "amount": _m(amount, "credit")}
            for item, qty, amount in lines
        ],
        **extra,
    }


def _purchase(number: str, on: date, party: str, lines: list[tuple[str, float, str]]):
    return _sale(
        number, on, party, lines, kind=VoucherTypeKind.PURCHASE, voucher_type="Purchase"
    )


def _vouchers(*raw: dict[str, Any]):
    return an.parse_vouchers(list(raw))


def _build(key: str, **kwargs: Any) -> dict[str, Any]:
    sort = kwargs.pop("sort", None)
    limit = kwargs.pop("limit", 10)
    return top.build(key, top.Inputs(since=SINCE, until=UNTIL, **kwargs), sort=sort, limit=limit)


def _names(report: dict[str, Any]) -> list[str]:
    return [row["name"] for row in report["rows"]]


# --------------------------------------------------------------------------
# Common rules
# --------------------------------------------------------------------------


def test_the_total_row_adds_only_the_rows_shown():
    """The specification's TOTAL is over the top N, not over everybody. An
    owner checking a top-two against Tally's own report adds up two rows."""
    window = _vouchers(
        _sale("1", date(2026, 4, 2), "A", [("Fan", 1, "300")]),
        _sale("2", date(2026, 4, 3), "B", [("Fan", 1, "200")]),
        _sale("3", date(2026, 4, 4), "C", [("Fan", 1, "100")]),
    )
    report = _build("customers", window=window, limit=2)

    assert _names(report) == ["A", "B"]
    assert report["row_count"] == 3
    assert report["total"]["amount"]["amount"] == "500.00"
    assert report["total"]["bills"] == 2
    # Dates are blank on the TOTAL row.
    assert report["total"]["last_date"] is None


def test_a_voucher_with_no_party_is_not_available_rather_than_dropped():
    report = _build("customers", window=_vouchers(_sale("1", date(2026, 4, 2), None, [])))
    assert _names(report) == [top.NOT_AVAILABLE]
    assert report["rows"][0]["drill"] is None


def test_the_alternate_sort_reorders_and_ranks_from_one():
    window = _vouchers(
        _sale("1", date(2026, 4, 2), "Big", [("Fan", 1, "900")]),
        _sale("2", date(2026, 4, 3), "Many", [("Bulb", 50, "100")]),
    )
    by_amount = _build("customers", window=window)
    by_quantity = _build("customers", window=window, sort="quantity")

    assert _names(by_amount) == ["Big", "Many"]
    assert _names(by_quantity) == ["Many", "Big"]
    assert [row["rank"] for row in by_quantity["rows"]] == [1, 2]
    assert [s["key"] for s in by_amount["sorts"]] == ["amount", "quantity"]


# --------------------------------------------------------------------------
# 4a / 4b
# --------------------------------------------------------------------------


def test_taxable_is_the_bill_less_the_lines_under_duties_and_taxes():
    window = _vouchers(_sale("1", date(2026, 4, 2), "A", [("Fan", 2, "1000")], gst="180"))
    ledgers = an.parse_ledgers(
        [
            {"name": "Output IGST", "parent_group": "Duties & Taxes"},
            {"name": "A", "parent_group": "Sundry Debtors"},
        ]
    )
    row = _build("customers", window=window, ledgers=ledgers)["rows"][0]

    assert row["cells"]["amount"]["amount"] == "1180.00"
    assert row["cells"]["taxable"]["amount"] == "1000.00"
    assert row["cells"]["quantity"] == 2


def test_an_item_takes_its_share_of_the_invoice_gst_and_counts_one_bill_per_invoice():
    window = _vouchers(
        _sale(
            "1",
            date(2026, 4, 2),
            "A",
            [("Fan", 1, "600"), ("Fan", 1, "200"), ("Bulb", 4, "200")],
            gst="100",
        ),
    )
    rows = {r["name"]: r["cells"] for r in _build("selling_products", window=window)["rows"]}

    assert rows["Fan"]["bills"] == 1
    assert rows["Fan"]["taxable"]["amount"] == "800.00"
    assert rows["Fan"]["amount"]["amount"] == "880.00"
    assert rows["Bulb"]["amount"]["amount"] == "220.00"


def test_fast_moving_ranks_on_quantity_and_averages_over_the_period_days():
    window = _vouchers(
        _sale("1", date(2026, 4, 2), "A", [("Bulb", 91, "91")]),
        _sale("2", date(2026, 4, 3), "A", [("Fan", 1, "5000")]),
    )
    report = _build("fast_moving", window=window)

    assert _names(report) == ["Bulb", "Fan"]
    # 1 Apr to 30 Jun is 91 days.
    assert report["rows"][0]["cells"]["daily"] == 1.0
    assert report["rows"][0]["cells"]["weekly"] == 7.0
    assert report["total"]["monthly"] == round((92 / 91) * 30, 3)


# --------------------------------------------------------------------------
# History reports
# --------------------------------------------------------------------------


def test_inactive_customers_are_those_quiet_for_more_than_180_days():
    history = _vouchers(
        _sale("1", date(2025, 10, 1), "Gone", []),
        _sale("2", date(2026, 6, 1), "Regular", []),
        _sale("3", date(2025, 9, 1), "Regular", []),
    )
    ledgers = an.parse_ledgers(
        [{"name": "Gone", "parent_group": "Sundry Debtors", "closing_balance": _m("500", "debit")}]
    )
    report = _build("inactive_customers", history=history, ledgers=ledgers)

    assert _names(report) == ["Gone"]
    cells = report["rows"][0]["cells"]
    assert cells["days"] == (UNTIL - date(2025, 10, 1)).days
    assert cells["balance"]["side"] == "debit"


def test_non_moving_keeps_stock_unsold_for_180_days_or_never_moved():
    history = _vouchers(
        _sale("1", date(2025, 11, 1), "A", [("Old", 1, "10")]),
        _sale("2", date(2026, 6, 1), "A", [("Fresh", 1, "10")]),
    )
    stock = an.parse_stock(
        [
            {"name": "Old", "closing_quantity": 5, "closing_value": _m("50", "debit")},
            {"name": "Fresh", "closing_quantity": 5, "closing_value": _m("50", "debit")},
            {"name": "Never", "closing_quantity": 2, "closing_value": _m("900", "debit")},
            {"name": "Empty", "closing_quantity": 0},
        ]
    )
    report = _build("non_moving", history=history, stock=stock)

    assert _names(report) == ["Never", "Old"]
    assert report["rows"][0]["cells"]["days"] == 0


def test_negative_stock_shows_magnitudes():
    stock = an.parse_stock(
        [
            {"name": "Short", "closing_quantity": -3, "closing_value": _m("300", "credit")},
            {"name": "Fine", "closing_quantity": 3},
        ]
    )
    report = _build("negative_stock", stock=stock)
    assert _names(report) == ["Short"]
    assert report["rows"][0]["cells"]["quantity"] == 3


# --------------------------------------------------------------------------
# Expenses and outstanding
# --------------------------------------------------------------------------


def test_expenses_include_sub_groups_and_net_debits_against_credits():
    window = an.parse_vouchers(
        [
            {
                "voucher_type": "Payment",
                "kind": "payment",
                "date": "2026-04-05",
                "ledger_entries": [
                    {"ledger_name": "Rent", "amount": _m("9100", "debit")},
                    {"ledger_name": "Diesel", "amount": _m("910", "debit")},
                    {"ledger_name": "Cash", "amount": _m("10010", "credit")},
                ],
            },
            {
                "voucher_type": "Journal",
                "kind": "journal",
                "date": "2026-04-06",
                "ledger_entries": [
                    {"ledger_name": "Rent", "amount": _m("100", "credit")},
                    {"ledger_name": "Cash", "amount": _m("100", "debit")},
                ],
            },
        ]
    )
    ledgers = an.parse_ledgers(
        [
            {"name": "Rent", "parent_group": "Indirect Expenses"},
            {"name": "Diesel", "parent_group": "Vehicle Running"},
            {"name": "Cash", "parent_group": "Cash-in-Hand"},
        ]
    )
    groups = an.parse_groups([{"name": "Vehicle Running", "parent": "Indirect Expenses"}])
    report = _build("expenses", window=window, ledgers=ledgers, groups=groups)

    assert _names(report) == ["Rent", "Diesel"]
    rent = report["rows"][0]["cells"]
    assert rent["amount"]["amount"] == "9000.00"
    assert rent["under"] == "Indirect Expenses"
    assert rent["daily"]["amount"] == "98.90"


def test_outstanding_ranks_the_longest_overdue_first_and_keeps_only_debtors():
    bills = an.parse_bills(
        [
            {
                "party_name": "Late",
                "bill_name": "1",
                "kind": "receivable",
                "bill_date": "2026-01-01",
                "due_date": "2026-02-01",
                "pending_amount": _m("100", "debit"),
            },
            {
                "party_name": "Big",
                "bill_name": "2",
                "kind": "receivable",
                "bill_date": "2026-06-01",
                "pending_amount": _m("9000", "debit"),
            },
            {
                "party_name": "Big",
                "bill_name": "3",
                "kind": "receivable",
                "bill_date": "2026-05-01",
                "pending_amount": _m("1000", "debit"),
            },
            {
                "party_name": "Supplier",
                "bill_name": "4",
                "kind": "receivable",
                "bill_date": "2025-01-01",
                "pending_amount": _m("1", "debit"),
            },
        ]
    )
    ledgers = an.parse_ledgers(
        [
            {"name": "Late", "parent_group": "Sundry Debtors"},
            {"name": "Big", "parent_group": "Sundry Debtors"},
            {"name": "Supplier", "parent_group": "Sundry Creditors"},
        ]
    )
    report = _build("outstanding_customers", bills=bills, ledgers=ledgers)

    assert _names(report) == ["Late", "Big"]
    big = report["rows"][1]["cells"]
    assert big["bills"] == 2
    assert big["oldest_due"] == "2026-05-01"
    assert big["latest_due"] == "2026-06-01"
    assert big["overdue_days"] == (UNTIL - date(2026, 5, 1)).days
    assert _names(_build("outstanding_customers", bills=bills, ledgers=ledgers, sort="amount")) == [
        "Big",
        "Late",
    ]


def test_payment_performance_weights_paid_bills_by_value():
    def receipt(on: date, party: str, bills: list[str]) -> dict[str, Any]:
        return {
            "voucher_type": "Receipt",
            "kind": "receipt",
            "date": on.isoformat(),
            "ledger_entries": [
                {"ledger_name": party, "amount": _m("1", "credit"), "bill_references": bills},
                {"ledger_name": "Cash", "amount": _m("1", "debit")},
            ],
        }

    history = _vouchers(
        _sale("S1", date(2026, 4, 1), "Prompt", [("Fan", 1, "100")]),
        _sale("S2", date(2026, 4, 1), "Mixed", [("Fan", 1, "300")]),
        _sale("S3", date(2026, 4, 1), "Mixed", [("Fan", 1, "100")]),
        _sale("S4", date(2026, 4, 1), "Mixed", [("Fan", 1, "100")]),
        receipt(date(2026, 4, 11), "Prompt", ["S1"]),
        receipt(date(2026, 5, 1), "Mixed", ["S2"]),
        receipt(date(2026, 4, 3), "Mixed", ["S3"]),
        # S4 is part-paid: still pending, so it does not count yet.
        receipt(date(2026, 4, 2), "Mixed", ["S4"]),
    )
    bills = an.parse_bills(
        [
            {
                "party_name": "Mixed",
                "bill_name": "S4",
                "kind": "receivable",
                "pending_amount": _m("50", "debit"),
            }
        ]
    )
    report = _build("payment_performance", history=history, bills=bills)

    rows = {r["name"]: r["cells"]["days"] for r in report["rows"]}
    assert rows == {"Prompt": 10, "Mixed": round((30 * 300 + 2 * 100) / 400)}
    assert _names(report) == ["Prompt", "Mixed"]


# --------------------------------------------------------------------------
# Profit
# --------------------------------------------------------------------------


def test_product_profit_costs_sales_at_average_purchase_cost():
    history = _vouchers(
        _purchase("P1", date(2026, 3, 1), "Supplier", [("Fan", 10, "1000")]),
        _purchase("P2", date(2026, 3, 2), "Supplier", [("Bulb", 10, "100")]),
    )
    window = _vouchers(
        _sale("1", date(2026, 4, 2), "A", [("Fan", 2, "300"), ("Bulb", 5, "40")]),
    )
    profit = _build(
        "profitable_products",
        window=window,
        history=history + window,
        stock=an.parse_stock(
            [{"name": "Fan", "opening_quantity": 10, "opening_value": _m("1000", "debit")}]
        ),
    )
    loss = _build("loss_products", window=window, history=history + window, stock=[])

    fan = profit["rows"][0]["cells"]
    assert _names(profit) == ["Fan"]
    assert fan["cost"]["amount"] == "200.00"
    assert fan["profit"]["amount"] == "100.00"
    assert fan["percent"] == "33.33"
    assert _names(loss) == ["Bulb"]
    assert loss["rows"][0]["cells"]["profit"]["amount"] == "10.00"


def test_customer_profit_leaves_out_invoices_it_cannot_cost():
    history = _vouchers(_purchase("P1", date(2026, 3, 1), "S", [("Fan", 1, "100")]))
    window = _vouchers(
        _sale("1", date(2026, 4, 2), "A", [("Fan", 1, "150")]),
        _sale("2", date(2026, 4, 3), "B", [("Mystery", 1, "150")]),
    )
    report = _build("profitable_customers", window=window, history=history + window, stock=[])

    assert _names(report) == ["A"]
    assert any("left out" in note for note in report["notes"])


def test_rate_variance_finds_the_spread_and_the_extra_paid():
    window = _vouchers(
        _purchase("P1", date(2026, 4, 1), "Cheap", [("Fan", 10, "1000")]),
        _purchase("P2", date(2026, 4, 2), "Dear", [("Fan", 10, "1200")]),
        _purchase("P3", date(2026, 4, 3), "Same", [("Bulb", 10, "100")]),
    )
    report = _build("rate_variance", window=window)

    assert _names(report) == ["Fan"]
    cells = report["rows"][0]["cells"]
    assert cells["high_from"] == "Dear"
    assert cells["low_from"] == "Cheap"
    assert cells["diff"]["amount"] == "20.00"
    assert cells["percent"] == "20.00"
    assert cells["extra"]["amount"] == "200.00"


def test_returns_read_credit_and_debit_notes():
    window = _vouchers(
        _sale(
            "C1",
            date(2026, 4, 2),
            "A",
            [("Fan", 1, "100")],
            kind=VoucherTypeKind.CREDIT_NOTE,
            voucher_type="Credit Note",
        ),
        _sale("1", date(2026, 4, 2), "B", [("Fan", 1, "100")]),
    )
    assert _names(_build("sales_returns", window=window)) == ["A"]
    assert _names(_build("purchase_returns", window=window)) == []


def test_every_report_builds_on_empty_books():
    for key in top.REPORT_KEYS:
        report = _build(key, stock=[], ledgers=[], bills=[])
        assert report["rows"] == [], key
        assert report["columns"], key
