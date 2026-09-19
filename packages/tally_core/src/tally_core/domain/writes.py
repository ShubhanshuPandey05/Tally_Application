"""What a phone may ask Tally to record, and what Tally says came of it.

These models are deliberately *not* :class:`~tally_core.domain.transactions.Voucher`.
A voucher read out of Tally is a fact — it has a number, a GUID, an AlterID and
a settled amount. A draft is a request that may still be refused, and giving the
two one shape invites code that treats an unposted request as a booked entry.

## Optional entries are the approval step

A draft carries :attr:`VoucherDraft.optional`. Tally records an optional voucher
in full but excludes it from every balance, every stock figure and every report
until somebody un-marks it in TallyPrime (Day Book → the voucher → Ctrl+L).

That is the whole approval mechanism, and it is Tally's rather than ours. The
accountant approves the entry on the screen they already use, and until they do,
a wrong entry sent from somebody's phone cannot move a single figure in the
books. A queue of our own would have to be reconciled, expired and audited, and
would ask the person who does the books to watch a second inbox.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .masters import VoucherTypeKind

#: The kinds a phone may create. Everything else is refused by name rather than
#: by omission, so adding a kind is a deliberate act with a migration behind it.
WRITEABLE_KINDS: frozenset[VoucherTypeKind] = frozenset(
    {
        VoucherTypeKind.RECEIPT,
        VoucherTypeKind.PAYMENT,
        VoucherTypeKind.SALES,
        VoucherTypeKind.SALES_ORDER,
        VoucherTypeKind.PURCHASE_ORDER,
    }
)


class BillAllocation(BaseModel):
    """Which bill a party amount belongs to."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    #: ``New Ref`` on an invoice that creates the debt, ``Agst Ref`` on the
    #: receipt that settles it, ``On Account`` when it settles nothing named.
    method: str = "Agst Ref"
    amount: Decimal


class DraftLedgerEntry(BaseModel):
    """One side of a draft entry.

    ``amount`` is signed here, unlike :class:`~tally_core.domain.money.Money`,
    and it is signed in *Tally's* convention: **negative is a debit, positive is
    a credit** (CLAUDE.md §4). The draft is the one place in this codebase that
    speaks Tally's convention directly, because it is the thing being handed to
    Tally, and converting at the boundary is easier to check than converting in
    the middle of a builder.
    """

    model_config = ConfigDict(frozen=True)

    ledger_name: str = Field(min_length=1)
    amount: Decimal
    #: Tally's own flag for which side this is. Kept explicit rather than
    #: derived from the sign, because Tally reads both and disagreeing with
    #: itself is how an entry posts backwards.
    is_deemed_positive: bool
    #: Bills this settles. A receipt against named bills clears those bills; one
    #: without them lands on account and leaves the invoices showing as unpaid.
    bill_references: list[BillAllocation] = Field(default_factory=list)

    @field_validator("ledger_name")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("a ledger name cannot be blank")
        return trimmed


class DraftInventoryEntry(BaseModel):
    """One stock line on a draft."""

    model_config = ConfigDict(frozen=True)

    item_name: str = Field(min_length=1)
    quantity: Decimal
    rate: Decimal | None = None
    amount: Decimal
    unit: str | None = None
    godown: str | None = None
    #: The ledger this line's value posts to (``Sales``, ``Purchase``). Tally
    #: needs it per line, not per voucher: a shop with taxed and exempt goods
    #: books them to different sales ledgers on the same invoice.
    ledger_name: str | None = None


class VoucherDraft(BaseModel):
    """A voucher a phone is asking Tally to record."""

    model_config = ConfigDict(frozen=True)

    kind: VoucherTypeKind
    #: The voucher *type* as the customer's own Tally names it ("Tax Invoice"),
    #: which is not the same as the kind. Left unset, the connector uses the
    #: canonical name for the kind, which every company has.
    voucher_type_name: str | None = None
    date: date
    #: Tally allocates the number when it is left unset, which is what should
    #: normally happen: a phone that picks its own number races every other
    #: person entering vouchers at the counter.
    voucher_number: str | None = None
    party_name: str | None = None
    narration: str | None = None
    reference: str | None = None
    ledger_entries: list[DraftLedgerEntry] = Field(default_factory=list)
    inventory_entries: list[DraftInventoryEntry] = Field(default_factory=list)
    #: Recorded but kept out of the books until approved inside TallyPrime.
    optional: bool = True

    @field_validator("kind")
    @classmethod
    def _writeable(cls, value: VoucherTypeKind) -> VoucherTypeKind:
        if value not in WRITEABLE_KINDS:
            raise ValueError(f"{value} cannot be created from the app")
        return value

    @property
    def is_balanced(self) -> bool:
        """Whether both sides of the entry sum to zero, as double entry requires.

        Orders are exempt: a sales or purchase order moves no money and carries
        no accounting entries at all, so an empty or one-sided order is correct
        rather than broken.

        **A stock line carries its own side of the entry.** On an invoice, the
        sales ledger is credited inside the line's accounting allocation rather
        than as a ledger entry beside it, so the sum has to include those or a
        perfectly good invoice looks lopsided. Sending the sales ledger *both*
        ways is the mistake this exists to make visible: it credits the ledger
        twice for one invoice.
        """
        if self.kind in {VoucherTypeKind.SALES_ORDER, VoucherTypeKind.PURCHASE_ORDER}:
            return True
        if not self.ledger_entries:
            return False
        total = sum((entry.amount for entry in self.ledger_entries), Decimal("0"))
        return total + self.allocated == 0

    @property
    def allocated(self) -> Decimal:
        """The value stock lines post to a ledger, signed Tally's way.

        Only lines that name a ledger count. A line without one is inventory
        movement with no accounting behind it -- a delivery note's shape -- and
        adding it to the balance would make every such voucher unbalanced.
        """
        return sum(
            (entry.amount for entry in self.inventory_entries if entry.ledger_name),
            Decimal("0"),
        )

    @property
    def total(self) -> Decimal:
        """The voucher's face value — the sum of one side, not of both."""
        return sum(
            (entry.amount for entry in self.ledger_entries if entry.amount > 0),
            Decimal("0"),
        )


#: The groups a party ledger may be created under.
#:
#: Two, and named rather than free text. A ledger's group decides which side of
#: the balance sheet it lands on and whether it appears in receivables or
#: payables, so letting a phone send any string would let somebody file a
#: customer under Indirect Expenses with nothing to stop them.
PARTY_GROUPS: dict[str, str] = {
    "customer": "Sundry Debtors",
    "supplier": "Sundry Creditors",
}


class LedgerDraft(BaseModel):
    """A party the app is asking TallyPrime to create.

    Deliberately minimal. A ledger in Tally carries dozens of fields -- credit
    limits, tax registrations, opening balances -- and every one of them is a
    decision about somebody's books that belongs in TallyPrime rather than in a
    form filled in at a counter. This creates a name under a group and nothing
    else, so the worst a mistake can do is add an empty ledger.

    **No opening balance, ever.** That is money, and inventing it from a phone
    would change a trial balance on the strength of a typo.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=300)
    #: ``customer`` or ``supplier``; the group is looked up, never sent raw.
    role: str = "customer"
    #: Bill-by-bill tracking. On by default because a party ledger without it
    #: cannot carry the outstanding bills that half this product reports on.
    bill_wise: bool = True
    gstin: str | None = Field(default=None, max_length=20)

    @field_validator("name")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("a ledger name cannot be blank")
        return trimmed

    @field_validator("role")
    @classmethod
    def _known_role(cls, value: str) -> str:
        if value not in PARTY_GROUPS:
            raise ValueError(f"role must be one of {', '.join(PARTY_GROUPS)}")
        return value

    @property
    def parent(self) -> str:
        return PARTY_GROUPS[self.role]


class StockItemDraft(BaseModel):
    """A stock item the app is asking TallyPrime to create.

    As minimal as :class:`LedgerDraft`, and for the same reason. **No opening
    quantity and no rate**: both are figures that would move a stock valuation.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=300)
    #: Tally's own name for "no group", which every company has.
    parent: str = "Primary"
    #: The unit has to already exist in Tally. Left unset, the item is created
    #: without one, which Tally allows and which a shop can fix in one screen.
    unit: str | None = Field(default=None, max_length=32)

    @field_validator("name")
    @classmethod
    def _trimmed(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("a stock item name cannot be blank")
        return trimmed


class MasterCreated(BaseModel):
    """What Tally reported after a master import."""

    model_config = ConfigDict(frozen=True)

    created: int = 0
    altered: int = 0
    errors: int = 0
    exceptions: int = 0
    name: str = ""
    message: str | None = None

    @property
    def ok(self) -> bool:
        # ``altered`` counts too: a master that already existed is not a
        # failure from the caller's point of view. They asked for it to exist.
        return (self.created + self.altered) > 0 and not (self.errors or self.exceptions)


class VoucherPosted(BaseModel):
    """What Tally reported after an import.

    Every count comes off Tally's own ``<IMPORTRESULT>`` rather than being
    inferred from the absence of an error. Verified live 2026-09-17: Tally
    answers with this block even for an empty payload, so a caller can always
    distinguish "created nothing" from "never got there".
    """

    model_config = ConfigDict(frozen=True)

    created: int = 0
    altered: int = 0
    ignored: int = 0
    errors: int = 0
    exceptions: int = 0
    #: Tally's internal id for the voucher just written. It is what lets the
    #: phone find the entry again, and it is the only handle that exists before
    #: Tally has allocated a voucher number.
    voucher_id: int | None = None
    #: Tally's own complaint, when it made one. Shown to the person who tried
    #: to create the entry, because "Ledger 'Ram Traders' does not exist" is
    #: something they can act on and a generic failure is not.
    message: str | None = None
    #: Whether the entry landed as optional, and so is waiting for somebody to
    #: approve it in TallyPrime rather than already counting.
    optional: bool = True
    #: Something the entry named is not in the books yet, as ``("ledger",
    #: "Ram Traders")``.
    #:
    #: Parsed from Tally's own complaint on the server rather than matched on
    #: the client, so the app can offer to create exactly that thing instead of
    #: doing regular expressions on an error message it was handed.
    missing_kind: str | None = None
    missing_name: str | None = None
    #: Held on the server until the PC comes back, rather than saved or lost.
    #:
    #: A queued entry is *not* ``ok`` -- nothing is in the books yet -- so
    #: anything deciding "did this work?" has to look at both, and the app says
    #: something different for each.
    queued: bool = False
    #: The queue row, so the phone can show or cancel it.
    pending_id: str | None = None
    #: Whether sending this again is known to be safe.
    #:
    #: Only ever true when something can *prove* nothing was written -- the PC
    #: was offline, TallyPrime was closed, the company was not open. After a
    #: timeout or a mid-request disconnect this stays false, because Tally can
    #: finish an import and lose the reply and no evidence afterwards separates
    #: that from a write that never arrived. A "Try again" button in the wrong
    #: place is how one receipt becomes two.
    can_retry: bool = False

    @property
    def ok(self) -> bool:
        return self.created > 0 and self.errors == 0 and self.exceptions == 0
