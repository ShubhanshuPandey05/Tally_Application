"""Correctness of the numbers.

These are the assertions that matter most in the whole backend. An auth bug
produces a 500 someone notices; an analytics bug produces a confident, wrong
number that an owner makes a purchasing decision on. The live-Tally work already
proved that a sign convention error looks completely plausible in the output --
so the sides are pinned explicitly here.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from tally_core.domain.masters import VoucherTypeKind
from tally_core.domain.money import Money, Side
from tally_core.domain.transactions import OutstandingKind

from tally_backend.services import analytics as an

TODAY = date(2026, 7, 23)


@pytest.fixture
def vouchers(samples):
    return an.effective(an.parse_vouchers(samples.vouchers(TODAY)))


# --------------------------------------------------------------------------
# Voucher totals
# --------------------------------------------------------------------------


def test_cancelled_vouchers_never_reach_a_total(samples, vouchers) -> None:
    """The fixture hides a cancelled ₹9,99,999 sale among real ones."""
    raw = an.parse_vouchers(samples.vouchers(TODAY))
    assert len(raw) == 4

    live = an.effective(raw)
    assert len(live) == 3

    total = an.total_for(live, VoucherTypeKind.SALES, on=TODAY)
    assert total.amount == Decimal("11800.00")


def test_todays_sales_is_the_gross_invoice_value(vouchers) -> None:
    """Gross, including tax -- which is what Tally's Sales Register shows."""
    total = an.total_for(vouchers, VoucherTypeKind.SALES, on=TODAY)
    assert total.amount == Decimal("11800.00")


def test_voucher_value_uses_entries_not_the_amount_field() -> None:
    """Live Tally often omits voucher-level AMOUNT on accounting vouchers."""
    voucher = an.parse_vouchers(
        [
            {
                "voucher_type": "Sales",
                "kind": "sales",
                "date": TODAY.isoformat(),
                # No "amount" key at all.
                "ledger_entries": [
                    {
                        "ledger_name": "Debtor",
                        "amount": {"amount": "500.00", "side": "debit"},
                    },
                    {
                        "ledger_name": "Sales",
                        "amount": {"amount": "500.00", "side": "credit"},
                    },
                ],
            }
        ]
    )[0]
    assert an.voucher_value(voucher).amount == Decimal("500.00")


def test_a_period_total_excludes_days_outside_it(vouchers) -> None:
    yesterday_total = an.total_for(vouchers, VoucherTypeKind.SALES, on=TODAY - timedelta(days=1))
    assert yesterday_total.amount == Decimal("5000.00")


def test_purchases_and_sales_do_not_bleed_into_each_other(vouchers) -> None:
    sales = an.total_for(vouchers, VoucherTypeKind.SALES, on=TODAY)
    purchases = an.total_for(vouchers, VoucherTypeKind.PURCHASE, on=TODAY)
    assert sales.amount == Decimal("11800.00")
    assert purchases.amount == Decimal("212600.00")


def test_trend_includes_days_with_no_trade(vouchers) -> None:
    """Missing points would let a chart draw a line across a closed week."""
    series = an.daily_series(
        vouchers,
        VoucherTypeKind.SALES,
        since=TODAY - timedelta(days=6),
        until=TODAY,
    )
    assert len(series) == 7
    assert series[-1]["value"] == "11800.00"
    assert any(point["value"] == "0.00" for point in series)


def test_top_parties_are_ranked_by_value(vouchers) -> None:
    ranked = an.top_parties(vouchers, VoucherTypeKind.SALES)
    assert [entry["name"] for entry in ranked] == ["Reliance Retail", "Local Kirana"]
    assert ranked[0]["amount"]["amount"] == "11800.00"


def test_top_products_aggregate_quantity_and_value(vouchers) -> None:
    ranked = an.top_products(vouchers, VoucherTypeKind.SALES)
    assert ranked[0]["name"] == "Rice"
    assert ranked[0]["quantity"] == 100.0
    assert ranked[0]["unit"] == "KG"


# --------------------------------------------------------------------------
# Balances and the sign convention
# --------------------------------------------------------------------------


def test_cash_and_bank_land_on_the_debit_side(samples) -> None:
    """An asset must never render as an overdraft.

    This is the exact bug the first live Tally run exposed: with the sign
    convention inverted, a healthy bank balance reads as money owed.
    """
    ledgers = an.parse_ledgers(samples.ledgers())

    cash = an.group_balance(ledgers, an.CASH_GROUPS)
    bank = an.group_balance(ledgers, an.BANK_GROUPS)

    assert cash.side is Side.DEBIT
    assert cash.amount == Decimal("344220.00")
    assert bank.side is Side.DEBIT
    assert bank.amount == Decimal("125000.00")


def test_signed_value_is_debit_positive_for_charting(samples) -> None:
    ledgers = an.parse_ledgers(samples.ledgers())
    cash = an.money_out(an.group_balance(ledgers, an.CASH_GROUPS))
    assert cash["signed"] == "344220.00"
    assert cash["side"] == "debit"


def test_an_overdrawn_bank_reads_negative() -> None:
    overdrawn = an.parse_ledgers(
        [
            {
                "name": "OD Account",
                "parent_group": "Bank Accounts",
                "closing_balance": {"amount": "50000.00", "side": "credit"},
            }
        ]
    )
    balance = an.money_out(an.group_balance(overdrawn, an.BANK_GROUPS))
    assert balance["signed"] == "-50000.00"
    assert balance["side"] == "credit"


def test_group_matching_is_case_insensitive() -> None:
    ledgers = an.parse_ledgers(
        [
            {
                "name": "Petty Cash",
                "parent_group": "CASH-IN-HAND",
                "closing_balance": {"amount": "100.00", "side": "debit"},
            }
        ]
    )
    assert an.group_balance(ledgers, an.CASH_GROUPS).amount == Decimal("100.00")


# --------------------------------------------------------------------------
# Outstanding
# --------------------------------------------------------------------------


def test_receivables_and_payables_are_separated(samples) -> None:
    bills = an.parse_bills(samples.bills(TODAY))

    receivable = an.outstanding_summary(bills, OutstandingKind.RECEIVABLE, as_of=TODAY)
    payable = an.outstanding_summary(bills, OutstandingKind.PAYABLE, as_of=TODAY)

    assert receivable["total"]["amount"] == "11800.00"
    assert payable["total"]["amount"] == "212600.00"


def test_a_bill_not_yet_due_is_not_counted_as_overdue(samples) -> None:
    summary = an.outstanding_summary(
        an.parse_bills(samples.bills(TODAY)), OutstandingKind.RECEIVABLE, as_of=TODAY
    )
    assert summary["overdue"]["amount"] == "0.00"
    assert summary["ageing"]["not_due"]["amount"] == "11800.00"


def test_ageing_buckets_match_the_live_verified_case(samples) -> None:
    """SARA DISITIBUTOR's ₹2,12,600 sat 113 days overdue -> the 91-180 bucket."""
    summary = an.outstanding_summary(
        an.parse_bills(samples.bills(TODAY)), OutstandingKind.PAYABLE, as_of=TODAY
    )
    assert summary["ageing"]["91_180"]["amount"] == "212600.00"
    assert summary["overdue"]["amount"] == "212600.00"


def test_ageing_buckets_always_sum_to_the_total(samples) -> None:
    bills = an.parse_bills(samples.bills(TODAY))
    for kind in (OutstandingKind.RECEIVABLE, OutstandingKind.PAYABLE):
        summary = an.outstanding_summary(bills, kind, as_of=TODAY)
        bucketed = sum(
            Decimal(bucket["amount"]) for bucket in summary["ageing"].values()
        )
        assert bucketed == Decimal(summary["total"]["amount"]), f"{kind} buckets do not reconcile"


# --------------------------------------------------------------------------
# Group outstanding
# --------------------------------------------------------------------------


def _advance(party: str, amount: str) -> dict:
    """A customer prepayment: a credit-side bill sitting in a debtor's ledger."""
    return {
        "party_name": party,
        "bill_name": "ADV-1",
        "kind": "payable",
        "bill_date": (TODAY - timedelta(days=5)).isoformat(),
        "pending_amount": {"amount": amount, "side": "credit", "currency": "INR"},
        "opening_amount": {"amount": amount, "side": "credit", "currency": "INR"},
        "is_advance": True,
    }


def test_group_outstanding_places_parties_by_their_ledger_group(samples) -> None:
    report = an.group_outstanding(
        an.parse_bills(samples.bills(TODAY)),
        an.parse_ledgers(samples.ledgers()),
        group="Sundry Debtors",
        kind=OutstandingKind.RECEIVABLE,
        as_of=TODAY,
    )

    assert [party["party"] for party in report["parties"]] == ["Reliance Retail"]
    assert report["summary"]["total"]["amount"] == "11800.00"
    assert report["summary"]["party_count"] == 1
    # The creditor's bill is in the same dataset and must not leak in.
    assert report["summary"]["bill_count"] == 1


def test_group_outstanding_is_a_different_axis_from_bill_side(samples) -> None:
    """The distinction the whole report exists for.

    An advance from a customer closes on the credit side, so
    ``outstanding_summary`` correctly calls it a payable. It is still money
    sitting against a Sundry Debtor, so the group report must show it -- as an
    advance that reduces the net, not as debt that inflates the total.
    """
    bills = an.parse_bills(samples.bills(TODAY) + [_advance("Reliance Retail", "1800.00")])
    ledgers = an.parse_ledgers(samples.ledgers())

    flat = an.outstanding_summary(bills, OutstandingKind.RECEIVABLE, as_of=TODAY)
    assert flat["total"]["amount"] == "11800.00", "the advance is not a receivable"

    report = an.group_outstanding(
        bills, ledgers, group="Sundry Debtors", kind=OutstandingKind.RECEIVABLE, as_of=TODAY
    )
    summary = report["summary"]
    assert summary["total"]["amount"] == "11800.00"
    assert summary["advances"]["amount"] == "1800.00"
    assert summary["net"]["amount"] == "10000.00"
    assert summary["net"]["side"] == "debit"
    assert summary["bill_count"] == 2

    party = report["parties"][0]
    assert party["net"]["amount"] == "10000.00"
    assert party["advances"]["amount"] == "1800.00"


def test_group_outstanding_ageing_ignores_advances(samples) -> None:
    """An advance has no due date to be overdue against."""
    bills = an.parse_bills(samples.bills(TODAY) + [_advance("Reliance Retail", "1800.00")])
    report = an.group_outstanding(
        bills,
        an.parse_ledgers(samples.ledgers()),
        group="Sundry Debtors",
        kind=OutstandingKind.RECEIVABLE,
        as_of=TODAY,
    )
    summary = report["summary"]
    bucketed = sum(Decimal(bucket["amount"]) for bucket in summary["ageing"].values())
    assert bucketed == Decimal(summary["total"]["amount"])


def test_group_outstanding_counts_parties_it_could_not_place(samples) -> None:
    """A party with no ledger row is reported, not silently dropped.

    Under-reporting a receivables total without saying so is the failure mode
    worth guarding: the owner reads a smaller number and believes it.
    """
    report = an.group_outstanding(
        an.parse_bills(samples.bills(TODAY)),
        an.parse_ledgers([led for led in samples.ledgers() if led["name"] != "Reliance Retail"]),
        group="Sundry Debtors",
        kind=OutstandingKind.RECEIVABLE,
        as_of=TODAY,
    )
    assert report["parties"] == []
    assert report["ungrouped_party_count"] == 1


def test_group_outstanding_matches_party_names_tally_spelled_differently(samples) -> None:
    """Tally keeps whatever was typed; the same party arrives spaced two ways."""
    ledgers = an.parse_ledgers(
        [
            {
                "name": "reliance   retail",
                "parent_group": "Sundry Debtors",
                "closing_balance": {"amount": "11800.00", "side": "debit"},
            }
        ]
    )
    report = an.group_outstanding(
        an.parse_bills(samples.bills(TODAY)),
        ledgers,
        group="sundry debtors",
        kind=OutstandingKind.RECEIVABLE,
        as_of=TODAY,
    )
    assert report["summary"]["party_count"] == 1


def test_group_payable_reads_the_creditors_group(samples) -> None:
    report = an.group_outstanding(
        an.parse_bills(samples.bills(TODAY)),
        an.parse_ledgers(samples.ledgers()),
        group="Sundry Creditors",
        kind=OutstandingKind.PAYABLE,
        as_of=TODAY,
    )
    party = report["parties"][0]
    assert party["party"] == "SARA DISITIBUTOR"
    assert party["days_overdue"] == 113
    assert report["summary"]["net"]["side"] == "credit"


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------


def test_negative_and_low_stock_are_counted_separately(samples) -> None:
    """Negative stock is a data-entry error, not merely 'very low'."""
    summary = an.inventory_summary(an.parse_stock(samples.stock()))

    assert summary["negative_stock_count"] == 1
    assert summary["negative_stock"][0]["name"] == "Oil"
    assert summary["low_stock_count"] == 1
    assert summary["low_stock"][0]["name"] == "Sugar"


def test_slow_moving_excludes_anything_that_sold(samples, vouchers) -> None:
    items = an.parse_stock(samples.stock())
    idle = an.slow_moving(items, vouchers)
    names = {entry["name"] for entry in idle}

    assert "Rice" not in names, "Rice sold today and is not slow-moving"
    assert "Sugar" in names
    # Negative stock is not "on hand", so it does not appear as slow-moving.
    assert "Oil" not in names


# --------------------------------------------------------------------------
# Voucher classification
# --------------------------------------------------------------------------
#
# A voucher row names its type but does not carry the accounting class, and a
# live TallyPrime sends no <PARENT> on the voucher either. So a shop that
# renamed its sales voucher type -- "Tax Invoice", "GST Sales", the normal case
# -- had every sale classified OTHER, and every sales figure in the product read
# zero while the books were full of sales.


def renamed_sale(
    day: date = TODAY,
    voucher_type: str = "Tax Invoice",
    items: list[dict] | None = None,
) -> an.Voucher:
    """A sale exactly as the connector delivers it: unclassifiable by name."""
    return an.Voucher.model_validate(
        {
            "voucher_number": "1",
            "voucher_type": voucher_type,
            # What the parser derives when the name matches nothing it knows.
            "kind": "other",
            "date": day.isoformat(),
            "party_name": "Reliance Retail",
            "amount": {"amount": "11800.00", "side": "credit"},
            "inventory_entries": items or [],
        }
    )


def test_a_renamed_sales_type_is_classified_from_the_master(samples) -> None:
    kinds = an.voucher_kind_map(an.parse_voucher_types(samples.voucher_types()))
    [classified] = an.classify([renamed_sale()], kinds)

    assert classified.kind is VoucherTypeKind.SALES
    assert classified.voucher_type == "Tax Invoice", "the shop's own name is preserved"


def test_a_renamed_sale_reaches_the_sales_total(samples) -> None:
    kinds = an.voucher_kind_map(an.parse_voucher_types(samples.voucher_types()))

    before = an.total_for([renamed_sale()], VoucherTypeKind.SALES, on=TODAY)
    after = an.total_for(an.classify([renamed_sale()], kinds), VoucherTypeKind.SALES, on=TODAY)

    assert before.amount == Decimal("0.00"), "the bug: a full day of sales totalling nothing"
    assert after.amount == Decimal("11800.00")


def test_classification_survives_case_and_padding(samples) -> None:
    kinds = an.voucher_kind_map(an.parse_voucher_types(samples.voucher_types()))
    [classified] = an.classify([renamed_sale(voucher_type="  TAX INVOICE ")], kinds)

    assert classified.kind is VoucherTypeKind.SALES


def test_an_unmapped_type_keeps_what_the_parser_decided(samples) -> None:
    # Degradation, not destruction: a voucher type missing from the master read
    # must not lose a classification that was already correct.
    kinds = an.voucher_kind_map(an.parse_voucher_types(samples.voucher_types()))
    known_good = an.parse_vouchers(samples.vouchers(TODAY))[0]
    assert known_good.kind is VoucherTypeKind.SALES

    [unchanged] = an.classify([known_good], {"something else": VoucherTypeKind.JOURNAL})
    assert unchanged.kind is VoucherTypeKind.SALES

    [still_sales] = an.classify([known_good], kinds)
    assert still_sales.kind is VoucherTypeKind.SALES


def test_an_empty_map_changes_nothing(samples) -> None:
    # This is the path taken when the voucher-type read fails. It must leave the
    # dashboard exactly as it was rather than blanking every figure.
    original = an.parse_vouchers(samples.vouchers(TODAY))
    assert an.classify(original, {}) == original


def test_a_master_that_says_other_does_not_downgrade_a_voucher() -> None:
    # Tally reports its own memorandum/reversing types with no useful parent.
    # Trusting OTHER from the master would undo a correct name-based match.
    sale = an.parse_vouchers(
        [
            {
                "voucher_number": "9",
                "voucher_type": "Sales",
                "kind": "sales",
                "date": TODAY.isoformat(),
                "amount": {"amount": "100.00", "side": "credit"},
            }
        ]
    )
    [result] = an.classify(sale, {"sales": VoucherTypeKind.OTHER})
    assert result.kind is VoucherTypeKind.SALES


def test_slow_moving_does_not_condemn_the_whole_catalogue(samples) -> None:
    # Slow-moving keys off `kind is SALES`. With sales misclassified, nothing
    # counts as sold and every item in the shop is reported as not selling.
    items = an.parse_stock(samples.stock())
    sale_of_rice = renamed_sale(items=[{"item_name": "Rice", "quantity": 10.0}])

    unclassified = {e["name"] for e in an.slow_moving(items, [sale_of_rice])}
    assert "Rice" in unclassified, "the bug"

    kinds = an.voucher_kind_map(an.parse_voucher_types(samples.voucher_types()))
    fixed = {e["name"] for e in an.slow_moving(items, an.classify([sale_of_rice], kinds))}
    assert "Rice" not in fixed


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------


def test_money_is_serialised_as_a_string_not_a_float() -> None:
    """Binary floating point must never touch a rupee amount."""
    rendered = an.money_out(Money(amount=Decimal("0.10"), side=Side.DEBIT))
    assert isinstance(rendered["amount"], str)
    assert rendered["amount"] == "0.10"


def test_change_percentage_is_none_without_a_baseline() -> None:
    """No sales last month is not a 0% or 100% movement -- it is unknowable."""
    from tally_backend.services.dashboard import _change_pct

    assert _change_pct(Money(amount=Decimal("100"), side=Side.DEBIT), Money.zero()) is None
    assert (
        _change_pct(
            Money(amount=Decimal("150"), side=Side.DEBIT),
            Money(amount=Decimal("100"), side=Side.DEBIT),
        )
        == 50.0
    )
