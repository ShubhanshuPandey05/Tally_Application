"""The "Adjustment Details" table of a receipt or payment print.

A receipt allocates its amount against bills -- "SMA26/002, Agst Ref, 2,100" --
and that is all the voucher itself says. The print a business sends puts the
bill beside it: when it was raised, on what credit, for how much, and what is
still pending once this receipt is counted. None of that is on the receipt.
It is on the invoice that raised the bill and on every voucher that settled
part of it since, all of which the history sync already holds.

**Pending is printed only when it can be right.** It is the bill less every
settlement of it, and a settlement the store does not hold makes it a
confident wrong figure. So the bill's own voucher must be found in the
history read here: every settlement comes after the bill, so a window that
holds the bill holds them too. A bill raised before the window -- an opening
bill, or one older than two years -- keeps its name and this voucher's amount
and leaves the rest blank, which is what a hand-made print would do with a
bill nobody could look up.

Everything here is arithmetic on stored vouchers. Nothing asks the shop's
Tally, because somebody tapping Share is not a reason to run an export on the
PC that is also their till.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from tally_core.domain.masters import VoucherTypeKind
from tally_core.domain.money import Money, Side
from tally_core.domain.transactions import BillLine, LedgerEntry, Voucher

from .analytics import money_out, voucher_key

#: Bill types that raise a bill rather than settle one. Tally's own words.
_RAISING = {"new ref"}

#: Allocations that are not against any bill: printed with their amount and
#: nothing to look up.
_UNMATCHED = {"advance", "on account"}

#: Vouchers whose settlement of a bill is a note rather than money, which the
#: print counts in a column of its own -- "C/Note Amt." on a receipt, "D/Note
#: Amt." on a payment.
_NOTE_KINDS = {VoucherTypeKind.CREDIT_NOTE, VoucherTypeKind.DEBIT_NOTE}


def _fold(name: str | None) -> str:
    return " ".join((name or "").split()).casefold()


@dataclass(frozen=True)
class _Allocation:
    voucher: Voucher
    bill: BillLine
    #: The allocation's own amount when Tally sent one, else the line's when
    #: the line carried exactly one bill -- which is what an allocation of a
    #: single-bill line is. ``None`` when neither can be said.
    amount: Money | None


def _allocations(entry: LedgerEntry, voucher: Voucher) -> list[_Allocation]:
    """Every bill on one line, from the full allocations or from bare names.

    A voucher read before allocations carried their type and amount still has
    the names, and the names are enough to find the bill.
    """
    bills = list(entry.bills) or [BillLine(name=name) for name in entry.bill_references]
    single = len(bills) == 1
    return [
        _Allocation(
            voucher=voucher,
            bill=bill,
            amount=bill.amount if bill.amount is not None else (entry.amount if single else None),
        )
        for bill in bills
        if bill.name.strip()
    ]


def _party_lines(voucher: Voucher) -> list[LedgerEntry]:
    flagged = [e for e in voucher.ledger_entries if e.is_party]
    if flagged:
        return flagged
    party = _fold(voucher.party_name)
    return [e for e in voucher.ledger_entries if party and _fold(e.ledger_name) == party]


def _is_raising(allocation: _Allocation, settling_side: Side | None) -> bool:
    kind = _fold(allocation.bill.bill_type)
    if kind in _RAISING:
        return True
    if kind:
        return False
    # No type, from a voucher read before types were: the side decides. A bill
    # is raised on the side opposite the one that settles it.
    return (
        allocation.amount is not None
        and settling_side is not None
        and allocation.amount.side is not settling_side
    )


def adjustments(voucher: Voucher, history: list[Voucher] | None) -> list[dict[str, Any]]:
    """One row per bill this voucher's party line was allocated against.

    ``history`` is every stored voucher the bills could have been raised and
    settled in, ``voucher`` itself included or not. ``None`` means the store
    could not be read for a window worth searching: every row then carries
    only what this voucher says about itself.
    """
    own_key = voucher_key(voucher)
    others = [
        v
        for v in (history or [])
        if v.date <= voucher.date and voucher_key(v) != own_key
    ]
    # Oldest first, so the first raising allocation found is the bill itself.
    others.sort(key=lambda v: (v.date, v.voucher_number or ""))

    rows: list[dict[str, Any]] = []
    for line in _party_lines(voucher):
        ledger = _fold(line.ledger_name)
        prior = [
            allocation
            for other in others
            for entry in other.ledger_entries
            if _fold(entry.ledger_name) == ledger
            for allocation in _allocations(entry, other)
        ]
        for own in _allocations(line, voucher):
            rows.append(_row(own, prior, searched=history is not None))
    return rows


def _row(own: _Allocation, prior: list[_Allocation], *, searched: bool) -> dict[str, Any]:
    paid = own.amount
    settling_side = paid.side if paid is not None else None
    kind = _fold(own.bill.bill_type)

    raised: _Allocation | None = None
    if searched and kind not in _RAISING and kind not in _UNMATCHED:
        name = _fold(own.bill.name)
        same_bill = [a for a in prior if _fold(a.bill.name) == name]
        raised = next((a for a in same_bill if _is_raising(a, settling_side)), None)

    bill_amount: Money | None = None
    notes = Decimal(0)
    pending: Decimal | None = None
    bill_date = own.bill.bill_date
    credit_days = own.bill.credit_days

    if raised is not None and raised.amount is not None and paid is not None:
        bill_amount = raised.amount
        bill_date = raised.bill.bill_date or raised.voucher.date
        if raised.bill.credit_days is not None:
            credit_days = raised.bill.credit_days
        settled = Decimal(0)
        # Every later allocation of the same bill on the settling side: earlier
        # receipts, and notes. Raised-side allocations after the first are the
        # bill being added to, which the bill amount above already is not.
        for allocation in prior:
            if allocation is raised or allocation.amount is None:
                continue
            if _fold(allocation.bill.name) != _fold(own.bill.name):
                continue
            if allocation.voucher.date < raised.voucher.date:
                continue
            if allocation.amount.side is not paid.side:
                continue
            if allocation.voucher.kind in _NOTE_KINDS:
                notes += allocation.amount.amount
            else:
                settled += allocation.amount.amount
        pending = bill_amount.amount - notes - settled - paid.amount

    currency = (paid or bill_amount or Money.zero()).currency
    return {
        "name": own.bill.name,
        "bill_type": own.bill.bill_type,
        "bill_date": bill_date.isoformat() if isinstance(bill_date, date) else None,
        "credit_days": credit_days,
        "bill_amount": _magnitude(bill_amount),
        "note_amount": _plain(notes, currency) if bill_amount is not None and notes else None,
        "paid_amount": _magnitude(paid),
        # Null, never zero, when the bill could not be found: "nothing pending"
        # and "we cannot say" are different statements on a receipt somebody
        # is going to file.
        "pending_amount": _plain(pending, currency) if pending is not None else None,
    }


def _magnitude(value: Money | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return _plain(value.amount, value.currency)


def _plain(amount: Decimal, currency: str) -> dict[str, Any]:
    """A plain amount, debit-positive in ``signed``.

    A negative pending -- a bill settled for more than it was raised -- keeps
    its sign in ``signed`` so the print can show the minus, rather than being
    clamped to a nil that would hide the overpayment.
    """
    return money_out(
        Money(amount=abs(amount), side=Side.CREDIT if amount < 0 else Side.DEBIT, currency=currency)
    )
