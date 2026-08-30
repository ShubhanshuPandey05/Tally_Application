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
from tally_core.domain.transactions import (
    InventoryEntry,
    LedgerEntry,
    OutstandingBill,
    OutstandingKind,
    Voucher,
)

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

#: Where parties live in a stock Indian chart of accounts. Only a default: the
#: group-outstanding report takes an explicit group name, so a company that
#: renamed these or files parties under sub-groups can still be read correctly.
DEFAULT_PARTY_GROUP = {
    OutstandingKind.RECEIVABLE: "Sundry Debtors",
    OutstandingKind.PAYABLE: "Sundry Creditors",
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
    """Newest first, in the row shape every list in the product shares.

    Goes through :func:`_row_out` rather than building its own dict so these
    rows carry a ``key``: the dashboard's recent activity and the day book both
    have to open the same voucher detail as everything else, and a row without
    an identity is a dead end the user can see but not follow.
    """
    ordered = sorted(vouchers, key=lambda v: (v.date, v.voucher_number or ""), reverse=True)
    return [_row_out(v) for v in ordered[:limit]]


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


def bill_line(bill: OutstandingBill, as_of: date) -> dict[str, Any]:
    """One bill, as both outstanding endpoints emit it.

    Shared so the flat report and the group report cannot drift into two
    slightly different bill shapes -- the app decodes both with one parser.
    """
    return {
        "party": bill.party_name,
        "bill_name": bill.bill_name,
        "bill_date": bill.bill_date.isoformat() if bill.bill_date else None,
        "due_date": bill.due_date.isoformat() if bill.due_date else None,
        "amount": money_out(magnitude(bill.pending_amount)),
        "days_overdue": bill.days_overdue(as_of),
        "ageing_bucket": bill.ageing_bucket(as_of),
        "is_advance": bill.is_advance,
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
# Group outstanding
# --------------------------------------------------------------------------


def _norm(name: str) -> str:
    """Fold a Tally name for comparison.

    Tally preserves whatever a shop typed, so the same party can arrive as
    "SARA  DISITIBUTOR" from one collection and "Sara Disitibutor" from
    another. Matching raw would drop that party out of its own group.
    """
    return " ".join(name.split()).casefold()


def party_group_index(ledgers: list[Ledger]) -> dict[str, str]:
    """Normalised ledger name -> its direct parent group."""
    return {
        _norm(led.name): led.parent_group for led in ledgers if led.parent_group
    }


def group_outstanding(
    bills: list[OutstandingBill],
    ledgers: list[Ledger],
    *,
    group: str,
    kind: OutstandingKind,
    as_of: date,
) -> dict[str, Any]:
    """Outstanding for every party filed under one ledger group.

    The other axis to :func:`outstanding_summary`. That one splits bills by the
    side each bill closes on, which is the honest answer to "who owes me
    money": a customer's advance is a payable even though it sits in a debtor's
    ledger. This answers the question Tally's Group Outstanding report answers
    instead -- "show me my debtors" -- where a party belongs on group
    membership alone, whichever way each of their bills happens to point.

    Opposite-side bills are therefore kept and reported as ``advances`` rather
    than dropped. Dropping them would overstate the group by the size of every
    prepayment; folding them into the total would understate the debt that is
    actually chaseable. Both numbers are published, and ``net`` is their sum.

    Membership uses the ledger's **direct** parent group. A party filed under a
    home-made sub-group is counted as ungrouped rather than quietly claimed for
    its ancestor, and the count of those is returned so the app can say the
    report is incomplete instead of silently under-reporting.
    """
    wanted = _norm(group)
    index = party_group_index(ledgers)
    #: The side this group's bills are expected to close on. Anything else is
    #: an advance or a contra entry.
    expected = Side.DEBIT if kind is OutstandingKind.RECEIVABLE else Side.CREDIT

    members: dict[str, list[OutstandingBill]] = defaultdict(list)
    ungrouped: set[str] = set()
    for bill in bills:
        parent = index.get(_norm(bill.party_name))
        if parent is None:
            ungrouped.add(bill.party_name)
        elif _norm(parent) == wanted:
            members[bill.party_name].append(bill)

    total = Money.zero()
    advances = Money.zero()
    overdue = Money.zero()
    net = Money.zero()
    buckets: dict[str, Money] = defaultdict(Money.zero)
    bill_count = 0
    rows: list[tuple[int, Decimal, dict[str, Any]]] = []

    for party, party_bills in members.items():
        party_total = Money.zero()
        party_advances = Money.zero()
        party_net = Money.zero()
        worst = 0

        for bill in party_bills:
            amount = magnitude(bill.pending_amount)
            party_net = party_net + bill.pending_amount

            if bill.pending_amount.side is expected:
                party_total = party_total + amount
                bucket = bill.ageing_bucket(as_of)
                buckets[bucket] = buckets[bucket] + amount
                if bucket != "not_due":
                    overdue = overdue + amount
                worst = max(worst, bill.days_overdue(as_of))
            else:
                party_advances = party_advances + amount

        total = total + party_total
        advances = advances + party_advances
        net = net + party_net
        bill_count += len(party_bills)

        party_bills.sort(key=lambda b: b.days_overdue(as_of), reverse=True)
        rows.append(
            (
                worst,
                party_total.amount,
                {
                    "party": party,
                    "group": group,
                    "total": money_out(party_total),
                    "advances": money_out(party_advances),
                    "net": money_out(party_net),
                    "bill_count": len(party_bills),
                    "days_overdue": worst,
                    "bills": [bill_line(b, as_of) for b in party_bills],
                },
            )
        )

    # Worst overdue first, then largest: the order an owner works the phone in.
    rows.sort(key=lambda row: (row[0], row[1]), reverse=True)

    return {
        "group": group,
        "kind": str(kind),
        "summary": {
            "total": money_out(magnitude(total)),
            "overdue": money_out(magnitude(overdue)),
            "advances": money_out(magnitude(advances)),
            "net": money_out(net),
            "bill_count": bill_count,
            "party_count": len(members),
            "ageing": {
                name: money_out(magnitude(buckets[name]))
                for name in ("not_due", "1_30", "31_60", "61_90", "91_180", "180_plus")
            },
        },
        "parties": [row[2] for row in rows],
        # Parties with bills but no ledger row in this read. Not an error, but
        # the difference between "you have no other debtors" and "we could not
        # tell", which an owner deserves to see.
        "ungrouped_party_count": len(ungrouped),
    }


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


# --------------------------------------------------------------------------
# Drill-down
# --------------------------------------------------------------------------
#
# Everything below answers "show me *this* one, in full". The dashboard and the
# list reports summarise; these functions deliberately do not. A voucher detail
# that hid a ledger line, or a statement that rounded a movement away, would be
# the one screen in the product an accountant checks a real figure against --
# and it would be wrong.
#
# All of it is derived from vouchers the sync has already stored. No new read of
# a customer's TallyPrime happens because somebody tapped a row.


def voucher_key(voucher: Voucher) -> str:
    """The identity a stored voucher is filed under.

    Delegates to :func:`~.voucher_store.record_key` rather than reimplementing
    it. Two algorithms for one identity is how a drill-down ends up opening a
    different voucher from the row that was tapped, and the failure is invisible
    until somebody notices the amounts disagree.

    ``date`` is passed as an ISO string because that is what the stored payload
    holds -- the fallback key is a hash of those exact characters, and a
    ``date`` object would stringify differently and never match.
    """
    from .voucher_store import record_key

    return record_key(
        {
            "guid": voucher.guid,
            "master_id": voucher.master_id,
            "voucher_type": voucher.voucher_type,
            "voucher_number": voucher.voucher_number,
            "date": voucher.date.isoformat(),
            "party_name": voucher.party_name,
        }
    )


def _entry_out(entry: LedgerEntry) -> dict[str, Any]:
    """One ledger line.

    The side is kept rather than flattened to a magnitude: which line is Dr and
    which is Cr *is* the content of this row.
    """
    return {
        "ledger": entry.ledger_name,
        "amount": money_out(entry.amount),
        "is_party": entry.is_party,
        "cost_centre": entry.cost_centre,
        "bill_references": list(entry.bill_references),
    }


def _inventory_out(entry: InventoryEntry) -> dict[str, Any]:
    return {
        "item": entry.item_name,
        "quantity": round(abs(entry.quantity), 3),
        "unit": entry.unit,
        "rate": money_out(entry.rate) if entry.rate is not None else None,
        "amount": money_out(magnitude(entry.amount)),
        "godown": entry.godown,
        "batch": entry.batch,
    }


def voucher_detail(voucher: Voucher, *, inventory_kept: bool = True) -> dict[str, Any]:
    """Everything posted on one voucher.

    ``debit_total`` and ``credit_total`` are emitted so the app can show that
    the voucher balances. They are not decoration: sides that do not agree mean
    the read dropped a line, and showing both totals is the only way a reader
    can tell that from a voucher that genuinely has one entry.
    """
    debits = _sum([e.amount for e in voucher.ledger_entries if e.amount.side is Side.DEBIT])
    credits = _sum([e.amount for e in voucher.ledger_entries if e.amount.side is Side.CREDIT])

    return {
        "key": voucher_key(voucher),
        "date": voucher.date.isoformat(),
        "voucher_number": voucher.voucher_number,
        "voucher_type": voucher.voucher_type,
        "kind": str(voucher.kind),
        "party": voucher.party_name,
        "narration": voucher.narration,
        "reference": voucher.reference,
        "amount": money_out(voucher_value(voucher)),
        # Cancelled and optional vouchers are excluded from every total in this
        # product, so a detail screen that showed one without saying so would be
        # explaining a figure that is in none of the figures around it.
        "is_cancelled": voucher.is_cancelled,
        "is_optional": voucher.is_optional,
        "debit_total": money_out(magnitude(debits)),
        "credit_total": money_out(magnitude(credits)),
        "ledger_entries": [_entry_out(e) for e in voucher.ledger_entries],
        "inventory_entries": [_inventory_out(e) for e in voucher.inventory_entries],
        # Whether an empty stock list means "this voucher had no items" or
        # "we did not keep them for a voucher this old".
        #
        # The history sync drops inventory lines from slices older than
        # ``sync_inventory_days`` -- line items are what make a voucher export
        # large, and keeping four years of them would be what freezes a shop's
        # Tally during a backfill. That is the right trade, but it leaves this
        # screen able to show an old invoice with no Items card at all, which
        # states that the invoice sold nothing. Same rule as everywhere else in
        # the product: never render missing data as a figure.
        "inventory_omitted": not inventory_kept and not voucher.inventory_entries,
    }


def find_voucher(vouchers: list[Voucher], key: str) -> Voucher | None:
    for voucher in vouchers:
        if voucher_key(voucher) == key:
            return voucher
    return None


def _row_out(voucher: Voucher) -> dict[str, Any]:
    """The list-row shape, shared by every drill-down that lists vouchers.

    Carries ``key`` so every row in the product is tappable through to the same
    voucher detail, wherever the row was rendered.
    """
    return {
        "key": voucher_key(voucher),
        "date": voucher.date.isoformat(),
        "voucher_number": voucher.voucher_number,
        "voucher_type": voucher.voucher_type,
        "kind": str(voucher.kind),
        "party": voucher.party_name,
        "narration": voucher.narration,
        "amount": money_out(voucher_value(voucher)),
    }


def ledger_statement(
    vouchers: list[Voucher], *, ledger: str, since: date, until: date
) -> dict[str, Any]:
    """Every voucher that touched one ledger, with a running total.

    The running figure is the movement *within the requested window*, not the
    ledger's balance. Tally evaluates a closing balance against today and takes
    no date to evaluate against (see CLAUDE.md), so an opening balance for an
    arbitrary past window is not available to us -- and manufacturing one by
    subtracting backwards from today's closing would produce a figure an
    accountant could reconcile against nothing. The caller pairs this with the
    ledger's real closing balance and labels the two differently.

    A voucher can post to the same ledger more than once -- an invoice split
    across two cost centres, say. Those lines are netted into one row, because
    two rows for one voucher makes the running column step twice for a single
    transaction.
    """
    wanted = _norm(ledger)

    matched: list[tuple[Voucher, Money]] = []
    for voucher in vouchers:
        if not (since <= voucher.date <= until):
            continue
        lines = [e for e in voucher.ledger_entries if _norm(e.ledger_name) == wanted]
        if not lines:
            continue
        matched.append((voucher, _sum([line.amount for line in lines])))

    # Oldest first: a running total that counts down the page is not a running
    # total. The app reverses it for display if it wants newest at the top.
    matched.sort(key=lambda pair: (pair[0].date, pair[0].voucher_number or ""))

    rows: list[dict[str, Any]] = []
    running = Money.zero()
    debits = Money.zero()
    credits = Money.zero()
    for voucher, movement in matched:
        running = running + movement
        if movement.side is Side.DEBIT:
            debits = debits + movement
        else:
            credits = credits + movement
        rows.append(
            {
                **_row_out(voucher),
                "movement": money_out(movement),
                "running": money_out(running),
            }
        )

    return {
        "ledger": ledger,
        "from_date": since.isoformat(),
        "to_date": until.isoformat(),
        "voucher_count": len(rows),
        "debit_total": money_out(magnitude(debits)),
        "credit_total": money_out(magnitude(credits)),
        "net_movement": money_out(running),
        "entries": rows,
    }


_MONTH_NAMES = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


def register(
    vouchers: list[Voucher],
    kind: VoucherTypeKind,
    *,
    since: date,
    until: date,
    limit: int = 500,
) -> dict[str, Any]:
    """A sales or purchase register: the vouchers, plus the two cuts of them an
    owner actually asks for -- by month and by party.

    Both cuts are computed over the *whole* window rather than over the
    truncated row list. A monthly chart that only covered the first 500
    vouchers would disagree with the total printed above it, which is worse
    than showing no chart.
    """
    selected = [v for v in vouchers if v.kind is kind and since <= v.date <= until]
    selected.sort(key=lambda v: (v.date, v.voucher_number or ""), reverse=True)

    monthly: dict[str, Money] = defaultdict(Money.zero)
    counts: dict[str, int] = defaultdict(int)
    total = Money.zero()
    for voucher in selected:
        value = voucher_value(voucher)
        total = total + value
        bucket = f"{voucher.date.year:04d}-{voucher.date.month:02d}"
        monthly[bucket] = monthly[bucket] + value
        counts[bucket] += 1

    months = [
        {
            "month": bucket,
            "label": f"{_MONTH_NAMES[int(bucket[5:7]) - 1]} {bucket[:4]}",
            "total": money_out(magnitude(monthly[bucket])),
            "voucher_count": counts[bucket],
        }
        for bucket in sorted(monthly)
    ]

    return {
        "kind": str(kind),
        "from_date": since.isoformat(),
        "to_date": until.isoformat(),
        "voucher_count": len(selected),
        "total": money_out(magnitude(total)),
        "months": months,
        "by_party": top_parties(selected, kind, limit=20),
        "by_product": top_products(selected, kind, limit=20),
        # Truncated on purpose; ``voucher_count`` above is the honest count, so
        # a capped list never reads as a shorter register than the real one.
        "vouchers": [_row_out(v) for v in selected[:limit]],
        "truncated": len(selected) > limit,
    }


#: Voucher classes that bring stock in and take it out. Direction is taken from
#: the accounting class rather than from the sign on the quantity: Tally's
#: inventory lines are signed inconsistently between voucher types, and a
#: purchase counted as an outward movement turns a stock report into fiction.
_INWARD_KINDS = {
    VoucherTypeKind.PURCHASE,
    VoucherTypeKind.RECEIPT_NOTE,
    # A sales return brings the goods back onto the shelf.
    VoucherTypeKind.CREDIT_NOTE,
}
_OUTWARD_KINDS = {
    VoucherTypeKind.SALES,
    VoucherTypeKind.DELIVERY_NOTE,
    # A purchase return sends them back to the supplier.
    VoucherTypeKind.DEBIT_NOTE,
}


def item_movement(
    vouchers: list[Voucher], *, item: str, since: date, until: date
) -> dict[str, Any]:
    """Everything that moved one stock item in the window."""
    wanted = _norm(item)

    rows: list[dict[str, Any]] = []
    qty_in = qty_out = 0.0
    value_in = Money.zero()
    value_out = Money.zero()
    unit: str | None = None

    for voucher in vouchers:
        if not (since <= voucher.date <= until):
            continue
        lines = [e for e in voucher.inventory_entries if _norm(e.item_name) == wanted]
        if not lines:
            continue

        quantity = sum(abs(line.quantity) for line in lines)
        value = _sum([line.amount for line in lines])
        unit = unit or next((line.unit for line in lines if line.unit), None)

        if voucher.kind in _INWARD_KINDS:
            direction = "in"
            qty_in += quantity
            value_in = value_in + value
        elif voucher.kind in _OUTWARD_KINDS:
            direction = "out"
            qty_out += quantity
            value_out = value_out + value
        else:
            # Stock journals, physical verification, and anything a shop named
            # something we cannot classify. Counted in neither total rather than
            # guessed into one -- an owner reconciling a shortfall needs the row
            # visible and the arithmetic honest about not including it.
            direction = "other"

        rows.append(
            {
                **_row_out(voucher),
                "direction": direction,
                "quantity": round(quantity, 3),
                "unit": unit,
                "value": money_out(magnitude(value)),
            }
        )

    rows.sort(key=lambda row: row["date"], reverse=True)

    return {
        "item": item,
        "unit": unit,
        "from_date": since.isoformat(),
        "to_date": until.isoformat(),
        "voucher_count": len(rows),
        "quantity_in": round(qty_in, 3),
        "quantity_out": round(qty_out, 3),
        "net_quantity": round(qty_in - qty_out, 3),
        "value_in": money_out(magnitude(value_in)),
        "value_out": money_out(magnitude(value_out)),
        "movements": rows,
    }
