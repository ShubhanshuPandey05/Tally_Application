"""Creating a voucher in TallyPrime.

The XML shape here was not inferred. It was read back out of a live TallyPrime
on 2026-09-17 by exporting the Day Book as ``TALLYREQUEST=Export / TYPE=Data``,
which returns vouchers in exactly the form Tally imports them::

    <VOUCHER VCHTYPE="Receipt" ACTION="Create" OBJVIEW="Accounting Voucher View">
      <DATE>20260331</DATE>
      <VOUCHERTYPENAME>Receipt</VOUCHERTYPENAME>
      <PARTYLEDGERNAME>HDFC BANK LTD</PARTYLEDGERNAME>
      <ISOPTIONAL>No</ISOPTIONAL>
      <ALLLEDGERENTRIES.LIST>
        <LEDGERNAME>HDFC BANK LTD</LEDGERNAME>
        <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
        <AMOUNT>-1.00</AMOUNT>
      </ALLLEDGERENTRIES.LIST>
    </VOUCHER>

Tally's own export pads every voucher with several hundred empty tags for
features nobody uses. None of them are required on import, and naming only the
leaves keeps a write small for the same reason it keeps a read small.

Two things in that sample decide the whole builder:

* ``AMOUNT`` is **negative for a debit** and ``ISDEEMEDPOSITIVE`` is ``Yes`` for
  the same line. Both are sent, and they must agree: Tally reads both, and an
  entry whose sign and flag disagree posts backwards with no error.
* ``ISOPTIONAL`` is a first-class voucher field. Setting it to ``Yes`` is what
  makes this whole feature safe — the voucher is recorded in full and excluded
  from every balance, stock figure and report until somebody opens it in
  TallyPrime and un-marks it.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import ClassVar
from xml.etree import ElementTree as ET

from pydantic import Field

from ...domain.masters import VoucherTypeKind
from ...domain.writes import (
    DraftInventoryEntry,
    DraftLedgerEntry,
    VoucherDraft,
    VoucherPosted,
)
from ..codec import first_text
from ..envelope import build_import_envelope, tag, tally_date, xml_escape
from ..errors import TallyResponseError
from ..mutation import MutationParams, TallyMutation, register_mutation

#: The registered name of the one write this product performs. Exported so
#: callers name it once rather than spelling the string at each call site.
CREATE_VOUCHER = "voucher.create"

#: The canonical voucher type name every company has, per kind. A customer who
#: has renamed theirs ("Tax Invoice") sends ``voucher_type_name`` instead; this
#: is only the fallback, and it is a fallback that always exists.
_CANONICAL_TYPE: dict[VoucherTypeKind, str] = {
    VoucherTypeKind.RECEIPT: "Receipt",
    VoucherTypeKind.PAYMENT: "Payment",
    VoucherTypeKind.SALES: "Sales",
    VoucherTypeKind.SALES_ORDER: "Sales Order",
    VoucherTypeKind.PURCHASE_ORDER: "Purchase Order",
}

ACCOUNTING_VIEW = "Accounting Voucher View"
INVOICE_VIEW = "Invoice Voucher View"

#: Which of Tally's entry screens the voucher belongs to. ``OBJVIEW`` is how
#: Tally decides whether to expect accounting lines, stock lines or both.
#:
#: Orders are entered on the invoice screen too. Verified live 2026-09-19 on
#: "D.D Enterprises": a sales order and a purchase order entered by hand in
#: TallyPrime both export as ``Invoice Voucher View``, and there is no separate
#: order view -- the "Order Voucher View" this used to send was a guess.
_VIEW: dict[VoucherTypeKind, str] = {
    VoucherTypeKind.RECEIPT: ACCOUNTING_VIEW,
    VoucherTypeKind.PAYMENT: ACCOUNTING_VIEW,
    VoucherTypeKind.SALES: INVOICE_VIEW,
    VoucherTypeKind.SALES_ORDER: INVOICE_VIEW,
    VoucherTypeKind.PURCHASE_ORDER: INVOICE_VIEW,
}

#: Where an order's goods come from or go to, and which batch, when the app
#: does not say. "Any" is Tally's own word for "not decided yet": an order is a
#: promise about goods, and the godown is settled when they actually move.
ANY = "Any"

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _order_date(value: date) -> str:
    """``31-Mar-26``: the form Tally exports an order's due date in, and the
    one verified to import. Built by hand rather than with ``%b`` so the
    machine's locale cannot change the month's name."""
    return f"{value.day}-{_MONTHS[value.month - 1]}-{value:%y}"


def _view_for(draft: VoucherDraft) -> str:
    """Which entry screen this particular voucher belongs on.

    A sale with stock lines is an invoice; a sale that is just a figure is an
    accounting entry, and telling Tally otherwise gets it refused.

    Verified live on 2026-09-17 against "D.D Enterprises": the same sale sent
    as ``Invoice Voucher View`` came back ``CREATED 0, EXCEPTIONS 1`` with no
    ``<LINEERROR>`` to explain itself, and as ``Accounting Voucher View`` came
    back ``CREATED 1``. A silent refusal is the worst kind, so this is decided
    from the draft rather than from the kind alone.
    """
    if draft.kind is VoucherTypeKind.SALES and not draft.inventory_entries:
        return ACCOUNTING_VIEW
    return _VIEW[draft.kind]


def _amount(value: Decimal) -> str:
    """Render an amount the way Tally writes it: plain, two places, signed."""
    return f"{value:.2f}"


def _quantity(value: Decimal, unit: str | None) -> str:
    """Tally's quantity literal — ``10.00 Nos``, or bare when there is no unit."""
    rendered = f"{value:.2f}"
    return f"{rendered} {unit}" if unit else rendered


def _rate(value: Decimal, unit: str | None) -> str:
    """Tally's rate literal — ``100.00/Nos``, which is not the quantity form."""
    rendered = f"{value:.2f}"
    return f"{rendered}/{unit}" if unit else rendered


def _flag(value: bool) -> str:
    return "Yes" if value else "No"


def _ledger_entry_xml(entry: DraftLedgerEntry) -> str:
    parts = [
        tag("LEDGERNAME", entry.ledger_name),
        tag("ISDEEMEDPOSITIVE", _flag(entry.is_deemed_positive)),
        tag("AMOUNT", _amount(entry.amount)),
    ]
    for bill in entry.bill_references:
        parts.append(
            "<BILLALLOCATIONS.LIST>"
            f"{tag('NAME', bill.name)}"
            f"{tag('BILLTYPE', bill.method)}"
            f"{tag('AMOUNT', _amount(bill.amount))}"
            "</BILLALLOCATIONS.LIST>"
        )
    return f"<ALLLEDGERENTRIES.LIST>{''.join(parts)}</ALLLEDGERENTRIES.LIST>"


def _inventory_entry_xml(
    entry: DraftInventoryEntry,
    *,
    positive: bool,
    order: tuple[str, date] | None = None,
) -> str:
    quantity = _quantity(entry.quantity, entry.unit)
    # A debit line -- goods coming in on a purchase order -- carries a negative
    # amount, like every other debit in Tally's XML. Tally's own purchase order
    # exports its stock line as ``ISDEEMEDPOSITIVE Yes`` with ``-59375.00``.
    amount = _amount(-entry.amount if positive else entry.amount)
    parts = [
        tag("STOCKITEMNAME", entry.item_name),
        tag("ISDEEMEDPOSITIVE", _flag(positive)),
        # Both are sent and both are needed. Tally bills against BILLEDQTY and
        # moves stock against ACTUALQTY; a line that sets only one is how an
        # invoice goes out for goods that never left the godown.
        tag("ACTUALQTY", quantity),
        tag("BILLEDQTY", quantity),
        tag("AMOUNT", amount),
    ]
    if entry.rate is not None:
        parts.insert(2, tag("RATE", _rate(entry.rate, entry.unit)))
    if entry.ledger_name:
        parts.append(
            "<ACCOUNTINGALLOCATIONS.LIST>"
            f"{tag('LEDGERNAME', entry.ledger_name)}"
            f"{tag('ISDEEMEDPOSITIVE', _flag(positive))}"
            f"{tag('AMOUNT', amount)}"
            "</ACCOUNTINGALLOCATIONS.LIST>"
        )
    if order is not None:
        # The part of an order Tally will not do without. Every line names its
        # order number and due date inside a batch allocation; without them
        # Tally refuses the whole voucher as "Bad Order Number in Voucher!".
        # Ten variations were refused before one hand-entered order was
        # exported and copied -- see :data:`_VIEW`.
        number, due = order
        parts.append(
            "<BATCHALLOCATIONS.LIST>"
            f"{tag('GODOWNNAME', entry.godown or ANY)}"
            f"{tag('BATCHNAME', ANY)}"
            f"{tag('ORDERNO', number)}"
            f"{tag('ORDERDUEDATE', _order_date(due))}"
            f"{tag('AMOUNT', amount)}"
            f"{tag('ACTUALQTY', quantity)}"
            f"{tag('BILLEDQTY', quantity)}"
            "</BATCHALLOCATIONS.LIST>"
        )
    elif entry.godown:
        parts.append(
            "<BATCHALLOCATIONS.LIST>"
            f"{tag('GODOWNNAME', entry.godown)}"
            f"{tag('BATCHNAME', 'Primary Batch')}"
            f"{tag('ACTUALQTY', quantity)}"
            f"{tag('BILLEDQTY', quantity)}"
            f"{tag('AMOUNT', _amount(entry.amount))}"
            "</BATCHALLOCATIONS.LIST>"
        )
    return f"<ALLINVENTORYENTRIES.LIST>{''.join(parts)}</ALLINVENTORYENTRIES.LIST>"


def build_voucher_xml(draft: VoucherDraft) -> str:
    """Render one ``<VOUCHER>`` message body for an import."""
    type_name = draft.voucher_type_name or _CANONICAL_TYPE[draft.kind]
    view = _view_for(draft)

    parts = [
        tag("DATE", tally_date(draft.date)),
        # Tally falls back to DATE when this is absent, but only sometimes, and
        # a voucher whose effective date drifted lands in a different period
        # than the one the person chose on their phone.
        tag("EFFECTIVEDATE", tally_date(draft.date)),
        tag("VOUCHERTYPENAME", type_name),
        tag("ISOPTIONAL", _flag(draft.optional)),
        # Said twice on purpose. PERSISTEDVIEW is what Tally's own export
        # carries, and OBJVIEW on the attribute is what the importer reads;
        # setting only one leaves the voucher openable on the wrong screen.
        tag("PERSISTEDVIEW", view),
    ]
    if draft.voucher_number:
        parts.append(tag("VOUCHERNUMBER", draft.voucher_number))
    if draft.party_name:
        parts.append(tag("PARTYLEDGERNAME", draft.party_name))
        parts.append(tag("PARTYNAME", draft.party_name))
    if draft.reference:
        parts.append(tag("REFERENCE", draft.reference))
    if draft.narration:
        parts.append(tag("NARRATION", draft.narration))

    parts.extend(_ledger_entry_xml(entry) for entry in draft.ledger_entries)

    # On a sale the goods leave, so the stock line is an outward movement; on a
    # purchase order they are coming in. Getting this backwards moves stock the
    # wrong way, which no amount of checking the rupee total would reveal.
    positive = draft.kind is VoucherTypeKind.PURCHASE_ORDER
    order = (
        (draft.order_number, draft.order_due_date or draft.date)
        if draft.order_number
        else None
    )
    parts.extend(
        _inventory_entry_xml(entry, positive=positive, order=order)
        for entry in draft.inventory_entries
    )

    attrs = f'VCHTYPE="{xml_escape(type_name)}" ACTION="Create" OBJVIEW="{xml_escape(view)}"'
    return f"<VOUCHER {attrs}>{''.join(parts)}</VOUCHER>"


class CreateVoucherParams(MutationParams):
    draft: VoucherDraft
    #: Set by the connector from its own settings, never by the phone. The
    #: machine standing next to the till decides whether entries arrive needing
    #: approval, because that is a bookkeeping policy for the business rather
    #: than a choice for whoever is filling in the form.
    force_optional: bool = Field(default=True)


@register_mutation
class CreateVoucherMutation(TallyMutation[CreateVoucherParams, VoucherPosted]):
    """Post one voucher into a company's books."""

    name: ClassVar[str] = CREATE_VOUCHER
    version: ClassVar[int] = 1
    params_model: ClassVar[type[CreateVoucherParams]] = CreateVoucherParams

    def build(self, params: CreateVoucherParams) -> str:
        draft = params.draft
        # The connector's policy can only ever make an entry *more* cautious.
        # A phone asking for a regular entry on a machine set to optional gets
        # an optional one; the reverse is not expressible, so a compromised or
        # buggy client cannot post straight into somebody's books.
        if params.force_optional and not draft.optional:
            draft = draft.model_copy(update={"optional": True})

        if not draft.is_balanced:
            out_by = sum(e.amount for e in draft.ledger_entries) + draft.allocated
            raise ValueError(
                f"a {draft.kind} must balance to zero; out by {out_by} "
                f"(ledgers {sum(e.amount for e in draft.ledger_entries)}, "
                f"stock allocations {draft.allocated})"
            )

        return build_import_envelope(
            request_id="Vouchers",
            company=params.company,
            messages=[build_voucher_xml(draft)],
        )

    def parse(self, root: ET.Element, params: CreateVoucherParams) -> VoucherPosted:
        result = root.find(".//IMPORTRESULT")
        if result is None:
            raise TallyResponseError("import reply carried no <IMPORTRESULT>")

        def count(name: str) -> int:
            raw = first_text(result, name)
            try:
                return int((raw or "0").strip() or 0)
            except ValueError:
                return 0

        created = count("CREATED")
        errors = count("ERRORS")
        exceptions = count("EXCEPTIONS")

        # LASTVCHID is 0 when nothing was written, which is not an id. Reporting
        # it as one would give the phone a handle that resolves to nothing.
        voucher_id = count("LASTVCHID") or None

        # A voucher Tally can name a fault with ("Ledger 'Ram Traders' does not
        # exist") never reaches this method: Tally reports those as an in-band
        # <LINEERROR> and `parse_xml` raises TallyResponseError carrying its
        # exact words, which is the message the person on the phone should see.
        # What lands here is the quieter failure -- a reply that parsed, with
        # nothing created and no complaint attached.
        message: str | None = None
        if errors or exceptions:
            message = "TallyPrime refused the entry without giving a reason."
        elif created == 0:
            message = "TallyPrime accepted the request but recorded nothing."

        return VoucherPosted(
            created=created,
            altered=count("ALTERED"),
            ignored=count("IGNORED"),
            errors=errors,
            exceptions=exceptions,
            voucher_id=voucher_id,
            message=message,
            optional=params.force_optional or params.draft.optional,
        )
