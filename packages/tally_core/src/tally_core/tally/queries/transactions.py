"""Transaction queries: vouchers (day book) and bill-wise outstanding."""

from __future__ import annotations

from datetime import date, timedelta
from xml.etree import ElementTree as ET

from pydantic import model_validator

from ...domain.masters import VoucherTypeKind
from ...domain.money import Money, Side
from ...domain.transactions import (
    InventoryEntry,
    LedgerEntry,
    OutstandingBill,
    OutstandingKind,
    Voucher,
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
)
_INVENTORY_ENTRY_FIELDS = (
    "StockItemName",
    "Rate",
    "BilledQty",
    "ActualQty",
    "Amount",
    "BatchAllocations.GodownName",
    "BatchAllocations.BatchName",
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
    """
    return (
        f'$Date >= $$Date:"{_tally_date(from_date)}" '
        f'AND $Date <= $$Date:"{_tally_date(to_date)}"'
    )


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
            *_leaf_fetch(_LEDGER_WRAPPERS, _LEDGER_ENTRY_FIELDS),
        ]
        if params.include_inventory:
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
                )
            )
        return vouchers


def _entry_amount(entry: ET.Element) -> Money:
    """Resolve a voucher line's amount and side.

    ``ISDEEMEDPOSITIVE`` is Tally's authoritative Dr/Cr flag and it does not
    always agree with the sign on ``AMOUNT``; when present it wins, because
    disagreeing with it produces registers that do not balance.
    """
    money = Money.from_tally(find_text(entry, "AMOUNT"))
    flag = find_text(entry, "ISDEEMEDPOSITIVE")
    if flag is None:
        return money
    expected = Side.DEBIT if parse_bool(flag) else Side.CREDIT
    if money.side is expected:
        return money
    return Money(amount=money.amount, side=expected, currency=money.currency)


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
            ),
        )
    return entries


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
