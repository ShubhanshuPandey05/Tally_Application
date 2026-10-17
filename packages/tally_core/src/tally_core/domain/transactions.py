"""Transaction-side domain models: vouchers, ledger entries, outstanding bills."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .masters import VoucherTypeKind
from .money import Money, Side


class LedgerEntry(BaseModel):
    """One ledger line inside a voucher."""

    model_config = ConfigDict(frozen=True)

    ledger_name: str
    amount: Money
    is_party: bool = False
    cost_centre: str | None = None
    bill_references: list[str] = Field(default_factory=list)
    #: The percentage a tax line was charged at, as Tally recorded it on the
    #: invoice (``RATEOFINVOICETAX``). Tally prints it beside the tax ledger --
    #: "IGST 12 ... 12 %" -- and it is null on every line that is not a tax.
    tax_rate: float | None = None


class InventoryEntry(BaseModel):
    """One stock line inside a voucher."""

    model_config = ConfigDict(frozen=True)

    item_name: str
    quantity: float = 0.0
    unit: str | None = None
    rate: Money | None = None
    amount: Money = Field(default_factory=Money.zero)
    godown: str | None = None
    batch: str | None = None
    #: HSN/SAC and the integrated GST rate **as this voucher recorded them**.
    #:
    #: TallyPrime resolves both when the invoice is saved -- from the item, its
    #: stock group or the company, whichever the shop set up -- and stores the
    #: answer on the line. That is the figure the invoice was taxed at; the
    #: stock master only says what the item is taxed at today. Null on a line
    #: read by a connector from before these were fetched.
    hsn_code: str | None = None
    gst_rate: float | None = None


class VoucherParty(BaseModel):
    """A buyer or consignee as written on one invoice.

    Not the ledger master: an invoice keeps its own copy of the name and
    address it was raised to, so a bill sent last year still shows last year's
    address after the customer moves.
    """

    model_config = ConfigDict(frozen=True)

    name: str | None = None
    address: list[str] = Field(default_factory=list)
    gstin: str | None = None
    state: str | None = None


class InvoiceDetails(BaseModel):
    """The header of a printed invoice: who it went to and how it travelled.

    Every field is optional and most are empty on most invoices -- a shop that
    never fills in "Dispatched through" still gets the box, blank, exactly as
    Tally prints it.
    """

    model_config = ConfigDict(frozen=True)

    buyer: VoucherParty | None = None
    consignee: VoucherParty | None = None
    place_of_supply: str | None = None
    delivery_note: str | None = None
    delivery_note_date: date | None = None
    payment_terms: str | None = None
    reference_date: date | None = None
    other_references: str | None = None
    buyer_order_no: str | None = None
    buyer_order_date: date | None = None
    dispatch_doc_no: str | None = None
    dispatched_through: str | None = None
    destination: str | None = None
    bill_of_lading_no: str | None = None
    bill_of_lading_date: date | None = None
    vehicle_no: str | None = None
    terms_of_delivery: list[str] = Field(default_factory=list)


class Voucher(BaseModel):
    """A posted transaction."""

    model_config = ConfigDict(frozen=True)

    voucher_number: str | None = None
    voucher_type: str
    kind: VoucherTypeKind = VoucherTypeKind.OTHER
    date: date
    guid: str | None = None
    #: Tally's change counter for this voucher. Bumped on every edit, so it is
    #: both the cursor an incremental sync advances and the tiebreaker that says
    #: whether an incoming copy is newer than the one already stored.
    alter_id: int | None = None
    #: Stable per-company identity. Unlike ``guid`` it is not globally unique,
    #: so it is kept only as a fallback key for vouchers Tally sends without one.
    master_id: int | None = None
    party_name: str | None = None
    narration: str | None = None
    reference: str | None = None
    amount: Money = Field(default_factory=Money.zero)
    is_cancelled: bool = False
    is_optional: bool = False
    ledger_entries: list[LedgerEntry] = Field(default_factory=list)
    inventory_entries: list[InventoryEntry] = Field(default_factory=list)
    #: Null when the voucher carries none of it -- a receipt, a journal -- and
    #: on anything read by a connector from before these fields were fetched.
    invoice: InvoiceDetails | None = None

    @property
    def is_effective(self) -> bool:
        """Cancelled and optional vouchers must never reach a total."""
        return not (self.is_cancelled or self.is_optional)


class OutstandingKind(StrEnum):
    RECEIVABLE = "receivable"
    PAYABLE = "payable"


class OutstandingBill(BaseModel):
    """A single unsettled bill from bill-wise details."""

    model_config = ConfigDict(frozen=True)

    party_name: str
    bill_name: str
    kind: OutstandingKind
    bill_date: date | None = None
    due_date: date | None = None
    opening_amount: Money = Field(default_factory=Money.zero)
    pending_amount: Money = Field(default_factory=Money.zero)
    voucher_number: str | None = None
    credit_period_days: int | None = None
    is_advance: bool = Field(
        default=False,
        description="An on-account advance rather than a bill raised against a supply.",
    )

    def days_overdue(self, as_of: date) -> int:
        """Positive when past due, 0 when not yet due or no due date is known."""
        if self.due_date is None:
            return 0
        return max((as_of - self.due_date).days, 0)

    def ageing_bucket(self, as_of: date) -> str:
        """Standard receivables ageing buckets used across the dashboard."""
        overdue = self.days_overdue(as_of)
        if overdue <= 0:
            return "not_due"
        if overdue <= 30:
            return "1_30"
        if overdue <= 60:
            return "31_60"
        if overdue <= 90:
            return "61_90"
        if overdue <= 180:
            return "91_180"
        return "180_plus"


class TrialBalanceRow(BaseModel):
    """One group/ledger line of a trial balance."""

    model_config = ConfigDict(frozen=True)

    name: str
    parent: str | None = None
    is_group: bool = False
    opening_balance: Money = Field(default_factory=Money.zero)
    debit_total: Money = Field(default_factory=Money.zero)
    credit_total: Money = Field(default_factory=Money.zero)
    closing_balance: Money = Field(default_factory=Money.zero)


class BalanceSnapshot(BaseModel):
    """A named balance at a point in time -- cash, bank, a group total."""

    model_config = ConfigDict(frozen=True)

    name: str
    balance: Money
    as_of: date
    side_hint: Side | None = Field(
        default=None,
        description="Expected natural side; lets the UI flag an abnormal balance.",
    )
