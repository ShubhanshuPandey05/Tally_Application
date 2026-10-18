"""Top Prime Reports: ranked "who and what, most and least" tables.

Rebuilt from a TallyPrime TDL module a customer already used ("My Top
Reports"), so the rules here follow that module's specification rather than
inventing new ones: the same rows, the same columns, the same default sort and
the same F5 alternate. Where this product cannot see what Tally sees, the
report says so in ``notes`` instead of quietly computing something else.

Every report comes back in **one table shape** -- columns, rows, a TOTAL row --
so the app has one screen that renders all of them. A screen per report would
be twenty-odd copies of the same list that drift apart one fix at a time.

Rules common to every report (from the specification):

* Only regular vouchers count: not optional, not cancelled. The caller passes
  :func:`analytics.effective` vouchers.
* Voucher types are read by their accounting class, so a shop's own "Tax
  Invoice" counts as Sales. The caller passes classified vouchers.
* Only the first ``limit`` rows are shown, and the TOTAL row totals **only
  those rows** -- the figure in Tally's own report, and the one an owner
  checking a top-ten against it will look for.
* Quantities are added as plain numbers: units are not converted. Ten boxes and
  five kilos is fifteen, exactly as the TDL report prints it.
* Amounts are magnitudes unless a column is a balance, where Dr/Cr is part of
  the answer.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from tally_core.domain.masters import Ledger, LedgerGroup, StockItem, VoucherTypeKind
from tally_core.domain.money import Money, Side
from tally_core.domain.transactions import OutstandingBill, Voucher

from . import analytics as an

#: The specification's "no party on the voucher" label.
NOT_AVAILABLE = "Not Available"

#: "Not sold for 180+ days" -- the threshold both idle reports use.
IDLE_DAYS = 180

#: A rate spread smaller than this is rounding on the invoice, not a cheaper
#: supplier.
MIN_RATE_DIFF_PERCENT = Decimal("0.01")

TAX_GROUP = "Duties & Taxes"
EXPENSE_GROUPS = ("Direct Expenses", "Indirect Expenses")

HUNDRED = Decimal(100)
TWO = Decimal("0.01")


class UnknownReport(LookupError):
    """A report key nothing here builds."""


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------


@dataclass
class Inputs:
    """Everything a report may read, loaded by the caller.

    Each field is only filled for the reports that need it (see
    :data:`NEEDS`), so a top-customers request never pays for a stock export.
    """

    since: date
    until: date
    #: Vouchers in the window, effective and classified.
    window: list[Voucher] = field(default_factory=list)
    #: Vouchers from the start of stored history to ``until``. For the reports
    #: that ask "when did this last happen", which a window cannot answer.
    history: list[Voucher] = field(default_factory=list)
    ledgers: list[Ledger] | None = None
    groups: list[LedgerGroup] | None = None
    stock: list[StockItem] | None = None
    bills: list[OutstandingBill] | None = None
    #: Caveats the loader already knows about (history not fully synced, a
    #: master read missing), shown under the report.
    notes: list[str] = field(default_factory=list)


#: Which inputs each report reads. The endpoint loads exactly these.
NEEDS: dict[str, set[str]] = {
    "customers": {"window", "ledgers"},
    "suppliers": {"window", "ledgers"},
    "selling_products": {"window"},
    "buying_products": {"window"},
    "fast_moving": {"window"},
    "non_moving": {"history", "stock"},
    "inactive_customers": {"history", "ledgers"},
    "expenses": {"window", "ledgers"},
    "outstanding_customers": {"bills", "ledgers"},
    "outstanding_suppliers": {"bills", "ledgers"},
    "payment_performance": {"history", "bills", "ledgers"},
    "profitable_products": {"window", "history", "stock"},
    "loss_products": {"window", "history", "stock"},
    "profitable_customers": {"window", "history", "stock"},
    "loss_customers": {"window", "history", "stock"},
    "rate_variance": {"window"},
    "sales_returns": {"window", "ledgers"},
    "purchase_returns": {"window", "ledgers"},
    "negative_stock": {"history", "stock"},
}


# --------------------------------------------------------------------------
# The table shape
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    key: str
    label: str
    #: ``text`` · ``date`` · ``count`` · ``days`` · ``quantity`` · ``money`` ·
    #: ``balance`` (money with its Dr/Cr side) · ``rate`` · ``percent``.
    type: str
    #: Whether the TOTAL row adds this column up. Dates, days, rates and
    #: percentages are blank there: a sum of percentages is not a percentage.
    total: bool = False


@dataclass
class Row:
    name: str
    cells: dict[str, Any]
    #: Where tapping the row goes: ``{"type": "ledger"|"item"|"outstanding",
    #: "name": ..., "kind": ...}``.
    drill: dict[str, Any] | None = None


@dataclass(frozen=True)
class Sort:
    key: str
    label: str
    order: Callable[[Row], Any]
    #: Largest first, unless the report says smallest.
    descending: bool = True
    #: The column this sort ranks on, which the app shows as each row's lead
    #: figure. The sort's own key unless that is not a column.
    column: str | None = None


@dataclass
class Report:
    key: str
    title: str
    columns: list[Column]
    rows: list[Row]
    sorts: list[Sort]
    notes: list[str] = field(default_factory=list)
    #: Values the TOTAL row computes rather than adds -- daily, weekly and
    #: monthly averages, which are the total over the period days.
    derived_totals: Callable[[dict[str, Any]], dict[str, Any]] | None = None


def _money_of(value: Decimal) -> Money:
    return Money(amount=abs(value), side=Side.DEBIT)


def _cell_out(column: Column, value: Any) -> Any:
    """A raw cell value in the shape the app decodes."""
    if value is None:
        return None
    if column.type in {"money", "rate"}:
        return an.money_out(_money_of(value))
    if column.type == "balance":
        return an.money_out(value)
    if column.type == "date":
        return value.isoformat()
    if column.type == "percent":
        return str(value.quantize(TWO))
    if column.type == "quantity":
        return round(float(value), 3)
    return value


def _sorted(rows: list[Row], sort: Sort) -> list[Row]:
    """Order rows, with rows that have nothing to sort on at the bottom.

    The name breaks ties, so the same books always give the same ranking --
    a report that reshuffles equal rows on every open reads as unstable.
    """
    present = [r for r in rows if sort.order(r) is not None]
    missing = [r for r in rows if sort.order(r) is None]
    present.sort(key=lambda r: r.name.casefold())
    present.sort(key=sort.order, reverse=sort.descending)
    missing.sort(key=lambda r: r.name.casefold())
    return present + missing


def render(report: Report, *, since: date, until: date, sort: str, limit: int) -> dict[str, Any]:
    """Sort, cut to the top ``limit`` and total what is shown."""
    chosen = next((s for s in report.sorts if s.key == sort), report.sorts[0])
    ordered = _sorted(report.rows, chosen)
    shown = ordered[:limit] if limit > 0 else ordered

    totals: dict[str, Any] = {}
    for column in report.columns:
        if not column.total:
            continue
        if column.type == "balance":
            running = Money.zero()
            for row in shown:
                if row.cells.get(column.key) is not None:
                    running = running + row.cells[column.key]
            totals[column.key] = running
        elif column.type == "count":
            totals[column.key] = sum(int(row.cells.get(column.key) or 0) for row in shown)
        else:
            totals[column.key] = sum(
                (Decimal(str(row.cells.get(column.key) or 0)) for row in shown), Decimal(0)
            )
    if report.derived_totals is not None:
        totals.update(report.derived_totals(totals))

    return {
        "key": report.key,
        "title": report.title,
        "from_date": since.isoformat(),
        "to_date": until.isoformat(),
        "sort": chosen.key,
        "sorts": [
            {"key": s.key, "label": s.label, "column": s.column or s.key} for s in report.sorts
        ],
        "limit": limit,
        "row_count": len(ordered),
        "columns": [
            {"key": c.key, "label": c.label, "type": c.type, "total": c.total}
            for c in report.columns
        ],
        "rows": [
            {
                "rank": index + 1,
                "name": row.name,
                "drill": row.drill,
                "cells": {c.key: _cell_out(c, row.cells.get(c.key)) for c in report.columns},
            }
            for index, row in enumerate(shown)
        ],
        "total": {c.key: _cell_out(c, totals.get(c.key)) for c in report.columns},
        "notes": report.notes,
    }


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------


def _norm(name: str | None) -> str:
    return " ".join((name or "").split()).casefold()


def _party(voucher: Voucher) -> str:
    return (voucher.party_name or "").strip() or NOT_AVAILABLE


def _group_members(ledgers: list[Ledger], groups: list[LedgerGroup] | None, root: str) -> set[str]:
    """Normalised names of the ledgers under ``root``, sub-groups included.

    Without the group tree, direct membership only: guessing that an unknown
    group sits under ``root`` would be guessing.
    """
    wanted = {_norm(root)} | {_norm(g.name) for g in an.sub_groups(groups or [], root)}
    return {_norm(led.name) for led in ledgers if _norm(led.parent_group) in wanted}


def _is_gst(name: str) -> bool:
    """A GST line, by name: the three heads, or a ledger that just says GST."""
    return an._gst_head(name) is not None or "gst" in name.casefold()


#: Words that name a tax ledger when the group tree cannot say. Whole words,
#: so "Taxi Hire" is not a tax.
_TAX_WORDS = {"gst", "igst", "cgst", "sgst", "utgst", "tcs", "tds", "cess", "vat", "tax"}


def _named_like_tax(name: str) -> bool:
    words = set(name.casefold().replace("-", " ").replace("_", " ").split())
    return _is_gst(name) or bool(words & _TAX_WORDS)


def _tax_ledgers(inputs: Inputs) -> Callable[[str], bool]:
    """Whether a ledger line is a tax, by its group where the masters say.

    The specification's Tax Amount is every line under Duties & Taxes -- GST,
    TCS, cess. Without the ledger masters, fall back to recognising GST by
    name, which is what the monthly summary has always done.
    """
    if inputs.ledgers:
        members = _group_members(inputs.ledgers, inputs.groups, TAX_GROUP)
        if members:
            return lambda name: _norm(name) in members
    return _named_like_tax


def _quantity(voucher: Voucher) -> Decimal:
    return sum((Decimal(str(abs(e.quantity))) for e in voucher.inventory_entries), Decimal(0))


def _period_days(since: date, until: date) -> Decimal:
    return Decimal((until - since).days + 1)


def _of_kind(vouchers: Iterable[Voucher], *kinds: VoucherTypeKind) -> list[Voucher]:
    return [v for v in vouchers if v.kind in kinds]


def _ledger_drill(name: str) -> dict[str, Any] | None:
    return None if name == NOT_AVAILABLE else {"type": "ledger", "name": name}


def _item_drill(name: str) -> dict[str, Any]:
    return {"type": "item", "name": name}


def _amount_sort(key: str) -> Callable[[Row], Any]:
    return lambda row: row.cells.get(key)


def _date_sort(key: str) -> Callable[[Row], Any]:
    return lambda row: row.cells.get(key)


def _lines_note(vouchers: list[Voucher]) -> list[str]:
    """Say so when stock lines are missing from part of the window.

    History older than the sync's inventory horizon is stored without its
    lines, to keep exports small. A report built on lines would quietly show
    those months as no sales at all.
    """
    bare = [
        v
        for v in vouchers
        if v.kind in {VoucherTypeKind.SALES, VoucherTypeKind.PURCHASE} and not v.inventory_entries
    ]
    if not bare:
        return []
    return [
        f"{len(bare)} invoice{'s' if len(bare) != 1 else ''} in this period carry no item "
        "lines (accounting-only invoices, or older history synced without lines), so "
        "they are not counted here."
    ]


# --------------------------------------------------------------------------
# 4a. Party reports: customers, suppliers, sales and purchase returns
# --------------------------------------------------------------------------


def _party_report(
    inputs: Inputs, *, key: str, title: str, kind: VoucherTypeKind, date_label: str
) -> Report:
    is_tax = _tax_ledgers(inputs)
    last: dict[str, date] = {}
    bills: dict[str, int] = defaultdict(int)
    qty: dict[str, Decimal] = defaultdict(Decimal)
    taxable: dict[str, Decimal] = defaultdict(Decimal)
    amount: dict[str, Decimal] = defaultdict(Decimal)

    for voucher in _of_kind(inputs.window, kind):
        party = _party(voucher)
        bill = an.voucher_value(voucher).amount
        tax = sum(
            (e.amount.amount for e in voucher.ledger_entries if is_tax(e.ledger_name)),
            Decimal(0),
        )
        last[party] = max(last.get(party, voucher.date), voucher.date)
        bills[party] += 1
        qty[party] += _quantity(voucher)
        taxable[party] += bill - tax
        amount[party] += bill

    rows = [
        Row(
            name=party,
            drill=_ledger_drill(party),
            cells={
                "last_date": last[party],
                "bills": bills[party],
                "quantity": qty[party],
                "taxable": taxable[party],
                "amount": amount[party],
            },
        )
        for party in amount
    ]
    return Report(
        key=key,
        title=title,
        columns=[
            Column("last_date", date_label, "date"),
            Column("bills", "No. of bills", "count", total=True),
            Column("quantity", "Quantity", "quantity", total=True),
            Column("taxable", "Taxable amount", "money", total=True),
            Column("amount", "Bill amount", "money", total=True),
        ],
        rows=rows,
        sorts=[
            Sort("amount", "Bill amount", _amount_sort("amount")),
            Sort("quantity", "Quantity", _amount_sort("quantity")),
        ],
    )


# --------------------------------------------------------------------------
# 4b. Item reports: selling, buying, fast moving
# --------------------------------------------------------------------------


@dataclass
class _ItemTotals:
    last: date | None = None
    vouchers: set[int] = field(default_factory=set)
    qty: Decimal = Decimal(0)
    taxable: Decimal = Decimal(0)
    amount: Decimal = Decimal(0)


def _item_totals(vouchers: list[Voucher], kind: VoucherTypeKind) -> dict[str, _ItemTotals]:
    """Per item: lines summed, GST apportioned to each line by its value.

    A voucher's GST is carried on ledger lines, not on the stock lines it was
    charged for, so each line takes its share of the voucher's GST pro rata to
    its taxable value. On a single-rate invoice that is exactly the tax on the
    line; on a mixed-rate one it is the nearest honest split without the rate
    on every line.
    """
    totals: dict[str, _ItemTotals] = {}
    for index, voucher in enumerate(_of_kind(vouchers, kind)):
        lines = [e for e in voucher.inventory_entries if e.item_name]
        if not lines:
            continue
        gst = sum(
            (
                e.amount.amount
                for e in voucher.ledger_entries
                if _is_gst(e.ledger_name)
            ),
            Decimal(0),
        )
        base = sum((line.amount.amount for line in lines), Decimal(0))
        for line in lines:
            item = totals.setdefault(line.item_name.strip(), _ItemTotals())
            value = line.amount.amount
            share = (gst * value / base) if base else Decimal(0)
            item.last = voucher.date if item.last is None else max(item.last, voucher.date)
            # An item on two lines of one invoice is one bill.
            item.vouchers.add(index)
            item.qty += Decimal(str(abs(line.quantity)))
            item.taxable += value
            item.amount += value + share
    return totals


def _item_report(
    inputs: Inputs, *, key: str, title: str, kind: VoucherTypeKind, date_label: str
) -> Report:
    totals = _item_totals(inputs.window, kind)
    rows = [
        Row(
            name=name,
            drill=_item_drill(name),
            cells={
                "last_date": t.last,
                "bills": len(t.vouchers),
                "quantity": t.qty,
                "taxable": t.taxable,
                "amount": t.amount,
            },
        )
        for name, t in totals.items()
    ]
    return Report(
        key=key,
        title=title,
        columns=[
            Column("last_date", date_label, "date"),
            Column("bills", "No. of bills", "count", total=True),
            Column("quantity", "Quantity", "quantity", total=True),
            Column("taxable", "Taxable amount", "money", total=True),
            Column("amount", "Bill amount", "money", total=True),
        ],
        rows=rows,
        sorts=[
            Sort("amount", "Bill amount", _amount_sort("amount")),
            Sort("quantity", "Quantity", _amount_sort("quantity")),
        ],
        notes=_lines_note(_of_kind(inputs.window, kind)),
    )


def fast_moving(inputs: Inputs) -> Report:
    days = _period_days(inputs.since, inputs.until)
    totals = _item_totals(inputs.window, VoucherTypeKind.SALES)

    def averages(qty: Decimal) -> dict[str, Decimal]:
        daily = qty / days
        return {"daily": daily, "weekly": daily * 7, "monthly": daily * 30}

    rows = [
        Row(
            name=name,
            drill=_item_drill(name),
            cells={
                "last_date": t.last,
                "bills": len(t.vouchers),
                "quantity": t.qty,
                **averages(t.qty),
                "amount": t.amount,
            },
        )
        for name, t in totals.items()
    ]
    return Report(
        key="fast_moving",
        title="Top Fast Moving Products",
        columns=[
            Column("last_date", "Last sale date", "date"),
            Column("bills", "No. of bills", "count", total=True),
            Column("quantity", "Quantity", "quantity", total=True),
            Column("daily", "Daily sales", "quantity"),
            Column("weekly", "Weekly sales", "quantity"),
            Column("monthly", "Monthly sales", "quantity"),
            Column("amount", "Bill amount", "money", total=True),
        ],
        rows=rows,
        sorts=[
            Sort("quantity", "Quantity", _amount_sort("quantity")),
            Sort("amount", "Bill amount", _amount_sort("amount")),
        ],
        notes=_lines_note(_of_kind(inputs.window, VoucherTypeKind.SALES)),
        derived_totals=lambda t: averages(t.get("quantity", Decimal(0))),
    )


# --------------------------------------------------------------------------
# Last movement dates, from history
# --------------------------------------------------------------------------


def _last_item_dates(history: list[Voucher], kind: VoucherTypeKind) -> dict[str, date]:
    last: dict[str, date] = {}
    for voucher in _of_kind(history, kind):
        for line in voucher.inventory_entries:
            key = _norm(line.item_name)
            if key:
                last[key] = max(last.get(key, voucher.date), voucher.date)
    return last


def non_moving(inputs: Inputs) -> Report:
    sold = _last_item_dates(inputs.history, VoucherTypeKind.SALES)
    bought = _last_item_dates(inputs.history, VoucherTypeKind.PURCHASE)

    rows: list[Row] = []
    for item in inputs.stock or []:
        if item.closing_quantity == 0:
            continue
        key = _norm(item.name)
        reference = sold.get(key) or bought.get(key)
        # None, not 0, for an item that never moved: "0 days" reads as "moved
        # today", the opposite of what the row is saying.
        days = (inputs.until - reference).days if reference else None
        if days is not None and days <= IDLE_DAYS:
            continue
        rows.append(
            Row(
                name=item.name,
                drill=_item_drill(item.name),
                cells={
                    "last_date": sold.get(key),
                    "days": days,
                    "quantity": Decimal(str(item.closing_quantity)),
                    "value": item.closing_value.amount,
                },
            )
        )
    return Report(
        key="non_moving",
        title="Top Non-Moving Products",
        columns=[
            Column("last_date", "Last sale date", "date"),
            Column("days", "Days", "days"),
            Column("quantity", "Closing qty", "quantity", total=True),
            Column("value", "Closing value", "money", total=True),
        ],
        rows=rows,
        sorts=[
            Sort("value", "Closing value", _amount_sort("value")),
            Sort("quantity", "Closing qty", _amount_sort("quantity")),
        ],
        notes=[
            f"Items in stock that have not sold for more than {IDLE_DAYS} days, or "
            "never moved. Closing stock is as it stands in Tally today."
        ],
    )


def inactive_customers(inputs: Inputs) -> Report:
    last: dict[str, tuple[str, date]] = {}
    for voucher in _of_kind(inputs.history, VoucherTypeKind.SALES):
        if not voucher.party_name:
            continue
        key = _norm(voucher.party_name)
        held = last.get(key)
        if held is None or voucher.date > held[1]:
            last[key] = (voucher.party_name.strip(), voucher.date)

    balances = {_norm(led.name): led.closing_balance for led in inputs.ledgers or []}
    rows: list[Row] = []
    for key, (name, when) in last.items():
        days = (inputs.until - when).days
        if days <= IDLE_DAYS:
            continue
        rows.append(
            Row(
                name=name,
                drill=_ledger_drill(name),
                cells={"last_date": when, "days": days, "balance": balances.get(key)},
            )
        )
    return Report(
        key="inactive_customers",
        title="Top Inactive Customers",
        columns=[
            Column("last_date", "Last sale date", "date"),
            Column("days", "Days", "days"),
            Column("balance", "Closing balance", "balance", total=True),
        ],
        rows=rows,
        sorts=[
            Sort(
                "balance",
                "Closing balance",
                lambda r: r.cells["balance"].signed if r.cells.get("balance") else None,
            ),
            Sort("last_date", "Last sale date", _date_sort("last_date"), descending=False),
        ],
        notes=[
            f"Customers who have not bought for more than {IDLE_DAYS} days. "
            "Closing balance is as it stands in Tally today."
        ],
    )


# --------------------------------------------------------------------------
# Expenses
# --------------------------------------------------------------------------


def expenses(inputs: Inputs) -> Report:
    ledgers = inputs.ledgers or []
    members: dict[str, Ledger] = {}
    for root in EXPENSE_GROUPS:
        names = _group_members(ledgers, inputs.groups, root)
        members.update({_norm(led.name): led for led in ledgers if _norm(led.name) in names})

    movement: dict[str, Decimal] = defaultdict(Decimal)
    for voucher in inputs.window:
        for entry in voucher.ledger_entries:
            key = _norm(entry.ledger_name)
            if key in members:
                movement[key] += entry.amount.signed

    days = _period_days(inputs.since, inputs.until)

    def averages(amount: Decimal) -> dict[str, Decimal]:
        daily = amount / days
        return {"daily": daily, "weekly": daily * 7, "monthly": daily * 30}

    rows = [
        Row(
            name=members[key].name,
            drill=_ledger_drill(members[key].name),
            cells={"under": members[key].parent_group, "amount": amount, **averages(amount)},
        )
        for key, amount in movement.items()
        if amount != 0
    ]
    notes = []
    if not ledgers:
        notes.append(
            "The ledger list has not been read from Tally yet, so expenses cannot be found."
        )
    return Report(
        key="expenses",
        title="Top Expense Accounts",
        columns=[
            Column("under", "Under", "text"),
            Column("amount", "Amount", "money", total=True),
            Column("daily", "Daily avg.", "money"),
            Column("weekly", "Weekly avg.", "money"),
            Column("monthly", "Monthly avg.", "money"),
        ],
        rows=rows,
        # Signed, so a ledger that was mostly credited sorts below one that was
        # spent from -- its magnitude is shown, its direction decides the rank.
        sorts=[Sort("amount", "Amount", _amount_sort("amount"))],
        notes=notes,
        derived_totals=lambda t: averages(t.get("amount", Decimal(0))),
    )


# --------------------------------------------------------------------------
# Outstanding
# --------------------------------------------------------------------------


def _outstanding(inputs: Inputs, *, key: str, title: str, receivable: bool) -> Report:
    root = "Sundry Debtors" if receivable else "Sundry Creditors"
    expected = Side.DEBIT if receivable else Side.CREDIT
    ledgers = inputs.ledgers or []
    members = _group_members(ledgers, inputs.groups, root) if ledgers else None

    by_party: dict[str, list[OutstandingBill]] = defaultdict(list)
    names: dict[str, str] = {}
    for bill in inputs.bills or []:
        if bill.pending_amount.is_zero or bill.pending_amount.side is not expected:
            continue
        party = _norm(bill.party_name)
        if members is not None and party not in members:
            continue
        by_party[party].append(bill)
        names.setdefault(party, bill.party_name.strip())

    rows: list[Row] = []
    for party, bills in by_party.items():
        dues = [b.due_date or b.bill_date for b in bills if (b.due_date or b.bill_date)]
        oldest = min(dues) if dues else None
        rows.append(
            Row(
                name=names[party],
                drill={
                    "type": "outstanding",
                    "name": names[party],
                    "kind": "receivable" if receivable else "payable",
                },
                cells={
                    "oldest_due": oldest,
                    "latest_due": max(dues) if dues else None,
                    "overdue_days": (inputs.until - oldest).days if oldest else None,
                    "bills": len(bills),
                    "amount": sum((b.pending_amount.amount for b in bills), Decimal(0)),
                },
            )
        )
    notes = ["Pending bills as they stand in Tally today."]
    if members is None:
        notes.append(
            f"The ledger list has not been read yet, so every party's bills are counted, "
            f"not only those under {root}."
        )
    return Report(
        key=key,
        title=title,
        columns=[
            Column("oldest_due", "Oldest due date", "date"),
            Column("latest_due", "Latest due date", "date"),
            Column("overdue_days", "Overdue days", "days"),
            Column("bills", "No. of bills", "count", total=True),
            Column("amount", "Outstanding", "money", total=True),
        ],
        rows=rows,
        sorts=[
            Sort("oldest_due", "Oldest due", _date_sort("oldest_due"), descending=False),
            Sort("amount", "Outstanding", _amount_sort("amount")),
        ],
        notes=notes,
    )


def payment_performance(inputs: Inputs) -> Report:
    """How many days, on average, a customer takes to clear a bill.

    Tally's own figure is not exported, so it is worked from the vouchers: a
    sales bill counts once it is no longer pending, cleared on the date of the
    last receipt that named it, and each bill weighs in by its value -- a
    customer who pays the big invoices late is a slow payer however quickly
    they settle the small ones.
    """
    ledgers = inputs.ledgers or []
    members = _group_members(ledgers, inputs.groups, "Sundry Debtors") if ledgers else None

    raised: dict[tuple[str, str], tuple[date, Decimal, str]] = {}
    for voucher in _of_kind(inputs.history, VoucherTypeKind.SALES):
        if voucher.party_name and voucher.voucher_number:
            raised[(_norm(voucher.party_name), _norm(voucher.voucher_number))] = (
                voucher.date,
                an.voucher_value(voucher).amount,
                voucher.party_name.strip(),
            )

    cleared_on: dict[tuple[str, str], date] = {}
    for voucher in _of_kind(inputs.history, VoucherTypeKind.RECEIPT, VoucherTypeKind.CREDIT_NOTE):
        for entry in voucher.ledger_entries:
            for ref in entry.bill_references:
                key = (_norm(entry.ledger_name), _norm(ref))
                if key in raised:
                    cleared_on[key] = max(cleared_on.get(key, voucher.date), voucher.date)

    pending = {(_norm(b.party_name), _norm(b.bill_name)) for b in inputs.bills or []}

    weighted: dict[str, Decimal] = defaultdict(Decimal)
    weight: dict[str, Decimal] = defaultdict(Decimal)
    names: dict[str, str] = {}
    for key, when in cleared_on.items():
        if key in pending:
            continue
        party = key[0]
        if members is not None and party not in members:
            continue
        billed, value, name = raised[key]
        days = Decimal(max((when - billed).days, 0))
        value = value or Decimal(1)
        weighted[party] += days * value
        weight[party] += value
        names[party] = name

    balances = {_norm(led.name): led.closing_balance for led in ledgers}
    rows = [
        Row(
            name=names[party],
            drill=_ledger_drill(names[party]),
            cells={
                "days": int((weighted[party] / weight[party]).to_integral_value()),
                "balance": balances.get(party),
            },
        )
        for party in names
        if weight[party]
    ]
    return Report(
        key="payment_performance",
        title="Top Customers by Payment Performance",
        columns=[
            Column("days", "Bill clearance (days)", "days"),
            Column("balance", "Closing balance", "balance", total=True),
        ],
        rows=rows,
        sorts=[
            Sort(
                "fastest", "Fastest payers", _amount_sort("days"), descending=False, column="days"
            ),
            Sort("slowest", "Slowest payers", _amount_sort("days"), column="days"),
        ],
        notes=[
            "Average days from bill date to the receipt that settled it, weighted by bill "
            "value, over the bills in synced history that have been fully paid. Tally's "
            "own Payment Performance can differ where a bill was settled in parts."
        ],
    )


# --------------------------------------------------------------------------
# Profit
# --------------------------------------------------------------------------


def _cost_rates(inputs: Inputs) -> dict[str, Decimal]:
    """Each item's average cost: opening stock plus every purchase held.

    Tally values cost of goods at the item's average cost by default; this is
    that average over the books this product holds. Falls back to the item's
    closing rate for an item with no purchase or opening to average.
    """
    value: dict[str, Decimal] = defaultdict(Decimal)
    qty: dict[str, Decimal] = defaultdict(Decimal)
    for item in inputs.stock or []:
        key = _norm(item.name)
        if item.opening_quantity > 0:
            value[key] += item.opening_value.amount
            qty[key] += Decimal(str(item.opening_quantity))
    for voucher in _of_kind(inputs.history, VoucherTypeKind.PURCHASE):
        if voucher.date > inputs.until:
            continue
        for line in voucher.inventory_entries:
            key = _norm(line.item_name)
            value[key] += line.amount.amount
            qty[key] += Decimal(str(abs(line.quantity)))

    rates = {key: value[key] / qty[key] for key in qty if qty[key] > 0}
    for item in inputs.stock or []:
        key = _norm(item.name)
        if key not in rates and item.closing_rate is not None:
            rates[key] = item.closing_rate.amount
    return rates


_COST_NOTE = (
    "Cost is each item's average purchase cost (opening stock plus purchases), "
    "the method Tally uses by default."
)


def _profit_cells(sales: Decimal, cost: Decimal) -> dict[str, Any]:
    profit = sales - cost
    return {
        "sales": sales,
        "cost": cost,
        "profit": abs(profit),
        "percent": (profit / sales * HUNDRED) if sales else None,
        "_signed": profit,
    }


def _product_profit(inputs: Inputs, *, loss: bool) -> Report:
    rates = _cost_rates(inputs)
    qty: dict[str, Decimal] = defaultdict(Decimal)
    sales: dict[str, Decimal] = defaultdict(Decimal)
    names: dict[str, str] = {}
    for voucher in _of_kind(inputs.window, VoucherTypeKind.SALES, VoucherTypeKind.CREDIT_NOTE):
        sign = -1 if voucher.kind is VoucherTypeKind.CREDIT_NOTE else 1
        for line in voucher.inventory_entries:
            key = _norm(line.item_name)
            if not key:
                continue
            names.setdefault(key, line.item_name.strip())
            qty[key] += sign * Decimal(str(abs(line.quantity)))
            sales[key] += sign * line.amount.amount

    rows: list[Row] = []
    uncosted = 0
    for key, name in names.items():
        if qty[key] <= 0 or sales[key] <= 0:
            continue
        if key not in rates:
            uncosted += 1
            continue
        cells = _profit_cells(sales[key], qty[key] * rates[key])
        if (cells["_signed"] < 0) is not loss or cells["_signed"] == 0:
            continue
        rows.append(Row(name=name, drill=_item_drill(name), cells={"quantity": qty[key], **cells}))

    return _profit_report(
        rows,
        key="loss_products" if loss else "profitable_products",
        title="Top Non-Profitable Products" if loss else "Top Profitable Products",
        lead=[Column("quantity", "Sold qty", "quantity", total=True)],
        loss=loss,
        uncosted=uncosted,
        noun="item",
    )


def _customer_profit(inputs: Inputs, *, loss: bool) -> Report:
    rates = _cost_rates(inputs)
    last: dict[str, date] = {}
    bills: dict[str, int] = defaultdict(int)
    sales: dict[str, Decimal] = defaultdict(Decimal)
    cost: dict[str, Decimal] = defaultdict(Decimal)
    uncosted = 0

    for voucher in _of_kind(inputs.window, VoucherTypeKind.SALES):
        lines = [e for e in voucher.inventory_entries if e.item_name]
        if not lines:
            continue
        if any(_norm(line.item_name) not in rates for line in lines):
            # A voucher with one line we cannot cost would understate its cost
            # and overstate its profit. Leave it out and count it instead.
            uncosted += 1
            continue
        party = _party(voucher)
        last[party] = max(last.get(party, voucher.date), voucher.date)
        bills[party] += 1
        for line in lines:
            sales[party] += line.amount.amount
            cost[party] += Decimal(str(abs(line.quantity))) * rates[_norm(line.item_name)]

    rows: list[Row] = []
    for party in sales:
        if sales[party] == 0:
            continue
        cells = _profit_cells(sales[party], cost[party])
        if (cells["_signed"] < 0) is not loss or cells["_signed"] == 0:
            continue
        rows.append(
            Row(
                name=party,
                drill=_ledger_drill(party),
                cells={"last_date": last[party], "bills": bills[party], **cells},
            )
        )

    return _profit_report(
        rows,
        key="loss_customers" if loss else "profitable_customers",
        title="Top Non-Profitable Customers" if loss else "Top Profitable Customers",
        lead=[
            Column("last_date", "Last sale date", "date"),
            Column("bills", "No. of bills", "count", total=True),
        ],
        loss=loss,
        uncosted=uncosted,
        noun="invoice",
    )


def _profit_report(
    rows: list[Row],
    *,
    key: str,
    title: str,
    lead: list[Column],
    loss: bool,
    uncosted: int,
    noun: str,
) -> Report:
    notes = [_COST_NOTE]
    if uncosted:
        notes.append(
            f"{uncosted} {noun}{'s' if uncosted != 1 else ''} left out: an item on "
            f"{'it' if noun == 'invoice' else 'them'} has no purchase cost to work from."
        )
    return Report(
        key=key,
        title=title,
        columns=[
            *lead,
            Column("sales", "Sales value", "money", total=True),
            Column("cost", "Cost", "money", total=True),
            Column("profit", "Gross loss" if loss else "Gross profit", "money", total=True),
            Column("percent", "GP %", "percent"),
        ],
        rows=rows,
        sorts=[
            Sort("profit", "Gross loss" if loss else "Gross profit", _amount_sort("profit")),
            Sort(
                "percent",
                "GP % lowest" if loss else "GP % highest",
                _amount_sort("percent"),
                descending=not loss,
            ),
        ],
        notes=notes,
    )


# --------------------------------------------------------------------------
# Purchase rate spread
# --------------------------------------------------------------------------


def rate_variance(inputs: Inputs) -> Report:
    @dataclass
    class Spread:
        high: Decimal | None = None
        high_from: str = ""
        low: Decimal | None = None
        low_from: str = ""
        qty: Decimal = Decimal(0)
        value: Decimal = Decimal(0)

    spreads: dict[str, Spread] = {}
    names: dict[str, str] = {}
    for voucher in _of_kind(inputs.window, VoucherTypeKind.PURCHASE):
        supplier = _party(voucher)
        for line in voucher.inventory_entries:
            quantity = Decimal(str(abs(line.quantity)))
            value = line.amount.amount
            if not line.item_name or quantity == 0 or value == 0:
                continue
            key = _norm(line.item_name)
            names.setdefault(key, line.item_name.strip())
            spread = spreads.setdefault(key, Spread())
            # The actual bill rate, after any discount on the line.
            rate = value / quantity
            if spread.high is None or rate > spread.high:
                spread.high, spread.high_from = rate, supplier
            if spread.low is None or rate < spread.low:
                spread.low, spread.low_from = rate, supplier
            spread.qty += quantity
            spread.value += value

    rows: list[Row] = []
    for key, s in spreads.items():
        assert s.high is not None and s.low is not None
        diff = s.high - s.low
        percent = diff / s.low * HUNDRED if s.low else Decimal(0)
        if percent <= MIN_RATE_DIFF_PERCENT:
            continue
        rows.append(
            Row(
                name=names[key],
                drill=_item_drill(names[key]),
                cells={
                    "high_from": s.high_from,
                    "high": s.high,
                    "low_from": s.low_from,
                    "low": s.low,
                    "diff": diff,
                    "percent": percent,
                    "quantity": s.qty,
                    "extra": s.value - s.low * s.qty,
                },
            )
        )
    return Report(
        key="rate_variance",
        title="Top Lower/Higher Rate Item Suppliers",
        columns=[
            Column("high_from", "High rate supplier", "text"),
            Column("high", "High rate", "rate"),
            Column("low_from", "Low rate supplier", "text"),
            Column("low", "Low rate", "rate"),
            Column("diff", "Rate diff.", "rate"),
            Column("percent", "Diff. %", "percent"),
            Column("quantity", "Purc. qty", "quantity", total=True),
            Column("extra", "Extra cost", "money", total=True),
        ],
        rows=rows,
        sorts=[
            Sort("percent", "Diff. %", _amount_sort("percent")),
            Sort("diff", "Rate diff.", _amount_sort("diff")),
            Sort("low", "Low rate", _amount_sort("low"), descending=False),
        ],
        notes=[
            "Extra cost is what was paid above the lowest rate seen in the period for "
            "the same item.",
            *_lines_note(_of_kind(inputs.window, VoucherTypeKind.PURCHASE)),
        ],
    )


# --------------------------------------------------------------------------
# Negative stock
# --------------------------------------------------------------------------


def negative_stock(inputs: Inputs) -> Report:
    sold = _last_item_dates(inputs.history, VoucherTypeKind.SALES)
    bought = _last_item_dates(inputs.history, VoucherTypeKind.PURCHASE)
    rows = [
        Row(
            name=item.name,
            drill=_item_drill(item.name),
            cells={
                "last_sale": sold.get(_norm(item.name)),
                "last_purchase": bought.get(_norm(item.name)),
                "quantity": Decimal(str(abs(item.closing_quantity))),
                "value": item.closing_value.amount,
            },
        )
        for item in inputs.stock or []
        if item.closing_quantity < 0
    ]
    return Report(
        key="negative_stock",
        title="Top Negative Stock Products",
        columns=[
            Column("last_sale", "Last sale date", "date"),
            Column("last_purchase", "Last purchase date", "date"),
            Column("quantity", "Negative qty", "quantity", total=True),
            Column("value", "Negative value", "money", total=True),
        ],
        rows=rows,
        sorts=[
            Sort("value", "Negative value", _amount_sort("value")),
            Sort("quantity", "Negative qty", _amount_sort("quantity")),
        ],
        notes=["Stock is as it stands in Tally today."],
    )


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------


_BUILDERS: dict[str, Callable[[Inputs], Report]] = {
    "customers": lambda i: _party_report(
        i, key="customers", title="Top Prime Customers",
        kind=VoucherTypeKind.SALES, date_label="Last sale date",
    ),
    "suppliers": lambda i: _party_report(
        i, key="suppliers", title="Top Prime Suppliers",
        kind=VoucherTypeKind.PURCHASE, date_label="Last purchase date",
    ),
    "selling_products": lambda i: _item_report(
        i, key="selling_products", title="Top Selling Products",
        kind=VoucherTypeKind.SALES, date_label="Last sale date",
    ),
    "buying_products": lambda i: _item_report(
        i, key="buying_products", title="Top Buying Products",
        kind=VoucherTypeKind.PURCHASE, date_label="Last purchase date",
    ),
    "fast_moving": fast_moving,
    "non_moving": non_moving,
    "inactive_customers": inactive_customers,
    "expenses": expenses,
    "outstanding_customers": lambda i: _outstanding(
        i, key="outstanding_customers", title="Top Outstanding Customers", receivable=True
    ),
    "outstanding_suppliers": lambda i: _outstanding(
        i, key="outstanding_suppliers", title="Top Outstanding Suppliers", receivable=False
    ),
    "payment_performance": payment_performance,
    "profitable_products": lambda i: _product_profit(i, loss=False),
    "loss_products": lambda i: _product_profit(i, loss=True),
    "profitable_customers": lambda i: _customer_profit(i, loss=False),
    "loss_customers": lambda i: _customer_profit(i, loss=True),
    "rate_variance": rate_variance,
    "sales_returns": lambda i: _party_report(
        i, key="sales_returns", title="Top Sales Return Customers",
        kind=VoucherTypeKind.CREDIT_NOTE, date_label="Last return date",
    ),
    "purchase_returns": lambda i: _party_report(
        i, key="purchase_returns", title="Top Purchase Return Suppliers",
        kind=VoucherTypeKind.DEBIT_NOTE, date_label="Last return date",
    ),
    "negative_stock": negative_stock,
}

REPORT_KEYS = tuple(_BUILDERS)


def build(key: str, inputs: Inputs, *, sort: str | None = None, limit: int = 10) -> dict[str, Any]:
    builder = _BUILDERS.get(key)
    if builder is None:
        raise UnknownReport(key)
    report = builder(inputs)
    report.notes = [*inputs.notes, *report.notes]
    return render(
        report,
        since=inputs.since,
        until=inputs.until,
        sort=sort or report.sorts[0].key,
        limit=limit,
    )
