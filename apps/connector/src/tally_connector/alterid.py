"""Does a master's ``AlterID`` move when a voucher changes its balance?

The whole incremental-master design turns on this one question, and no
documentation answers it. TallyPrime stamps every master with an ``AlterID``
and every voucher with its own, and a collection can be filtered on
``$AlterID > n`` -- that much is verified. What is *not* established is which
event bumps a **master's** counter:

* If posting a voucher against "Cash-in-Hand" bumps that ledger's ``AlterID``,
  then an incremental master read is simply correct: ask for the ledgers above
  the cursor and you get every ledger whose balance moved.
* If it does not -- if ``AlterID`` only tracks edits to the master *record*
  (renamed, regrouped, opening balance changed) -- then an incremental master
  read returns correct names and **stale balances**, silently. Sales would sit
  at yesterday's figure with no error anywhere, which in an accounting product
  is worse than being slow.

The second outcome is not hypothetical: the closest open-source equivalent
(``tally-database-loader``) ships exactly that behaviour and its own release
history marked incremental sync "not stable". So this is settled by measurement
on a real shop's Tally before anything is built on top of it.

**How to run it.** Twice, either side of a real voucher::

    tally-connector alterid-probe --company "Bhtia Supermarket"
    ... now pass one voucher in TallyPrime against any ledger ...
    tally-connector alterid-probe --company "Bhtia Supermarket"

The first run records a baseline; the second compares and prints a verdict. It
reads only -- the operator posts the voucher, because this product never writes
to Tally.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tally_core.tally import TallyClient, get_query

from .loaded import GuardedClient

logger = logging.getLogger(__name__)

#: Where the baseline lives between the two runs. Beside the connector's own
#: data rather than in the working directory: the operator running this is
#: following instructions over the phone, and "run it again from the same
#: folder" is one more thing to get wrong.
BASELINE_NAME = "alterid-probe.json"


@dataclass(frozen=True)
class MasterState:
    """One master's change counter and the figure a voucher would move."""

    alter_id: int | None
    #: Rendered rather than numeric: this is only ever compared for equality
    #: and printed, and a string keeps Dr/Cr visible in the report.
    balance: str


@dataclass
class ProbeSnapshot:
    """Everything one run observes, and what gets written to the baseline."""

    company: str
    taken_at: str
    master_alter_id: int | None
    voucher_alter_id: int | None
    ledgers: dict[str, MasterState] = field(default_factory=dict)
    stock_items: dict[str, MasterState] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "taken_at": self.taken_at,
            "master_alter_id": self.master_alter_id,
            "voucher_alter_id": self.voucher_alter_id,
            "ledgers": {
                name: {"alter_id": s.alter_id, "balance": s.balance}
                for name, s in self.ledgers.items()
            },
            "stock_items": {
                name: {"alter_id": s.alter_id, "balance": s.balance}
                for name, s in self.stock_items.items()
            },
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> ProbeSnapshot:
        def states(blob: Any) -> dict[str, MasterState]:
            if not isinstance(blob, dict):
                return {}
            return {
                name: MasterState(
                    alter_id=entry.get("alter_id"), balance=entry.get("balance", "")
                )
                for name, entry in blob.items()
                if isinstance(entry, dict)
            }

        return cls(
            company=raw.get("company", ""),
            taken_at=raw.get("taken_at", ""),
            master_alter_id=raw.get("master_alter_id"),
            voucher_alter_id=raw.get("voucher_alter_id"),
            ledgers=states(raw.get("ledgers")),
            stock_items=states(raw.get("stock_items")),
        )


@dataclass
class Verdict:
    """What the comparison concluded, and what it means for the sync design."""

    #: ``True``  -- every master whose balance moved also had its AlterID bumped.
    #: ``False`` -- at least one moved balance carried an unchanged AlterID.
    #: ``None``  -- nothing moved, so the run proves nothing either way.
    tracks_transactions: bool | None
    voucher_alter_id_moved: bool
    master_alter_id_moved: bool
    #: name -> (old balance, new balance, alter_id moved?)
    moved: list[tuple[str, str, str, bool]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def baseline_path(data_dir: Path) -> Path:
    return data_dir / BASELINE_NAME


async def take_snapshot(tally: GuardedClient, company: str) -> ProbeSnapshot:
    """Read the counters and the figures a voucher would move.

    Three reads, all of which the connector already makes in normal operation,
    so this costs a shop's Tally no more than one ordinary refresh.
    """
    markers_query = get_query("company.markers")
    markers = await tally.execute(
        markers_query, markers_query.validate_params({"company": company})
    )

    ledgers_query = get_query("ledgers.list")
    ledgers = await tally.execute(
        ledgers_query, ledgers_query.validate_params({"company": company})
    )

    stock_query = get_query("stock_items.list")
    stock_items = await tally.execute(
        stock_query, stock_query.validate_params({"company": company})
    )

    return ProbeSnapshot(
        company=company,
        taken_at=datetime.now(UTC).isoformat(timespec="seconds"),
        master_alter_id=markers.master_alter_id,
        voucher_alter_id=markers.voucher_alter_id,
        ledgers={
            lg.name: MasterState(alter_id=lg.alter_id, balance=str(lg.closing_balance))
            for lg in ledgers
        },
        stock_items={
            item.name: MasterState(
                alter_id=item.alter_id,
                # Quantity as well as value: a stock transfer moves the former
                # without necessarily moving the latter.
                balance=f"{item.closing_quantity} @ {item.closing_value}",
            )
            for item in stock_items
        },
    )


def compare(before: ProbeSnapshot, after: ProbeSnapshot) -> Verdict:
    """Decide what the two snapshots say about master AlterID semantics."""
    verdict = Verdict(
        tracks_transactions=None,
        voucher_alter_id_moved=_moved(before.voucher_alter_id, after.voucher_alter_id),
        master_alter_id_moved=_moved(before.master_alter_id, after.master_alter_id),
    )

    for label, old_side, new_side in (
        ("ledger", before.ledgers, after.ledgers),
        ("stock item", before.stock_items, after.stock_items),
    ):
        for name, new in new_side.items():
            old = old_side.get(name)
            if old is None:
                # Created between the runs. Its balance has no "before" to have
                # moved from, so it says nothing about the question being asked.
                verdict.notes.append(f"new {label} {name!r} appeared between runs")
                continue
            if old.balance == new.balance:
                continue
            verdict.moved.append(
                (name, old.balance, new.balance, _moved(old.alter_id, new.alter_id))
            )

    if not verdict.moved:
        verdict.notes.append(
            "no master's balance changed between the two runs -- either no "
            "voucher was passed, or it did not affect a ledger or stock item"
        )
        return verdict

    # Conservative on a mixed result. If even one balance moved without its
    # AlterID moving, an incremental master read can miss it, and "usually
    # correct" is not a property an accounting figure can have.
    verdict.tracks_transactions = all(bumped for *_, bumped in verdict.moved)
    return verdict


def _moved(before: int | None, after: int | None) -> bool:
    """Whether a counter advanced. Absent on either side means unknowable."""
    if before is None or after is None:
        return False
    return after > before


async def run_probe(client: TallyClient, company: str | None, path: Path) -> int:
    """Record a baseline, or compare against one. Returns a process exit code."""
    tally = GuardedClient(client)

    companies_query = get_query("companies.list")
    companies = await tally.execute(
        companies_query, companies_query.validate_params({})
    )
    if not companies:
        print("[FAIL] no company is open in TallyPrime; open one and re-run")
        return 1

    target = company or companies[0].name
    print(f"Company : {target}")
    print(f"Baseline: {path}")
    print()

    existing = _load(path)
    current = await take_snapshot(tally, target)

    if existing is None or existing.company != target:
        _save(path, current)
        _print_recorded(current)
        return 0

    verdict = compare(existing, current)
    _print_verdict(existing, current, verdict)

    # The baseline is left in place on an inconclusive run so the operator can
    # pass a voucher and simply run it again, rather than starting over.
    if verdict.tracks_transactions is not None:
        path.unlink(missing_ok=True)
    return 0


def _load(path: Path) -> ProbeSnapshot | None:
    if not path.exists():
        return None
    try:
        return ProbeSnapshot.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        # A corrupt baseline must not strand the operator -- recording a fresh
        # one is always a valid next step.
        logger.warning("ignoring unreadable baseline %s: %s", path, exc)
        return None


def _save(path: Path, snapshot: ProbeSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot.to_json(), indent=2), encoding="utf-8")


def _print_recorded(snapshot: ProbeSnapshot) -> None:
    print("Baseline recorded.")
    print(f"  master AlterID (AltMstId) : {_show(snapshot.master_alter_id)}")
    print(f"  voucher AlterID (AltVchId): {_show(snapshot.voucher_alter_id)}")
    print(f"  ledgers                   : {len(snapshot.ledgers)}")
    print(f"  stock items               : {len(snapshot.stock_items)}")
    print()
    if snapshot.master_alter_id is None:
        print("  Note: this TallyPrime did not report AltMstId. Incremental master")
        print("        reads are not available on it at all; the answer will have to")
        print("        be a date/name-scoped read regardless of what the probe finds.")
        print()
    print("Next:")
    print("  1. In TallyPrime, pass ONE voucher against any ledger (a receipt or")
    print("     payment is enough). Accept it so the balance actually moves.")
    print("  2. Run this same command again.")


def _print_verdict(
    before: ProbeSnapshot, after: ProbeSnapshot, verdict: Verdict
) -> None:
    print(f"Comparing against the baseline taken at {before.taken_at}.")
    print()
    print(f"  master AlterID (AltMstId) : {_show(before.master_alter_id)}"
          f" -> {_show(after.master_alter_id)}"
          f"  [{'moved' if verdict.master_alter_id_moved else 'unchanged'}]")
    print(f"  voucher AlterID (AltVchId): {_show(before.voucher_alter_id)}"
          f" -> {_show(after.voucher_alter_id)}"
          f"  [{'moved' if verdict.voucher_alter_id_moved else 'unchanged'}]")
    print()

    if not verdict.voucher_alter_id_moved:
        print("  [WARN] No voucher was registered between the two runs. If you did")
        print("         pass one, this Tally is not reporting AltVchId and the whole")
        print("         incremental path is unavailable on it.")
        print()

    if verdict.moved:
        print(f"  {len(verdict.moved)} master(s) changed balance:")
        for name, old, new, bumped in verdict.moved[:10]:
            flag = "AlterID moved" if bumped else "AlterID UNCHANGED"
            print(f"    - {name}: {old} -> {new}  [{flag}]")
        if len(verdict.moved) > 10:
            print(f"    ... and {len(verdict.moved) - 10} more")
        print()

    for note in verdict.notes:
        print(f"  Note: {note}")
    if verdict.notes:
        print()

    print("Verdict")
    print("-------")
    if verdict.tracks_transactions is None:
        print("  INCONCLUSIVE. Nothing moved, so nothing was measured.")
        print("  The baseline has been kept -- pass a voucher and run this again.")
    elif verdict.tracks_transactions:
        print("  Master AlterID DOES track transactions on this TallyPrime.")
        print("  An incremental master read is sufficient on its own: filtering")
        print("  ledgers.list on $AlterID > cursor returns every ledger whose")
        print("  balance moved.")
    else:
        print("  Master AlterID does NOT track transactions on this TallyPrime.")
        print("  A balance moved while its master's AlterID stood still, so an")
        print("  incremental master read would serve a stale figure with no error.")
        print("  Balances must instead be re-read for the masters named by the")
        print("  vouchers a delta returns.")


def _show(value: int | None) -> str:
    return "not reported" if value is None else str(value)
