"""The master-AlterID probe.

The probe exists to settle one question on a real shop's Tally: does a master's
``AlterID`` move when a *voucher* moves that master's balance? These tests pin
the comparison logic, because getting the verdict backwards would send the sync
design down the wrong path -- and the wrong path serves stale balances with no
error anywhere.
"""

from __future__ import annotations

import json

from tally_connector.alterid import MasterState, ProbeSnapshot, compare


def snapshot(
    *,
    master: int | None = 100,
    voucher: int | None = 500,
    ledgers: dict[str, tuple[int | None, str]] | None = None,
) -> ProbeSnapshot:
    return ProbeSnapshot(
        company="Acme",
        taken_at="2026-08-29T10:00:00+00:00",
        master_alter_id=master,
        voucher_alter_id=voucher,
        ledgers={
            name: MasterState(alter_id=alter_id, balance=balance)
            for name, (alter_id, balance) in (ledgers or {}).items()
        },
    )


def test_balance_moved_without_alter_id_means_masters_cannot_be_synced_incrementally():
    """The outcome the whole probe exists to catch.

    A stale balance served as current is the failure mode that has no symptom,
    so a single unbumped master is enough to rule the approach out.
    """
    before = snapshot(ledgers={"Cash": (7, "100.00 Dr")})
    after = snapshot(voucher=501, ledgers={"Cash": (7, "250.00 Dr")})

    verdict = compare(before, after)

    assert verdict.tracks_transactions is False
    assert verdict.voucher_alter_id_moved is True
    assert verdict.moved == [("Cash", "100.00 Dr", "250.00 Dr", False)]


def test_balance_and_alter_id_moving_together_clears_the_incremental_path():
    before = snapshot(ledgers={"Cash": (7, "100.00 Dr")})
    after = snapshot(master=101, voucher=501, ledgers={"Cash": (9, "250.00 Dr")})

    verdict = compare(before, after)

    assert verdict.tracks_transactions is True
    assert verdict.master_alter_id_moved is True


def test_one_unbumped_master_among_many_still_fails_the_check():
    """Conservative on a mixed result: "usually correct" is not a property an
    accounting figure can have."""
    before = snapshot(ledgers={"Cash": (7, "100.00 Dr"), "Sales": (8, "500.00 Cr")})
    after = snapshot(
        voucher=501, ledgers={"Cash": (9, "250.00 Dr"), "Sales": (8, "650.00 Cr")}
    )

    verdict = compare(before, after)

    assert verdict.tracks_transactions is False


def test_nothing_moving_is_inconclusive_rather_than_a_pass():
    """No voucher passed means nothing was measured. Reporting that as success
    would bake an unverified assumption into the design."""
    before = snapshot(ledgers={"Cash": (7, "100.00 Dr")})
    after = snapshot(ledgers={"Cash": (7, "100.00 Dr")})

    verdict = compare(before, after)

    assert verdict.tracks_transactions is None
    assert verdict.moved == []
    assert any("no master's balance changed" in note for note in verdict.notes)


def test_a_master_created_between_runs_is_noted_not_counted():
    """A new ledger has no prior balance to have moved from, so it cannot
    speak to the question -- but a silent skip would look like a bug."""
    before = snapshot(ledgers={"Cash": (7, "100.00 Dr")})
    after = snapshot(
        voucher=501, ledgers={"Cash": (7, "100.00 Dr"), "New Party": (12, "0.00 Dr")}
    )

    verdict = compare(before, after)

    assert verdict.tracks_transactions is None
    assert any("New Party" in note for note in verdict.notes)


def test_absent_counters_never_read_as_movement():
    """A Tally that does not report AltVchId returns the element empty. Treating
    None -> None as "moved" would claim a voucher was passed when none was."""
    before = snapshot(master=None, voucher=None, ledgers={"Cash": (None, "100.00 Dr")})
    after = snapshot(master=None, voucher=None, ledgers={"Cash": (None, "250.00 Dr")})

    verdict = compare(before, after)

    assert verdict.voucher_alter_id_moved is False
    assert verdict.master_alter_id_moved is False
    # The balance still moved, and no AlterID backed it up.
    assert verdict.tracks_transactions is False


def test_snapshot_survives_the_round_trip_through_the_baseline_file():
    """The two runs are separate processes, so the baseline is the only thing
    carrying the first observation to the second."""
    original = snapshot(ledgers={"Cash": (7, "100.00 Dr")})
    original.stock_items = {"Widget": MasterState(alter_id=3, balance="5.0 @ 50.00 Dr")}

    restored = ProbeSnapshot.from_json(json.loads(json.dumps(original.to_json())))

    assert restored.company == "Acme"
    assert restored.master_alter_id == 100
    assert restored.ledgers["Cash"] == MasterState(alter_id=7, balance="100.00 Dr")
    assert restored.stock_items["Widget"].alter_id == 3


def test_stock_items_are_compared_alongside_ledgers():
    """Inventory value moves on a voucher just as a ledger balance does, and the
    stock tiles are read from the same incremental path."""
    before = snapshot()
    before.stock_items = {"Widget": MasterState(alter_id=3, balance="5.0 @ 50.00 Dr")}
    after = snapshot(voucher=501)
    after.stock_items = {"Widget": MasterState(alter_id=3, balance="2.0 @ 20.00 Dr")}

    verdict = compare(before, after)

    assert verdict.tracks_transactions is False
    assert verdict.moved[0][0] == "Widget"
