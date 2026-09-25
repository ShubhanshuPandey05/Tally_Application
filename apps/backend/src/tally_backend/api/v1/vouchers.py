"""Creating a voucher from the app.

The only endpoint in this API that changes anything in a customer's books.
Everything else under ``/companies/{company_id}`` reads.

The shape a phone sends is deliberately *not* Tally's. A client posts a party,
an amount and — for a sale or an order — some lines, and this module turns that
into the double entry TallyPrime needs. Two reasons it is not the client's job:

1. **The sign convention is the easiest thing in this product to get backwards**
   (CLAUDE.md §4: negative is a debit). Encoding it once here beats encoding it
   in every client that ever talks to this API.
2. **A phone that could name both sides of an entry could write anything.** It
   would be a general journal API wearing a receipt's clothes, and the list of
   things somebody may create would stop being enforceable.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from tally_core.domain.masters import VoucherTypeKind
from tally_core.domain.writes import (
    PARTY_GROUPS,
    BillAllocation,
    DraftInventoryEntry,
    DraftLedgerEntry,
    LedgerDraft,
    StockItemDraft,
    VoucherDraft,
)

from ...core.errors import AppError, NotFound
from ...db.models import Company, Connector, PendingVoucher, PendingVoucherState, utc_now
from ...services import analytics as an
from ...services.audit import record
from ...services.reads import FetchMode, NoDataYet
from ..deps import (
    CompanyDep,
    MasterServiceDep,
    PrincipalDep,
    ReadServiceDep,
    SessionDep,
    VoucherWriteServiceDep,
)

router = APIRouter(prefix="/companies/{company_id}", tags=["vouchers"])

#: What a client may ask for, mapped to the domain kind. A string the client
#: sends is never turned into a ``VoucherTypeKind`` directly -- that would let
#: any kind the enum happens to hold through, including the ones nobody
#: reviewed.
_KINDS: dict[str, VoucherTypeKind] = {
    "receipt": VoucherTypeKind.RECEIPT,
    "payment": VoucherTypeKind.PAYMENT,
    "sales": VoucherTypeKind.SALES,
    "purchase": VoucherTypeKind.PURCHASE,
    "sales_order": VoucherTypeKind.SALES_ORDER,
    "purchase_order": VoucherTypeKind.PURCHASE_ORDER,
}


class BadDraft(AppError):
    status_code = 422
    code = "bad_draft"


class LineRequest(BaseModel):
    """One stock line on a sale or an order."""

    item: str = Field(min_length=1, max_length=300)
    quantity: Decimal = Field(gt=0)
    rate: Decimal | None = Field(default=None, ge=0)
    amount: Decimal = Field(ge=0)
    unit: str | None = Field(default=None, max_length=32)
    godown: str | None = Field(default=None, max_length=300)


class TaxRequest(BaseModel):
    """One duty or tax ledger on a sale or an order -- CGST, SGST, IGST.

    The amount, not a rate. The phone works it out from the rate somebody
    chose, so what reaches TallyPrime is the figure they saw on screen before
    pressing send rather than one recomputed here with different rounding.
    """

    ledger: str = Field(min_length=1, max_length=300)
    amount: Decimal = Field(gt=0)

    @field_validator("ledger")
    @classmethod
    def _named(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ledger must not be blank")
        return value.strip()


class BillRequest(BaseModel):
    """An invoice a receipt or payment settles."""

    bill: str = Field(min_length=1, max_length=300)
    amount: Decimal = Field(gt=0)


class CreateVoucherRequest(BaseModel):
    kind: str
    date: date
    party: str = Field(min_length=1, max_length=300)
    amount: Decimal = Field(gt=0)
    #: The other side of the entry: the cash or bank ledger for a receipt or
    #: payment, the sales ledger for an invoice. Defaulted per kind when the
    #: client does not say, because "Cash" and "Sales" exist in every company.
    account: str | None = Field(default=None, max_length=300)
    narration: str | None = Field(default=None, max_length=2000)
    reference: str | None = Field(default=None, max_length=200)
    #: The customer's own name for the voucher type ("Tax Invoice"). Left unset,
    #: the connector uses the canonical name for the kind.
    voucher_type: str | None = Field(default=None, max_length=300)
    lines: list[LineRequest] = Field(default_factory=list, max_length=200)
    bills: list[BillRequest] = Field(default_factory=list, max_length=100)
    #: Added on top of ``amount``, which stays the taxable value: the items'
    #: total, or the figure typed for a sale with no items.
    taxes: list[TaxRequest] = Field(default_factory=list, max_length=10)
    #: Whether the entry waits for approval in TallyPrime. Only a request:
    #: a connector set to optional makes every entry optional whatever this
    #: says, so it can make an entry more cautious and never less.
    optional: bool = True

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in _KINDS:
            raise ValueError(f"kind must be one of {', '.join(sorted(_KINDS))}")
        return value


class VoucherCreatedResponse(BaseModel):
    """What happened, in terms the app can act on without re-reading Tally."""

    ok: bool
    #: True when the entry is sitting in TallyPrime waiting to be approved. The
    #: app leads with this, because "saved" and "saved but not counting yet"
    #: are different things to tell somebody who just took ₹5,000 over a
    #: counter.
    awaiting_approval: bool
    voucher_id: int | None = None
    message: str | None = None
    #: Whether offering "Try again" is safe. False after a timeout even though
    #: the entry may not exist, because the app must not invite somebody to
    #: create a second receipt for the same money.
    can_retry: bool = False
    #: Held on the server because the PC was not reachable. Not the same as
    #: ``ok`` -- nothing is in the books yet -- and the app says so plainly
    #: rather than letting somebody walk away thinking it is recorded.
    queued: bool = False
    pending_id: str | None = None
    #: Something the entry named is not in the books yet. The app offers to
    #: create exactly this rather than matching on the message text.
    missing_kind: str | None = None
    missing_name: str | None = None


#: The ledger the other side of the entry lands on when the client does not
#: name one. Every company has these, and a shop that uses different ones sends
#: ``account`` instead.
_DEFAULT_ACCOUNT: dict[VoucherTypeKind, str] = {
    VoucherTypeKind.RECEIPT: "Cash",
    VoucherTypeKind.PAYMENT: "Cash",
    VoucherTypeKind.SALES: "Sales",
    VoucherTypeKind.PURCHASE: "Purchase",
    VoucherTypeKind.SALES_ORDER: "Sales",
    VoucherTypeKind.PURCHASE_ORDER: "Purchase",
}

_ORDER_PREFIX = {
    VoucherTypeKind.SALES_ORDER: "SO",
    VoucherTypeKind.PURCHASE_ORDER: "PO",
}


def _order_number(kind: VoucherTypeKind, on: date) -> str:
    """An order number for somebody who did not type one: ``SO-31Mar26-4F2A``.

    Tally refuses an order without a number, and the voucher number cannot be
    used because Tally only allocates it after saving. Readable enough to say
    over the phone, and random at the end so two orders taken at one counter in
    the same minute do not share one.
    """
    stamp = f"{on:%d}{_MONTHS[on.month - 1]}{on:%y}"
    return f"{_ORDER_PREFIX[kind]}-{stamp}-{secrets.token_hex(2).upper()}"


_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _to_draft(body: CreateVoucherRequest) -> VoucherDraft:
    """Turn what a phone sent into a balanced double entry.

    Tally's sign convention applies here and nowhere above: **negative is a
    debit, positive is a credit**, and ``ISDEEMEDPOSITIVE`` is ``Yes`` on the
    debit. Every entry below is written out in full rather than derived from a
    shared helper, because the four cases genuinely differ and a clever
    abstraction over them is how one of them silently posts backwards.
    """
    kind = _KINDS[body.kind]
    account = body.account or _DEFAULT_ACCOUNT.get(kind)

    if body.taxes and kind in {VoucherTypeKind.RECEIPT, VoucherTypeKind.PAYMENT}:
        raise BadDraft(
            f"taxes on a {kind}",
            user_message="Taxes go on a sale or an order, not on a receipt or payment.",
        )
    tax_total = sum((t.amount for t in body.taxes), Decimal("0"))

    bills = [
        BillAllocation(name=b.bill, method="Agst Ref", amount=b.amount) for b in body.bills
    ]
    if bills and sum(b.amount for b in bills) != body.amount:
        raise BadDraft(
            "bill allocations do not sum to the voucher amount",
            user_message="The bills selected do not add up to the amount entered.",
        )

    lines = [
        DraftInventoryEntry(
            item_name=line.item,
            quantity=line.quantity,
            rate=line.rate,
            amount=line.amount,
            unit=line.unit,
            godown=line.godown,
            # Every stock line's value posts to the sales or purchase ledger --
            # on an order too, which is how Tally's own orders are shaped. A
            # shop that splits taxed and exempt goods across two ledgers is a
            # case this deliberately does not try to guess at.
            ledger_name=account,
        )
        for line in body.lines
    ]

    common = {
        "kind": kind,
        "date": body.date,
        "party_name": body.party,
        "narration": body.narration,
        "reference": body.reference,
        "voucher_type_name": body.voucher_type,
        # The person's choice, which the connector can only ever tighten.
        "optional": body.optional,
    }

    if kind in {VoucherTypeKind.SALES_ORDER, VoucherTypeKind.PURCHASE_ORDER}:
        # An order moves no money and carries no accounting entries at all.
        if not lines:
            raise BadDraft(
                "an order with no lines",
                user_message="Add at least one item to the order.",
            )
        if sum(line.amount for line in lines) != body.amount:
            raise BadDraft(
                "stock lines do not sum to the order amount",
                user_message="The items do not add up to the order total.",
            )
        # Shaped like an order entered by hand in TallyPrime and exported:
        # the party at the full value, each line's value on the sales or
        # purchase ledger, each tax on its own ledger. A sales order has the
        # customer on the debit side; a purchase order is the mirror of it.
        sale = kind is VoucherTypeKind.SALES_ORDER
        gross = body.amount + tax_total
        order_entries = [
            DraftLedgerEntry(
                ledger_name=body.party,
                amount=-gross if sale else gross,
                is_deemed_positive=sale,
            ),
            *(
                DraftLedgerEntry(
                    ledger_name=t.ledger,
                    amount=t.amount if sale else -t.amount,
                    is_deemed_positive=not sale,
                )
                for t in body.taxes
            ),
        ]
        # The reference is the order number, as on Tally's own orders.
        number = (body.reference or "").strip() or _order_number(kind, body.date)
        return VoucherDraft(
            **{**common, "reference": number},
            ledger_entries=order_entries,
            inventory_entries=lines,
            order_number=number,
            order_due_date=body.date,
        )

    if account is None:
        raise BadDraft(
            f"no account ledger for {kind}",
            user_message="Choose which account this goes to.",
        )

    if kind is VoucherTypeKind.RECEIPT:
        # Money in: the customer credited, the cash or bank ledger debited.
        # Credit first, because that is the order a TallyPrime receipt is
        # entered and read in -- a payment starts with its debit instead.
        entries = [
            DraftLedgerEntry(
                ledger_name=body.party,
                amount=body.amount,
                is_deemed_positive=False,
                bill_references=bills,
            ),
            DraftLedgerEntry(
                ledger_name=account, amount=-body.amount, is_deemed_positive=True
            ),
        ]
    elif kind is VoucherTypeKind.PAYMENT:
        # Money out: the supplier is debited, cash or bank credited.
        entries = [
            DraftLedgerEntry(
                ledger_name=body.party,
                amount=-body.amount,
                is_deemed_positive=True,
                bill_references=bills,
            ),
            DraftLedgerEntry(
                ledger_name=account, amount=body.amount, is_deemed_positive=False
            ),
        ]
    elif kind is VoucherTypeKind.PURCHASE:
        # A purchase is the mirror of a sale: the supplier is credited with
        # the goods and the tax on them, the purchase ledger and each tax
        # ledger debited. GST paid is recoverable input, not a cost, so it
        # never lands in the purchase ledger.
        if lines and sum(line.amount for line in lines) != body.amount:
            raise BadDraft(
                "stock lines do not sum to the invoice amount",
                user_message="The items do not add up to the invoice total.",
            )
        entries = [
            DraftLedgerEntry(
                ledger_name=body.party,
                amount=body.amount + tax_total,
                is_deemed_positive=False,
                # The supplier's bill is a debt this purchase creates.
                bill_references=[
                    BillAllocation(name=b.bill, method="New Ref", amount=b.amount)
                    for b in body.bills
                ],
            ),
        ]
        # As on a sale, the purchase ledger is debited in exactly one place:
        # inside each stock line when there are lines, here when there are not.
        if not lines:
            entries.append(
                DraftLedgerEntry(
                    ledger_name=account, amount=-body.amount, is_deemed_positive=True
                )
            )
        entries.extend(
            DraftLedgerEntry(ledger_name=t.ledger, amount=-t.amount, is_deemed_positive=True)
            for t in body.taxes
        )
    else:
        # A sale: the customer owes us, and the sales ledger is credited.
        if lines and sum(line.amount for line in lines) != body.amount:
            raise BadDraft(
                "stock lines do not sum to the invoice amount",
                user_message="The items do not add up to the invoice total.",
            )
        entries = [
            DraftLedgerEntry(
                ledger_name=body.party,
                # The customer owes for the goods and the tax on them.
                amount=-(body.amount + tax_total),
                is_deemed_positive=True,
                # A sale creates the debt rather than settling one, so its bill
                # reference is a New Ref. Using Agst Ref here would try to
                # allocate against an invoice that does not exist yet.
                bill_references=[
                    BillAllocation(name=b.bill, method="New Ref", amount=b.amount)
                    for b in body.bills
                ],
            ),
        ]
        # The credit goes in exactly one place. With stock lines it lives in
        # each line's accounting allocation, and adding a ledger entry here as
        # well would credit the sales ledger twice for one invoice -- Tally
        # either refuses the voucher or books double the sale, and no figure on
        # screen would say which.
        if not lines:
            entries.append(
                DraftLedgerEntry(
                    ledger_name=account, amount=body.amount, is_deemed_positive=False
                )
            )
        # Each tax is credited to its own duty ledger. GST collected is owed
        # onward, not earned, so it must never land in the sales ledger.
        entries.extend(
            DraftLedgerEntry(ledger_name=t.ledger, amount=t.amount, is_deemed_positive=False)
            for t in body.taxes
        )

    return VoucherDraft(**common, ledger_entries=entries, inventory_entries=lines)


@router.post("/vouchers", response_model=VoucherCreatedResponse)
async def create_voucher(
    body: CreateVoucherRequest,
    company: CompanyDep,
    principal: PrincipalDep,
    writes: VoucherWriteServiceDep,
    request: Request,
) -> VoucherCreatedResponse:
    """Create one voucher in this company's books.

    Access is the same as reading the company: ``get_company`` has already
    checked the subscription and that this person may see these books. There is
    no extra role gate, deliberately — the point of the feature is that the
    person at the counter can record what they just took, and the control on a
    wrong entry is that it waits for approval inside TallyPrime rather than a
    permission that would stop them using it at all.
    """
    draft = _to_draft(body)
    posted = await writes.create(
        company=company,
        draft=draft,
        user_id=principal.user.id,
        request=request,
    )

    # Only the party is offered for creation. A missing cash, bank, sales or
    # tax ledger is a mistyped name, and offering to add it would file "CGST"
    # under Sundry Debtors as a customer.
    missing_kind, missing_name = posted.missing_kind, posted.missing_name
    if missing_kind == "ledger" and missing_name != body.party:
        missing_kind = missing_name = None

    return VoucherCreatedResponse(
        ok=posted.ok,
        awaiting_approval=posted.ok and posted.optional,
        voucher_id=posted.voucher_id,
        message=posted.message,
        can_retry=posted.can_retry,
        queued=posted.queued,
        pending_id=posted.pending_id,
        missing_kind=missing_kind,
        missing_name=missing_name,
    )


class PendingEntryResponse(BaseModel):
    """One entry that has not reached TallyPrime yet.

    No amount. This list exists so somebody can see *that* an entry is stuck
    and cancel it; what it was worth is a figure, and figures belong on the
    screens built to show them with their freshness beside them.
    """

    id: str
    kind: str
    party: str | None = None
    state: str
    attempts: int
    #: Tally's own words from the last attempt, when there were any.
    last_error: str | None = None
    created_at: datetime
    expires_at: datetime
    #: Still on its way. What the badge counts.
    is_open: bool


@router.get("/vouchers/pending", response_model=list[PendingEntryResponse])
async def list_pending(
    company: CompanyDep,
    session: SessionDep,
) -> list[PendingEntryResponse]:
    """Entries waiting for this company's PC, newest first.

    Settled rows are included rather than hidden: somebody who watched an entry
    go into the queue needs to be able to find out what became of it, and a
    list that silently drops the failures is how a receipt goes missing.
    """
    rows = (
        await session.execute(
            select(PendingVoucher)
            .where(PendingVoucher.company_id == company.id)
            .order_by(PendingVoucher.created_at.desc())
            .limit(100)
        )
    ).scalars().all()

    return [
        PendingEntryResponse(
            id=row.id,
            kind=row.kind,
            party=row.party_name,
            state=str(row.state),
            attempts=row.attempts,
            last_error=row.last_error,
            created_at=row.created_at,
            expires_at=row.expires_at,
            is_open=row.is_open,
        )
        for row in rows
    ]


@router.delete("/vouchers/pending/{pending_id}", status_code=204)
async def cancel_pending(
    pending_id: str,
    company: CompanyDep,
    session: SessionDep,
    principal: PrincipalDep,
    request: Request,
) -> None:
    """Withdraw an entry before it is sent.

    Only ``waiting`` can be cancelled. A row already claimed by a drain is
    ``sending`` and may be on the wire this second -- cancelling it here would
    tell somebody it was withdrawn while it lands in their books.
    """
    row = await session.get(PendingVoucher, pending_id)
    # 404 for "not yours" as well as "not there", same as everywhere else.
    if row is None or row.company_id != company.id:
        raise NotFound(
            f"pending voucher {pending_id}",
            user_message="That entry was not found.",
        )

    # `!=`, not `is not`. The column is a String, so SQLAlchemy hands back a
    # plain str -- which compares *equal* to the StrEnum member but is never
    # identical to it. An identity check here silently refused every
    # cancellation.
    if row.state != PendingVoucherState.WAITING:
        raise AppError(
            f"cannot cancel a {row.state} entry",
            user_message=(
                "That entry is already on its way to TallyPrime and cannot be "
                "cancelled."
            ),
        )

    row.state = PendingVoucherState.CANCELLED
    row.settled_at = utc_now()
    await record(
        session,
        action="voucher.cancel_pending",
        org_id=company.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={"kind": row.kind},
        request=request,
    )
    await session.commit()


# --------------------------------------------------------------------------
# The one or two master records a counter genuinely needs
# --------------------------------------------------------------------------


def _real_name(value: str) -> str:
    """A name that is only spaces is not a name.

    Checked here rather than left to the domain model, so a bad request comes
    back as a 422 naming the field instead of a 500 from a validator that ran
    too late to be reported properly.
    """
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("cannot be blank")
    return trimmed


class CreateLedgerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    #: ``customer`` or ``supplier``. Not the group name: which side of the
    #: balance sheet a party lands on is not something a form should be able to
    #: send as free text.
    role: str = "customer"
    gstin: str | None = Field(default=None, max_length=20)

    _trim_name = field_validator("name")(_real_name)

    @field_validator("role")
    @classmethod
    def _known_role(cls, value: str) -> str:
        if value not in PARTY_GROUPS:
            raise ValueError(f"role must be one of {', '.join(sorted(PARTY_GROUPS))}")
        return value


class CreateStockItemRequest(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    unit: str | None = Field(default=None, max_length=32)

    _trim_name = field_validator("name")(_real_name)


class MasterCreatedResponse(BaseModel):
    ok: bool
    name: str
    message: str | None = None


@router.post("/ledgers", response_model=MasterCreatedResponse)
async def create_ledger(
    body: CreateLedgerRequest,
    company: CompanyDep,
    principal: PrincipalDep,
    masters: MasterServiceDep,
    request: Request,
) -> MasterCreatedResponse:
    """Add a customer or supplier to TallyPrime.

    Reached when an entry failed because the party was not in the books, and
    always after somebody confirmed the name. Creating one silently for every
    unrecognised spelling is how a chart of accounts ends up holding "Ram
    Traders", "Ram traders" and "Ram Trader" with an outstanding split across
    all three.

    No opening balance. That is money, and inventing it from a phone would move
    a trial balance on the strength of a typo.
    """
    created = await masters.create_ledger(
        company=company,
        draft=LedgerDraft(name=body.name, role=body.role, gstin=body.gstin),
        user_id=principal.user.id,
        request=request,
    )
    return MasterCreatedResponse(
        ok=created.ok, name=created.name, message=created.message
    )


@router.post("/stock-items", response_model=MasterCreatedResponse)
async def create_stock_item(
    body: CreateStockItemRequest,
    company: CompanyDep,
    principal: PrincipalDep,
    masters: MasterServiceDep,
    request: Request,
) -> MasterCreatedResponse:
    """Add a stock item to TallyPrime.

    No opening quantity and no rate, for the same reason a ledger gets no
    opening balance: both would move a stock valuation.
    """
    created = await masters.create_stock_item(
        company=company,
        draft=StockItemDraft(name=body.name, unit=body.unit),
        user_id=principal.user.id,
        request=request,
    )
    return MasterCreatedResponse(
        ok=created.ok, name=created.name, message=created.message
    )


# --------------------------------------------------------------------------
# Name pickers
# --------------------------------------------------------------------------

#: Where a party lives in a chart of accounts. A receipt, payment, sale or
#: order is always against one of these two, so offering the whole ledger list
#: would bury the three names somebody actually wants under Duties & Taxes.
_PARTY_GROUPS = {"sundry debtors", "sundry creditors"}

#: Tally's own group for GST, TDS and every other duty ledger.
_TAX_GROUPS = {"duties & taxes", "duties and taxes"}

#: How many names a picker returns. The app loads each list once and filters on
#: the phone, so this has to cover a whole chart of accounts rather than one
#: screenful -- names are a few bytes each.
_PICKER_LIMIT = 5000


class NameResponse(BaseModel):
    """One suggestion. A name and where it sits -- never a balance.

    This feeds a picker while somebody is typing, so it is deliberately the
    leanest thing that works: a shop with two thousand ledgers should not send
    two thousand closing balances to fill a dropdown, over a connection whose
    upload is already the bottleneck.
    """

    name: str
    group: str | None = None


class ItemResponse(NameResponse):
    """A stock item, with what the line sheet fills in when it is picked.

    ``rate`` is a decimal string, like every other amount this API sends.
    """

    unit: str | None = None
    rate: str | None = None


def _matches(name: str, query: str | None) -> bool:
    if not query:
        return True
    return query.strip().lower() in name.lower()


async def _snapshot_rows(reads: ReadServiceDep, company: Company, dataset: str) -> list:
    """The stored rows, or none at all.

    A company that has never synced has no snapshot, and the read layer says so
    with a 503. That is right for a report and wrong for a picker: the field it
    feeds is a text box somebody can type into, so an empty list of suggestions
    is a working form and an error is a shop that cannot record a sale until a
    backfill finishes.
    """
    try:
        result = await reads.fetch(company, dataset=dataset, mode=FetchMode.CACHED)
    except NoDataYet:
        return []
    return result.payload if isinstance(result.payload, list) else []


@router.get("/masters/parties", response_model=list[NameResponse])
async def party_names(
    company: CompanyDep,
    reads: ReadServiceDep,
    q: str | None = None,
    limit: int = _PICKER_LIMIT,
) -> list[NameResponse]:
    """Customers and suppliers, for the party field.

    Served from the stored ledger snapshot the refresher already keeps, so
    typing a name costs a shop's TallyPrime nothing. A snapshot that is an hour
    old is the right trade here: a party added in TallyPrime five minutes ago
    can still be typed in full, and the entry will name it correctly.
    """
    return await _ledgers_under(reads, company, _PARTY_GROUPS, q, limit)


@router.get("/masters/items", response_model=list[ItemResponse])
async def item_names(
    company: CompanyDep,
    reads: ReadServiceDep,
    q: str | None = None,
    limit: int = _PICKER_LIMIT,
) -> list[ItemResponse]:
    """Stock items, for the line sheet, with the unit and a starting rate.

    Picking an item fills its unit and rate, so somebody at a counter types a
    quantity and nothing else. The rate is the item's current stock rate from
    TallyPrime -- what the stock summary shows -- and only a suggestion: the
    field stays editable, because the price a customer is charged is agreed
    at the counter, not read from a master.
    """
    rows = await _snapshot_rows(reads, company, "stock_items.list")

    out: list[ItemResponse] = []
    for item in an.parse_stock(rows):
        if not _matches(item.name, q):
            continue
        rate = item.closing_rate
        out.append(
            ItemResponse(
                name=item.name,
                group=item.parent_group,
                unit=item.base_unit or None,
                rate=str(rate.amount) if rate is not None and rate.amount > 0 else None,
            )
        )
        if len(out) >= limit:
            break

    out.sort(key=lambda n: n.name.lower())
    return out


async def _ledgers_under(
    reads: ReadServiceDep, company: Company, groups: set[str], q: str | None, limit: int
) -> list[NameResponse]:
    """Ledgers filed under one of ``groups``, at any depth, from the snapshot.

    Sub-groups count. Shops file parties under home-made groups -- "Local
    Suppliers" under Sundry Creditors -- and matching the direct parent only
    left those parties out of the picker with nothing to say why. Without the
    group tree membership falls back to the direct parent, as the outstanding
    report does.

    Sorted before the cut, not after. The app loads this list once and filters
    it on the phone, so cutting in snapshot order let one group's names fill
    the whole list and the other group's never arrive.
    """
    rows = await _snapshot_rows(reads, company, "ledgers.list")
    tree = an.parse_groups(await _snapshot_rows(reads, company, "groups.list"))
    wanted = set(groups)
    for root in groups:
        wanted |= {grp.name.strip().lower() for grp in an.sub_groups(tree, root)}

    out: list[NameResponse] = []
    for row in rows:
        name = (row or {}).get("name")
        # ``parent_group`` is what the domain Ledger calls it; ``parent`` is
        # what Tally's XML calls it and what a stock item still uses. Reading
        # only one of them silently returned nobody.
        parent = (row or {}).get("parent_group") or (row or {}).get("parent") or ""
        if not name or parent.strip().lower() not in wanted:
            continue
        if not _matches(name, q):
            continue
        out.append(NameResponse(name=name, group=parent))

    out.sort(key=lambda n: n.name.lower())
    return out[:limit]


@router.get("/masters/taxes", response_model=list[NameResponse])
async def tax_ledger_names(
    company: CompanyDep, reads: ReadServiceDep, q: str | None = None, limit: int = _PICKER_LIMIT
) -> list[NameResponse]:
    """Duty and tax ledgers -- CGST, SGST, IGST, cess -- for a sale or order."""
    return await _ledgers_under(reads, company, _TAX_GROUPS, q, limit)


@router.get("/masters/accounts", response_model=list[NameResponse])
async def cash_and_bank_names(
    company: CompanyDep, reads: ReadServiceDep, q: str | None = None, limit: int = _PICKER_LIMIT
) -> list[NameResponse]:
    """Cash and bank ledgers, for the other side of a receipt or payment.

    The same groups the dashboard counts as cash and bank, so the accounts
    offered here are exactly the ones whose balances the home screen shows.
    """
    return await _ledgers_under(reads, company, an.CASH_GROUPS | an.BANK_GROUPS, q, limit)


@router.get("/masters/sales-accounts", response_model=list[NameResponse])
async def sales_ledger_names(
    company: CompanyDep, reads: ReadServiceDep, q: str | None = None, limit: int = _PICKER_LIMIT
) -> list[NameResponse]:
    """Sales ledgers, for the value of a sale's or sales order's goods.

    Offered rather than assumed: a GST-registered shop's is usually "Gst Sales"
    or "Sales @18%", and a default of plain "Sales" is refused by TallyPrime
    in every company that never created one.
    """
    return await _ledgers_under(reads, company, {"sales accounts"}, q, limit)


@router.get("/masters/purchase-accounts", response_model=list[NameResponse])
async def purchase_ledger_names(
    company: CompanyDep, reads: ReadServiceDep, q: str | None = None, limit: int = _PICKER_LIMIT
) -> list[NameResponse]:
    """Purchase ledgers, for the value of a purchase order's goods."""
    return await _ledgers_under(reads, company, {"purchase accounts"}, q, limit)


class EntrySettingsResponse(BaseModel):
    """What the entry form may offer for this company."""

    #: True when the Tally PC is set to regular entries, so the person may
    #: choose per entry. False when it is set to optional -- or too old to say,
    #: or never connected -- and every entry will wait for approval.
    can_post_regular: bool


@router.get("/entry-settings", response_model=EntrySettingsResponse)
async def entry_settings(company: CompanyDep, session: SessionDep) -> EntrySettingsResponse:
    """Whether this company's Tally PC lets an entry skip approval.

    The choice belongs to the shop's own PC, set in the connector window. This
    only tells the app whether to offer it; the connector enforces the setting
    on every import, so a stale answer here can make an entry more cautious
    than asked, never less.
    """
    connector = await session.get(Connector, company.connector_id)
    mode = connector.voucher_entry_mode if connector is not None else None
    return EntrySettingsResponse(can_post_regular=mode == "regular")
