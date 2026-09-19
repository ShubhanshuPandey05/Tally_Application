"""The write path: what reaches Tally, and what must never reach it.

The XML assertions here are checked against a real TallyPrime export taken on
2026-09-17 (see ``tally/mutations/vouchers.py``), so a change that breaks one of
them is a change that stops Tally accepting the voucher.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from tally_core.domain.masters import VoucherTypeKind
from tally_core.domain.writes import (
    BillAllocation,
    DraftInventoryEntry,
    DraftLedgerEntry,
    LedgerDraft,
    MasterCreated,
    StockItemDraft,
    VoucherDraft,
)
from tally_core.tally import get_mutation, get_query, mutation_manifest
from tally_core.tally.codec import parse_xml
from tally_core.tally.errors import TallyResponseError, UnknownQueryError

FIXTURES = Path(__file__).parent / "fixtures"

IMPORT_OK = """<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>
<BODY><DATA><IMPORTRESULT>
<CREATED>1</CREATED><ALTERED>0</ALTERED><DELETED>0</DELETED>
<LASTVCHID>4821</LASTVCHID><IGNORED>0</IGNORED><ERRORS>0</ERRORS>
<EXCEPTIONS>0</EXCEPTIONS></IMPORTRESULT></DATA></BODY></ENVELOPE>"""

IMPORT_NOTHING = """<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER>
<BODY><DATA><IMPORTRESULT>
<CREATED>0</CREATED><ALTERED>0</ALTERED><DELETED>0</DELETED>
<LASTVCHID>0</LASTVCHID><IGNORED>0</IGNORED><ERRORS>0</ERRORS>
<EXCEPTIONS>0</EXCEPTIONS></IMPORTRESULT></DATA></BODY></ENVELOPE>"""


def receipt(**overrides: object) -> VoucherDraft:
    base: dict[str, object] = {
        "kind": VoucherTypeKind.RECEIPT,
        "date": date(2026, 9, 17),
        "party_name": "Ram & Sons",
        "ledger_entries": [
            DraftLedgerEntry(
                ledger_name="Cash", amount=Decimal("-5000"), is_deemed_positive=True
            ),
            DraftLedgerEntry(
                ledger_name="Ram & Sons",
                amount=Decimal("5000"),
                is_deemed_positive=False,
                bill_references=[
                    BillAllocation(name="INV-001", amount=Decimal("5000")),
                ],
            ),
        ],
    }
    base.update(overrides)
    return VoucherDraft(**base)  # type: ignore[arg-type]


def build(draft: VoucherDraft, *, force_optional: bool = True) -> str:
    mutation = get_mutation("voucher.create")
    params = mutation.validate_params(
        {
            "company": "D.D Enterprises",
            "draft": draft.model_dump(mode="json"),
            "force_optional": force_optional,
        }
    )
    return mutation.build(params)


# -- the registries stay apart ---------------------------------------------


def test_a_mutation_is_not_reachable_through_the_read_registry() -> None:
    """The one structural guarantee behind the read-only rule.

    If this ever passes, a read job naming a mutation would post a voucher.
    """
    with pytest.raises(UnknownQueryError):
        get_query("voucher.create")


def test_the_manifest_names_the_writes_this_build_supports() -> None:
    assert {"name": "voucher.create", "version": 1} in mutation_manifest()


def test_a_write_is_never_retried_on_a_timeout() -> None:
    # Tally can finish an import and lose the reply, so a retry books the
    # voucher twice with no way to tell afterwards which attempt landed.
    assert get_mutation("voucher.create").retry_on_timeout is False


# -- what reaches Tally ------------------------------------------------------


def test_an_entry_is_optional_by_default() -> None:
    assert "<ISOPTIONAL>Yes</ISOPTIONAL>" in build(receipt())


def test_the_connector_can_force_an_entry_to_be_optional() -> None:
    # The phone asked for a regular entry; this machine is set to optional.
    # Cautious wins.
    assert "<ISOPTIONAL>Yes</ISOPTIONAL>" in build(
        receipt(optional=False), force_optional=True
    )


def test_a_regular_entry_is_only_possible_when_the_machine_allows_it() -> None:
    xml = build(receipt(optional=False), force_optional=False)
    assert "<ISOPTIONAL>No</ISOPTIONAL>" in xml


def test_a_phone_cannot_make_an_entry_less_cautious_than_the_machine() -> None:
    """The asymmetry that makes the setting a policy rather than a suggestion.

    A client that asks for a regular entry on a machine set to optional gets an
    optional one. There is no payload that reverses this.
    """
    assert "<ISOPTIONAL>Yes</ISOPTIONAL>" in build(
        receipt(optional=False), force_optional=True
    )
    assert "<ISOPTIONAL>Yes</ISOPTIONAL>" in build(
        receipt(optional=True), force_optional=False
    )


def test_the_envelope_is_an_import_pinned_to_one_company() -> None:
    xml = build(receipt())
    assert "<TALLYREQUEST>Import</TALLYREQUEST>" in xml
    # Unpinned, Tally imports into whichever company is active in the UI.
    assert "<SVCURRENTCOMPANY>D.D Enterprises</SVCURRENTCOMPANY>" in xml


def test_a_company_name_with_an_ampersand_survives() -> None:
    # "Ram & Sons" is an ordinary Indian trade name and raw & is a rejected
    # envelope, not a mangled one.
    assert "<PARTYLEDGERNAME>Ram &amp; Sons</PARTYLEDGERNAME>" in build(receipt())
    assert "<PARTYLEDGERNAME>Ram & Sons</PARTYLEDGERNAME>" not in build(receipt())


def test_the_sign_and_the_flag_agree_on_every_line() -> None:
    """Tally reads both, and an entry where they disagree posts backwards.

    Negative is a debit and carries ISDEEMEDPOSITIVE=Yes -- confirmed against a
    live export where a bank receipt of -1.00 was flagged Yes.
    """
    xml = build(receipt())
    cash = xml[xml.index("<LEDGERNAME>Cash</LEDGERNAME>") :]
    cash = cash[: cash.index("</ALLLEDGERENTRIES.LIST>")]
    assert "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>" in cash
    assert "<AMOUNT>-5000.00</AMOUNT>" in cash


def test_a_receipt_carries_the_bill_it_settles() -> None:
    xml = build(receipt())
    assert "<BILLTYPE>Agst Ref</BILLTYPE>" in xml
    assert "<NAME>INV-001</NAME>" in xml


def test_an_unbalanced_entry_never_reaches_tally() -> None:
    lopsided = receipt(
        ledger_entries=[
            DraftLedgerEntry(
                ledger_name="Cash", amount=Decimal("-5000"), is_deemed_positive=True
            )
        ]
    )
    with pytest.raises(ValueError, match="balance"):
        build(lopsided)


def test_a_sale_carries_its_stock_lines_and_moves_them_outward() -> None:
    sale = VoucherDraft(
        kind=VoucherTypeKind.SALES,
        date=date(2026, 9, 17),
        party_name="Ram & Sons",
        # Only the party side here. The sales credit lives in the stock line's
        # accounting allocation -- putting it in both places credits the ledger
        # twice for one invoice.
        ledger_entries=[
            DraftLedgerEntry(
                ledger_name="Ram & Sons", amount=Decimal("-1180"), is_deemed_positive=True
            ),
        ],
        inventory_entries=[
            DraftInventoryEntry(
                item_name="Sugar 1kg",
                quantity=Decimal("10"),
                rate=Decimal("118"),
                amount=Decimal("1180"),
                unit="Nos",
                ledger_name="Sales",
            )
        ],
    )
    xml = build(sale)
    assert 'OBJVIEW="Invoice Voucher View"' in xml
    assert "<STOCKITEMNAME>Sugar 1kg</STOCKITEMNAME>" in xml
    # Both, always: Tally bills against one and moves stock against the other.
    assert "<ACTUALQTY>10.00 Nos</ACTUALQTY>" in xml
    assert "<BILLEDQTY>10.00 Nos</BILLEDQTY>" in xml
    # The rate literal is not the quantity literal.
    assert "<RATE>118.00/Nos</RATE>" in xml
    # Goods leaving on a sale.
    assert "<ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>" in xml


def test_an_order_needs_no_accounting_entries() -> None:
    """A sales order moves no money, so an empty ledger side is correct."""
    order = VoucherDraft(
        kind=VoucherTypeKind.SALES_ORDER,
        date=date(2026, 9, 17),
        party_name="Ram & Sons",
        inventory_entries=[
            DraftInventoryEntry(
                item_name="Sugar 1kg",
                quantity=Decimal("10"),
                amount=Decimal("1180"),
                unit="Nos",
            )
        ],
    )
    assert order.is_balanced
    xml = build(order)
    assert '<VOUCHER VCHTYPE="Sales Order"' in xml


def test_a_purchase_order_brings_stock_inward() -> None:
    order = VoucherDraft(
        kind=VoucherTypeKind.PURCHASE_ORDER,
        date=date(2026, 9, 17),
        party_name="Wholesaler",
        inventory_entries=[
            DraftInventoryEntry(
                item_name="Sugar 1kg", quantity=Decimal("10"), amount=Decimal("1000")
            )
        ],
    )
    assert "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>" in build(order)


def test_a_kind_the_app_may_not_create_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be created"):
        VoucherDraft(kind=VoucherTypeKind.JOURNAL, date=date(2026, 9, 17))


def test_a_customers_own_voucher_type_name_is_used_when_given() -> None:
    xml = build(receipt(voucher_type_name="Counter Receipt"))
    assert "<VOUCHERTYPENAME>Counter Receipt</VOUCHERTYPENAME>" in xml
    assert 'VCHTYPE="Counter Receipt"' in xml


# -- what comes back ---------------------------------------------------------


def parse(raw: str, *, force_optional: bool = True) -> object:
    mutation = get_mutation("voucher.create")
    params = mutation.validate_params(
        {
            "company": "D.D Enterprises",
            "draft": receipt().model_dump(mode="json"),
            "force_optional": force_optional,
        }
    )
    return mutation.parse(parse_xml(raw), params)


def test_a_created_voucher_reports_its_id() -> None:
    posted = parse(IMPORT_OK)
    assert posted.ok  # type: ignore[attr-defined]
    assert posted.created == 1  # type: ignore[attr-defined]
    assert posted.voucher_id == 4821  # type: ignore[attr-defined]
    assert posted.optional is True  # type: ignore[attr-defined]


def test_creating_nothing_is_not_reported_as_success() -> None:
    posted = parse(IMPORT_NOTHING)
    assert not posted.ok  # type: ignore[attr-defined]
    # LASTVCHID of 0 is not an id, and handing it back would give the phone a
    # handle that resolves to nothing.
    assert posted.voucher_id is None  # type: ignore[attr-defined]
    assert posted.message is not None  # type: ignore[attr-defined]


def test_tallys_own_complaint_reaches_the_caller() -> None:
    """Tally reports a refused voucher in-band, with HTTP 200.

    ``parse_xml`` raises on it, so the words the person needs ("Ledger ... does
    not exist") arrive as the error rather than being flattened into a count.

    The fixture is a real reply, recorded live on 2026-09-17 by importing a
    voucher naming ledgers that do not exist. Tally answered CREATED 0,
    ERRORS 0, EXCEPTIONS 1 -- note that it counted the refusal under
    *exceptions* rather than errors, which is why both are checked everywhere
    a success is decided.
    """
    refused = (FIXTURES / "import_refused.xml").read_text(encoding="utf-8", errors="replace")

    with pytest.raises(TallyResponseError, match="does not exist"):
        parse(refused)


# -- the property that ties the feature together -----------------------------


def test_an_entry_awaiting_approval_reaches_no_total() -> None:
    """The reason optional entries are safe, asserted rather than assumed.

    An entry created from a phone shows in the Day Book and changes no figure,
    in TallyFlow exactly as in TallyPrime. If ``is_effective`` ever stopped
    excluding optional vouchers, the app would report a sale that Tally itself
    does not count -- and the two would disagree with nothing on screen saying
    why.
    """
    from tally_core.domain.transactions import Voucher

    waiting = Voucher(
        voucher_type="Receipt",
        date=date(2026, 9, 17),
        is_optional=True,
    )
    approved = waiting.model_copy(update={"is_optional": False})

    assert waiting.is_effective is False
    assert approved.is_effective is True


def test_an_invoice_credits_its_sales_ledger_exactly_once() -> None:
    """The stock line carries the credit; a ledger entry must not repeat it.

    Sending both credits the sales ledger twice for one invoice. Tally either
    refuses the voucher or books double the sale, and nothing on a screen
    afterwards would say which happened.
    """
    invoice = VoucherDraft(
        kind=VoucherTypeKind.SALES,
        date=date(2026, 9, 17),
        party_name="Ram & Sons",
        ledger_entries=[
            DraftLedgerEntry(
                ledger_name="Ram & Sons",
                amount=Decimal("-1180"),
                is_deemed_positive=True,
            )
        ],
        inventory_entries=[
            DraftInventoryEntry(
                item_name="Sugar 1kg",
                quantity=Decimal("10"),
                rate=Decimal("118"),
                amount=Decimal("1180"),
                unit="Nos",
                ledger_name="Sales",
            )
        ],
    )

    # Balanced even though the ledger side alone is not: the stock line's
    # accounting allocation is the other half.
    assert invoice.is_balanced
    assert invoice.allocated == Decimal("1180")

    xml = build(invoice)
    assert xml.count("<LEDGERNAME>Sales</LEDGERNAME>") == 1


def test_a_stock_line_with_no_ledger_is_not_counted_as_a_side() -> None:
    """Inventory movement with no accounting behind it -- a delivery note's
    shape. Counting it would make every such voucher look unbalanced."""
    draft = VoucherDraft(
        kind=VoucherTypeKind.SALES,
        date=date(2026, 9, 17),
        party_name="Ram & Sons",
        ledger_entries=[
            DraftLedgerEntry(
                ledger_name="Ram & Sons", amount=Decimal("-100"), is_deemed_positive=True
            ),
            DraftLedgerEntry(
                ledger_name="Sales", amount=Decimal("100"), is_deemed_positive=False
            ),
        ],
        inventory_entries=[
            DraftInventoryEntry(
                item_name="Sugar 1kg", quantity=Decimal("1"), amount=Decimal("100")
            )
        ],
    )

    assert draft.allocated == Decimal("0")
    assert draft.is_balanced


# -- creating a party or an item ---------------------------------------------


def build_master(name: str, draft, company: str = "D.D Enterprises") -> str:
    mutation = get_mutation(name)
    return mutation.build(
        mutation.validate_params(
            {"company": company, "draft": draft.model_dump(mode="json")}
        )
    )


def test_a_master_import_uses_the_all_masters_report() -> None:
    """A different header form from a voucher, verified live on 2026-09-17.

    Tally parsed the <LEDGER> and got as far as validating its parent group
    before refusing, which is how far the structure has to be right to reach.
    """
    xml = build_master("master.ledger", LedgerDraft(name="Ram & Sons"))

    assert "<TALLYREQUEST>Import Data</TALLYREQUEST>" in xml
    assert "<REPORTNAME>All Masters</REPORTNAME>" in xml
    assert "<SVCURRENTCOMPANY>D.D Enterprises</SVCURRENTCOMPANY>" in xml


def test_a_customer_lands_under_sundry_debtors() -> None:
    xml = build_master("master.ledger", LedgerDraft(name="Ram & Sons", role="customer"))

    assert "<PARENT>Sundry Debtors</PARENT>" in xml


def test_a_supplier_lands_under_sundry_creditors() -> None:
    xml = build_master("master.ledger", LedgerDraft(name="Wholesaler", role="supplier"))

    assert "<PARENT>Sundry Creditors</PARENT>" in xml


def test_a_group_cannot_be_named_directly() -> None:
    """Which side of the balance sheet a party lands on is chosen from a role.

    Free text here would let a customer be filed under Indirect Expenses with
    nothing to stop it.
    """
    with pytest.raises(ValueError, match="role must be one of"):
        LedgerDraft(name="Ram & Sons", role="Indirect Expenses")


def test_a_party_is_created_with_bill_tracking_on() -> None:
    # Without it the ledger cannot carry the outstanding bills that half this
    # product reports on.
    assert "<ISBILLWISEON>Yes</ISBILLWISEON>" in build_master(
        "master.ledger", LedgerDraft(name="Ram & Sons")
    )


def test_a_created_party_carries_no_money() -> None:
    """An opening balance would move a trial balance on the strength of a typo.

    Sent as an explicit zero rather than omitted: Tally is being told the
    ledger opens at nothing, which is not the same as leaving it to decide.
    """
    xml = build_master("master.ledger", LedgerDraft(name="Ram & Sons"))

    assert "<OPENINGBALANCE>0</OPENINGBALANCE>" in xml


def test_an_ampersand_in_a_party_name_survives() -> None:
    xml = build_master("master.ledger", LedgerDraft(name="Ram & Sons"))

    # Both places it appears: the attribute and the language name list.
    assert 'NAME="Ram &amp; Sons"' in xml
    assert "<NAME>Ram &amp; Sons</NAME>" in xml
    assert "Ram & Sons" not in xml


def test_a_stock_item_carries_its_unit_both_ways() -> None:
    """Tally reads one or the other depending on how old the company's data is,
    and a mismatch leaves an item whose quantities cannot be entered."""
    xml = build_master(
        "master.stock_item", StockItemDraft(name="Sugar 1kg", unit="Nos")
    )

    assert "<BASEUNITS>Nos</BASEUNITS>" in xml
    assert "<ADDITIONALUNITS>Nos</ADDITIONALUNITS>" in xml


def test_a_stock_item_without_a_unit_names_none() -> None:
    # Allowed by Tally, and fixable in one screen -- better than guessing at a
    # unit that may not exist in the company.
    xml = build_master("master.stock_item", StockItemDraft(name="Sugar 1kg"))

    assert "BASEUNITS" not in xml


def test_a_created_item_carries_no_quantity_or_rate() -> None:
    xml = build_master(
        "master.stock_item", StockItemDraft(name="Sugar 1kg", unit="Nos")
    )

    assert "OPENINGBALANCE" not in xml
    assert "OPENINGVALUE" not in xml
    assert "STANDARDPRICE" not in xml


def test_a_blank_master_name_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot be blank"):
        LedgerDraft(name="   ")
    with pytest.raises(ValueError, match="cannot be blank"):
        StockItemDraft(name="  ")


def test_a_master_import_must_name_its_company() -> None:
    # Unpinned, Tally imports into whichever company is active in the UI.
    with pytest.raises(ValueError, match="name its company"):
        build_master("master.ledger", LedgerDraft(name="Ram & Sons"), company="")


def test_a_master_that_already_existed_is_not_a_failure() -> None:
    """The caller asked for it to exist, and it does.

    Tally reports an existing master as ALTERED rather than CREATED, and
    treating that as an error would show a confusing refusal for the case where
    two people at a counter added the same customer.
    """
    assert MasterCreated(created=0, altered=1).ok is True
    assert MasterCreated(created=0, altered=0).ok is False
    assert MasterCreated(created=1, errors=1).ok is False
    assert MasterCreated(created=1, exceptions=1).ok is False


def test_a_sale_without_stock_lines_is_an_accounting_entry() -> None:
    """Verified live on 2026-09-17 against "D.D Enterprises".

    The same sale sent as an Invoice Voucher View came back CREATED 0,
    EXCEPTIONS 1 with no <LINEERROR> at all; as an Accounting Voucher View it
    came back CREATED 1. A silent refusal is the worst kind of failure, so the
    view is chosen from what is actually on the voucher.
    """
    plain = VoucherDraft(
        kind=VoucherTypeKind.SALES,
        date=date(2026, 3, 31),
        party_name="Ram & Sons",
        ledger_entries=[
            DraftLedgerEntry(
                ledger_name="Ram & Sons", amount=Decimal("-1180"), is_deemed_positive=True
            ),
            DraftLedgerEntry(
                ledger_name="Sales", amount=Decimal("1180"), is_deemed_positive=False
            ),
        ],
    )

    xml = build(plain)
    assert 'OBJVIEW="Accounting Voucher View"' in xml
    assert "Invoice Voucher View" not in xml


def test_a_sale_with_stock_lines_is_still_an_invoice() -> None:
    invoice = VoucherDraft(
        kind=VoucherTypeKind.SALES,
        date=date(2026, 3, 31),
        party_name="Ram & Sons",
        ledger_entries=[
            DraftLedgerEntry(
                ledger_name="Ram & Sons", amount=Decimal("-1180"), is_deemed_positive=True
            )
        ],
        inventory_entries=[
            DraftInventoryEntry(
                item_name="Sugar 1kg",
                quantity=Decimal("10"),
                amount=Decimal("1180"),
                ledger_name="Sales",
            )
        ],
    )

    assert 'OBJVIEW="Invoice Voucher View"' in build(invoice)


def test_an_order_is_built_the_way_tallyprime_exports_one():
    """Verified live 2026-09-19: this shape created a sales order and a
    purchase order in "D.D Enterprises" after ten guesses were refused as
    "Bad Order Number in Voucher!"."""
    from tally_core.tally.mutations.vouchers import build_voucher_xml

    draft = VoucherDraft(
        kind=VoucherTypeKind.PURCHASE_ORDER,
        date=date(2026, 3, 31),
        party_name="Wholesaler",
        reference="PO-17",
        order_number="PO-17",
        ledger_entries=[
            DraftLedgerEntry(
                ledger_name="Wholesaler", amount=Decimal("100"), is_deemed_positive=False
            )
        ],
        inventory_entries=[
            DraftInventoryEntry(
                item_name="Cloth Item 5",
                quantity=Decimal("1"),
                rate=Decimal("100"),
                amount=Decimal("100"),
                unit="Pcs",
                ledger_name="Gst Purchase",
            )
        ],
    )

    xml = build_voucher_xml(draft)

    assert 'OBJVIEW="Invoice Voucher View"' in xml
    # Every line carries the order number and due date, in a batch that is
    # "Any" godown and "Any" batch until the goods actually move.
    assert (
        "<BATCHALLOCATIONS.LIST><GODOWNNAME>Any</GODOWNNAME><BATCHNAME>Any</BATCHNAME>"
        "<ORDERNO>PO-17</ORDERNO><ORDERDUEDATE>31-Mar-26</ORDERDUEDATE>"
    ) in xml
    # Goods coming in are a debit: negative, on the line and its ledger.
    assert xml.count("<AMOUNT>-100.00</AMOUNT>") == 3
    assert "<LEDGERNAME>Gst Purchase</LEDGERNAME>" in xml
