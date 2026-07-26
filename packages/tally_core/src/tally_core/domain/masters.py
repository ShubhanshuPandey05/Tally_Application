"""Master-data domain models: companies, groups, ledgers, stock items.

These are the internal representation the API and the Flutter app speak. They
deliberately drop Tally's field names and internal GUIDs-as-strings in favour of
stable, product-shaped names, so a Tally schema change is absorbed by a mapper
rather than rippling into the UI.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .money import Money


class Company(BaseModel):
    """A company loaded in TallyPrime."""

    model_config = ConfigDict(frozen=True)

    name: str
    guid: str | None = None
    financial_year_from: date | None = None
    books_from: date | None = None
    gstin: str | None = None
    state: str | None = None
    base_currency: str = "INR"


class LedgerGroup(BaseModel):
    """A Tally group (the tree that ledgers hang off)."""

    model_config = ConfigDict(frozen=True)

    name: str
    parent: str | None = None
    guid: str | None = None
    is_revenue: bool = False
    is_deemed_positive: bool = True
    primary_group: str | None = Field(
        default=None,
        description="Root-level ancestor, e.g. 'Sundry Debtors' -> 'Current Assets'.",
    )


class Ledger(BaseModel):
    """A ledger account with its current balance."""

    model_config = ConfigDict(frozen=True)

    name: str
    parent_group: str | None = None
    guid: str | None = None
    opening_balance: Money = Field(default_factory=Money.zero)
    closing_balance: Money = Field(default_factory=Money.zero)
    is_bill_wise: bool = False
    credit_period_days: int | None = None
    credit_limit: Money | None = None
    gstin: str | None = None
    phone: str | None = None
    email: str | None = None
    address: list[str] = Field(default_factory=list)
    state: str | None = None


class StockUnit(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    is_simple: bool = True


class StockItem(BaseModel):
    """An inventory item with closing quantity and value."""

    model_config = ConfigDict(frozen=True)

    name: str
    parent_group: str | None = None
    category: str | None = None
    guid: str | None = None
    base_unit: str | None = None
    closing_quantity: float = 0.0
    closing_value: Money = Field(default_factory=Money.zero)
    closing_rate: Money | None = None
    opening_quantity: float = 0.0
    opening_value: Money = Field(default_factory=Money.zero)
    reorder_level: float | None = None
    hsn_code: str | None = None
    gst_rate: float | None = None

    @property
    def is_negative_stock(self) -> bool:
        return self.closing_quantity < 0

    @property
    def is_below_reorder(self) -> bool:
        if self.reorder_level is None:
            return False
        return self.closing_quantity < self.reorder_level


class VoucherTypeKind(StrEnum):
    """Tally's parent voucher classes, normalised.

    Users rename voucher types freely ("Tax Invoice", "Retail Sale"), but the
    parent class stays stable -- that is what analytics must group on.
    """

    SALES = "sales"
    PURCHASE = "purchase"
    RECEIPT = "receipt"
    PAYMENT = "payment"
    CONTRA = "contra"
    JOURNAL = "journal"
    CREDIT_NOTE = "credit_note"
    DEBIT_NOTE = "debit_note"
    DELIVERY_NOTE = "delivery_note"
    RECEIPT_NOTE = "receipt_note"
    STOCK_JOURNAL = "stock_journal"
    PHYSICAL_STOCK = "physical_stock"
    OTHER = "other"

    @classmethod
    def from_parent(cls, parent: str | None) -> VoucherTypeKind:
        if not parent:
            return cls.OTHER
        key = parent.strip().lower().replace(" ", "_")
        aliases = {
            "sales": cls.SALES,
            "purchase": cls.PURCHASE,
            "receipt": cls.RECEIPT,
            "payment": cls.PAYMENT,
            "contra": cls.CONTRA,
            "journal": cls.JOURNAL,
            "credit_note": cls.CREDIT_NOTE,
            "debit_note": cls.DEBIT_NOTE,
            "delivery_note": cls.DELIVERY_NOTE,
            "receipt_note": cls.RECEIPT_NOTE,
            "stock_journal": cls.STOCK_JOURNAL,
            "physical_stock": cls.PHYSICAL_STOCK,
        }
        return aliases.get(key, cls.OTHER)


class VoucherType(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    parent: str | None = None
    kind: VoucherTypeKind = VoucherTypeKind.OTHER
    is_deemed_positive: bool = True
