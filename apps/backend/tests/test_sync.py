"""Chunked backfill, incremental sync, and the store they share.

The behaviours under test here are the ones whose failure is silent. A planner
that leaves a gap, a delta that deletes what it should not, a cursor that steps
past an edit -- none of them raise, and all of them produce a dashboard that is
merely *wrong*. Several tests therefore assert on how many times Tally was
asked and with what window, not just on the numbers that came back.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from tally_backend.db.models import (
    CompanySyncState,
    SyncChunk,
    SyncRun,
    SyncState,
    VoucherRecord,
    utc_now,
)
from tally_backend.services.sync import SyncCoordinator, SyncService, plan_backfill
from tally_backend.services.voucher_store import VoucherStore, record_key

TODAY = date(2026, 8, 5)


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def plan(books_from: date | None, *, today: date = TODAY, months: int = 6, years: int = 4):
    return plan_backfill(
        books_from=books_from,
        today=today,
        chunk_months=months,
        max_history_years=years,
        inventory_days=400,
    )


def test_the_plan_starts_at_today_and_walks_backwards() -> None:
    """Newest first, so the dashboard is useful before the sync finishes."""
    windows = plan(date(2022, 4, 1))

    assert windows[0].to_date == TODAY
    assert windows[0].from_date > windows[1].from_date
    assert windows[-1].from_date == date(2022, 8, 5), "floored at the history cap"


def test_the_plan_has_no_gaps_and_no_overlaps() -> None:
    """A gap is months of missing trade that no error would ever report."""
    windows = plan(date(2022, 4, 1))

    for newer, older in zip(windows, windows[1:], strict=False):
        assert older.to_date == newer.from_date - timedelta(days=1)


def test_the_plan_is_floored_at_the_books_start() -> None:
    """A shop that opened last year must not be asked for four empty years."""
    windows = plan(date(2026, 1, 15))

    assert len(windows) == 1
    assert windows[0].from_date == date(2026, 1, 15)
    assert windows[0].to_date == TODAY


def test_the_plan_is_capped_even_when_the_books_go_back_further() -> None:
    windows = plan(date(2010, 4, 1))

    assert windows[-1].from_date == date(2022, 8, 5)
    assert len(windows) == 8


def test_an_unknown_books_start_still_produces_a_bounded_plan() -> None:
    """Tally not answering must not mean an unbounded read."""
    windows = plan(None)
    assert windows[-1].from_date == date(2022, 8, 5)


def test_books_starting_in_the_future_produce_no_work() -> None:
    assert plan(date(2027, 1, 1)) == []


def test_only_recent_slices_carry_inventory_lines() -> None:
    """Stock detail is most of the bytes and nothing reads it on old invoices."""
    windows = plan(date(2022, 4, 1))

    assert windows[0].include_inventory is True
    assert windows[-1].include_inventory is False


def test_smaller_chunks_mean_more_and_shorter_reads() -> None:
    """The knob that decides whether a first sync works at all."""
    six = plan(date(2024, 8, 5), months=6)
    three = plan(date(2024, 8, 5), months=3)

    assert len(three) > len(six)
    assert max(w.days for w in three) < max(w.days for w in six)


# --------------------------------------------------------------------------
# The store
# --------------------------------------------------------------------------


def voucher(
    *,
    guid: str,
    day: date,
    alter_id: int | None = None,
    amount: str = "1000.00",
    cancelled: bool = False,
) -> dict:
    return {
        "guid": guid,
        "alter_id": alter_id,
        "voucher_number": guid,
        "voucher_type": "Sales",
        "kind": "sales",
        "date": day.isoformat(),
        "is_cancelled": cancelled,
        "amount": {"amount": amount, "side": "credit", "currency": "INR"},
        "ledger_entries": [
            {
                "ledger_name": "Party",
                "amount": {"amount": amount, "side": "debit", "currency": "INR"},
            },
            {
                "ledger_name": "Sales",
                "amount": {"amount": amount, "side": "credit", "currency": "INR"},
            },
        ],
        "inventory_entries": [],
    }


def test_identity_survives_an_edit() -> None:
    """A renumbered voucher is the same voucher, not a second one."""
    before = voucher(guid="g-1", day=TODAY, alter_id=10)
    after = voucher(guid="g-1", day=TODAY - timedelta(days=1), alter_id=11)
    after["voucher_number"] = "renumbered"

    assert record_key(before) == record_key(after)


def test_identity_falls_back_when_tally_sends_no_guid() -> None:
    keyed = record_key({"master_id": 512, "voucher_type": "Sales"})
    assert keyed == "m:512"
    # And with neither, something stable rather than nothing.
    assert record_key({"voucher_type": "Sales", "voucher_number": "7"}).startswith("h:")


async def test_an_edit_replaces_rather_than_duplicates(app, linked_company) -> None:
    company_id = linked_company["company_id"]

    async with app.state.session_factory() as session:
        store = VoucherStore(session)
        await store.ingest(company_id, [voucher(guid="g-1", day=TODAY, alter_id=10)])
        result = await store.ingest(
            company_id,
            [voucher(guid="g-1", day=TODAY, alter_id=11, amount="2500.00")],
        )
        await session.commit()

        assert result.updated == 1
        assert await store.count(company_id) == 1
        rows = await store.read(company_id, from_date=TODAY, to_date=TODAY)
        assert rows[0]["amount"]["amount"] == "2500.00"


async def test_an_older_revision_never_overwrites_a_newer_one(app, linked_company) -> None:
    """Reads overlap; the store must not go backwards when they do."""
    company_id = linked_company["company_id"]

    async with app.state.session_factory() as session:
        store = VoucherStore(session)
        await store.ingest(
            company_id, [voucher(guid="g-1", day=TODAY, alter_id=11, amount="2500.00")]
        )
        result = await store.ingest(
            company_id, [voucher(guid="g-1", day=TODAY, alter_id=10, amount="1000.00")]
        )
        await session.commit()

        assert result.skipped == 1
        rows = await store.read(company_id, from_date=TODAY, to_date=TODAY)
        assert rows[0]["amount"]["amount"] == "2500.00"


async def test_a_delta_never_deletes(app, linked_company) -> None:
    """A quiet day returns nothing changed -- not "everything is gone"."""
    company_id = linked_company["company_id"]

    async with app.state.session_factory() as session:
        store = VoucherStore(session)
        await store.ingest(company_id, [voucher(guid="g-1", day=TODAY, alter_id=10)])
        result = await store.ingest(company_id, [])  # additive: no window
        await session.commit()

        assert result.deleted == 0
        assert await store.count(company_id) == 1


async def test_a_full_window_read_removes_what_tally_no_longer_has(
    app, linked_company
) -> None:
    """The only mechanism that ever catches a deletion in Tally."""
    company_id = linked_company["company_id"]

    async with app.state.session_factory() as session:
        store = VoucherStore(session)
        await store.ingest(
            company_id,
            [
                voucher(guid="g-1", day=TODAY, alter_id=10),
                voucher(guid="g-2", day=TODAY, alter_id=11),
            ],
        )
        result = await store.ingest(
            company_id,
            [voucher(guid="g-1", day=TODAY, alter_id=10)],
            window=(TODAY, TODAY),
        )
        await session.commit()

        assert result.deleted == 1
        assert await store.count(company_id) == 1


async def test_a_full_window_read_leaves_other_windows_alone(app, linked_company) -> None:
    """Re-reading this quarter must not empty last year."""
    company_id = linked_company["company_id"]
    old = TODAY - timedelta(days=400)

    async with app.state.session_factory() as session:
        store = VoucherStore(session)
        await store.ingest(
            company_id,
            [
                voucher(guid="old", day=old, alter_id=1),
                voucher(guid="new", day=TODAY, alter_id=2),
            ],
        )
        await store.ingest(company_id, [], window=(TODAY, TODAY))
        await session.commit()

        assert await store.count(company_id) == 1
        assert await store.read(company_id, from_date=old, to_date=old)


async def test_an_undated_voucher_is_skipped_not_stored(app, linked_company) -> None:
    """It cannot be placed on any report, and would poison a coverage window."""
    company_id = linked_company["company_id"]

    async with app.state.session_factory() as session:
        store = VoucherStore(session)
        broken = voucher(guid="g-1", day=TODAY)
        broken["date"] = "not-a-date"
        result = await store.ingest(company_id, [broken])
        await session.commit()

        assert result.skipped == 1
        assert await store.count(company_id) == 0


# --------------------------------------------------------------------------
# Running a backfill
# --------------------------------------------------------------------------


@pytest.fixture
def coordinator(app, settings) -> SyncCoordinator:
    settings.sync_chunk_pause_seconds = 0
    return SyncCoordinator(app.state.session_factory, app.state.hub, settings)


def markers(voucher_alter_id: int | None = 500, master_alter_id: int | None = 20) -> dict:
    return {
        "name": "Bhtia Supermarket",
        "voucher_alter_id": voucher_alter_id,
        "master_alter_id": master_alter_id,
        "books_from": (TODAY - timedelta(days=365)).isoformat(),
    }


def vouchers_by_window(fake) -> None:
    """Answer each slice with one voucher dated inside it.

    Keyed on the window so a planner that sent overlapping or wrong ranges shows
    up as a wrong voucher count rather than as an identical response every time.
    """

    def respond(params):
        start = date.fromisoformat(params["from_date"])
        end = date.fromisoformat(params["to_date"])
        return [
            voucher(guid=f"v-{start.isoformat()}", day=start, alter_id=100),
            voucher(guid=f"v-{end.isoformat()}", day=end, alter_id=101),
        ]

    fake.set("vouchers.list", respond)


async def run_backfill(coordinator: SyncCoordinator, company_id: str) -> None:
    """Start a backfill and wait for it, so tests are not timing-dependent."""
    await coordinator.start_backfill(company_id, today=TODAY)
    task = coordinator._tasks.get(company_id)
    if task is not None:
        await task


async def test_a_backfill_reads_one_slice_at_a_time(
    app, coordinator, linked_company, fake_connector
) -> None:
    """The whole point: never one four-year export."""
    fake_connector.set("company.markers", markers())
    vouchers_by_window(fake_connector)

    await run_backfill(coordinator, linked_company["company_id"])

    windows = [
        (params["from_date"], params["to_date"])
        for query, params in fake_connector.calls
        if query == "vouchers.list"
    ]
    assert len(windows) == 2, "a year of books at six-month slices"
    assert all(
        (date.fromisoformat(to) - date.fromisoformat(frm)).days <= 190
        for frm, to in windows
    )


async def test_a_backfill_asks_tally_where_the_books_start(
    app, coordinator, linked_company, fake_connector
) -> None:
    """A company open eight months must not cost four years of empty exports."""
    fake_connector.set("company.markers", markers())
    vouchers_by_window(fake_connector)

    await run_backfill(coordinator, linked_company["company_id"])

    async with app.state.session_factory() as session:
        run = await SyncService(session, app.state.settings).latest_run(
            linked_company["company_id"]
        )
        assert run.total_chunks == 2
        assert run.state is SyncState.SUCCEEDED


async def test_a_finished_backfill_records_the_window_it_covers(
    app, coordinator, linked_company, fake_connector
) -> None:
    fake_connector.set("company.markers", markers())
    vouchers_by_window(fake_connector)

    await run_backfill(coordinator, linked_company["company_id"])

    async with app.state.session_factory() as session:
        state = await session.get(CompanySyncState, linked_company["company_id"])
        assert state.backfilled_to == TODAY
        assert state.backfilled_from == TODAY - timedelta(days=365)
        assert state.covers(TODAY - timedelta(days=30), TODAY)


async def test_the_cursor_is_taken_before_the_backfill_not_after(
    app, coordinator, linked_company, fake_connector
) -> None:
    """A voucher edited *during* a multi-minute backfill must not be lost.

    The marker is read first and stored as the cursor, so an edit made while
    the slices are being read has a higher AlterID and is picked up by the next
    delta. Recording the marker afterwards would step the cursor past it.
    """
    fake_connector.set("company.markers", markers(voucher_alter_id=500))
    vouchers_by_window(fake_connector)

    await run_backfill(coordinator, linked_company["company_id"])

    async with app.state.session_factory() as session:
        state = await session.get(CompanySyncState, linked_company["company_id"])
        assert state.voucher_alter_id == 500


async def test_a_failing_slice_stops_the_run_and_keeps_what_was_read(
    app, coordinator, linked_company, fake_connector
) -> None:
    """A struggling Tally is left alone, and finished work is not thrown away."""
    fake_connector.set("company.markers", markers())
    seen: list[str] = []

    def respond(params):
        seen.append(params["from_date"])
        if len(seen) > 1:
            raise RuntimeError("unreachable: the run should have stopped")
        return [voucher(guid="v-1", day=TODAY, alter_id=100)]

    fake_connector.set("vouchers.list", respond)

    await coordinator.start_backfill(linked_company["company_id"], today=TODAY)
    task = coordinator._tasks.get(linked_company["company_id"])

    # Fail every read from the second slice onwards.
    async def failing_run(**kwargs):
        if kwargs["query"] == "vouchers.list" and len(seen) >= 1:
            from tally_core.protocol import JobError, JobResult

            return JobResult.failure(
                "job",
                JobError(
                    code="connector_offline",
                    message="offline",
                    user_message="Your Tally PC is offline.",
                    retryable=True,
                ),
            )
        return await fake_connector.run(**kwargs)

    app.state.hub.run = failing_run
    if task is not None:
        await task

    async with app.state.session_factory() as session:
        service = SyncService(session, app.state.settings)
        run = await service.latest_run(linked_company["company_id"])
        assert run.state is SyncState.FAILED
        assert run.completed_chunks == 1
        assert run.user_message == "Your Tally PC is offline."

        # The finished slice is coverage, and the failed one is not claimed.
        state = await session.get(CompanySyncState, linked_company["company_id"])
        assert state.backfilled_to == TODAY
        assert state.backfilled_from > TODAY - timedelta(days=365)


async def test_a_stopped_backfill_resumes_at_the_slice_it_reached(
    app, coordinator, linked_company, fake_connector
) -> None:
    """Resuming beats restarting by exactly the exports already served."""
    company_id = linked_company["company_id"]
    fake_connector.set("company.markers", markers())
    vouchers_by_window(fake_connector)

    await run_backfill(coordinator, company_id)

    async with app.state.session_factory() as session:
        run = await SyncService(session, app.state.settings).latest_run(company_id)
        # Rewind: pretend the second slice never ran and the instance died.
        chunk = await session.scalar(
            select(SyncChunk).where(SyncChunk.run_id == run.id, SyncChunk.seq == 1)
        )
        chunk.state = SyncState.PENDING
        run.state = SyncState.FAILED
        run.completed_chunks = 1
        state = await session.get(CompanySyncState, company_id)
        state.backfilled_from = chunk.to_date + timedelta(days=1)
        await session.commit()

    before = fake_connector.call_count("vouchers.list")
    resumed = await coordinator.ensure_backfill(company_id)
    task = coordinator._tasks.get(company_id)
    if task is not None:
        await task

    assert resumed is not None
    assert fake_connector.call_count("vouchers.list") == before + 1, (
        "only the outstanding slice should be re-read"
    )


async def test_resuming_retries_slices_that_were_left_failed(
    app, coordinator, linked_company, fake_connector
) -> None:
    """The state a real stuck backfill is actually in.

    ``_fail_chunk`` leaves a slice ``FAILED`` once it runs out of attempts --
    not ``PENDING`` -- and ``_begin_next_chunk`` only claims ``PENDING`` ones.
    So a resume used to find no work, end immediately and be marked failed
    again with the same message: an app whose "Continue reading" button could
    never make progress, no matter how healthy Tally was. Seen on a real
    backfill stuck for four days with seven failed slices.
    """
    company_id = linked_company["company_id"]
    fake_connector.set("company.markers", markers())
    vouchers_by_window(fake_connector)

    await run_backfill(coordinator, company_id)

    async with app.state.session_factory() as session:
        run = await SyncService(session, app.state.settings).latest_run(company_id)
        chunk = await session.scalar(
            select(SyncChunk).where(SyncChunk.run_id == run.id, SyncChunk.seq == 1)
        )
        # Exactly what _fail_chunk leaves behind: failed, attempts spent.
        chunk.state = SyncState.FAILED
        chunk.attempts = app.state.settings.sync_chunk_attempts
        chunk.error = "connector disconnected"
        run.state = SyncState.FAILED
        run.error = "connector disconnected"
        run.user_message = "Your Tally PC went offline."
        run.completed_chunks = 1
        await session.commit()

    before = fake_connector.call_count("vouchers.list")
    resumed = await coordinator.ensure_backfill(company_id)
    task = coordinator._tasks.get(company_id)
    if task is not None:
        await task

    assert resumed is not None
    assert fake_connector.call_count("vouchers.list") == before + 1, (
        "the failed slice must be re-read, not skipped"
    )

    async with app.state.session_factory() as session:
        run = await SyncService(session, app.state.settings).latest_run(company_id)
        assert run.state is SyncState.SUCCEEDED
        # The stale failure must not survive a run that then succeeded.
        assert run.error is None
        assert run.user_message is None


async def test_resuming_reclaims_a_slice_left_running_by_a_killed_instance(
    app, coordinator, linked_company, fake_connector
) -> None:
    """Nothing else ever clears RUNNING, so it would block the run forever."""
    company_id = linked_company["company_id"]
    fake_connector.set("company.markers", markers())
    vouchers_by_window(fake_connector)

    await run_backfill(coordinator, company_id)

    async with app.state.session_factory() as session:
        run = await SyncService(session, app.state.settings).latest_run(company_id)
        chunk = await session.scalar(
            select(SyncChunk).where(SyncChunk.run_id == run.id, SyncChunk.seq == 1)
        )
        chunk.state = SyncState.RUNNING
        run.state = SyncState.FAILED
        run.completed_chunks = 1
        await session.commit()

    before = fake_connector.call_count("vouchers.list")
    await coordinator.ensure_backfill(company_id)
    task = coordinator._tasks.get(company_id)
    if task is not None:
        await task

    assert fake_connector.call_count("vouchers.list") == before + 1

    async with app.state.session_factory() as session:
        run = await SyncService(session, app.state.settings).latest_run(company_id)
        assert run.state is SyncState.SUCCEEDED


async def test_cancelling_stops_between_slices_and_keeps_the_history(
    app, coordinator, linked_company, fake_connector
) -> None:
    """Stopping is not undoing: whatever Tally already gave us stays."""
    company_id = linked_company["company_id"]
    fake_connector.set("company.markers", markers())
    vouchers_by_window(fake_connector)

    # Cancel the moment the first slice has been served, so the run is stopped
    # at a known point rather than whenever the test's timing happens to land.
    async def cancel_after_first(**kwargs):
        result = await fake_connector.run(**kwargs)
        if kwargs["query"] == "vouchers.list":
            async with app.state.session_factory() as session:
                await SyncService(session, app.state.settings).request_cancel(company_id)
                await session.commit()
        return result

    app.state.hub.run = cancel_after_first
    await run_backfill(coordinator, company_id)

    async with app.state.session_factory() as session:
        run = await SyncService(session, app.state.settings).latest_run(company_id)
        assert run.state is SyncState.CANCELLED
        assert 0 < run.completed_chunks < run.total_chunks
        assert await VoucherStore(session).count(company_id) > 0, (
            "slices already read are kept"
        )


# --------------------------------------------------------------------------
# Incremental sync
# --------------------------------------------------------------------------


async def backfilled(app, coordinator, linked_company, fake_connector) -> str:
    fake_connector.set("company.markers", markers())
    vouchers_by_window(fake_connector)
    await run_backfill(coordinator, linked_company["company_id"])
    fake_connector.calls.clear()
    return linked_company["company_id"]


async def test_an_unchanged_company_costs_one_tiny_request(
    app, coordinator, linked_company, fake_connector
) -> None:
    """The payoff: a quiet shop is not re-exported every few minutes."""
    company_id = await backfilled(app, coordinator, linked_company, fake_connector)

    outcome = await coordinator.delta(company_id, today=TODAY)

    assert outcome.unchanged is True
    assert fake_connector.call_count("company.markers") == 1
    assert fake_connector.call_count("vouchers.list") == 0


async def test_a_changed_company_asks_only_for_what_changed(
    app, coordinator, linked_company, fake_connector
) -> None:
    company_id = await backfilled(app, coordinator, linked_company, fake_connector)
    fake_connector.set("company.markers", markers(voucher_alter_id=980))
    fake_connector.set(
        "vouchers.list", [voucher(guid="fresh", day=TODAY, alter_id=975)]
    )

    outcome = await coordinator.delta(company_id, today=TODAY)

    assert outcome.vouchers_changed == 1
    sent = next(p for q, p in fake_connector.calls if q == "vouchers.list")
    assert sent["alter_id_min"] == 500, "the cursor from the backfill"


async def test_the_cursor_advances_to_the_marker_not_to_what_came_back(
    app, coordinator, linked_company, fake_connector
) -> None:
    """A voucher created between the two reads must not be skipped next time."""
    company_id = await backfilled(app, coordinator, linked_company, fake_connector)
    fake_connector.set("company.markers", markers(voucher_alter_id=980))
    fake_connector.set(
        "vouchers.list", [voucher(guid="fresh", day=TODAY, alter_id=1200)]
    )

    await coordinator.delta(company_id, today=TODAY)

    async with app.state.session_factory() as session:
        state = await session.get(CompanySyncState, company_id)
        assert state.voucher_alter_id == 980


async def test_a_tally_without_change_ids_still_syncs(
    app, coordinator, linked_company, fake_connector
) -> None:
    """Degrade to a bounded date window rather than to nothing."""
    company_id = await backfilled(app, coordinator, linked_company, fake_connector)
    fake_connector.set("company.markers", markers(voucher_alter_id=None, master_alter_id=None))
    fake_connector.set("vouchers.list", [voucher(guid="fresh", day=TODAY, alter_id=None)])

    outcome = await coordinator.delta(company_id, today=TODAY)

    assert outcome.vouchers_changed == 1
    sent = next(p for q, p in fake_connector.calls if q == "vouchers.list")
    assert "alter_id_min" not in sent
    window_days = (
        date.fromisoformat(sent["to_date"]) - date.fromisoformat(sent["from_date"])
    ).days
    assert window_days <= 400, "must never widen back into a full-history export"


async def test_masters_are_re_read_only_when_they_changed(
    app, coordinator, linked_company, fake_connector, loaded
) -> None:
    """Re-reading every ledger every sweep is most of a connector's day."""
    company_id = await backfilled(app, coordinator, linked_company, fake_connector)

    await coordinator.delta(company_id, today=TODAY)
    assert fake_connector.call_count("ledgers.list") == 0

    fake_connector.set("company.markers", markers(master_alter_id=99))
    await coordinator.delta(company_id, today=TODAY)
    assert fake_connector.call_count("ledgers.list") == 1


async def test_a_deletion_in_tally_eventually_disappears(
    app, coordinator, linked_company, fake_connector
) -> None:
    """The reconcile window is the only thing that can notice a deleted voucher."""
    company_id = await backfilled(app, coordinator, linked_company, fake_connector)

    async with app.state.session_factory() as session:
        store = VoucherStore(session)
        await store.ingest(company_id, [voucher(guid="doomed", day=TODAY, alter_id=200)])
        await _age_reconcile(session, company_id)
        await session.commit()
        assert await store.count(company_id) > 0

    # Tally now returns nothing for the recent window: the voucher was deleted.
    fake_connector.set("company.markers", markers(voucher_alter_id=500))
    fake_connector.set("vouchers.list", [])

    outcome = await coordinator.delta(company_id, today=TODAY)

    assert outcome.reconciled is True
    async with app.state.session_factory() as session:
        gone = await session.scalar(
            select(VoucherRecord).where(
                VoucherRecord.company_id == company_id,
                VoucherRecord.record_key == "doomed",
            )
        )
        assert gone is None


async def test_reconciling_is_not_done_on_every_sync(
    app, coordinator, linked_company, fake_connector
) -> None:
    """It is a full re-read; doing it every few minutes defeats the delta."""
    company_id = await backfilled(app, coordinator, linked_company, fake_connector)
    fake_connector.set("company.markers", markers(voucher_alter_id=500))
    fake_connector.set("vouchers.list", [])

    async with app.state.session_factory() as session:
        await _age_reconcile(session, company_id)
        await session.commit()

    first = await coordinator.delta(company_id, today=TODAY)
    second = await coordinator.delta(company_id, today=TODAY)

    assert first.reconciled is True
    assert second.reconciled is False


async def test_a_finished_backfill_counts_as_a_reconcile(
    app, coordinator, linked_company, fake_connector
) -> None:
    """Its newest slice was a full read of the recent window; do not repeat it."""
    company_id = await backfilled(app, coordinator, linked_company, fake_connector)

    outcome = await coordinator.delta(company_id, today=TODAY)

    assert outcome.reconciled is False
    assert fake_connector.call_count("vouchers.list") == 0


async def _age_reconcile(session, company_id: str) -> None:
    """Push the last reconcile far enough back that another one is due."""
    state = await session.get(CompanySyncState, company_id)
    state.last_reconcile_at = utc_now() - timedelta(days=7)


# --------------------------------------------------------------------------
# What the reports and the app see
# --------------------------------------------------------------------------


async def test_a_backfilled_report_costs_no_tally_reads(
    app, coordinator, client, linked_company, fake_connector
) -> None:
    """Scrolling back through past months must not export them again."""
    company_id = await backfilled(app, coordinator, linked_company, fake_connector)
    fake_connector.set("voucher_types.list", [])

    response = await client.get(
        f"/v1/companies/{company_id}/reports/daybook",
        params={
            "from_date": (TODAY - timedelta(days=200)).isoformat(),
            "to_date": (TODAY - timedelta(days=170)).isoformat(),
        },
        headers=linked_company["headers"],
    )

    assert response.status_code == 200, response.text
    assert fake_connector.call_count("vouchers.list") == 0, (
        "a window the store covers must not be exported from Tally again"
    )


async def test_a_window_the_store_does_not_cover_still_goes_to_tally(
    app, coordinator, client, linked_company, fake_connector
) -> None:
    """A partial answer would look exactly like a quiet month."""
    company_id = await backfilled(app, coordinator, linked_company, fake_connector)
    fake_connector.set("voucher_types.list", [])
    fake_connector.set("vouchers.list", [])

    before = fake_connector.call_count("vouchers.list")
    await client.get(
        f"/v1/companies/{company_id}/reports/daybook",
        params={
            "from_date": (TODAY - timedelta(days=2000)).isoformat(),
            "to_date": TODAY.isoformat(),
        },
        headers=linked_company["headers"],
    )
    assert fake_connector.call_count("vouchers.list") == before + 1


async def test_the_status_endpoint_describes_a_finished_sync(
    app, coordinator, client, linked_company, fake_connector
) -> None:
    company_id = await backfilled(app, coordinator, linked_company, fake_connector)

    response = await client.get(
        f"/v1/companies/{company_id}/sync", headers=linked_company["headers"]
    )
    body = response.json()

    assert response.status_code == 200
    assert body["state"] == "succeeded"
    assert body["running"] is False
    assert body["progress"] == 1.0
    assert body["completed_chunks"] == body["total_chunks"] == 2
    assert body["history"]["has_history"] is True
    assert body["history"]["supports_incremental"] is True


async def test_the_status_endpoint_is_honest_before_anything_has_run(
    client, linked_company
) -> None:
    """No history and no run is a real state the first-run screen renders."""
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/sync",
        headers=linked_company["headers"],
    )
    body = response.json()

    assert body["state"] == "idle"
    assert body["progress"] == 0.0
    assert body["history"]["has_history"] is False
    assert body["eta_seconds"] is None


async def test_no_estimate_is_offered_before_the_first_slice_finishes(
    app, linked_company
) -> None:
    """An ETA invented from nothing is worse than none at all."""
    async with app.state.session_factory() as session:
        run = SyncRun(
            company_id=linked_company["company_id"],
            total_chunks=8,
            completed_chunks=0,
            state=SyncState.RUNNING,
            started_at=utc_now(),
            heartbeat_at=utc_now(),
        )
        session.add(run)
        await session.commit()
        assert run.eta_seconds is None
        assert run.progress == 0.0

        run.completed_chunks = 2
        assert run.eta_seconds is not None


async def test_a_second_sync_is_refused_while_one_is_running(
    app, client, linked_company
) -> None:
    """Two backfills against one Tally would queue inside it and defeat chunking."""
    async with app.state.session_factory() as session:
        session.add(
            SyncRun(
                company_id=linked_company["company_id"],
                state=SyncState.RUNNING,
                total_chunks=8,
                heartbeat_at=utc_now(),
            )
        )
        await session.commit()

    response = await client.post(
        f"/v1/companies/{linked_company['company_id']}/sync",
        headers=linked_company["headers"],
    )
    assert response.status_code == 409


async def test_an_abandoned_run_does_not_lock_a_company_out(
    app, settings, linked_company
) -> None:
    """A killed instance leaves a RUNNING row; it must not be a permanent lock."""
    async with app.state.session_factory() as session:
        session.add(
            SyncRun(
                company_id=linked_company["company_id"],
                state=SyncState.RUNNING,
                total_chunks=8,
                heartbeat_at=utc_now() - timedelta(hours=2),
            )
        )
        await session.commit()

        service = SyncService(session, settings)
        assert await service.active_run(linked_company["company_id"]) is None
