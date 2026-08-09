"""End-to-end mapper tests: recorded Tally XML in, domain objects out."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tally_core.domain.masters import VoucherTypeKind
from tally_core.domain.money import Side
from tally_core.domain.transactions import OutstandingKind
from tally_core.tally import get_query, parse_xml, registry_manifest
from tally_core.tally.errors import UnknownQueryError


def run(name: str, xml: str, **params):
    query = get_query(name)
    validated = query.validate_params({"company": "Ram & Sons Traders", **params})
    return query.parse(parse_xml(xml), validated)


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


def test_registry_exposes_expected_queries():
    names = {entry["name"] for entry in registry_manifest()}
    assert names == {
        "companies.list",
        "company.markers",
        "groups.list",
        "ledgers.list",
        "stock_items.list",
        "voucher_types.list",
        "vouchers.list",
        "outstanding.bills",
    }


def test_unknown_query_is_refused():
    """Version skew must fail loudly, not silently return nothing."""
    with pytest.raises(UnknownQueryError):
        get_query("vouchers.create")


# --------------------------------------------------------------------------
# Companies
# --------------------------------------------------------------------------


def test_companies_map(fixture_xml):
    companies = run("companies.list", fixture_xml("companies_list"))
    assert [c.name for c in companies] == ["Ram & Sons Traders", "Shreeji Enterprises"]

    first = companies[0]
    assert first.financial_year_from == date(2024, 4, 1)
    assert first.gstin == "27AABCU9603R1ZM"
    assert first.state == "Maharashtra"

    # Second company uses the alternate date format and omits GSTIN entirely.
    assert companies[1].books_from == date(2023, 4, 1)
    assert companies[1].gstin is None


def test_company_envelope_escapes_ampersand():
    """An unescaped & in a company name makes Tally reject the whole envelope."""
    query = get_query("ledgers.list")
    xml = query.build(query.validate_params({"company": "Ram & Sons Traders"}))
    assert "Ram &amp; Sons Traders" in xml
    assert "Ram & Sons" not in xml


# --------------------------------------------------------------------------
# Company change markers
# --------------------------------------------------------------------------


def test_company_markers_map(fixture_xml):
    markers = run("company.markers", fixture_xml("company_markers"))
    assert markers.name == "Ram & Sons Traders"
    assert markers.master_alter_id == 1842
    assert markers.voucher_alter_id == 93117
    assert markers.books_from == date(2022, 4, 1)
    assert markers.ending_at == date(2026, 3, 31)
    assert markers.supports_incremental is True


def test_company_markers_ignore_other_open_companies(fixture_xml):
    """Advancing a cursor with a neighbouring company's counter loses vouchers."""
    query = get_query("company.markers")
    params = query.validate_params({"company": "Shreeji Enterprises"})
    markers = query.parse(parse_xml(fixture_xml("company_markers")), params)

    assert markers.name == "Shreeji Enterprises"
    assert markers.voucher_alter_id == 2010


def test_company_markers_absent_means_no_incremental_sync(fixture_xml):
    """An older Tally drops the unknown fields silently; that is not zero."""
    markers = run("company.markers", fixture_xml("company_markers_legacy"))
    assert markers.voucher_alter_id is None
    assert markers.master_alter_id is None
    assert markers.supports_incremental is False
    # Still useful: the books-from date is what bounds a date-window backfill.
    assert markers.books_from == date(2022, 4, 1)


def test_company_markers_absent_company_is_not_an_error(fixture_xml):
    """A company that is not open must degrade, not raise."""
    query = get_query("company.markers")
    params = query.validate_params({"company": "Never Opened Ltd"})
    markers = query.parse(parse_xml(fixture_xml("company_markers")), params)

    assert markers.name == "Never Opened Ltd"
    assert markers.supports_incremental is False


# --------------------------------------------------------------------------
# Ledgers
# --------------------------------------------------------------------------


def test_ledgers_map(fixture_xml):
    ledgers = {lg.name: lg for lg in run("ledgers.list", fixture_xml("ledgers_list"))}
    assert len(ledgers) == 4

    cash = ledgers["Cash"]
    assert cash.closing_balance.amount == Decimal("127450.75")
    assert cash.closing_balance.side is Side.DEBIT

    bank = ledgers["HDFC Bank A/c"]
    assert bank.opening_balance.amount == Decimal("250000.00")  # lakh-grouped commas
    assert bank.closing_balance.side is Side.CREDIT  # overdrawn

    debtor = ledgers["Gupta Electronics"]
    assert debtor.is_bill_wise is True
    assert debtor.credit_period_days == 30
    assert debtor.address == [
        "Shop 14, Lamington Road",
        "Grant Road East",
        "Mumbai 400007",
    ]
    assert debtor.email == "accounts@guptaelec.in"

    # Sales is a revenue ledger: credit balance, and empty opening tag is safe.
    assert ledgers["Sales - Local"].closing_balance.side is Side.CREDIT
    assert debtor.opening_balance.is_zero


def test_ledger_group_filter_uses_subgroup_walk():
    query = get_query("ledgers.list")
    xml = query.build(
        query.validate_params({"company": "Acme", "group": "Bank Accounts"})
    )
    assert "$$IsSubGroupOf" in xml
    assert "Bank Accounts" in xml


# --------------------------------------------------------------------------
# Stock
# --------------------------------------------------------------------------


def test_stock_items_map(fixture_xml):
    items = {i.name: i for i in run("stock_items.list", fixture_xml("stock_items"))}

    bulb = items["LED Bulb 9W"]
    assert bulb.closing_quantity == 1250.0  # comma-grouped, unit-suffixed
    assert bulb.closing_value.amount == Decimal("225000.00")
    assert bulb.base_unit == "Nos"
    assert bulb.hsn_code == "85395000"
    assert bulb.is_below_reorder is False

    assert items["Ceiling Fan 1200mm"].is_below_reorder is True
    assert items["Copper Wire 1.5sqmm"].is_negative_stock is True


def test_reorder_absent_is_not_below_reorder(fixture_xml):
    """No reorder level configured must never render as a stock alert."""
    items = {i.name: i for i in run("stock_items.list", fixture_xml("stock_items"))}
    wire = items["Copper Wire 1.5sqmm"]
    assert wire.reorder_level is None
    assert wire.is_below_reorder is False


# --------------------------------------------------------------------------
# Vouchers
# --------------------------------------------------------------------------


VOUCHER_PARAMS = {"from_date": date(2025, 7, 1), "to_date": date(2025, 7, 31)}


def test_vouchers_map(fixture_xml):
    vouchers = run("vouchers.list", fixture_xml("vouchers_daybook"), **VOUCHER_PARAMS)
    assert len(vouchers) == 4

    invoice = vouchers[0]
    assert invoice.voucher_number == "INV/2025/0412"
    assert invoice.date == date(2025, 7, 15)
    assert invoice.party_name == "Gupta Electronics"
    # Renamed voucher type still classifies via its parent class.
    assert invoice.voucher_type == "Tax Invoice"
    assert invoice.kind is VoucherTypeKind.SALES
    assert invoice.narration == "Being goods sold vide our challan 118"


def test_ledger_entry_side_follows_is_deemed_positive(fixture_xml):
    """ISDEEMEDPOSITIVE is authoritative; trusting the sign alone unbalances registers."""
    invoice = run("vouchers.list", fixture_xml("vouchers_daybook"), **VOUCHER_PARAMS)[0]
    entries = {e.ledger_name: e for e in invoice.ledger_entries}
    assert len(entries) == 4

    party = entries["Gupta Electronics"]
    # AMOUNT is -59000 but ISDEEMEDPOSITIVE=Yes -> the debtor is debited.
    assert party.amount.side is Side.DEBIT
    assert party.amount.amount == Decimal("59000.00")
    assert party.is_party is True
    assert party.bill_references == ["INV/2025/0412"]

    assert entries["Sales - Local"].amount.side is Side.CREDIT

    debits = sum(e.amount.signed for e in invoice.ledger_entries)
    assert debits == Decimal("0.00"), "voucher must balance"


def test_inventory_entries_map(fixture_xml):
    invoice = run("vouchers.list", fixture_xml("vouchers_daybook"), **VOUCHER_PARAMS)[0]
    assert len(invoice.inventory_entries) == 2

    line = invoice.inventory_entries[0]
    assert line.item_name == "LED Bulb 9W"
    assert line.quantity == 100.0
    assert line.unit == "Nos"
    assert line.godown == "Main Location"


def test_accounting_voucher_uses_ledgerentries_wrapper(fixture_xml):
    """Receipts nest lines under LEDGERENTRIES, invoices under ALLLEDGERENTRIES."""
    receipt = run("vouchers.list", fixture_xml("vouchers_daybook"), **VOUCHER_PARAMS)[1]
    assert receipt.kind is VoucherTypeKind.RECEIPT
    assert len(receipt.ledger_entries) == 2
    assert receipt.ledger_entries[0].ledger_name == "HDFC Bank A/c"


def test_cancelled_and_optional_vouchers_are_flagged_not_dropped(fixture_xml):
    """They must reach the day book for audit, but never a total."""
    vouchers = run("vouchers.list", fixture_xml("vouchers_daybook"), **VOUCHER_PARAMS)
    cancelled = vouchers[2]
    optional = vouchers[3]

    assert cancelled.is_cancelled is True
    assert cancelled.is_effective is False
    assert optional.is_optional is True
    assert optional.is_effective is False

    effective = [v for v in vouchers if v.is_effective]
    assert len(effective) == 2


def test_multiline_narration_is_not_truncated(fixture_xml):
    optional = run("vouchers.list", fixture_xml("vouchers_daybook"), **VOUCHER_PARAMS)[3]
    assert optional.narration == "Stock replenishment for festive season orders"


def test_voucher_date_range_is_validated():
    query = get_query("vouchers.list")
    with pytest.raises(ValueError, match="from_date must not be after to_date"):
        query.validate_params(
            {"company": "Acme", "from_date": date(2025, 7, 31), "to_date": date(2025, 7, 1)}
        )


def test_voucher_envelope_carries_date_range():
    query = get_query("vouchers.list")
    xml = query.build(query.validate_params({"company": "Acme", **VOUCHER_PARAMS}))
    assert "<SVFROMDATE>20250701</SVFROMDATE>" in xml
    assert "<SVTODATE>20250731</SVTODATE>" in xml


def test_voucher_envelope_filters_on_date_because_the_period_does_not():
    """The static variables above are not what scopes a Voucher collection.

    Verified live 2026-08-09: with only SVFROMDATE/SVTODATE set, asking for one
    week, one month and two years all returned the *same* vouchers -- the whole
    of the company's open financial year. A dashboard asking for 40 days was
    being served the year, and a six-month sync chunk was too, which is the
    chunking doing nothing at all.
    """
    query = get_query("vouchers.list")
    xml = query.build(query.validate_params({"company": "Acme", **VOUCHER_PARAMS}))

    assert "<FILTER>TFDateFilter</FILTER>" in xml
    assert '$Date &gt;= $$Date:&quot;20250701&quot;' in xml
    assert '$Date &lt;= $$Date:&quot;20250731&quot;' in xml


def test_voucher_date_filter_uses_an_unambiguous_literal():
    """Not `1-Jul-2025` (depends on Tally's language) nor `01-07-2025` (ambiguous)."""
    query = get_query("vouchers.list")
    xml = query.build(query.validate_params({"company": "Acme", **VOUCHER_PARAMS}))

    assert "Jul" not in xml
    assert "01-07-2025" not in xml


def test_voucher_date_filter_survives_alongside_the_other_filters():
    """Tally ANDs multiple <FILTER>s; a delta sync needs both to apply."""
    query = get_query("vouchers.list")
    xml = query.build(
        query.validate_params(
            {
                "company": "Acme",
                **VOUCHER_PARAMS,
                "alter_id_min": 4100,
                "voucher_type": "Sales",
            }
        )
    )

    assert "<FILTER>TFDateFilter</FILTER>" in xml
    assert "<FILTER>TFAlterIdFilter</FILTER>" in xml
    assert "<FILTER>TFVoucherTypeFilter</FILTER>" in xml


def test_voucher_alter_ids_map(fixture_xml):
    """The cursor an incremental sync advances has to survive the mapper."""
    vouchers = run("vouchers.list", fixture_xml("vouchers_daybook"), **VOUCHER_PARAMS)
    assert [v.alter_id for v in vouchers] == [4101, 4102, 4130, 4090]
    assert vouchers[0].master_id == 511


def test_voucher_alter_id_filter_is_opt_in():
    """A backfill must never carry the filter, or it returns an empty window."""
    query = get_query("vouchers.list")
    xml = query.build(query.validate_params({"company": "Acme", **VOUCHER_PARAMS}))
    assert "TFAlterIdFilter" not in xml
    assert "AlterID" not in xml


def test_voucher_envelope_does_not_ask_for_alter_ids():
    """Live Tally sends MASTERID unasked, and a crashed Tally costs a shop its day.

    A c0000005 access violation was seen on a real company while these two were
    in the FETCH list. That did not prove them guilty -- the window had grown at
    the same time -- but requesting a field the mapper already receives for free
    is risk with no upside, so the envelope stays as it was verified.
    """
    query = get_query("vouchers.list")
    xml = query.build(query.validate_params({"company": "Acme", **VOUCHER_PARAMS}))
    assert "<FETCH>MasterID</FETCH>" not in xml
    assert "<FETCH>AlterID</FETCH>" not in xml


def test_voucher_envelope_names_leaf_fields_not_whole_sub_collections():
    """The fix for TallyPrime dying with c0000005 mid-export.

    ``<FETCH>AllLedgerEntries</FETCH>`` does not mean "that wrapper's fields";
    it makes Tally build the entire object graph under every voucher line --
    GST rate details, VAT classifications, tax-object allocations, interest
    collections, old audit ids. Measured live: 6.83 MB vs 893 KB for identical
    parsed output over the same six months.
    """
    query = get_query("vouchers.list")
    xml = query.build(query.validate_params({"company": "Acme", **VOUCHER_PARAMS}))

    for wrapper in ("AllLedgerEntries", "LedgerEntries",
                    "AllInventoryEntries", "InventoryEntries"):
        assert f"<FETCH>{wrapper}</FETCH>" not in xml, (
            f"{wrapper} requested as a whole sub-collection again"
        )

    # Every field the mapper actually reads is still named explicitly.
    for leaf in (
        "AllLedgerEntries.LedgerName",
        "AllLedgerEntries.Amount",
        "AllLedgerEntries.IsDeemedPositive",
        "AllLedgerEntries.IsPartyLedger",
        "AllLedgerEntries.BillAllocations.Name",
        "AllLedgerEntries.CategoryAllocations.Category",
        "AllInventoryEntries.StockItemName",
        "AllInventoryEntries.BilledQty",
        "AllInventoryEntries.BatchAllocations.GodownName",
    ):
        assert f"<FETCH>{leaf}</FETCH>" in xml, f"{leaf} is no longer requested"


def test_voucher_envelope_keeps_both_entry_wrappers():
    """Accounting vouchers use LEDGERENTRIES, invoices ALLLEDGERENTRIES.

    The parser still falls back between them, so the request must still supply
    both -- it costs 2% more payload and keeps that fallback meaningful.
    """
    query = get_query("vouchers.list")
    xml = query.build(query.validate_params({"company": "Acme", **VOUCHER_PARAMS}))
    assert "<FETCH>LedgerEntries.LedgerName</FETCH>" in xml
    assert "<FETCH>InventoryEntries.StockItemName</FETCH>" in xml


def test_voucher_envelope_does_not_ask_for_persisted_view():
    """The mapper ignores PERSISTEDVIEW, so requesting it was pure weight."""
    query = get_query("vouchers.list")
    xml = query.build(query.validate_params({"company": "Acme", **VOUCHER_PARAMS}))
    assert "PersistedView" not in xml


def test_vouchers_without_inventory_ask_for_no_inventory_fields():
    """A ledger-only read must not drag the stock graph along."""
    query = get_query("vouchers.list")
    xml = query.build(
        query.validate_params(
            {"company": "Acme", **VOUCHER_PARAMS, "include_inventory": False}
        )
    )
    assert "InventoryEntries" not in xml
    assert "AllLedgerEntries.LedgerName" in xml


def test_voucher_alter_id_filter_narrows_to_changes():
    query = get_query("vouchers.list")
    xml = query.build(
        query.validate_params({"company": "Acme", **VOUCHER_PARAMS, "alter_id_min": 93117})
    )
    assert "<FILTER>TFAlterIdFilter</FILTER>" in xml
    assert "$AlterID &gt; 93117" in xml


def test_voucher_alter_id_filter_cannot_carry_tdl():
    """The value is interpolated into TDL that runs on a customer's machine."""
    query = get_query("vouchers.list")
    with pytest.raises(ValueError):
        query.validate_params(
            {"company": "Acme", **VOUCHER_PARAMS, "alter_id_min": '1 OR $$Sys:"x"'}
        )


# --------------------------------------------------------------------------
# Outstanding
# --------------------------------------------------------------------------


AS_OF = date(2025, 8, 20)


def test_outstanding_classifies_by_bill_side(fixture_xml):
    bills = run("outstanding.bills", fixture_xml("outstanding_bills"), as_of=AS_OF)

    # The zero-balance bill is dropped even though Tally returned it.
    assert len(bills) == 4

    by_name = {b.bill_name: b for b in bills}
    assert by_name["INV/2025/0388"].kind is OutstandingKind.RECEIVABLE
    assert by_name["PUR/220"].kind is OutstandingKind.PAYABLE

    # A customer advance sits in a debtor ledger but is genuinely a payable.
    advance = by_name["ADV/CUST/07"]
    assert advance.party_name == "Gupta Electronics"
    assert advance.kind is OutstandingKind.PAYABLE


def test_outstanding_kind_filter(fixture_xml):
    receivables = run(
        "outstanding.bills",
        fixture_xml("outstanding_bills"),
        as_of=AS_OF,
        kind="receivable",
    )
    assert {b.kind for b in receivables} == {OutstandingKind.RECEIVABLE}
    assert len(receivables) == 2


def test_credit_period_units_convert_to_days(fixture_xml):
    bills = {b.bill_name: b for b in run(
        "outstanding.bills", fixture_xml("outstanding_bills"), as_of=AS_OF
    )}
    assert bills["INV/2025/0388"].credit_period_days == 30
    assert bills["INV/2025/0412"].credit_period_days == 30  # "1 Month"
    assert bills["PUR/220"].credit_period_days == 45


def test_ageing_buckets(fixture_xml):
    bills = {b.bill_name: b for b in run(
        "outstanding.bills", fixture_xml("outstanding_bills"), as_of=AS_OF
    )}

    # Billed 1-Apr + 30 days -> due 1-May; 111 days overdue at 20-Aug.
    old = bills["INV/2025/0388"]
    assert old.due_date == date(2025, 5, 1)
    assert old.days_overdue(AS_OF) == 111
    assert old.ageing_bucket(AS_OF) == "91_180"

    # Billed 15-Jul + 30 days -> due 14-Aug; 6 days overdue.
    recent = bills["INV/2025/0412"]
    assert recent.ageing_bucket(AS_OF) == "1_30"

    # No credit period -> no due date -> never counted as overdue.
    advance = bills["ADV/CUST/07"]
    assert advance.due_date is None
    assert advance.ageing_bucket(AS_OF) == "not_due"


def test_outstanding_scopes_both_date_bounds():
    """Live Tally returns an empty bill collection unless SVFROMDATE is set too."""
    query = get_query("outstanding.bills")
    xml = query.build(query.validate_params({"company": "Acme", "as_of": AS_OF}))

    assert "<SVFROMDATE>" in xml
    assert f"<SVTODATE>{AS_OF:%Y%m%d}</SVTODATE>" in xml
    # A $$IsNonZero filter on this collection made Tally return nothing at all,
    # so settled bills are dropped in the mapper instead.
    assert "IsNonZero" not in xml


def test_outstanding_from_date_defaults_wide_enough_for_old_bills():
    query = get_query("outstanding.bills")
    params = query.validate_params({"company": "Acme", "as_of": AS_OF})
    assert params.from_date is not None
    assert params.from_date < AS_OF.replace(year=AS_OF.year - 1)


# --------------------------------------------------------------------------
# Read-only guarantee
# --------------------------------------------------------------------------


@pytest.mark.parametrize("entry", registry_manifest())
def test_no_query_can_mutate_tally(entry):
    """MVP is read-only; a stray Import envelope would be a silent write path."""
    query = get_query(entry["name"])
    params = {"company": "Acme"}
    if entry["name"] == "vouchers.list":
        params |= VOUCHER_PARAMS
    if entry["name"] == "outstanding.bills":
        params["as_of"] = AS_OF

    xml = query.build(query.validate_params(params))
    assert "<TALLYREQUEST>Export</TALLYREQUEST>" in xml
    assert "Import" not in xml
    assert 'ISMODIFY="Yes"' not in xml
