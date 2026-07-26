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


class Voucher(BaseModel):
    """A posted transaction."""

    model_config = ConfigDict(frozen=True)

    voucher_number: str | None = None
    voucher_type: str
    kind: VoucherTypeKind = VoucherTypeKind.OTHER
    date: date
    guid: str | None = None
    party_name: str | None = None
    narration: str | None = None
    reference: str | None = None
    amount: Money = Field(default_factory=Money.zero)
    is_cancelled: bool = False
    is_optional: bool = False
    ledger_entries: list[LedgerEntry] = Field(default_factory=list)
    inventory_entries: list[InventoryEntry] = Field(default_factory=list)

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
