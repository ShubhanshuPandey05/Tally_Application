"""Domain models shared by the connector, the backend API and (via codegen) Flutter.

Nothing in here knows that Tally exists. Mapping happens in ``tally_core.tally.mappers``.
"""

from .masters import (
    BankAccount,
    Company,
    CompanyProfile,
    GodownStock,
    Ledger,
    LedgerGroup,
    StockItem,
    StockUnit,
    TallyLicence,
    VoucherType,
    VoucherTypeKind,
)
from .money import Money, Side
from .transactions import (
    BalanceSnapshot,
    BankDetails,
    BillLine,
    InventoryEntry,
    InvoiceDetails,
    LedgerEntry,
    OutstandingBill,
    OutstandingKind,
    TrialBalanceRow,
    Voucher,
    VoucherParty,
)

__all__ = [
    "BalanceSnapshot",
    "BankAccount",
    "BankDetails",
    "BillLine",
    "Company",
    "CompanyProfile",
    "GodownStock",
    "InventoryEntry",
    "InvoiceDetails",
    "Ledger",
    "LedgerEntry",
    "LedgerGroup",
    "Money",
    "OutstandingBill",
    "OutstandingKind",
    "Side",
    "StockItem",
    "StockUnit",
    "TallyLicence",
    "TrialBalanceRow",
    "Voucher",
    "VoucherParty",
    "VoucherType",
    "VoucherTypeKind",
]
