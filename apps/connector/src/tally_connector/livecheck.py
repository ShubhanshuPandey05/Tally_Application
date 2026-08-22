"""End-to-end verification against a real TallyPrime.

``tally-connector livecheck --company "<name>"`` runs every registered query
against the live instance and asserts the results make accounting sense --
balances fall on their natural side, vouchers balance, stock reconciles.

This exists because unit tests against recorded fixtures cannot catch a wrong
assumption about Tally's wire format. The first live run of this check found
four real bugs, including an inverted debit/credit convention that put every
asset on the wrong side.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from tally_core.domain.masters import VoucherTypeKind
from tally_core.domain.money import Side
from tally_core.tally import TallyClient, get_query, registry_manifest
from tally_core.tally.errors import TallyError

from .loaded import GuardedClient, company_key

logger = logging.getLogger(__name__)

#: Group name -> the side a balance in it must naturally fall on. Only groups
#: whose sign is unambiguous are listed; a wrong side here means the debit/credit
#: convention has regressed.
NATURAL_SIDE: dict[str, Side] = {
    "Cash-in-Hand": Side.DEBIT,
    "Bank Accounts": Side.DEBIT,
    "Fixed Assets": Side.DEBIT,
    "Current Assets": Side.DEBIT,
    "Direct Expenses": Side.DEBIT,
    "Indirect Expenses": Side.DEBIT,
    "Purchase Accounts": Side.DEBIT,
    "Sundry Debtors": Side.DEBIT,
    "Capital Account": Side.CREDIT,
    "Sundry Creditors": Side.CREDIT,
    "Sales Accounts": Side.CREDIT,
    "Direct Incomes": Side.CREDIT,
    "Indirect Incomes": Side.CREDIT,
    "Loans (Liability)": Side.CREDIT,
}


#: How long the backend gives a heavy read before it gives up and answers the
#: phone from the last snapshot. Mirrors ``heavy_job_timeout_seconds``. A query
#: that takes longer than this on the customer's own machine can never reach the
#: dashboard live, however healthy every other check looks -- which is exactly
#: how a shop ends up with an empty sales tile and no error to point at.
BACKEND_HEAVY_BUDGET_SECONDS = 180.0


@dataclass
class Report:
    passed: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    #: Query name -> seconds on the wire. Tally serves one caller at a time, so
    #: these add up rather than overlap.
    timings: dict[str, float] = field(default_factory=dict)

    def ok(self, message: str) -> None:
        self.passed.append(message)
        print(f"  [ OK ] {message}")

    def fail(self, message: str) -> None:
        self.failed.append(message)
        print(f"  [FAIL] {message}")

    def skip(self, message: str) -> None:
        self.skipped.append(message)
        print(f"  [SKIP] {message}")

    def check(self, condition: bool, message: str) -> bool:
        self.ok(message) if condition else self.fail(message)
        return condition


def section(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


async def livecheck(client: TallyClient, company: str | None = None) -> int:
    """Run the full verification. Returns the number of failures."""
    report = Report()

    # Every read below goes through the company guard rather than straight at
    # the client. The one-off check further down settles whether the company is
    # open *now*; this check runs for minutes, and an operator who closes it
    # partway through would otherwise take TallyPrime down with c0000005 on the
    # next voucher read -- the exact crash the guard exists to prevent.
    tally = GuardedClient(client)

    section("Connection")
    if not await tally.is_alive():
        report.fail(
            "TallyPrime is not responding. If Tally is open, check for a modal "
            "dialog in its window -- an open dialog blocks all requests."
        )
        return 1
    report.ok("TallyPrime is responding")

    # -- companies ------------------------------------------------------
    section("Companies")
    companies = await run(tally, "companies.list", {}, report)
    if not companies:
        report.fail("no company is open in Tally; open one and re-run")
        return len(report.failed)

    names = [c.name for c in companies]
    report.ok(f"{len(companies)} company/companies open: {', '.join(names)}")

    # Matched the way the guard matches, so a --company that differs only in
    # case or spacing is not accepted here and then refused on every read. The
    # name Tally spells is what the reads are scoped with from here on.
    asked = company or names[0]
    selected = next((c for c in companies if company_key(c.name) == company_key(asked)), None)
    if selected is None:
        report.fail(f"company {asked!r} is not open (open: {', '.join(names)})")
        return len(report.failed)

    target = selected.name
    report.ok(f"using {target!r}")

    books_from = selected.books_from or selected.financial_year_from or date.today()
    period = {"from_date": books_from, "to_date": date.today()}

    # -- groups ---------------------------------------------------------
    section("Groups")
    groups = await run(tally, "groups.list", {"company": target}, report)
    report.check(bool(groups), f"{len(groups)} group(s) returned")
    report.check(
        all(g.name for g in groups), "every group has a name (NAME attribute parsed)"
    )

    # -- ledgers: the sign convention -----------------------------------
    section("Ledgers and the debit/credit convention")
    ledgers = await run(tally, "ledgers.list", {"company": target}, report)
    report.check(bool(ledgers), f"{len(ledgers)} ledger(s) returned")
    report.check(all(lg.name for lg in ledgers), "every ledger has a name")

    checked = 0
    wrong: list[str] = []
    for ledger in ledgers:
        expected = NATURAL_SIDE.get(ledger.parent_group or "")
        if expected is None or ledger.closing_balance.is_zero:
            continue
        checked += 1
        if ledger.closing_balance.side is not expected:
            wrong.append(
                f"{ledger.name} ({ledger.parent_group}) = "
                f"{ledger.closing_balance}, expected {expected.value}"
            )

    if checked == 0:
        report.skip("no non-zero balances in sign-checkable groups")
    elif wrong:
        report.fail(f"{len(wrong)}/{checked} balance(s) on the wrong side:")
        for line in wrong[:8]:
            print(f"         - {line}")
    else:
        report.ok(f"all {checked} balance(s) fall on their natural accounting side")

    # -- voucher types --------------------------------------------------
    section("Voucher types")
    types = await run(tally, "voucher_types.list", {"company": target}, report)
    if types:
        classified = [t for t in types if t.kind is not VoucherTypeKind.OTHER]
        report.ok(f"{len(types)} type(s), {len(classified)} classified")
    else:
        report.skip("no voucher types returned")

    # -- vouchers -------------------------------------------------------
    section("Vouchers")
    vouchers = await run(tally, "vouchers.list", {"company": target, **period}, report)
    if not vouchers:
        report.skip("no vouchers in the period; skipping voucher checks")
    else:
        report.ok(f"{len(vouchers)} voucher(s) from {books_from} to {date.today()}")

        unbalanced = []
        for voucher in vouchers:
            if not voucher.ledger_entries:
                continue
            total = sum(e.amount.signed for e in voucher.ledger_entries)
            if total != Decimal("0.00"):
                unbalanced.append(
                    f"{voucher.voucher_type} #{voucher.voucher_number}: {total}"
                )

        with_entries = sum(1 for v in vouchers if v.ledger_entries)
        if unbalanced:
            # The classic cause is double-counting lines returned under two
            # different wrappers.
            report.fail(f"{len(unbalanced)}/{with_entries} voucher(s) do not balance:")
            for line in unbalanced[:8]:
                print(f"         - {line}")
        else:
            report.ok(f"all {with_entries} voucher(s) balance to zero")

        unclassified = [v for v in vouchers if v.kind is VoucherTypeKind.OTHER]
        if unclassified:
            kinds = sorted({v.voucher_type for v in unclassified})
            report.fail(f"{len(unclassified)} voucher(s) unclassified: {kinds}")
        else:
            report.ok("every voucher classified to an accounting kind")

        report.check(
            all(v.date for v in vouchers), "every voucher has a parsed date"
        )

    # -- incremental sync ----------------------------------------------
    await check_incremental(tally, target, period, len(vouchers), report)

    # -- stock ----------------------------------------------------------
    section("Stock")
    items = await run(tally, "stock_items.list", {"company": target}, report)
    if not items:
        report.skip("no stock items in this company")
    else:
        report.ok(f"{len(items)} stock item(s)")

        priced = [i for i in items if i.closing_rate and not i.closing_rate.is_zero]
        if not priced:
            report.skip("no items carry a closing rate")
        else:
            report.ok(f"{len(priced)} item(s) have a parsed rate")
            mismatched = []
            for item in priced:
                expected = Decimal(str(item.closing_quantity)) * item.closing_rate.amount
                if abs(item.closing_value.amount - abs(expected)) > Decimal("1.00"):
                    mismatched.append(
                        f"{item.name}: {item.closing_quantity} x "
                        f"{item.closing_rate.amount} != {item.closing_value.amount}"
                    )
            if mismatched:
                report.fail(f"{len(mismatched)} item(s) fail quantity x rate = value:")
                for line in mismatched[:6]:
                    print(f"         - {line}")
            else:
                report.ok("quantity x rate reconciles with value for every priced item")

        in_stock = [i for i in items if i.closing_quantity > 0]
        wrong_side = [
            i for i in in_stock if i.closing_value.side is not Side.DEBIT
            and not i.closing_value.is_zero
        ]
        if wrong_side:
            report.fail(f"{len(wrong_side)} item(s) hold stock valued as a credit")
        elif in_stock:
            report.ok(f"all {len(in_stock)} in-stock item(s) valued as an asset (Dr)")

    # -- outstanding ----------------------------------------------------
    section("Outstanding bills")
    bills = await run(
        tally, "outstanding.bills", {"company": target, "as_of": date.today()}, report
    )
    if not bills:
        billwise = [lg for lg in ledgers if lg.is_bill_wise]
        open_billwise = [lg for lg in billwise if not lg.closing_balance.is_zero]
        if open_billwise:
            report.fail(
                f"no bills returned, but {len(open_billwise)} bill-wise ledger(s) "
                f"carry a balance (e.g. {open_billwise[0].name}) -- the Bills "
                f"collection is probably wrong"
            )
        else:
            report.skip("no bill-wise ledgers carry a balance; nothing to reconcile")
    else:
        report.ok(f"{len(bills)} outstanding bill(s)")
        as_of = date.today()
        report.check(
            all(b.party_name and b.bill_name for b in bills),
            "every bill has a party and a reference",
        )
        overdue = [b for b in bills if b.days_overdue(as_of) > 0]
        report.ok(f"{len(overdue)} bill(s) past due")

    # -- timing ---------------------------------------------------------
    report_timings(report)

    # -- summary --------------------------------------------------------
    section("Summary")
    total = len(report.passed) + len(report.failed)
    print(f"  {len(report.passed)}/{total} checks passed, {len(report.skipped)} skipped")
    if report.failed:
        print("\n  Failures:")
        for line in report.failed:
            print(f"    - {line}")
    return len(report.failed)


async def check_incremental(
    tally: GuardedClient,
    company: str,
    period: dict[str, date],
    total_vouchers: int,
    report: Report,
) -> None:
    """Verify the change-id sync end to end, because its failure is silent.

    Incremental sync rests on two claims about the customer's Tally that no
    fixture can settle: that it reports a highest voucher ``AlterID``, and that
    it honours ``$AlterID > n`` as a collection filter. If the first is false the
    backend must fall back to date windows; if the *second* is false while the
    first is true, every "fetch only what changed" read quietly returns the
    entire history instead -- the exact multi-minute export this whole mechanism
    exists to avoid, and nothing else in the system would notice.

    The probe asks for vouchers altered after the newest one that exists. The
    honest answer is none.
    """
    section("Incremental sync")
    markers = await run_one(tally, "company.markers", {"company": company}, report)
    if markers is None:
        return

    if not markers.supports_incremental:
        report.skip(
            "this TallyPrime does not report AltVchId; the backend will sync by "
            "date window instead (correct, just slower)"
        )
        return

    report.ok(
        f"change ids reported: vouchers up to {markers.voucher_alter_id}, "
        f"masters up to {markers.master_alter_id}"
    )
    if markers.books_from:
        report.ok(f"books start {markers.books_from} -- the floor for a backfill")

    changed = await run(
        tally,
        "vouchers.list",
        {
            "company": company,
            **period,
            "include_inventory": False,
            "alter_id_min": markers.voucher_alter_id,
        },
        report,
        label="vouchers.list (delta)",
    )

    if not changed:
        report.ok("$AlterID filter honoured: nothing newer than the newest voucher")
        return

    if total_vouchers and len(changed) >= total_vouchers:
        report.fail(
            f"$AlterID filter appears to be ignored: asked for vouchers newer "
            f"than {markers.voucher_alter_id} and got {len(changed)} of "
            f"{total_vouchers}. Daily syncs would re-export the whole history."
        )
        return

    # A handful can legitimately come back: an operator editing a voucher while
    # the check runs bumps its AlterID past the marker we read a moment ago.
    report.ok(
        f"$AlterID filter honoured: {len(changed)} voucher(s) changed since the "
        f"marker was read"
    )


async def run_one(
    tally: GuardedClient, query_name: str, params: dict[str, Any], report: Report
) -> Any | None:
    """Execute a query that returns a single object rather than a collection."""
    started = time.monotonic()
    try:
        query = get_query(query_name)
        return await tally.execute(query, query.validate_params(params))
    except Exception as exc:  # noqa: BLE001 - a livecheck must never abort early
        message = getattr(exc, "user_message", None) or f"{type(exc).__name__}: {exc}"
        report.fail(f"{query_name}: {message}")
        return None
    finally:
        report.timings[query_name] = time.monotonic() - started


async def run(
    tally: GuardedClient,
    query_name: str,
    params: dict[str, Any],
    report: Report,
    *,
    label: str | None = None,
) -> list[Any]:
    """Execute one query, converting failure into a reported check."""
    started = time.monotonic()
    name = label or query_name
    try:
        query = get_query(query_name)
        return await tally.execute(query, query.validate_params(params))
    except TallyError as exc:
        report.fail(f"{name}: {exc.user_message} ({exc})")
        return []
    except Exception as exc:  # noqa: BLE001 - a livecheck must never abort early
        report.fail(f"{name}: {type(exc).__name__}: {exc}")
        return []
    finally:
        report.timings[name] = time.monotonic() - started


def report_timings(report: Report) -> None:
    """Print how long each read took, against the budget the backend allows.

    Correctness checks passing while the dashboard stays empty is a real and
    confusing state, and the difference is almost always here: on a shop with
    years of history, ``vouchers.list`` is an order of magnitude slower than
    everything else and quietly overruns its budget every time.
    """
    section("Timing")
    if not report.timings:
        report.skip("nothing ran")
        return

    for name, seconds in sorted(report.timings.items(), key=lambda kv: -kv[1]):
        over = seconds > BACKEND_HEAVY_BUDGET_SECONDS
        print(f"  [{'SLOW' if over else ' OK '}] {name:<22} {seconds:7.1f}s")

    slowest = max(report.timings.values())
    if slowest > BACKEND_HEAVY_BUDGET_SECONDS:
        report.fail(
            f"a read took {slowest:.0f}s, over the backend's "
            f"{BACKEND_HEAVY_BUDGET_SECONDS:.0f}s budget for one read"
        )
        print("         The dashboard will fall back to its last snapshot for that")
        print("         section rather than show live data. Narrow the date window,")
        print("         or raise TALLYFLOW_HEAVY_JOB_TIMEOUT_SECONDS on the backend.")
    else:
        report.ok(f"every read finished inside the {BACKEND_HEAVY_BUDGET_SECONDS:.0f}s budget")


def print_manifest() -> None:
    print(f"Queries under test: {len(registry_manifest())}")
    for entry in registry_manifest():
        print(f"  - {entry['name']}")
