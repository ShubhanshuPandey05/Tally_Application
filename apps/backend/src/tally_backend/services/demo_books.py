"""The demo company's books: invented, coherent, and the same every time.

A prospective customer should be able to open TallyFlow and see what it does
before they own a copy of TallyPrime, let alone install a connector. That means
a set of books with nothing behind it -- no shop, no PC, no gateway.

Three rules shape everything here, and each of them was chosen against an
obvious cheaper alternative:

**One stream of vouchers, everything else derived from it.**
    It would be far less code to invent a dashboard directly -- a sales figure,
    a receivables figure, a stock value. But those numbers appear on more than
    one screen, computed by different code from different datasets: sales come
    from vouchers, cash from ledger balances, what a customer owes from
    bill-wise details. Invent them separately and the demo contradicts itself
    the moment somebody taps through, which is exactly the moment they were
    starting to trust it. So this module posts double-entry vouchers and then
    *folds* them into ledger balances, stock levels and outstanding bills, the
    way the real books would.

**Deterministic, and stable in the past.**
    Every day's trade comes from a random generator seeded with that day's
    date, so the books are a pure function of the window they cover. Yesterday
    reads the same today as it did yesterday, the demo can be regenerated from
    nothing after a deploy, and two backend instances agree. A demo whose
    history quietly rewrites itself is worse than no demo: it is the one thing
    an accountant will notice.

**It ends today.**
    The books run to the current date, so "today's sales" is a real number and
    the dashboard is not a museum. :mod:`tally_backend.services.demo` extends
    them as days pass.

Nothing here touches the database or Tally. It returns serialised domain models
-- the same JSON a connector would have sent -- so the read path, the analytics
and the app cannot tell the difference, and no screen needs a demo branch.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from tally_core.domain.masters import (
    CompanyMarkers,
    Ledger,
    LedgerGroup,
    StockItem,
    VoucherType,
    VoucherTypeKind,
)
from tally_core.domain.money import Money, Side
from tally_core.domain.transactions import (
    InventoryEntry,
    LedgerEntry,
    OutstandingBill,
    OutstandingKind,
    Voucher,
)

#: The business. A hardware and electricals distributor in Indore -- a shape of
#: customer this product is actually sold to, so the screens fill with the kind
#: of names, items and amounts a real prospect recognises.
COMPANY_NAME = "Shree Balaji Electricals & Hardware"

GST_RATE = Decimal("0.18")
TWO_PLACES = Decimal("0.01")


def _q(value: Decimal | int | float | str) -> Decimal:
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _dr(value: Decimal) -> Money:
    return Money(amount=_q(value), side=Side.DEBIT)


def _cr(value: Decimal) -> Money:
    return Money(amount=_q(value), side=Side.CREDIT)


def _money(signed: Decimal) -> Money:
    """A balance from a signed running total, debit-positive."""
    return _dr(signed) if signed >= 0 else _cr(-signed)


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _Item:
    name: str
    unit: str
    cost: Decimal
    price: Decimal
    hsn: str
    opening: int
    reorder: int
    #: Weight in the sales mix. Bulbs and wire move every day; a water heater
    #: does not, and a demo where every product sells equally looks generated.
    weight: int
    #: Who this is bought from. A distributor buys a brand's range from that
    #: brand, so purchase vouchers group by supplier the way real ones do --
    #: and the purchase register has something to say beyond a list of dates.
    supplier: str


ITEMS: tuple[_Item, ...] = (
    _Item(
        "Havells LED Bulb 9W", "Nos", Decimal("62"), Decimal("95"),
        hsn="85395000", opening=900, reorder=250, weight=10,
        supplier="Havells India Ltd",
    ),
    _Item(
        "Syska LED Panel 12W", "Nos", Decimal("245"), Decimal("349"),
        hsn="94054090", opening=260, reorder=80, weight=7,
        supplier="Indore Wire & Cable Agency",
    ),
    _Item(
        "Philips Tube Light 20W", "Nos", Decimal("140"), Decimal("199"),
        hsn="94053100", opening=420, reorder=120, weight=8,
        supplier="Indore Wire & Cable Agency",
    ),
    _Item(
        "Anchor Switch 6A", "Nos", Decimal("28"), Decimal("45"),
        hsn="85365090", opening=1800, reorder=500, weight=9,
        supplier="Anchor Electricals",
    ),
    _Item(
        "Legrand Modular Plate 4M", "Nos", Decimal("210"), Decimal("299"),
        hsn="85389000", opening=340, reorder=90, weight=5,
        supplier="Anchor Electricals",
    ),
    _Item(
        "Finolex Wire 1.0mm 90m", "Roll", Decimal("1150"), Decimal("1499"),
        hsn="85444911", opening=120, reorder=30, weight=7,
        supplier="Finolex Cables Ltd",
    ),
    _Item(
        "Polycab Wire 1.5mm 90m", "Roll", Decimal("1620"), Decimal("2050"),
        hsn="85444911", opening=95, reorder=25, weight=6,
        supplier="Polycab Wires Pvt Ltd",
    ),
    _Item(
        "Havells MCB 16A SP", "Nos", Decimal("165"), Decimal("229"),
        hsn="85362010", opening=380, reorder=100, weight=6,
        supplier="Havells India Ltd",
    ),
    _Item(
        "Schneider DB 8 Way", "Nos", Decimal("890"), Decimal("1199"),
        hsn="85371000", opening=70, reorder=20, weight=3,
        supplier="Schneider Electric India",
    ),
    _Item(
        "Bajaj Ceiling Fan 1200mm", "Nos", Decimal("1450"), Decimal("1899"),
        hsn="84145110", opening=140, reorder=40, weight=5,
        supplier="Bajaj Electricals Ltd",
    ),
    _Item(
        "Crompton Table Fan 400mm", "Nos", Decimal("1180"), Decimal("1550"),
        hsn="84145110", opening=80, reorder=25, weight=3,
        supplier="Bajaj Electricals Ltd",
    ),
    _Item(
        "Orient Exhaust Fan 150mm", "Nos", Decimal("780"), Decimal("1050"),
        hsn="84145130", opening=90, reorder=30, weight=3,
        supplier="Bajaj Electricals Ltd",
    ),
    _Item(
        "V-Guard Stabilizer 4kVA", "Nos", Decimal("2350"), Decimal("2999"),
        hsn="85043100", opening=45, reorder=15, weight=2,
        supplier="Usha International",
    ),
    _Item(
        "Usha Water Heater 15L", "Nos", Decimal("4200"), Decimal("5499"),
        hsn="85161000", opening=30, reorder=10, weight=2,
        supplier="Usha International",
    ),
    _Item(
        "PVC Conduit Pipe 25mm", "Nos", Decimal("78"), Decimal("115"),
        hsn="39172390", opening=700, reorder=200, weight=6,
        supplier="Polycab Wires Pvt Ltd",
    ),
    _Item(
        "Copper Lug 25mm", "Nos", Decimal("12"), Decimal("22"),
        hsn="85359090", opening=2400, reorder=800, weight=4,
        supplier="Polycab Wires Pvt Ltd",
    ),
)


@dataclass(frozen=True)
class _Party:
    name: str
    city: str
    gstin: str
    credit_days: int
    #: How often this party buys, relative to the others.
    weight: int


CUSTOMERS: tuple[_Party, ...] = (
    _Party("Maheshwari Electricals", "Indore", "23AACCM1234F1Z5", 30, 10),
    _Party("Sai Traders", "Dewas", "23AAGCS4521K1ZP", 21, 9),
    _Party("Verma Hardware Stores", "Ujjain", "23AAHCV7788M1Z2", 30, 8),
    _Party("Raj Enterprises", "Indore", "23AACFR2210L1ZQ", 45, 8),
    _Party("Krishna Electricals", "Bhopal", "23AAECK9087J1Z8", 30, 7),
    _Party("New Light House", "Indore", "23AAFCN3311P1Z4", 15, 6),
    _Party("Agarwal Sanitary & Electricals", "Ratlam", "23AAGCA5566H1ZR", 30, 5),
    _Party("Bhatia Trading Co.", "Indore", "23AABCB1199G1Z9", 45, 5),
    _Party("Shanti Builders", "Khargone", "23AAJCS7412D1Z1", 60, 4),
    _Party("Om Sai Constructions", "Dhar", "23AAKCO3654B1Z7", 60, 3),
    _Party("Deepak Electric Works", "Indore", "23AALCD8821C1Z3", 21, 3),
    _Party("Metro Interiors", "Indore", "23AAMCM4477E1Z6", 30, 2),
)

SUPPLIERS: tuple[_Party, ...] = (
    _Party("Havells India Ltd", "New Delhi", "07AAACH1925Q1ZY", 30, 10),
    _Party("Polycab Wires Pvt Ltd", "Mumbai", "27AAACP2841M1ZK", 45, 8),
    _Party("Anchor Electricals", "Mumbai", "27AAACA5567R1ZJ", 30, 7),
    _Party("Bajaj Electricals Ltd", "Mumbai", "27AAACB1080P1ZN", 30, 6),
    _Party("Finolex Cables Ltd", "Pune", "27AAACF2401L1ZB", 45, 6),
    _Party("Schneider Electric India", "Bengaluru", "29AAACS5678T1ZD", 30, 4),
    _Party("Usha International", "Gurugram", "06AAACU1234N1ZW", 30, 3),
    _Party("Indore Wire & Cable Agency", "Indore", "23AANCI9932F1Z8", 15, 3),
)

#: Fixed monthly costs, posted on a set day so the expense breakdown has the
#: shape a real one does: rent and salaries dominating, the rest scattered.
MONTHLY_EXPENSES: tuple[tuple[str, Decimal, int], ...] = (
    ("Shop Rent", Decimal("48000"), 3),
    ("Salaries", Decimal("96000"), 5),
    ("Electricity Charges", Decimal("11400"), 12),
    ("Telephone & Internet", Decimal("3200"), 12),
    ("Accounting & Audit Fees", Decimal("9000"), 18),
)

#: Costs that follow trade rather than the calendar.
VARIABLE_EXPENSES: tuple[tuple[str, int, int], ...] = (
    ("Transportation Charges", 1800, 6500),
    ("Packing & Loading", 600, 2400),
    ("Fuel & Vehicle Expenses", 900, 3200),
    ("Office Expenses", 400, 1900),
)

CASH_LEDGER = "Cash"
BANK_LEDGER = "HDFC Bank - 4471"
SALES_LEDGER = "Sales - Local 18%"
PURCHASE_LEDGER = "Purchase - Local 18%"
CAPITAL_LEDGER = "Balaji Capital A/c"
ROUNDING_LEDGER = "Rounded Off"

OUTPUT_TAXES = ("Output CGST 9%", "Output SGST 9%")
INPUT_TAXES = ("Input CGST 9%", "Input SGST 9%")

#: Opening balances the books start from, before a single voucher is posted.
#: Capital is computed from the rest so the opening position balances -- an
#: accounting demo whose opening balance sheet does not add up is a demo an
#: accountant closes.
OPENING_BANK = Decimal("1265000")
OPENING_CASH = Decimal("148500")


GROUPS: tuple[tuple[str, str | None, str | None], ...] = (
    # (name, parent, primary group)
    ("Capital Account", None, "Capital Account"),
    ("Current Assets", None, "Current Assets"),
    ("Current Liabilities", None, "Current Liabilities"),
    ("Sundry Debtors", "Current Assets", "Current Assets"),
    ("Sundry Creditors", "Current Liabilities", "Current Liabilities"),
    ("Cash-in-Hand", "Current Assets", "Current Assets"),
    ("Bank Accounts", "Current Assets", "Current Assets"),
    ("Stock-in-Hand", "Current Assets", "Current Assets"),
    ("Duties & Taxes", "Current Liabilities", "Current Liabilities"),
    ("Sales Accounts", None, "Sales Accounts"),
    ("Purchase Accounts", None, "Purchase Accounts"),
    ("Direct Expenses", None, "Direct Expenses"),
    ("Indirect Expenses", None, "Indirect Expenses"),
    ("Fixed Assets", None, "Fixed Assets"),
)

#: Which group each non-party ledger belongs to.
LEDGER_GROUPS: dict[str, str] = {
    CASH_LEDGER: "Cash-in-Hand",
    BANK_LEDGER: "Bank Accounts",
    SALES_LEDGER: "Sales Accounts",
    PURCHASE_LEDGER: "Purchase Accounts",
    CAPITAL_LEDGER: "Capital Account",
    ROUNDING_LEDGER: "Indirect Expenses",
    "Transportation Charges": "Direct Expenses",
    "Packing & Loading": "Direct Expenses",
    "Shop Rent": "Indirect Expenses",
    "Salaries": "Indirect Expenses",
    "Electricity Charges": "Indirect Expenses",
    "Telephone & Internet": "Indirect Expenses",
    "Accounting & Audit Fees": "Indirect Expenses",
    "Fuel & Vehicle Expenses": "Indirect Expenses",
    "Office Expenses": "Indirect Expenses",
    **{name: "Duties & Taxes" for name in OUTPUT_TAXES + INPUT_TAXES},
}

VOUCHER_TYPES: tuple[tuple[str, str, VoucherTypeKind], ...] = (
    ("Sales", "Sales", VoucherTypeKind.SALES),
    ("Purchase", "Purchase", VoucherTypeKind.PURCHASE),
    ("Receipt", "Receipt", VoucherTypeKind.RECEIPT),
    ("Payment", "Payment", VoucherTypeKind.PAYMENT),
    ("Contra", "Contra", VoucherTypeKind.CONTRA),
    ("Journal", "Journal", VoucherTypeKind.JOURNAL),
    ("Credit Note", "Credit Note", VoucherTypeKind.CREDIT_NOTE),
    ("Debit Note", "Debit Note", VoucherTypeKind.DEBIT_NOTE),
)


# --------------------------------------------------------------------------
# The result
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DemoBooks:
    """One generated set of books, serialised the way a connector sends them."""

    books_from: date
    upto: date
    groups: list[dict[str, Any]]
    ledgers: list[dict[str, Any]]
    stock_items: list[dict[str, Any]]
    voucher_types: list[dict[str, Any]]
    bills: list[dict[str, Any]]
    markers: dict[str, Any]
    #: Every voucher, oldest first.
    vouchers: list[dict[str, Any]]

    def vouchers_between(self, start: date, end: date) -> list[dict[str, Any]]:
        return [v for v in self.vouchers if start.isoformat() <= v["date"] <= end.isoformat()]


@dataclass
class _OpenBill:
    """A raised bill, until it is paid off."""

    party: str
    name: str
    raised_on: date
    due_on: date
    opening: Decimal
    pending: Decimal
    voucher_number: str
    credit_days: int


class _Books:
    """The running position while the voucher stream is being posted."""

    def __init__(self, books_from: date) -> None:
        self.books_from = books_from
        #: Signed, debit-positive, per ledger.
        self.balances: dict[str, Decimal] = defaultdict(Decimal)
        self.quantities: dict[str, Decimal] = {i.name: Decimal(i.opening) for i in ITEMS}
        self.receivable: list[_OpenBill] = []
        self.payable: list[_OpenBill] = []
        self.vouchers: list[Voucher] = []
        self.alter_id = 1000
        self._numbers: dict[tuple[str, int], int] = defaultdict(int)

        opening_stock = sum((Decimal(i.opening) * i.cost for i in ITEMS), Decimal(0))
        self.balances[BANK_LEDGER] = OPENING_BANK
        self.balances[CASH_LEDGER] = OPENING_CASH
        # The plug, so the opening position balances exactly.
        self.balances[CAPITAL_LEDGER] = -(OPENING_BANK + OPENING_CASH + opening_stock)

    def number(self, prefix: str, day: date) -> str:
        """Voucher numbers restart each financial year, as Tally's do."""
        year = financial_year_start(day).year
        self._numbers[(prefix, year)] += 1
        serial = self._numbers[(prefix, year)]
        return f"{prefix}/{year % 100:02d}{(year + 1) % 100:02d}/{serial:04d}"

    def post(
        self,
        *,
        day: date,
        voucher_type: str,
        kind: VoucherTypeKind,
        number: str,
        entries: list[LedgerEntry],
        inventory: list[InventoryEntry] | None = None,
        party: str | None = None,
        narration: str | None = None,
    ) -> Voucher:
        for entry in entries:
            self.balances[entry.ledger_name] += entry.amount.signed
        for line in inventory or []:
            # A sale's inventory line is negative quantity; a purchase positive.
            self.quantities[line.item_name] = (
                self.quantities.get(line.item_name, Decimal(0)) + Decimal(str(line.quantity))
            )

        self.alter_id += 1
        total = sum((e.amount.amount for e in entries if e.amount.side is Side.DEBIT), Decimal(0))
        voucher = Voucher(
            voucher_number=number,
            voucher_type=voucher_type,
            kind=kind,
            date=day,
            # Stable across regenerations, which is what lets the demo be
            # re-seeded without every voucher arriving as a new row.
            guid=f"demo-{day.isoformat()}-{number.replace('/', '-')}",
            alter_id=self.alter_id,
            master_id=self.alter_id,
            party_name=party,
            narration=narration,
            amount=_dr(total),
            ledger_entries=entries,
            inventory_entries=inventory or [],
        )
        self.vouchers.append(voucher)
        return voucher


# --------------------------------------------------------------------------
# Financial years
# --------------------------------------------------------------------------


def financial_year_start(day: date) -> date:
    """1 April of the financial year ``day`` falls in."""
    return date(day.year if day.month >= 4 else day.year - 1, 4, 1)


def default_books_from(today: date, *, years: int) -> date:
    """Where the demo's books begin: ``years`` financial years back."""
    return date(financial_year_start(today).year - max(years, 0), 4, 1)


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


def build_books(*, books_from: date, upto: date) -> DemoBooks:
    """Post every voucher between the two dates, then fold them into balances."""
    ledger = _Books(books_from)

    day = books_from
    while day <= upto:
        _post_day(ledger, day)
        day += timedelta(days=1)

    return DemoBooks(
        books_from=books_from,
        upto=upto,
        groups=[g.model_dump(mode="json") for g in _groups()],
        ledgers=[led.model_dump(mode="json") for led in _ledgers(ledger)],
        stock_items=[item.model_dump(mode="json") for item in _stock(ledger)],
        voucher_types=[vt.model_dump(mode="json") for vt in _voucher_types()],
        bills=[bill.model_dump(mode="json") for bill in _bills(ledger)],
        markers=_markers(ledger, upto).model_dump(mode="json"),
        vouchers=[v.model_dump(mode="json") for v in ledger.vouchers],
    )


def _post_day(books: _Books, day: date) -> None:
    # Seeded with the date alone, so a day's trade is the same however many
    # times the books are rebuilt and whichever process rebuilds them.
    rng = random.Random(f"tallyflow-demo|{day.isoformat()}")
    closed = day.weekday() == 6

    if not closed:
        for _ in range(_sales_count(rng, day)):
            _post_sale(books, day, rng)
        _post_purchase(books, day, rng)
        for _ in range(rng.randint(0, 3)):
            _post_receipt(books, day, rng)
        for _ in range(2):
            _post_supplier_payment(books, day, rng)
        if rng.random() < 0.30:
            _post_variable_expense(books, day, rng)
        if day.weekday() == 5 and rng.random() < 0.8:
            _post_contra(books, day, rng)

    # Fixed costs are paid whether or not the shop opened that day.
    for name, amount, on_day in MONTHLY_EXPENSES:
        if day.day == on_day:
            _post_fixed_expense(books, day, name, amount)


def _sales_count(rng: random.Random, day: date) -> int:
    """How busy the day was.

    Two shapes on top of the noise, because a flat series is the giveaway that
    a chart is fake: the trade grows year on year, and October -- Diwali, when
    an electricals distributor makes its year -- is busier than August.
    """
    base = rng.choice((2, 3, 3, 4, 4, 5, 6))
    if day.month in (9, 10):
        base += 2
    elif day.month in (6, 7):
        base -= 1
    if day.weekday() == 5:
        base -= 1
    return max(base, 1)


def _weighted(rng: random.Random, options: tuple[Any, ...]) -> Any:
    return rng.choices(options, weights=[o.weight for o in options], k=1)[0]


def _tax_split(net: Decimal) -> tuple[Decimal, Decimal]:
    tax = _q(net * GST_RATE)
    half = _q(tax / 2)
    # The second half absorbs the rounding, so CGST + SGST is exactly the tax.
    return half, _q(tax - half)


def _post_sale(books: _Books, day: date, rng: random.Random) -> None:
    customer = _weighted(rng, CUSTOMERS)
    lines: list[InventoryEntry] = []
    net = Decimal(0)

    for item in rng.sample(ITEMS, k=rng.randint(1, 3)):
        quantity = _order_quantity(rng, item)
        # A distributor discounts, and a demo where every line is at list price
        # looks like a price list rather than a set of books.
        rate = _q(item.price * Decimal(str(rng.choice((1.0, 1.0, 0.97, 0.95, 0.92)))))
        value = _q(rate * quantity)
        net += value
        lines.append(
            InventoryEntry(
                item_name=item.name,
                quantity=-float(quantity),
                unit=item.unit,
                rate=_dr(rate),
                amount=_cr(value),
            )
        )

    cgst, sgst = _tax_split(net)
    total = _q(net + cgst + sgst)
    number = books.number("S", day)

    books.post(
        day=day,
        voucher_type="Sales",
        kind=VoucherTypeKind.SALES,
        number=number,
        party=customer.name,
        narration=f"Being goods sold to {customer.name}, {customer.city}",
        entries=[
            LedgerEntry(ledger_name=customer.name, amount=_dr(total), is_party=True,
                        bill_references=[number]),
            LedgerEntry(ledger_name=SALES_LEDGER, amount=_cr(net)),
            LedgerEntry(ledger_name=OUTPUT_TAXES[0], amount=_cr(cgst)),
            LedgerEntry(ledger_name=OUTPUT_TAXES[1], amount=_cr(sgst)),
        ],
        inventory=lines,
    )

    books.receivable.append(
        _OpenBill(
            party=customer.name,
            name=number,
            raised_on=day,
            due_on=day + timedelta(days=customer.credit_days),
            opening=total,
            pending=total,
            voucher_number=number,
            credit_days=customer.credit_days,
        )
    )


def _order_quantity(rng: random.Random, item: _Item) -> Decimal:
    """Cheap things move in boxes; a water heater moves one at a time."""
    if item.cost < Decimal("100"):
        return Decimal(rng.choice((10, 20, 25, 50, 100)))
    if item.cost < Decimal("1000"):
        return Decimal(rng.choice((2, 3, 5, 5, 10, 12)))
    return Decimal(rng.choice((1, 1, 2, 3)))


def _post_purchase(books: _Books, day: date, rng: random.Random) -> None:
    """Restock whatever has fallen below its reorder level.

    Driven by the stock position rather than by a coin toss, because the two
    have to agree: purchases invented independently of what was sold send
    either the stock value or the bank balance somewhere absurd over two years,
    and both of those are on the dashboard.
    """
    short = [i for i in ITEMS if books.quantities.get(i.name, Decimal(0)) < Decimal(i.reorder)]
    if not short:
        return

    # One supplier per voucher: the one we owe the most items to.
    by_supplier: dict[str, list[_Item]] = defaultdict(list)
    for item in short:
        by_supplier[item.supplier].append(item)
    supplier_name = max(by_supplier, key=lambda name: len(by_supplier[name]))
    supplier = next(p for p in SUPPLIERS if p.name == supplier_name)

    lines: list[InventoryEntry] = []
    net = Decimal(0)
    for item in by_supplier[supplier_name][:5]:
        # Back up to a few weeks of cover, rounded to a whole pack.
        target = Decimal(item.reorder) * Decimal(str(rng.choice((2.0, 2.5, 3.0))))
        quantity = (target - books.quantities.get(item.name, Decimal(0))).quantize(Decimal("1"))
        if quantity <= 0:
            continue
        value = _q(item.cost * quantity)
        net += value
        lines.append(
            InventoryEntry(
                item_name=item.name,
                quantity=float(quantity),
                unit=item.unit,
                rate=_dr(item.cost),
                amount=_dr(value),
            )
        )

    if not lines:
        return

    cgst, sgst = _tax_split(net)
    total = _q(net + cgst + sgst)
    number = books.number("P", day)

    books.post(
        day=day,
        voucher_type="Purchase",
        kind=VoucherTypeKind.PURCHASE,
        number=number,
        party=supplier.name,
        narration=f"Being goods purchased from {supplier.name}",
        entries=[
            LedgerEntry(ledger_name=PURCHASE_LEDGER, amount=_dr(net)),
            LedgerEntry(ledger_name=INPUT_TAXES[0], amount=_dr(cgst)),
            LedgerEntry(ledger_name=INPUT_TAXES[1], amount=_dr(sgst)),
            LedgerEntry(ledger_name=supplier.name, amount=_cr(total), is_party=True,
                        bill_references=[number]),
        ],
        inventory=lines,
    )

    books.payable.append(
        _OpenBill(
            party=supplier.name,
            name=number,
            raised_on=day,
            due_on=day + timedelta(days=supplier.credit_days),
            opening=total,
            pending=total,
            voucher_number=number,
            credit_days=supplier.credit_days,
        )
    )


def _post_receipt(books: _Books, day: date, rng: random.Random) -> None:
    """Collect against the oldest bills of one customer.

    Oldest-first is what makes the ageing buckets on the dashboard meaningful:
    what is left unpaid is genuinely the old debt, not a random subset.
    """
    due = [b for b in books.receivable if b.pending > 0 and b.raised_on < day]
    if not due:
        return

    # A customer who has been waited on longest is the one who pays.
    due.sort(key=lambda b: b.raised_on)
    party = due[0].party if rng.random() < 0.5 else rng.choice(due).party
    theirs = [b for b in due if b.party == party][:3]
    if not theirs:
        return

    collected = Decimal(0)
    references: list[str] = []
    for bill in theirs:
        # Part payments are ordinary, and they are what puts a bill in more
        # than one ageing bucket over its life.
        pay = bill.pending if rng.random() < 0.75 else _q(bill.pending * Decimal("0.5"))
        if pay <= 0:
            continue
        bill.pending = _q(bill.pending - pay)
        collected += pay
        references.append(bill.name)

    if collected <= 0:
        return

    into = CASH_LEDGER if collected < Decimal("15000") and rng.random() < 0.4 else BANK_LEDGER
    number = books.number("R", day)
    books.post(
        day=day,
        voucher_type="Receipt",
        kind=VoucherTypeKind.RECEIPT,
        number=number,
        party=party,
        narration=f"Being amount received from {party} against {', '.join(references)}",
        entries=[
            LedgerEntry(ledger_name=into, amount=_dr(collected)),
            LedgerEntry(ledger_name=party, amount=_cr(collected), is_party=True,
                        bill_references=references),
        ],
    )


def _post_supplier_payment(books: _Books, day: date, rng: random.Random) -> None:
    due = [b for b in books.payable if b.pending > 0 and b.due_on <= day]
    if not due:
        return
    due.sort(key=lambda b: b.due_on)
    bill = due[0]
    paid = bill.pending
    bill.pending = Decimal(0)

    number = books.number("PY", day)
    books.post(
        day=day,
        voucher_type="Payment",
        kind=VoucherTypeKind.PAYMENT,
        number=number,
        party=bill.party,
        narration=f"Being payment to {bill.party} against {bill.name}",
        entries=[
            LedgerEntry(ledger_name=bill.party, amount=_dr(paid), is_party=True,
                        bill_references=[bill.name]),
            LedgerEntry(ledger_name=BANK_LEDGER, amount=_cr(paid)),
        ],
    )


def _post_fixed_expense(books: _Books, day: date, name: str, amount: Decimal) -> None:
    number = books.number("PY", day)
    books.post(
        day=day,
        voucher_type="Payment",
        kind=VoucherTypeKind.PAYMENT,
        number=number,
        narration=f"Being {name.lower()} paid for {day.strftime('%B %Y')}",
        entries=[
            LedgerEntry(ledger_name=name, amount=_dr(amount)),
            LedgerEntry(ledger_name=BANK_LEDGER, amount=_cr(amount)),
        ],
    )


def _post_variable_expense(books: _Books, day: date, rng: random.Random) -> None:
    name, low, high = rng.choice(VARIABLE_EXPENSES)
    amount = _q(rng.randint(low, high))
    paid_from = CASH_LEDGER if rng.random() < 0.6 else BANK_LEDGER
    number = books.number("PY", day)
    books.post(
        day=day,
        voucher_type="Payment",
        kind=VoucherTypeKind.PAYMENT,
        number=number,
        narration=f"Being {name.lower()} paid",
        entries=[
            LedgerEntry(ledger_name=name, amount=_dr(amount)),
            LedgerEntry(ledger_name=paid_from, amount=_cr(amount)),
        ],
    )


def _post_contra(books: _Books, day: date, rng: random.Random) -> None:
    """The week's cash takings, banked."""
    on_hand = books.balances[CASH_LEDGER]
    if on_hand < Decimal("60000"):
        return
    amount = _q(on_hand * Decimal(str(rng.choice((0.5, 0.6, 0.75)))))
    number = books.number("C", day)
    books.post(
        day=day,
        voucher_type="Contra",
        kind=VoucherTypeKind.CONTRA,
        number=number,
        narration="Being cash deposited into bank",
        entries=[
            LedgerEntry(ledger_name=BANK_LEDGER, amount=_dr(amount)),
            LedgerEntry(ledger_name=CASH_LEDGER, amount=_cr(amount)),
        ],
    )


# --------------------------------------------------------------------------
# Folding the stream into masters
# --------------------------------------------------------------------------


#: Groups whose balances belong to the profit and loss rather than the balance
#: sheet. Tally calls this ``ISREVENUE`` and several reports lean on it.
_REVENUE_GROUPS = frozenset(
    {"Sales Accounts", "Purchase Accounts", "Direct Expenses", "Indirect Expenses"}
)


def _groups() -> list[LedgerGroup]:
    return [
        LedgerGroup(
            name=name,
            parent=parent,
            primary_group=primary,
            is_revenue=primary in _REVENUE_GROUPS,
        )
        for name, parent, primary in GROUPS
    ]


def _ledgers(books: _Books) -> list[Ledger]:
    customers = {p.name: p for p in CUSTOMERS}
    suppliers = {p.name: p for p in SUPPLIERS}
    rows: list[Ledger] = []

    for name, signed in sorted(books.balances.items()):
        if name in customers:
            party = customers[name]
            group, bill_wise, credit = "Sundry Debtors", True, party.credit_days
        elif name in suppliers:
            party = suppliers[name]
            group, bill_wise, credit = "Sundry Creditors", True, party.credit_days
        else:
            party = None
            group, bill_wise, credit = LEDGER_GROUPS.get(name, "Indirect Expenses"), False, None

        rows.append(
            Ledger(
                name=name,
                parent_group=group,
                alter_id=1,
                closing_balance=_money(signed),
                is_bill_wise=bill_wise,
                credit_period_days=credit,
                gstin=party.gstin if party else None,
                state="Madhya Pradesh" if party else None,
                address=[party.city] if party else [],
            )
        )
    return rows


def _stock(books: _Books) -> list[StockItem]:
    rows: list[StockItem] = []
    for item in ITEMS:
        quantity = books.quantities.get(item.name, Decimal(0))
        rows.append(
            StockItem(
                name=item.name,
                parent_group="Electricals",
                alter_id=1,
                base_unit=item.unit,
                closing_quantity=float(quantity),
                closing_value=_money(_q(quantity * item.cost)),
                closing_rate=_dr(item.cost),
                opening_quantity=float(item.opening),
                opening_value=_dr(_q(Decimal(item.opening) * item.cost)),
                reorder_level=float(item.reorder),
                hsn_code=item.hsn,
                gst_rate=18.0,
            )
        )
    return rows


def _voucher_types() -> list[VoucherType]:
    return [
        VoucherType(name=name, parent=parent, kind=kind)
        for name, parent, kind in VOUCHER_TYPES
    ]


def _bills(books: _Books) -> list[OutstandingBill]:
    rows: list[OutstandingBill] = []
    for bill in books.receivable:
        if bill.pending <= 0:
            continue
        rows.append(
            OutstandingBill(
                party_name=bill.party,
                bill_name=bill.name,
                kind=OutstandingKind.RECEIVABLE,
                bill_date=bill.raised_on,
                due_date=bill.due_on,
                opening_amount=_dr(bill.opening),
                pending_amount=_dr(bill.pending),
                voucher_number=bill.voucher_number,
                credit_period_days=bill.credit_days,
            )
        )
    for bill in books.payable:
        if bill.pending <= 0:
            continue
        rows.append(
            OutstandingBill(
                party_name=bill.party,
                bill_name=bill.name,
                kind=OutstandingKind.PAYABLE,
                bill_date=bill.raised_on,
                due_date=bill.due_on,
                opening_amount=_cr(bill.opening),
                pending_amount=_cr(bill.pending),
                voucher_number=bill.voucher_number,
                credit_period_days=bill.credit_days,
            )
        )
    return rows


def _markers(books: _Books, upto: date) -> CompanyMarkers:
    return CompanyMarkers(
        name=COMPANY_NAME,
        master_alter_id=1,
        voucher_alter_id=books.alter_id,
        books_from=books.books_from,
        financial_year_from=financial_year_start(upto),
        ending_at=date(financial_year_start(upto).year + 1, 3, 31),
    )
