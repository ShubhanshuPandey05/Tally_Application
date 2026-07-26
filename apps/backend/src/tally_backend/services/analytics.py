"""Turning Tally reads into the numbers a business owner actually asked for.

CLAUDE.md's success criteria are six questions -- how much did I sell today, who
owes me money, what is my cash position, what is running out, what did I make,
what changed since yesterday. This module answers them from the raw datasets.

It works on rehydrated domain models rather than the JSON in the snapshot,
because the accounting rules live there: ``Voucher.is_effective`` knows that a
cancelled voucher must never reach a total, and ``OutstandingBill.ageing_bucket``
knows the bucket boundaries. Re-implementing either against dicts is how two
screens end up disagreeing about the same number.

**Voucher value.** A voucher's value is taken as the total of one side of its
entries, not the ``AMOUNT`` field, which live Tally often omits on accounting
vouchers. Debits equal credits on every posted voucher -- verified against a
live instance -- so either side gives the gross invoice total, which is what a
shop owner means by "today's sales" and what Tally's own Sales Register shows.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from tally_core.domain.masters import Ledger, StockItem, VoucherType, VoucherTypeKind
from tally_core.domain.money import Money, Side
from tally_core.domain.transactions import OutstandingBill, OutstandingKind, Voucher

#: Tally's standard primary groups for liquid funds. Matched case-insensitively
#: against a ledger's parent group. Customers do rename groups, so the company
#: setup flow is expected to let them correct this later.
CASH_GROUPS = {"cash-in-hand", "cash in hand"}
BANK_GROUPS = {
    "bank accounts",
    "bank account",
    "bank od a/c",
    "bank od account",
    "bank occ a/c",
}

TWO_PLACES = Decimal("0.01")


# --------------------------------------------------------------------------
# Serialisation helpers
# --------------------------------------------------------------------------


def money_out(value: Money) -> dict[str, Any]:
    """Render Money for the API.

    All three of magnitude, side and signed value are emitted. The app should
    not have to re-derive an accounting sign convention, and ``signed`` is
    debit-positive so an asset balance reads positive when healthy and negative
    when overdrawn -- which is what a chart axis needs.
    """
    return {
        "amount": str(value.amount),
        "side": str(value.side),
        "signed": str(value.signed),
        "currency": value.currency,
        "display": str(value),
    }


def _zero() -> Money:
    return Money.zero()


def _sum(values: list[Money]) -> Money:
    total = Money.zero()
    for value in values:
        total = total + value
    return total


def magnitude(value: Money) -> Money:
    """Drop the side, keep the size. For KPIs where direction is in the label."""
    return Money(amount=value.amount, side=Side.DEBIT, currency=value.currency)


# --------------------------------------------------------------------------
# Rehydration
# --------------------------------------------------------------------------


def parse_vouchers(payload: Any) -> list[Voucher]:
    return [Voucher.model_validate(item) for item in payload or []]


def parse_ledgers(payload: Any) -> list[Ledger]:
    return [Ledger.model_validate(item) for item in payload or []]


def parse_bills(payload: Any) -> list[OutstandingBill]:
    return [OutstandingBill.model_validate(item) for item in payload or []]


def parse_stock(payload: Any) -> list[StockItem]:
    return [StockItem.model_validate(item) for item in payload or []]


def parse_voucher_types(payload: Any) -> list[VoucherType]:
    return [VoucherType.model_validate(item) for item in payload or []]


# --------------------------------------------------------------------------
# Voucher classification
# --------------------------------------------------------------------------


def voucher_kind_map(types: list[VoucherType]) -> dict[str, VoucherTypeKind]:
    """Voucher-type name -> accounting class, for one company.

    A voucher row identifies its type only by *name*. Shops rename those freely
    -- "Tax Invoice", "GST Sales", "Retail Bill" are all Sales -- and a live
    TallyPrime does not send a ``<PARENT>`` on the voucher itself, so a voucher
    read in isolation cannot be classified correctly. The VoucherType master
    does carry the parent, so this map is the only reliable source.
    """
    return {
        name.strip().casefold(): voucher_type.kind
        for voucher_type in types
        if (name := voucher_type.name)
    }


def classify(vouchers: list[Voucher], kinds: dict[str, VoucherTypeKind]) -> list[Voucher]:
    """Re-label vouchers using the company's own voucher-type masters.

    Only ever *improves* a classification: an unmapped type, or one the master
    itself reports as OTHER, keeps whatever the parser derived from the name. A
    missing or failed voucher-type read therefore degrades to the old behaviour
    rather than blanking every figure on the dashboard.

    This runs at read time, not at parse time in the connector, for two reasons:
    the map is company-wide and a single voucher cannot see it, and it means
    snapshots captured before this existed are corrected on the way out instead
    of needing a re-fetch from every customer's PC.
    """
    if not kinds:
        return vouchers

    result: list[Voucher] = []
    for voucher in vouchers:
        mapped = kinds.get(voucher.voucher_type.strip().casefold())
        if mapped is None or mapped is VoucherTypeKind.OTHER or mapped is voucher.kind:
            result.append(voucher)
        else:
            result.append(voucher.model_copy(update={"kind": mapped}))
    return result


# --------------------------------------------------------------------------
# Vouchers
# --------------------------------------------------------------------------


def voucher_value(voucher: Voucher) -> Money:
    """Gross value of a voucher, as a magnitude."""
    if voucher.ledger_entries:
        debits = _sum(
            [e.amount for e in voucher.ledger_entries if e.amount.side is Side.DEBIT]
        )
        if not debits.is_zero:
            return magnitude(debits)
        credits = _sum(
            [e.amount for e in voucher.ledger_entries if e.amount.side is Side.CREDIT]
        )
        if not credits.is_zero:
            return magnitude(credits)
    return magnitude(voucher.amount)


def effective(vouchers: list[Voucher]) -> list[Voucher]:
    """Drop cancelled and optional vouchers. They are not real transactions."""
    return [v for v in vouchers if v.is_effective]


def total_for(
    vouchers: list[Voucher],
    kind: VoucherTypeKind,
    *,
    on: date | None = None,
    since: date | None = None,
    until: date | None = None,
) -> Money:
    total = Money.zero()
    for voucher in vouchers:
        if voucher.kind is not kind:
            continue
        if on is not None and voucher.date != on:
            continue
        if since is not None and voucher.date < since:
            continue
        if until is not None and voucher.date > until:
            continue
        total = total + voucher_value(voucher)
    return magnitude(total)


def daily_series(
    vouchers: list[Voucher], kind: VoucherTypeKind, *, since: date, until: date
) -> list[dict[str, Any]]:
    """One point per day, including days with no activity.

    Gaps must be zeroes rather than missing points, or a chart will draw a
    straight line across a week the shop was closed and imply steady trade.
    """
    totals: dict[date, Money] = defaultdict(Money.zero)
    for voucher in vouchers:
        if voucher.kind is kind and since <= voucher.date <= until:
            totals[voucher.date] = totals[voucher.date] + voucher_value(voucher)

    series = []
    day = since
    while day <= until:
        series.append({"date": day.isoformat(), "value": str(magnitude(totals[day]).amount)})
        day += timedelta(days=1)
    return series


def top_parties(
    vouchers: list[Voucher], kind: VoucherTypeKind, *, limit: int = 5
) -> list[dict[str, Any]]:
    totals: dict[str, Money] = defaultdict(Money.zero)
    counts: dict[str, int] = defaultdict(int)

    for voucher in vouchers:
        if voucher.kind is not kind or not voucher.party_name:
            continue
        totals[voucher.party_name] = totals[voucher.party_name] + voucher_value(voucher)
        counts[voucher.party_name] += 1

    ranked = sorted(totals.items(), key=lambda kv: magnitude(kv[1]).amount, reverse=True)
    return [
        {
            "name": name,
            "amount": money_out(magnitude(total)),
            "voucher_count": counts[name],
        }
        for name, total in ranked[:limit]
    ]


def top_products(
    vouchers: list[Voucher], kind: VoucherTypeKind = VoucherTypeKind.SALES, *, limit: int = 5
) -> list[dict[str, Any]]:
    values: dict[str, Money] = defaultdict(Money.zero)
    quantities: dict[str, float] = defaultdict(float)
    units: dict[str, str | None] = {}

    for voucher in vouchers:
        if voucher.kind is not kind:
            continue
        for entry in voucher.inventory_entries:
            values[entry.item_name] = values[entry.item_name] + entry.amount
            quantities[entry.item_name] += abs(entry.quantity)
            units.setdefault(entry.item_name, entry.unit)

    ranked = sorted(values.items(), key=lambda kv: magnitude(kv[1]).amount, reverse=True)
    return [
        {
            "name": name,
            "amount": money_out(magnitude(total)),
            "quantity": round(quantities[name], 3),
            "unit": units.get(name),
        }
        for name, total in ranked[:limit]
    ]


def recent_transactions(vouchers: list[Voucher], *, limit: int = 10) -> list[dict[str, Any]]:
    ordered = sorted(vouchers, key=lambda v: (v.date, v.voucher_number or ""), reverse=True)
    return [
        {
            "date": v.date.isoformat(),
            "voucher_number": v.voucher_number,
            "voucher_type": v.voucher_type,
            "kind": str(v.kind),
            "party": v.party_name,
            "narration": v.narration,
            "amount": money_out(voucher_value(v)),
        }
        for v in ordered[:limit]
    ]


# --------------------------------------------------------------------------
# Balances
# --------------------------------------------------------------------------


def group_balance(ledgers: list[Ledger], groups: set[str]) -> Money:
    total = Money.zero()
    for ledger in ledgers:
        parent = (ledger.parent_group or "").strip().lower()
        if parent in groups:
            total = total + ledger.closing_balance
    return total


def balance_lines(ledgers: list[Ledger], groups: set[str]) -> list[dict[str, Any]]:
    lines = [
        {"name": led.name, "balance": money_out(led.closing_balance)}
        for led in ledgers
        if (led.parent_group or "").strip().lower() in groups
    ]
    return sorted(lines, key=lambda item: item["name"])


# --------------------------------------------------------------------------
# Outstanding
# --------------------------------------------------------------------------


def outstanding_summary(
    bills: list[OutstandingBill], kind: OutstandingKind, *, as_of: date
) -> dict[str, Any]:
    """Total, overdue split, and standard ageing buckets."""
    relevant = [b for b in bills if b.kind is kind]

    total = Money.zero()
    overdue = Money.zero()
    buckets: dict[str, Money] = defaultdict(Money.zero)

    for bill in relevant:
        amount = magnitude(bill.pending_amount)
        total = total + amount
        bucket = bill.ageing_bucket(as_of)
        buckets[bucket] = buckets[bucket] + amount
        if bucket != "not_due":
            overdue = overdue + amount

    return {
        "total": money_out(magnitude(total)),
        "overdue": money_out(magnitude(overdue)),
        "bill_count": len(relevant),
        "party_count": len({b.party_name for b in relevant}),
        "ageing": {
            name: money_out(magnitude(buckets[name]))
            for name in ("not_due", "1_30", "31_60", "61_90", "91_180", "180_plus")
        },
    }


def top_outstanding_parties(
    bills: list[OutstandingBill], kind: OutstandingKind, *, as_of: date, limit: int = 5
) -> list[dict[str, Any]]:
    totals: dict[str, Money] = defaultdict(Money.zero)
    oldest: dict[str, int] = defaultdict(int)

    for bill in bills:
        if bill.kind is not kind:
            continue
        totals[bill.party_name] = totals[bill.party_name] + magnitude(bill.pending_amount)
        oldest[bill.party_name] = max(oldest[bill.party_name], bill.days_overdue(as_of))

    ranked = sorted(totals.items(), key=lambda kv: magnitude(kv[1]).amount, reverse=True)
    return [
        {
            "name": name,
            "amount": money_out(magnitude(total)),
            "days_overdue": oldest[name],
        }
        for name, total in ranked[:limit]
    ]


# --------------------------------------------------------------------------
# Inventory
# --------------------------------------------------------------------------


def inventory_summary(items: list[StockItem]) -> dict[str, Any]:
    total = Money.zero()
    for item in items:
        total = total + item.closing_value

    negative = [i for i in items if i.is_negative_stock]
    low = [i for i in items if i.is_below_reorder and not i.is_negative_stock]

    return {
        "value": money_out(magnitude(total)),
        "item_count": len(items),
        "negative_stock_count": len(negative),
        "low_stock_count": len(low),
        # Negative stock means goods were billed out that the books say were
        # never received -- it is a data-entry error worth surfacing, not a
        # rounding detail, so a few examples ride along with the count.
        "negative_stock": [_stock_line(i) for i in negative[:5]],
        "low_stock": [_stock_line(i) for i in low[:5]],
    }


def _stock_line(item: StockItem) -> dict[str, Any]:
    return {
        "name": item.name,
        "quantity": item.closing_quantity,
        "unit": item.base_unit,
        "reorder_level": item.reorder_level,
        "value": money_out(magnitude(item.closing_value)),
    }


def slow_moving(
    items: list[StockItem], vouchers: list[Voucher], *, limit: int = 10
) -> list[dict[str, Any]]:
    """Stock on hand that no sales voucher in the window touched.

    "Which products aren't selling?" from CLAUDE.md. Only as trustworthy as the
    voucher window it is given, so callers should pass a meaningful period.
    """
    sold = {
        entry.item_name
        for voucher in vouchers
        if voucher.kind is VoucherTypeKind.SALES
        for entry in voucher.inventory_entries
    }
    idle = [i for i in items if i.name not in sold and i.closing_quantity > 0]
    idle.sort(key=lambda i: i.closing_value.amount, reverse=True)
    return [_stock_line(item) for item in idle[:limit]]
