"""Mapper tests against XML captured verbatim from a real TallyPrime.

Fixtures in ``fixtures/live/`` were recorded on 2026-07-23 from the company
"Bhtia Supermarket" (FY starting 2026-04-01). They are the ground truth for the
mappers: the hand-written fixtures encode *what the docs describe*, these encode
*what Tally actually sends*. Where the two disagreed, these won.

Every bug these caught on first contact with real data:

* Tally's sign convention is inverted from the usual one (negative = Debit).
* Invoices return their lines under BOTH ``ALLLEDGERENTRIES`` and
  ``LEDGERENTRIES``; reading both double-counted every purchase.
* ``PERSISTEDVIEW`` is a UI view name, not an accounting class, so every
  voucher classified as OTHER.
* Rates arrive as ``"80.00/KG"``, which parsed to zero.
* Master names live in the ``NAME`` *attribute*, not a child element.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tally_core.domain.masters import VoucherTypeKind
from tally_core.domain.money import Side
from tally_core.domain.transactions import OutstandingKind
from tally_core.tally import get_query, parse_xml

LIVE = Path(__file__).parent / "fixtures" / "live"
COMPANY = "Bhtia Supermarket"
PERIOD = {"from_date": date(2026, 4, 1), "to_date": date(2026, 7, 23)}


def run(query_name: str, fixture: str, **params):
    query = get_query(query_name)
    validated = query.validate_params({"company": COMPANY, **params})
    return query.parse(parse_xml((LIVE / f"{fixture}.xml").read_bytes()), validated)


@pytest.fixture(scope="module")
def ledgers():
    return {lg.name: lg for lg in run("ledgers.list", "ledgers")}


@pytest.fixture(scope="module")
def vouchers():
    return {v.voucher_type: v for v in run("vouchers.list", "vouchers", **PERIOD)}


# --------------------------------------------------------------------------
# Companies
# --------------------------------------------------------------------------


def test_company_maps_from_live_response():
    companies = run("companies.list", "companies")
    assert len(companies) == 1

    company = companies[0]
    assert company.name == "Bhtia Supermarket"
    assert company.financial_year_from == date(2026, 4, 1)
    assert company.books_from == date(2026, 4, 1)
    assert company.state == "Sikkim"


def test_leading_empty_element_is_skipped():
    """Every live collection begins with a nameless placeholder element."""
    root = parse_xml((LIVE / "companies.xml").read_bytes())
    assert len(list(root.iter("COMPANY"))) == 2  # placeholder + real
    assert len(run("companies.list", "companies")) == 1


# --------------------------------------------------------------------------
# The sign convention -- the single most important thing to get right
# --------------------------------------------------------------------------


#: Group -> the side a healthy balance in it must fall on.
NATURAL_SIDE = {
    "Cash-in-Hand": Side.DEBIT,
    "Bank Accounts": Side.DEBIT,
    "Fixed Assets": Side.DEBIT,
    "Direct Expenses": Side.DEBIT,
    "Purchase Accounts": Side.DEBIT,
    "Capital Account": Side.CREDIT,
    "Sundry Creditors": Side.CREDIT,
    "Sales Accounts": Side.CREDIT,
}


def test_every_ledger_lands_on_its_natural_accounting_side(ledgers):
    """The regression test for the inverted-sign bug.

    Tally writes a debit as a *negative* number. Reading that the usual way put
    cash, banks and every asset on the credit side -- i.e. the dashboard would
    have told a shop owner their bank account was overdrawn.
    """
    checked = 0
    for ledger in ledgers.values():
        expected = NATURAL_SIDE.get(ledger.parent_group or "")
        if expected is None or ledger.closing_balance.is_zero:
            continue
        checked += 1
        assert ledger.closing_balance.side is expected, (
            f"{ledger.name} ({ledger.parent_group}) came back "
            f"{ledger.closing_balance}, expected a {expected.value} balance"
        )
    assert checked >= 10, "fixture should exercise both sides across many groups"


def test_specific_live_balances(ledgers):
    assert str(ledgers["Cash"].closing_balance) == "344,220.00 Dr"
    assert str(ledgers["Hdfc Bank"].closing_balance) == "444,556.00 Dr"
    assert str(ledgers["Sangay Bhutia"].closing_balance) == "700,000.00 Cr"
    assert str(ledgers["SARA DISITIBUTOR"].closing_balance) == "212,600.00 Cr"


def test_input_and_output_gst_fall_on_opposite_sides(ledgers):
    """Input GST is a receivable, output GST a liability -- a good sign check."""
    assert ledgers["INPUT CGST"].closing_balance.side is Side.DEBIT
    assert ledgers["INPUT IGST"].closing_balance.side is Side.DEBIT
    assert ledgers["OUTPUT CGST"].closing_balance.side is Side.CREDIT
    assert ledgers["OUTPUT SGST"].closing_balance.side is Side.CREDIT


def test_ledger_count_and_names(ledgers):
    assert len(ledgers) == 18
    assert "Cash" in ledgers
    # Backslashes in a ledger name survive the round trip.
    assert "PURCASE A\\C" in ledgers


def test_groups_map(ledgers):
    groups = run("groups.list", "groups")
    assert len(groups) == 28
    by_name = {g.name: g for g in groups}
    assert by_name["Bank Accounts"].parent == "Current Assets"


# --------------------------------------------------------------------------
# Vouchers
# --------------------------------------------------------------------------


def test_all_voucher_kinds_classify(vouchers):
    """PERSISTEDVIEW is a view name; classifying on it made everything OTHER."""
    assert vouchers["Receipt"].kind is VoucherTypeKind.RECEIPT
    assert vouchers["Contra"].kind is VoucherTypeKind.CONTRA
    assert vouchers["Payment"].kind is VoucherTypeKind.PAYMENT
    assert vouchers["Purchase"].kind is VoucherTypeKind.PURCHASE
    assert vouchers["Sales"].kind is VoucherTypeKind.SALES
    assert not any(v.kind is VoucherTypeKind.OTHER for v in vouchers.values())


@pytest.mark.parametrize(
    "voucher_type", ["Receipt", "Contra", "Payment", "Purchase", "Sales"]
)
def test_every_voucher_balances(vouchers, voucher_type):
    """Debits must equal credits.

    This is the regression test for the double-count bug: Tally returned each
    invoice's lines under two different wrappers, and summing both left every
    purchase at twice its true value.
    """
    voucher = vouchers[voucher_type]
    total = sum(entry.amount.signed for entry in voucher.ledger_entries)
    assert total == Decimal("0.00"), (
        f"{voucher_type} #{voucher.voucher_number} does not balance: {total}"
    )


def test_invoice_lines_are_not_double_counted(vouchers):
    purchase = vouchers["Purchase"]
    assert len(purchase.ledger_entries) == 3
    assert [e.ledger_name for e in purchase.ledger_entries] == [
        "Reliance Retail",
        "Printer",
        "INPUT IGST",
    ]


def test_is_deemed_positive_drives_the_entry_side(vouchers):
    """AMOUNT=-700000 with ISDEEMEDPOSITIVE=Yes is a debit to Cash."""
    receipt = vouchers["Receipt"]
    entries = {e.ledger_name: e for e in receipt.ledger_entries}

    assert entries["Cash"].amount.side is Side.DEBIT
    assert entries["Cash"].amount.amount == Decimal("700000.00")
    assert entries["Sangay Bhutia"].amount.side is Side.CREDIT


def test_voucher_metadata(vouchers):
    receipt = vouchers["Receipt"]
    assert receipt.voucher_number == "1"
    assert receipt.date == date(2026, 4, 1)
    assert receipt.guid == "eda3e329-1dec-43b5-bcbe-ad1850489a21-00000001"
    assert receipt.is_cancelled is False
    assert receipt.is_effective is True


def test_sales_voucher_carries_inventory(vouchers):
    sales = vouchers["Sales"]
    assert len(sales.inventory_entries) == 1

    line = sales.inventory_entries[0]
    assert line.item_name == "JAGGERY"
    assert line.quantity == 600.0
    assert line.unit == "KG"


# --------------------------------------------------------------------------
# Stock
# --------------------------------------------------------------------------


def test_stock_items_map():
    items = {i.name: i for i in run("stock_items.list", "stock_items")}
    assert set(items) == {"JAGGERY", "MALT", "TERMERIC"}

    jaggery = items["JAGGERY"]
    assert jaggery.closing_quantity == 400.0
    assert jaggery.base_unit == "KG"
    # Stock on hand is an asset, so its value is a debit.
    assert jaggery.closing_value.amount == Decimal("32000.00")
    assert jaggery.closing_value.side is Side.DEBIT


def test_rates_parse_and_carry_no_accounting_side():
    """'80.00/KG' previously parsed to zero; a unit price is never Dr or Cr."""
    items = {i.name: i for i in run("stock_items.list", "stock_items")}

    assert items["JAGGERY"].closing_rate.amount == Decimal("80.00")
    assert items["MALT"].closing_rate.amount == Decimal("150.00")
    assert items["TERMERIC"].closing_rate.amount == Decimal("140.00")
    assert all(i.closing_rate.side is Side.DEBIT for i in items.values())


def test_rate_times_quantity_reconciles_with_value():
    """An independent cross-check that quantity, rate and value all parsed right."""
    for item in run("stock_items.list", "stock_items"):
        expected = Decimal(str(item.closing_quantity)) * item.closing_rate.amount
        assert item.closing_value.amount == expected, item.name


def test_unset_optional_fields_stay_none():
    """Empty <REORDERLEVEL/> must not become a false stock alert."""
    for item in run("stock_items.list", "stock_items"):
        assert item.reorder_level is None
        assert item.is_below_reorder is False


# --------------------------------------------------------------------------
# Empty results
# --------------------------------------------------------------------------


def test_empty_collection_yields_no_rows_not_an_error():
    """An empty collection is data, not a failure.

    This capture is what the *broken* query returned: without SVFROMDATE, live
    Tally answers with an empty collection rather than an error.
    """
    bills = run("outstanding.bills", "outstanding_empty", as_of=date(2026, 7, 23))
    assert bills == []


# --------------------------------------------------------------------------
# Outstanding bills
# --------------------------------------------------------------------------


AS_OF = date(2026, 7, 23)


@pytest.fixture(scope="module")
def bills():
    return run("outstanding.bills", "outstanding_bills", as_of=AS_OF)


def test_bill_members_are_found(bills):
    """Regression guard: Tally names each member <BILL>, not <BILLS>.

    Searching for the plural tag returned zero outstanding bills on a company
    that genuinely had one -- the receivables screen would have shown "nothing
    outstanding" while real money was owed.
    """
    assert len(bills) == 1


def test_bill_reconciles_with_its_ledger(bills, ledgers):
    """The bill's pending amount must equal the party ledger's balance."""
    bill = bills[0]
    assert bill.party_name == "SARA DISITIBUTOR"
    assert bill.bill_name == "2"

    ledger = ledgers[bill.party_name]
    assert bill.pending_amount.amount == ledger.closing_balance.amount
    assert bill.pending_amount.side is ledger.closing_balance.side


def test_bill_is_classified_by_its_balance_side(bills):
    """A credit balance on a supplier is a payable."""
    bill = bills[0]
    assert bill.kind is OutstandingKind.PAYABLE
    assert bill.pending_amount.side is Side.CREDIT
    assert bill.pending_amount.amount == Decimal("212600.00")
    assert bill.is_advance is False


def test_due_date_comes_from_the_jd_attribute(bills):
    """<BILLCREDITPERIOD> has empty text; the due date is in its JD attribute.

    JD counts days from 1899-12-31, so JD=46112 is 2026-04-01. Reading the
    element text alone left every bill with no due date, which silently made
    the whole ageing report read "not due".
    """
    bill = bills[0]
    assert bill.bill_date == date(2026, 4, 1)
    assert bill.due_date == date(2026, 4, 1)
    assert bill.credit_period_days == 0


def test_ageing_is_computed_from_the_live_due_date(bills):
    bill = bills[0]
    assert bill.days_overdue(AS_OF) == 113  # 2026-04-01 -> 2026-07-23
    assert bill.ageing_bucket(AS_OF) == "91_180"


def test_settled_bills_are_excluded(bills):
    """Reliance Retail's bill was raised then fully paid, so it must not appear."""
    assert all(b.party_name != "Reliance Retail" for b in bills)
    assert all(not b.pending_amount.is_zero for b in bills)


# --------------------------------------------------------------------------
# Stock by godown
# --------------------------------------------------------------------------


def test_godown_stock_maps_from_live_response():
    """Recorded 2026-10-11 from "D.D Enterprises", trimmed to three items.

    The capture this replaced an assumption with: a walked batch exports only
    its computed fields, under ``<BATCH>`` with no ``NAME`` attribute, and an
    item gets a row for the godown it was created in even when it holds
    nothing there -- twice for ABHILASHA, once per batch. Those fold to a
    single zero row rather than vanishing, so the analytics layer is the one
    place that decides a zero is not worth a line.
    """
    rows = {(r.godown, r.item_name): r for r in run("godown_stock.list", "godown_stock")}

    assert len(rows) == 8
    assert rows[("Main Location", "ABHILASHA")].quantity == 0.0
    assert rows[("Main Location", "ABHILASHA")].value.is_zero

    factory_a = rows[("Factory A", "ABHILASHA")]
    assert factory_a.quantity == 100.0
    assert factory_a.value.amount == Decimal("593750.00")
    # Stock is an asset, and Tally writes an asset as a negative number.
    assert factory_a.value.side is Side.DEBIT

    # The three places add up to the item's own closing balance of 370 Pcs,
    # which is the check `tally-connector livecheck` runs for every item.
    held = sum(r.quantity for (_, item), r in rows.items() if item == "ABHILASHA")
    assert held == 370.0


# --------------------------------------------------------------------------
# Company profile
# --------------------------------------------------------------------------


def test_company_profile_takes_its_gstin_from_the_gst_registration():
    """Recorded 2026-10-11 from "D.D Enterprises".

    ``GSTREGISTRATIONNUMBER`` is absent from this reply because the company
    object holds none: the number came from a ``TaxUnit`` and arrives as the
    computed ``TFGSTIN``. A profile read that asked only the company for it
    would print this business's tax invoices with no GSTIN on them, which is
    what happened until this read existed.
    """
    query = get_query("company.profile")
    params = query.validate_params({"company": "D.D Enterprises"})
    profile = query.parse(parse_xml((LIVE / "company_profile.xml").read_bytes()), params)

    assert profile.name == "D.D Enterprises"
    assert profile.gstin == "24BQTPK1669M1Z4"
    assert profile.state == "Gujarat"
    assert profile.pincode == "395005"
    # Off the company object, which the Company collection does not return --
    # recorded 2026-10-14, after the address was found missing on the invoice.
    assert profile.address == [
        "309,BLUE EMIMINENCE SANGINI DANDI ROAD",
        "JAHANGIRABAD, SURAT ,GUJARAT",
    ]
    assert profile.bank is not None
    assert profile.bank.account_number == "50200108679595"
    # Nothing entered in Tally is nothing on the page, never an empty line.
    assert profile.phone is None
    assert profile.mobile is None
    assert profile.website is None


def test_company_profile_of_another_company_is_not_borrowed():
    query = get_query("company.profile")
    params = query.validate_params({"company": "Somebody Else"})
    profile = query.parse(parse_xml((LIVE / "company_profile.xml").read_bytes()), params)

    assert profile.name == "Somebody Else"
    assert profile.gstin is None


# --------------------------------------------------------------------------
# A negative entry on a debit-natured line
# --------------------------------------------------------------------------


def test_a_round_off_taken_off_a_purchase_is_a_credit():
    """Recorded 2026-10-11: purchase 4 of "D.D Enterprises", lines only.

    The Round Off line is ``ISDEEMEDPOSITIVE=Yes`` with a *positive* amount --
    a debit-natured line that went the other way. Letting the flag decide read
    it as a debit of 0.21, and the voucher then failed to balance by 0.42.
    """
    query = get_query("vouchers.list")
    params = query.validate_params(
        {"company": "D.D Enterprises", "from_date": date(2025, 4, 1), "to_date": date(2025, 5, 1)}
    )
    (voucher,) = query.parse(parse_xml((LIVE / "voucher_round_off.xml").read_bytes()), params)

    lines = {e.ledger_name: e.amount for e in voucher.ledger_entries}
    assert lines["Round Off"].side is Side.CREDIT
    assert lines["Round Off"].amount == Decimal("0.21")
    assert lines["Gst Purchase"].side is Side.DEBIT
    assert sum(e.amount.signed for e in voucher.ledger_entries) == Decimal("0.00")


# --------------------------------------------------------------------------
# What a printed invoice needs
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def tax_invoice():
    """Recorded 2026-10-12: sales invoice 2025-26/01 of "D.D Enterprises".

    Tally's own print of this invoice is ``tmp/Sales Invoice Format.pdf``, and
    every assertion below is a line of that page.
    """
    query = get_query("vouchers.list")
    params = query.validate_params(
        {"company": "D.D Enterprises", "from_date": date(2025, 4, 1), "to_date": date(2025, 4, 30)}
    )
    (voucher,) = query.parse(parse_xml((LIVE / "voucher_tax_invoice.xml").read_bytes()), params)
    return voucher


def test_an_invoice_line_carries_its_own_hsn_and_gst_rate(tax_invoice):
    """The HSN column printed empty until these were read off the line."""
    lines = [(e.hsn_code, e.gst_rate) for e in tax_invoice.inventory_entries]
    assert lines == [
        ("621142", 12.0),
        ("621142", 5.0),
        ("620419", 5.0),
        ("620469", 12.0),
        ("62114300", 12.0),
    ]


def test_a_tax_line_carries_the_rate_it_was_charged_at(tax_invoice):
    rates = {e.ledger_name: e.tax_rate for e in tax_invoice.ledger_entries}
    assert rates["IGST 12"] == 12.0
    assert rates["IGST 5"] == 5.0
    # Not a tax, so no rate -- and never a zero standing in for one.
    assert rates["M/S DULHAN DRESSES"] is None


def test_an_invoice_keeps_its_buyer_consignee_and_dispatch_details(tax_invoice):
    invoice = tax_invoice.invoice
    assert invoice is not None
    assert invoice.buyer.name == "M/S DULHAN DRESSES"
    assert invoice.buyer.gstin == "10EGHPK3851A1Z0"
    assert invoice.buyer.state == "Bihar"
    assert invoice.buyer.address == [
        "Bairgania , Kappra Patti  Bairgania, Sitamarhi",
        "Bihar,Ashogi Pursottam, Sitamarhi,Bihar 843313",
    ]
    assert invoice.consignee.name == "M/S DULHAN DRESSES"
    assert invoice.consignee.gstin == "10EGHPK3851A1Z0"
    assert invoice.dispatched_through == "Anchal Logistics"
    assert invoice.bill_of_lading_no == "771522771058"
    assert invoice.bill_of_lading_date == date(2025, 4, 26)
    assert invoice.terms_of_delivery == ["Purchaser Party", "SAMPAT SACHIN KURTI"]
    # Left blank on the invoice, so blank here: the print keeps the caption
    # and shows nothing under it.
    assert invoice.destination is None
    assert invoice.vehicle_no is None


def test_a_voucher_with_no_invoice_details_has_none():
    query = get_query("vouchers.list")
    params = query.validate_params(
        {"company": "D.D Enterprises", "from_date": date(2025, 4, 1), "to_date": date(2025, 5, 1)}
    )
    (voucher,) = query.parse(parse_xml((LIVE / "voucher_round_off.xml").read_bytes()), params)
    assert voucher.invoice is None
    assert all(e.tax_rate is None for e in voucher.ledger_entries)


def test_a_stock_item_takes_its_hsn_and_rate_from_the_dated_lists():
    """Recorded 2026-10-12 from "D.D Enterprises".

    The ``HSNCODE`` inside ``GSTDETAILS.LIST`` is empty on both items; the code
    is in ``HSNDETAILS.LIST``. The rate is the IGST one, three levels down --
    reading the first ``GSTRATE`` in the tree would have returned the CGST
    half.
    """
    query = get_query("stock_items.list")
    params = query.validate_params({"company": "D.D Enterprises"})
    items = {
        i.name: i
        for i in query.parse(parse_xml((LIVE / "stock_items_gst.xml").read_bytes()), params)
    }
    assert items["Dupatta Set12  621142"].hsn_code == "621142"
    assert items["Dupatta Set12  621142"].gst_rate == 12.0
    assert items["Dupatta Set 5 621142"].hsn_code == "621142"
    assert items["Dupatta Set 5 621142"].gst_rate == 5.0
