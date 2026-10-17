"""Transaction queries: vouchers (day book) and bill-wise outstanding."""

from __future__ import annotations

from datetime import date, timedelta
from xml.etree import ElementTree as ET

from pydantic import model_validator

from ...domain.masters import VoucherTypeKind
from ...domain.money import Money, Side
from ...domain.transactions import (
    InventoryEntry,
    InvoiceDetails,
    LedgerEntry,
    OutstandingBill,
    OutstandingKind,
    Voucher,
    VoucherParty,
)
from ..codec import (
    find_text,
    first_text,
    parse_bool,
    parse_date,
    parse_float,
    parse_int,
    text_of,
)
from ..envelope import Collection, StaticVariables, build_export_envelope
from ..query import QueryParams, TallyQuery, register

#: Ledger and inventory lines live under different wrappers depending on the
#: voucher type: accounting-only vouchers use LEDGERENTRIES, invoices use
#: ALLLEDGERENTRIES.
#:
#: These are tried in order and the FIRST wrapper that yields entries wins.
#: Reading both double-counts: a live TallyPrime returns an invoice's lines
#: under *both* wrappers, so summing them reported every purchase at twice its
#: real value. Verified 2026-07-23.
_LEDGER_ENTRY_PATHS = ("./ALLLEDGERENTRIES.LIST", "./LEDGERENTRIES.LIST")
_INVENTORY_ENTRY_PATHS = (
    "./ALLINVENTORYENTRIES.LIST",
    "./INVENTORYENTRIES.LIST",
)

#: The leaf methods the mapper below actually reads out of each voucher line.
#:
#: These are named individually, as ``Wrapper.Method``, rather than by asking
#: for the wrapper alone. ``<FETCH>AllLedgerEntries</FETCH>`` is not a cheap
#: shorthand for "that wrapper's fields": it makes Tally materialise and
#: serialise the *entire* sub-object graph hanging off every line -- GST rate
#: details, VAT classifications, tax-object allocations, interest collections,
#: old audit entry ids -- none of which this module parses.
#:
#: Measured on a live TallyPrime (D.D Enterprises, six months of books): the
#: wrapper form returned 6.83 MB, the explicit form 893 KB for byte-identical
#: parsed output. That is 7.6x less for Tally to walk, allocate and encode.
#:
#: This matters beyond bandwidth. TallyPrime dies with ``c0000005 (Memory
#: Access Violation)`` while building these collections -- crash dumps taken on
#: this machine fault on a NULL read at offset 0x18 inside tally.exe, with our
#: own ``TFVouchers`` envelope still in process memory. That is a bug inside
#: Tally which no client can patch; the only lever this side of the wire is to
#: stop asking it to build the enormous object graph in the first place.
_LEDGER_ENTRY_FIELDS = (
    "LedgerName",
    "Amount",
    "IsDeemedPositive",
    "IsPartyLedger",
    "BillAllocations.Name",
    "CategoryAllocations.Category",
    # The rate a tax line was charged at, which Tally prints beside it.
    "RateOfInvoiceTax",
)
_INVENTORY_ENTRY_FIELDS = (
    "StockItemName",
    "Rate",
    "BilledQty",
    "ActualQty",
    "Amount",
    "BatchAllocations.GodownName",
    "BatchAllocations.BatchName",
    # HSN and GST rate as the invoice recorded them. These used to be looked up
    # on the stock master instead, and a shared invoice went out with an empty
    # HSN column: verified live 2026-10-12 on "D.D Enterprises", where sales
    # invoice 2025-26/01 carries ``GSTHSNNAME`` 621142 and an IGST rate of 12
    # on its own line. The two leaves of ``RateDetails`` are named for the same
    # reason as everything above -- the wrapper also drags in valuation types
    # and per-unit rates nothing reads.
    #
    # They are not free. Measured on those books (79 vouchers, 230 stock
    # lines, one financial year): 1.08 MB before, 1.10 MB with the HSN, 1.29 MB
    # with the rates, 1.39 MB with the invoice header below -- 29% in all. The
    # stock master cannot stand in for the rates: it held one for 30 items of
    # 144, where the invoice lines held one for 224 of 230.
    "GSTHSNName",
    "RateDetails.GSTRateDutyHead",
    "RateDetails.GSTRate",
)

#: What a printed invoice carries above its items: the buyer and consignee as
#: written on this voucher, and how the goods travelled. All plain fields of
#: the voucher itself, empty on anything that is not an invoice.
#:
#: Verified live 2026-10-12 against the same invoice, field by field against
#: Tally's own print of it. ``Address`` is the *consignee's* address and
#: ``BasicBuyerAddress`` the buyer's -- the two are the same on most bills,
#: which is exactly why reading the wrong one would go unnoticed.
_INVOICE_FIELDS = (
    "Address",
    "BasicBuyerName",
    "BasicBuyerAddress",
    "PartyGSTIN",
    "StateName",
    "PlaceOfSupply",
    "ConsigneeMailingName",
    "ConsigneeGSTIN",
    "ConsigneeStateName",
    "ReferenceDate",
    "BasicDueDateOfPymt",
    "BasicOrderRef",
    "BasicShipDocumentNo",
    "BasicShippedBy",
    "BasicFinalDestination",
    "BillOfLadingNo",
    "BillOfLadingDate",
    "BasicShipVesselNo",
    "BasicOrderTerms",
    # Order and delivery-note numbers sit in lists, because one invoice can
    # close several of each. The delivery note came back on three invoices in
    # the same books; no voucher there carries a buyer's order, so those two
    # names are from Tally's import format rather than from a live export.
    "InvoiceOrderList.BasicPurchaseOrderNo",
    "InvoiceOrderList.BasicOrderDate",
    "InvoiceDelNotes.BasicShipDeliveryNote",
    "InvoiceDelNotes.BasicShippingDate",
)

#: Both wrappers are still requested, for the same reason the parser still
#: reads both: accounting-only vouchers put their lines under LEDGERENTRIES and
#: invoices under ALLLEDGERENTRIES. Naming the leaves of both costs 2% more
#: payload than naming only the ``All`` ones and keeps the existing fallback
#: honest for voucher types this fix was not measured against.
_LEDGER_WRAPPERS = ("AllLedgerEntries", "LedgerEntries")
_INVENTORY_WRAPPERS = ("AllInventoryEntries", "InventoryEntries")


def _leaf_fetch(wrappers: tuple[str, ...], fields: tuple[str, ...]) -> list[str]:
    return [f"{wrapper}.{field}" for wrapper in wrappers for field in fields]


def _tally_date(value: date) -> str:
    """A date literal TDL will accept inside ``$$Date:"..."``.

    ``yyyymmdd`` deliberately, not ``1-Aug-2026``: the month-name form depends on
    Tally's interface language, and ``dd-mm-yyyy`` is indistinguishable from
    ``mm-dd-yyyy`` to anyone reading the envelope. All three were verified to
    filter identically on a live TallyPrime; only this one cannot be
    misinterpreted.
    """
    return f"{value:%Y%m%d}"


def _date_window_filter(from_date: date, to_date: date) -> str:
    """Restrict a Voucher collection to a date range.

    **``SVFROMDATE``/``SVTODATE`` do not do this.** They are still sent -- they
    set the period other parts of the response are computed against -- but a
    custom ``<COLLECTION><TYPE>Voucher</TYPE>`` ignores them for membership and
    yields the company's *current period* whatever the envelope asks for.
    Verified live 2026-08-09 against company "Bhtia Supermarket": asking for
    1--9 Aug, for the whole of April, and for a two-year span all returned the
    identical 21 vouchers spanning 2026-04-01 to 2026-08-09. With this filter
    the same three windows return 1, 15 and 21 vouchers, every one inside the
    window asked for.

    That silent widening is why this matters beyond correctness: the dashboard
    asks for about 40 days and the history sync asks for six months at a time,
    and both were being served the entire open financial year on every call --
    so the chunking that exists to keep exports small was doing nothing.

    **The window is tested twice, once without a date literal.** A TallyPrime
    in Educational mode accepts only the 1st, 2nd and last day of a month as a
    date, and ``$$Date:"20250430"`` there evaluates to *nothing* rather than to
    an error -- so the comparison above is false for every voucher and the
    window comes back empty. Verified live 2026-10-11 on "D.D Enterprises"
    (unlicensed, 79 vouchers): April returned 0 of its 24, a single day 0 of 5,
    and a history backfill had stored no vouchers at all while reporting
    success. Only windows that happened to start on a 1st and end on a 31st
    worked.

    The second test compares the voucher's own date as a number, ``yyyymmdd``,
    which parses no literal and so has nothing for the licence to refuse. On
    the same six windows it matched a count made outside Tally every time.
    The first test is kept beside it rather than replaced: it is the one
    proven on licensed installs, and joined by ``OR`` the pair can only ever
    return what one of them would have -- so a Tally that did not know the
    date functions would lose nothing it has today.
    """
    literal = (
        f'$Date >= $$Date:"{_tally_date(from_date)}" '
        f'AND $Date <= $$Date:"{_tally_date(to_date)}"'
    )
    as_number = "(($$YearOfDate:$Date * 10000) + ($$MonthOfDate:$Date * 100) + $$DayOfDate:$Date)"
    numeric = (
        f"{as_number} >= {_tally_date(from_date)} AND {as_number} <= {_tally_date(to_date)}"
    )
    return f"({literal}) OR ({numeric})"


class DateRangeParams(QueryParams):
    from_date: date
    to_date: date

    @model_validator(mode="after")
    def _check_order(self) -> DateRangeParams:
        if self.from_date > self.to_date:
            raise ValueError("from_date must not be after to_date")
        return self


# --------------------------------------------------------------------------
# Vouchers / Day Book
# --------------------------------------------------------------------------


class VoucherListParams(DateRangeParams):
    voucher_type: str | None = None
    include_inventory: bool = True
    #: Return only vouchers whose ``AlterID`` is greater than this -- everything
    #: created or edited since the sync that recorded it. This is what turns the
    #: daily read from "export the year again" into "export today's twenty
    #: edits". ``None`` means the whole window, which is what a first backfill
    #: and any Tally that does not report change ids both need.
    alter_id_min: int | None = None
    #: Return each voucher's identity and nothing else -- no ledger lines, no
    #: inventory lines. Only the deletion reconcile wants this: it compares the
    #: set of vouchers Tally still has against the set we stored, and the lines
    #: are irrelevant to that question.
    #:
    #: Measured live 2026-08-29 (D.D Enterprises, 17 months, 60 vouchers with
    #: 217 ledger and 218 inventory lines between them): 979,731 bytes with the
    #: lines, 74,357 without -- **13.2x**. Note where that saving is not:
    #: dropping only the inventory lines saved 1.5%. The cost is asking Tally to
    #: materialise the sub-object graph at all, not the number of lines in it.
    identity_only: bool = False


@register
class VoucherListQuery(TallyQuery[VoucherListParams, list[Voucher]]):
    """Vouchers in a date range, with their ledger and inventory lines.

    This one query backs the day book, the sales and purchase registers, and
    every "today's sales" style dashboard tile -- the backend slices the result
    by :class:`VoucherTypeKind` rather than issuing a separate Tally read per
    report, because a round trip to a customer's desktop is the expensive part.
    """

    name = "vouchers.list"
    params_model = VoucherListParams
    heavy = True

    def build(self, params: VoucherListParams) -> str:
        fetch = [
            "Date",
            "VoucherNumber",
            "VoucherTypeName",
            # NOT PersistedView. The mapper deliberately ignores it (it holds a
            # UI view name, not an accounting class), so requesting it was pure
            # weight in TDL running on a customer's machine.
            "PartyLedgerName",
            "Narration",
            "Reference",
            "IsCancelled",
            "IsOptional",
            "Amount",
            # AlterID and MasterID are deliberately NOT requested. Live Tally
            # already returns MASTERID on a Voucher collection without being
            # asked (see tests/fixtures/live/vouchers.xml), the parser reads
            # both when they arrive, and the incremental cursor comes from
            # `company.markers` rather than from any individual voucher -- so
            # naming them here buys nothing and puts two more field lookups
            # into TDL that runs on a customer's machine.
        ]
        if not params.identity_only:
            fetch += _leaf_fetch(_LEDGER_WRAPPERS, _LEDGER_ENTRY_FIELDS)
            if params.include_inventory:
                # Only where the item lines come too: the header of an invoice
                # is no use printed over an empty table.
                fetch += _INVOICE_FIELDS
                fetch += _leaf_fetch(_INVENTORY_WRAPPERS, _INVENTORY_ENTRY_FIELDS)

        # Always present. Tally will happily return the whole open year without
        # it -- see _date_window_filter.
        filters = {"TFDateFilter": _date_window_filter(params.from_date, params.to_date)}
        if params.voucher_type:
            escaped = params.voucher_type.replace('"', "")
            filters["TFVoucherTypeFilter"] = f'$VoucherTypeName = "{escaped}"'
        if params.alter_id_min is not None:
            # int() on an already-typed field, but the value is interpolated
            # into TDL that runs on a customer's machine, so it is re-forced to
            # a plain integer here rather than trusted to be one.
            filters["TFAlterIdFilter"] = f"$AlterID > {int(params.alter_id_min)}"

        return build_export_envelope(
            request_type="Collection",
            request_id="TFVouchers",
            static_variables=StaticVariables(
                company=params.company,
                from_date=params.from_date,
                to_date=params.to_date,
            ),
            collections=[
                Collection(
                    name="TFVouchers",
                    type="Voucher",
                    fetch=fetch,
                    filters=filters,
                )
            ],
        )

    def parse(self, root: ET.Element, params: VoucherListParams) -> list[Voucher]:
        vouchers: list[Voucher] = []
        for el in root.iter("VOUCHER"):
            voucher_date = parse_date(first_text(el, "DATE", "VOUCHERDATE"))
            if voucher_date is None:
                # A voucher with no parseable date cannot be placed on any
                # report; skipping beats poisoning a period total.
                continue

            voucher_type = first_text(el, "VOUCHERTYPENAME", "VCHTYPE") or el.get(
                "VCHTYPE", "Unknown"
            )
            # NOT PERSISTEDVIEW: on a live TallyPrime that field holds a UI view
            # name ("Accounting Voucher View", "Invoice Voucher View"), not an
            # accounting class, so every voucher classified as OTHER. PARENT is
            # the real accounting class when Tally sends it; otherwise the type
            # name itself matches for all default voucher types.
            parent_class = first_text(el, "PARENT", "VOUCHERTYPENAME") or voucher_type

            vouchers.append(
                Voucher(
                    voucher_number=find_text(el, "VOUCHERNUMBER"),
                    voucher_type=voucher_type,
                    kind=VoucherTypeKind.from_parent(parent_class),
                    date=voucher_date,
                    guid=find_text(el, "GUID"),
                    alter_id=parse_int(find_text(el, "ALTERID")),
                    master_id=parse_int(find_text(el, "MASTERID")),
                    party_name=first_text(el, "PARTYLEDGERNAME", "PARTYNAME"),
                    narration=find_text(el, "NARRATION"),
                    reference=find_text(el, "REFERENCE"),
                    amount=Money.from_tally(find_text(el, "AMOUNT")),
                    is_cancelled=parse_bool(find_text(el, "ISCANCELLED")),
                    is_optional=parse_bool(find_text(el, "ISOPTIONAL")),
                    ledger_entries=_parse_ledger_entries(el),
                    inventory_entries=(
                        _parse_inventory_entries(el) if params.include_inventory else []
                    ),
                    invoice=_parse_invoice(el) if params.include_inventory else None,
                )
            )
        return vouchers


def _entry_amount(entry: ET.Element) -> Money:
    """Resolve a voucher line's amount and side.

    **The sign on ``AMOUNT`` is the side, and ``ISDEEMEDPOSITIVE`` is not
    consulted.** The flag says which side a line *naturally* sits on, not which
    side this amount landed on, and the two part company on a negative entry --
    a round-off that took paise off a purchase is a debit-natured line carrying
    a positive amount, which is a credit.

    This used to let the flag win. Verified live 2026-10-11 on "D.D
    Enterprises" (79 vouchers, 256 ledger lines): the flag and the sign
    disagreed on 13 lines, every one a Round Off, and letting the flag win left
    13 vouchers that did not balance and a Round Off ledger 2.18 away from
    Tally's own closing balance. Reading the sign alone, every posted voucher
    balanced and every ledger reconciled to the paisa.
    """
    return Money.from_tally(find_text(entry, "AMOUNT"))


def _parse_ledger_entries(voucher: ET.Element) -> list[LedgerEntry]:
    for path in _LEDGER_ENTRY_PATHS:
        entries = _ledger_entries_at(voucher, path)
        if entries:
            return entries
    return []


def _ledger_entries_at(voucher: ET.Element, path: str) -> list[LedgerEntry]:
    entries: list[LedgerEntry] = []
    for entry in voucher.findall(path):
        ledger_name = first_text(entry, "LEDGERNAME", "NAME")
        if not ledger_name:
            continue
        bills = [
            t
            for t in (text_of(b) for b in entry.findall("./BILLALLOCATIONS.LIST/NAME"))
            if t
        ]
        entries.append(
            LedgerEntry(
                ledger_name=ledger_name,
                amount=_entry_amount(entry),
                is_party=parse_bool(find_text(entry, "ISPARTYLEDGER")),
                cost_centre=find_text(entry, "./CATEGORYALLOCATIONS.LIST/CATEGORY"),
                bill_references=bills,
                tax_rate=_optional_float(
                    find_text(entry, "./RATEOFINVOICETAX.LIST/RATEOFINVOICETAX")
                ),
            )
        )
    return entries


def _parse_inventory_entries(voucher: ET.Element) -> list[InventoryEntry]:
    for path in _INVENTORY_ENTRY_PATHS:
        entries = _inventory_entries_at(voucher, path)
        if entries:
            return entries
    return []


def _inventory_entries_at(voucher: ET.Element, path: str) -> list[InventoryEntry]:
    entries: list[InventoryEntry] = []
    for entry in voucher.findall(path):
        item_name = first_text(entry, "STOCKITEMNAME", "NAME")
        if not item_name:
            continue
        rate = find_text(entry, "RATE")
        quantity_text = first_text(entry, "BILLEDQTY", "ACTUALQTY")
        entries.append(
            InventoryEntry(
                item_name=item_name,
                quantity=parse_float(quantity_text),
                unit=_unit_from_quantity(quantity_text),
                rate=Money.from_tally(rate) if rate else None,
                amount=Money.from_tally(find_text(entry, "AMOUNT")),
                godown=first_text(
                    entry,
                    "./BATCHALLOCATIONS.LIST/GODOWNNAME",
                    "GODOWNNAME",
                ),
                batch=first_text(
                    entry,
                    "./BATCHALLOCATIONS.LIST/BATCHNAME",
                    "BATCHNAME",
                ),
                hsn_code=find_text(entry, "GSTHSNNAME"),
                gst_rate=_line_gst_rate(entry),
            ),
        )
    return entries


def _optional_float(value: str | None) -> float | None:
    """A number, or ``None`` where Tally sent nothing -- never a made-up zero."""
    return parse_float(value) if value and value.strip() else None


def _line_gst_rate(entry: ET.Element) -> float | None:
    """The full GST rate one invoice line was taxed at.

    A line carries a rate per duty head -- CGST 6, SGST 6, IGST 12 -- whichever
    of them the sale actually used, so the integrated rate is the one figure
    that is right for a local sale and an interstate one alike. The two halves
    are added only where a line has no integrated head at all.
    """
    rates: dict[str, float] = {}
    for detail in entry.findall("./RATEDETAILS.LIST"):
        head = (find_text(detail, "GSTRATEDUTYHEAD") or "").strip().upper()
        rate = _optional_float(find_text(detail, "GSTRATE"))
        if head and rate is not None:
            rates[head] = rate
    if "IGST" in rates:
        return rates["IGST"]
    if "CGST" in rates and "SGST/UTGST" in rates:
        return rates["CGST"] + rates["SGST/UTGST"]
    return None


def _lines(voucher: ET.Element, path: str) -> list[str]:
    return [t for t in (text_of(line) for line in voucher.findall(path)) if t]


def _parse_invoice(voucher: ET.Element) -> InvoiceDetails | None:
    """The buyer, consignee and dispatch details written on an invoice.

    ``None`` when the voucher has none of them, so a receipt does not carry an
    empty invoice header around and a reader can tell "not an invoice" from
    "an invoice with nothing filled in" by the kind, not by this.
    """
    buyer = VoucherParty(
        name=find_text(voucher, "BASICBUYERNAME"),
        address=_lines(voucher, "./BASICBUYERADDRESS.LIST/BASICBUYERADDRESS"),
        gstin=find_text(voucher, "PARTYGSTIN"),
        state=find_text(voucher, "STATENAME"),
    )
    consignee = VoucherParty(
        name=find_text(voucher, "CONSIGNEEMAILINGNAME"),
        address=_lines(voucher, "./ADDRESS.LIST/ADDRESS"),
        gstin=find_text(voucher, "CONSIGNEEGSTIN"),
        state=find_text(voucher, "CONSIGNEESTATENAME"),
    )
    details = InvoiceDetails(
        buyer=buyer if buyer != VoucherParty() else None,
        consignee=consignee if consignee != VoucherParty() else None,
        place_of_supply=find_text(voucher, "PLACEOFSUPPLY"),
        delivery_note=find_text(voucher, "./INVOICEDELNOTES.LIST/BASICSHIPDELIVERYNOTE"),
        delivery_note_date=parse_date(
            find_text(voucher, "./INVOICEDELNOTES.LIST/BASICSHIPPINGDATE")
        ),
        payment_terms=find_text(voucher, "BASICDUEDATEOFPYMT"),
        reference_date=parse_date(find_text(voucher, "REFERENCEDATE")),
        other_references=find_text(voucher, "BASICORDERREF"),
        buyer_order_no=find_text(voucher, "./INVOICEORDERLIST.LIST/BASICPURCHASEORDERNO"),
        buyer_order_date=parse_date(
            find_text(voucher, "./INVOICEORDERLIST.LIST/BASICORDERDATE")
        ),
        dispatch_doc_no=find_text(voucher, "BASICSHIPDOCUMENTNO"),
        dispatched_through=find_text(voucher, "BASICSHIPPEDBY"),
        destination=find_text(voucher, "BASICFINALDESTINATION"),
        bill_of_lading_no=find_text(voucher, "BILLOFLADINGNO"),
        bill_of_lading_date=parse_date(find_text(voucher, "BILLOFLADINGDATE")),
        vehicle_no=find_text(voucher, "BASICSHIPVESSELNO"),
        terms_of_delivery=_lines(voucher, "./BASICORDERTERMS.LIST/BASICORDERTERMS"),
    )
    return details if details != InvoiceDetails() else None


def _unit_from_quantity(value: str | None) -> str | None:
    """Pull the unit out of a Tally quantity string such as ``"12 Nos"``."""
    if not value:
        return None
    parts = value.strip().split()
    if len(parts) < 2:
        return None
    return parts[-1]


# --------------------------------------------------------------------------
# Outstanding (bill-wise)
# --------------------------------------------------------------------------


class OutstandingParams(QueryParams):
    as_of: date
    kind: OutstandingKind | None = None
    #: Start of the window Tally resolves bills over. Defaults to a wide span
    #: because a bill raised in an earlier year can still be outstanding today.
    from_date: date | None = None

    @model_validator(mode="after")
    def _default_from_date(self) -> OutstandingParams:
        if self.from_date is None:
            object.__setattr__(self, "from_date", self.as_of.replace(year=self.as_of.year - 5))
        return self


@register
class OutstandingQuery(TallyQuery[OutstandingParams, list[OutstandingBill]]):
    """Unsettled bills from bill-wise details.

    Receivable vs payable is derived from each bill's closing side rather than
    from the party's group: an advance from a customer sits in a debtor ledger
    but is genuinely a payable, and grouping it as a receivable would overstate
    what the owner is owed.
    """

    name = "outstanding.bills"
    params_model = OutstandingParams
    heavy = True

    def build(self, params: OutstandingParams) -> str:
        return build_export_envelope(
            request_type="Collection",
            request_id="TFBills",
            static_variables=StaticVariables(
                company=params.company,
                # Both bounds are required. With only SVTODATE set, live Tally
                # returns an empty collection.
                from_date=params.from_date,
                to_date=params.as_of,
            ),
            collections=[
                Collection(
                    name="TFBills",
                    type="Bills",
                    native_methods=[
                        "Name",
                        "Parent",
                        "BillDate",
                        "OpeningBalance",
                        "ClosingBalance",
                        "BillCreditPeriod",
                        "IsAdvance",
                        # NOT FinalBalance. Nothing parses it -- the mapper reads
                        # ClosingBalance -- and it is a derived aggregate, the
                        # most expensive kind of method for Tally to evaluate
                        # across the whole window. A live TallyPrime crashed with
                        # c0000005 on this collection while it was requested.
                    ],
                )
            ],
        )

    def parse(self, root: ET.Element, params: OutstandingParams) -> list[OutstandingBill]:
        bills: list[OutstandingBill] = []
        # Live Tally names each member <BILL>, singular. Looking only for the
        # plural <BILLS> silently returned zero outstanding bills on a company
        # that genuinely had them.
        for el in _iter_bills(root):
            bill_name = first_text(el, "NAME", "BILLREF") or el.get("NAME")
            party = find_text(el, "PARENT")
            if not bill_name or not party:
                continue

            pending = Money.from_tally(find_text(el, "CLOSINGBALANCE"))
            # Settled bills are filtered here rather than in TDL: a
            # $$IsNonZero filter on the collection made Tally return nothing
            # at all, and the correctness of the report matters more than the
            # bytes saved.
            if pending.is_zero:
                continue

            kind = (
                OutstandingKind.RECEIVABLE
                if pending.side is Side.DEBIT
                else OutstandingKind.PAYABLE
            )
            if params.kind is not None and kind is not params.kind:
                continue

            bill_date = parse_date(find_text(el, "BILLDATE"))
            due_date = _bill_due_date(el, bill_date)
            credit_period = (
                (due_date - bill_date).days
                if due_date is not None and bill_date is not None
                else _credit_period_days(find_text(el, "BILLCREDITPERIOD"))
            )

            bills.append(
                OutstandingBill(
                    party_name=party,
                    bill_name=bill_name,
                    kind=kind,
                    bill_date=bill_date,
                    due_date=due_date,
                    opening_amount=Money.from_tally(find_text(el, "OPENINGBALANCE")),
                    pending_amount=pending,
                    credit_period_days=credit_period,
                    is_advance=parse_bool(find_text(el, "ISADVANCE")),
                )
            )
        return bills


def _iter_bills(root: ET.Element) -> list[ET.Element]:
    """Collection members, under either spelling Tally might use."""
    members = list(root.iter("BILL"))
    return members or list(root.iter("BILLS"))


#: Tally day numbers count from this epoch. Confirmed against a live instance:
#: a bill dated 2026-04-01 with no credit period carried JD="46112".
_TALLY_DAY_ZERO = date(1899, 12, 31)


def _bill_due_date(el: ET.Element, bill_date: date | None) -> date | None:
    """Resolve a bill's due date.

    Tally puts the due date in a ``JD`` *attribute* on ``<BILLCREDITPERIOD>``
    and leaves the element text empty, so reading the text alone yields nothing.
    """
    element = el.find("BILLCREDITPERIOD")
    if element is not None:
        raw_jd = element.get("JD")
        if raw_jd and raw_jd.strip().isdigit():
            return _TALLY_DAY_ZERO + timedelta(days=int(raw_jd.strip()))

    return _due_date(bill_date, _credit_period_days(find_text(el, "BILLCREDITPERIOD")))


def _credit_period_days(value: str | None) -> int | None:
    """Tally credit periods read as ``"30 Days"``, ``"1 Month"`` or a bare number."""
    if not value:
        return None
    days = parse_float(value)
    if days == 0:
        return None
    lowered = value.lower()
    if "month" in lowered:
        days *= 30
    elif "week" in lowered:
        days *= 7
    elif "year" in lowered:
        days *= 365
    return int(days)


def _due_date(bill_date: date | None, credit_period: int | None) -> date | None:
    if bill_date is None or credit_period is None:
        return None
    from datetime import timedelta

    return bill_date + timedelta(days=credit_period)
