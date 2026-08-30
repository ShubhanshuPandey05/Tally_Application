"""Chunked history backfill and incremental sync.

The problem this solves, plainly: asking TallyPrime for four years of vouchers
in a single export does not return four years of vouchers. It returns a frozen
Tally. The gateway is single-threaded, the machine is usually the shop's till,
and a request large enough to exhaust it leaves the process alive but answering
nothing until somebody dismisses a dialog. That is not a timeout to tune -- it
is a request that must never be sent.

So the history is read the way it is safe to read:

**Backfill, newest slice first.** The books are cut into date windows (six
months by default) and read one at a time, with a pause between them. Newest
first is a product decision as much as a technical one: after the first slice
the dashboard already answers "what did I sell this week?", and the remaining
years fill in behind it while the owner is using the app. Each slice is a
separate job with its own deadline, so a slow one is retried or abandoned on its
own instead of taking the whole sync with it.

**After that, only what changed.** Tally stamps every voucher with an
``AlterID`` that increases on each edit, and a company reports the highest one
it has issued. Comparing that single number against the last sync answers "is
there anything new?" for the price of a tiny export -- and when there is,
``$AlterID > n`` returns just those vouchers. A shop that sold twenty things
today transfers twenty vouchers, not four years of them.

**Deletion is the gap in that story**, and it is handled rather than ignored: a
delta says what was created or edited and nothing about what was removed, so a
recent window is periodically re-read in full and treated as authoritative (see
:mod:`.voucher_store`). Without it, a voucher deleted in Tally would stay on the
dashboard forever.

Everything degrades. A TallyPrime that does not report change ids still syncs,
by re-reading a recent date window; a connector that drops mid-backfill leaves
its finished slices in place and the run resumes at the next one.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tally_core.domain.masters import CompanyMarkers
from tally_core.protocol import JobResult
from tally_core.tally.queries.masters import MAX_NAME_FILTER

from ..config import Settings
from ..core.errors import AppError, ConflictError, NotFound
from ..db.models import (
    Company,
    CompanySyncState,
    SyncChunk,
    SyncPhase,
    SyncRun,
    SyncState,
    as_utc,
    utc_now,
)
from ..hub import ConnectorHub
from .reads import ReadService
from .voucher_store import VoucherStore

logger = logging.getLogger(__name__)

VOUCHERS = "vouchers.list"
MARKERS = "company.markers"
#: Master datasets re-read only when the company's master change id moves. They
#: are cheap individually and pointless collectively: a shop adds a ledger every
#: few weeks, and re-reading all of them every quarter hour is most of what a
#: connector spends its day doing.
LEDGERS = "ledgers.list"
STOCK_ITEMS = "stock_items.list"
MASTER_DATASETS = (LEDGERS, "voucher_types.list", STOCK_ITEMS)

#: Masters that can be read incrementally. Voucher types are deliberately not
#: here: a company has a couple of dozen, they change about never, and a full
#: read of them costs less than the branch that would avoid it.
INCREMENTAL_MASTERS = frozenset({LEDGERS, STOCK_ITEMS})


def _touched_masters(vouchers: list[Any]) -> tuple[list[str], list[str]]:
    """The ledgers and stock items named by a batch of changed vouchers.

    These are exactly the masters whose closing balance can have moved, which is
    what makes a targeted balance re-read possible at all. Reads defensively:
    the payload has crossed a wire from a connector that may be older than this
    backend, and a malformed line must cost one master's freshness rather than
    the whole delta.
    """
    ledgers: set[str] = set()
    items: set[str] = set()
    for voucher in vouchers:
        if not isinstance(voucher, dict):
            continue
        for entry in voucher.get("ledger_entries") or ():
            if isinstance(entry, dict) and entry.get("ledger_name"):
                ledgers.add(entry["ledger_name"])
        for entry in voucher.get("inventory_entries") or ():
            if isinstance(entry, dict) and entry.get("item_name"):
                items.add(entry["item_name"])
    return sorted(ledgers), sorted(items)


def _merge_by_name(existing: list[Any], incoming: list[Any]) -> list[Any]:
    """Overlay freshly read master rows onto the stored collection.

    Order is preserved and rows nobody asked about are left untouched, so the
    result is the full collection with only the changed rows replaced. Rows that
    are genuinely new -- a ledger created since the last full read -- are
    appended rather than dropped.

    Name is the key because it is what Tally guarantees unique within a master
    type, and it is the only field every one of these collections carries.
    """
    replacements = {
        row["name"]: row
        for row in incoming
        if isinstance(row, dict) and row.get("name")
    }
    if not replacements:
        return existing

    merged = []
    seen = set()
    for row in existing:
        name = row.get("name") if isinstance(row, dict) else None
        if name in replacements:
            merged.append(replacements[name])
            seen.add(name)
        else:
            merged.append(row)

    merged.extend(row for name, row in replacements.items() if name not in seen)
    return merged


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    """One slice of history to read in a single job."""

    from_date: date
    to_date: date
    label: str
    include_inventory: bool

    @property
    def days(self) -> int:
        return (self.to_date - self.from_date).days + 1


def _shift_months(day: date, months: int) -> date:
    """``day`` moved by whole months, clamped to a valid day of month."""
    total = (day.year * 12 + (day.month - 1)) + months
    year, month = divmod(total, 12)
    month += 1
    # 31 Mar minus one month is 28/29 Feb, not an error.
    last_day = (date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)).day
    return date(year, month, min(day.day, last_day))


def _label(from_date: date, to_date: date) -> str:
    """A window described the way an owner would say it out loud."""
    if from_date.year == to_date.year:
        if from_date.month == to_date.month:
            return f"{from_date:%b %Y}"
        return f"{from_date:%b} - {to_date:%b %Y}"
    return f"{from_date:%b %Y} - {to_date:%b %Y}"


#: A leftover shorter than this is merged into the oldest slice instead of
#: becoming one. It lets a slice run up to a month over ``chunk_months``, which
#: is a far smaller risk than the stub windows it removes.
_TAIL_ABSORB_DAYS = 31


def plan_backfill(
    *,
    books_from: date | None,
    today: date,
    chunk_months: int,
    max_history_years: int,
    inventory_days: int,
    chunk_days: int | None = None,
) -> list[Window]:
    """Cut a company's history into readable slices, newest first.

    The floor is whichever is *later* of the books' start and the history
    ceiling: a shop that opened last year gets one slice, not four years of
    empty exports, and a shop with a decade of books is not asked for all of it.

    ``chunk_days`` overrides ``chunk_months`` and is what a re-plan uses once a
    slice has revealed how many vouchers a day this shop actually writes. Whole
    months are the right default when nothing is known yet; they are the wrong
    unit once something is.
    """
    floor = today.replace(year=today.year - max_history_years)
    if books_from is not None:
        floor = max(floor, books_from)
    if floor > today:
        # Books that start in the future: nothing sensible to read, and looping
        # on it would produce an unbounded plan.
        return []

    inventory_cutoff = today - timedelta(days=inventory_days)
    windows: list[Window] = []
    end = today

    while end >= floor:
        if chunk_days is not None:
            start = max(floor, end - timedelta(days=chunk_days - 1))
        else:
            start = max(floor, _shift_months(end, -chunk_months) + timedelta(days=1))
        # Absorb a short tail into this slice rather than leaving it as its own.
        # Whole-month arithmetic against a fixed floor otherwise ends a plan on
        # a one-day window: a whole extra export, a whole extra retry budget,
        # and a progress bar with a chunk that finishes instantly at the end.
        # Half a slice, for a day-based plan. A quarter left two-day stubs at
        # the end of the books: a whole extra export and a whole extra retry
        # budget to read almost nothing. The cost of absorbing is a final slice
        # up to 1.5x the target, which is well inside what the count ceiling
        # was chosen to tolerate.
        absorb = _TAIL_ABSORB_DAYS if chunk_days is None else max(1, chunk_days // 2)
        if 0 < (start - floor).days <= absorb:
            start = floor
        windows.append(
            Window(
                from_date=start,
                to_date=end,
                label=_label(start, end),
                # Stock lines are most of a voucher export's bytes and nothing
                # reads them on old invoices.
                include_inventory=end >= inventory_cutoff,
            )
        )
        if start <= floor:
            break
        end = start - timedelta(days=1)

    return windows


# --------------------------------------------------------------------------
# Status, as the app sees it
# --------------------------------------------------------------------------


def run_status(run: SyncRun | None, state: CompanySyncState | None) -> dict[str, Any]:
    """The sync's state, shaped for the progress screen.

    Everything the app needs to render a determinate bar and an honest caption
    without doing arithmetic of its own -- including ``eta_seconds: null``,
    which it must render as no estimate rather than as zero.
    """
    history = {
        "has_history": bool(state and state.has_history),
        "from_date": state.backfilled_from.isoformat()
        if state and state.backfilled_from
        else None,
        "to_date": state.backfilled_to.isoformat()
        if state and state.backfilled_to
        else None,
        "supports_incremental": bool(state and state.supports_incremental),
        "last_delta_at": as_utc(state.last_delta_at).isoformat()
        if state and state.last_delta_at
        else None,
    }

    if run is None:
        return {
            "state": "idle",
            "phase": None,
            "running": False,
            "progress": 1.0 if history["has_history"] else 0.0,
            "completed_chunks": 0,
            "total_chunks": 0,
            "current_label": None,
            "vouchers_ingested": 0,
            "elapsed_seconds": 0.0,
            "eta_seconds": None,
            "started_at": None,
            "finished_at": None,
            "error": None,
            "history": history,
        }

    return {
        "state": str(run.state),
        "phase": str(run.phase),
        "running": run.state in {SyncState.PENDING, SyncState.RUNNING},
        "progress": round(run.progress, 4),
        "completed_chunks": run.completed_chunks,
        "total_chunks": run.total_chunks,
        "current_label": run.current_label,
        "vouchers_ingested": run.vouchers_ingested,
        "elapsed_seconds": round(run.elapsed_seconds, 1),
        "eta_seconds": round(run.eta_seconds, 1) if run.eta_seconds is not None else None,
        "started_at": as_utc(run.started_at).isoformat(),
        "finished_at": as_utc(run.finished_at).isoformat() if run.finished_at else None,
        # The owner-facing sentence, never the exception text.
        "error": run.user_message,
        "history": history,
    }


# --------------------------------------------------------------------------
# Request-scoped operations
# --------------------------------------------------------------------------


class SyncService:
    """Reads and mutates sync bookkeeping inside one request's session."""

    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    async def state_for(self, company_id: str) -> CompanySyncState:
        state = await self._session.get(CompanySyncState, company_id)
        if state is None:
            state = CompanySyncState(company_id=company_id)
            self._session.add(state)
            await self._session.flush()
        return state

    async def latest_run(self, company_id: str) -> SyncRun | None:
        stmt = (
            select(SyncRun)
            .where(SyncRun.company_id == company_id)
            .order_by(SyncRun.started_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def active_run(self, company_id: str) -> SyncRun | None:
        """A run that is genuinely still going.

        A ``RUNNING`` row whose heartbeat has stopped belongs to a backend
        instance that was killed mid-sync. Treating it as active would lock the
        company out of ever syncing again, so it is deliberately not counted --
        it is resumable work, not a live run.
        """
        stmt = select(SyncRun).where(
            SyncRun.company_id == company_id,
            SyncRun.state.in_([SyncState.PENDING, SyncState.RUNNING]),
        )
        for run in (await self._session.execute(stmt)).scalars():
            age = (utc_now() - as_utc(run.heartbeat_at)).total_seconds()
            if age <= self._settings.sync_run_stale_after_seconds:
                return run
        return None

    async def status(self, company_id: str) -> dict[str, Any]:
        run = await self.latest_run(company_id)
        state = await self._session.get(CompanySyncState, company_id)
        return run_status(run, state)

    async def create_backfill(
        self, company: Company, *, books_from: date | None, today: date
    ) -> SyncRun:
        """Plan a backfill and persist it. Does not execute it."""
        if await self.active_run(company.id) is not None:
            raise ConflictError(
                "a sync is already running for this company",
                user_message="A sync is already running for this company.",
            )

        windows = plan_backfill(
            books_from=books_from,
            today=today,
            chunk_months=self._settings.sync_chunk_months,
            max_history_years=self._settings.sync_max_history_years,
            inventory_days=self._settings.sync_inventory_days,
        )
        run = SyncRun(
            company_id=company.id,
            phase=SyncPhase.BACKFILL,
            state=SyncState.PENDING,
            total_chunks=len(windows),
            current_label=windows[0].label if windows else None,
        )
        self._session.add(run)
        await self._session.flush()

        for seq, window in enumerate(windows):
            self._session.add(
                SyncChunk(
                    run_id=run.id,
                    seq=seq,
                    from_date=window.from_date,
                    to_date=window.to_date,
                    label=window.label,
                )
            )
        await self._session.flush()

        state = await self.state_for(company.id)
        if books_from is not None:
            state.books_from = books_from
        return run

    async def request_cancel(self, company_id: str) -> bool:
        run = await self.active_run(company_id)
        if run is None:
            return False
        # A flag rather than a task cancellation: the runner checks it between
        # slices. Killing a job mid-export would not give Tally its time back.
        run.cancel_requested = True
        await self._session.flush()
        return True


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


@dataclass
class DeltaOutcome:
    """What one incremental sync actually did."""

    ran: bool = False
    vouchers_changed: int = 0
    vouchers_removed: int = 0
    masters_refreshed: bool = False
    #: How many master collections had balances re-read for the ledgers and
    #: stock items the changed vouchers named. Distinct from
    #: ``masters_refreshed``, which counts *record* edits -- the two are
    #: different events and conflating them hides which one is misbehaving.
    balances_refreshed: int = 0
    reconciled: bool = False
    unchanged: bool = False
    error: str | None = None


class SyncCoordinator:
    """Owns the long-running sync work for this backend instance.

    Runs outside the request cycle for the obvious reason -- a backfill takes
    minutes and an HTTP request must not -- and takes a session *factory* rather
    than a session, because holding one transaction open across a multi-minute
    sync would pin a pooled connection and block every migration behind it.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        hub: ConnectorHub,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._hub = hub
        self._settings = settings
        #: One sync per company at a time, per instance. Two backfills against
        #: one Tally would queue inside the connector and defeat the chunking.
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._stopping = asyncio.Event()

    # -- lifecycle -------------------------------------------------------

    async def stop(self) -> None:
        self._stopping.set()
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()

    def is_running(self, company_id: str) -> bool:
        task = self._tasks.get(company_id)
        return task is not None and not task.done()

    # -- starting work ---------------------------------------------------

    async def refresh(self, company_id: str) -> bool:
        """What pull-to-refresh should do, for a company that has history.

        Before this, a hard refresh went straight at Tally for every dataset in
        full -- the single most expensive thing the app could ask for, triggered
        by the button users press when something looks wrong. Now it runs the
        same incremental delta the background sweep does: markers, then only
        what changed, then the balances those changes touched.

        Returns whether the caller may read from the snapshot. ``False`` means
        this company has no history yet and the ordinary live read is still the
        only way to answer it.

        Throttled on the same floor as a snapshot refresh, so four staff opening
        the app at once cost one delta rather than four -- and a delta already in
        flight is joined rather than duplicated.
        """
        async with self._session_factory() as session:
            state = await session.get(CompanySyncState, company_id)
            if state is None or not state.has_history:
                return False
            last = state.last_delta_at

        if self.is_running(company_id):
            # A backfill or delta is already working this company. Its result
            # will be published; racing a second one at the same Tally is the
            # exact pile-up the pipeline exists to prevent.
            return True

        if last is not None:
            age = (utc_now() - as_utc(last)).total_seconds()
            if age < self._settings.min_refresh_interval_seconds:
                logger.debug(
                    "refresh of company %s throttled (%.0fs since the last delta)",
                    company_id,
                    age,
                )
                return True

        try:
            await self.delta(company_id)
        except Exception:  # noqa: BLE001 - a refresh must never 500
            logger.exception("on-demand delta failed for company %s", company_id)
        return True

    async def start_backfill(self, company_id: str, *, today: date | None = None) -> str:
        """Plan a backfill and put it on this instance's work list.

        The books-from date is read from Tally first, and it is worth the round
        trip: a company that opened eight months ago gets two slices instead of
        eight, which is eight fewer exports against a machine somebody is
        working on.
        """
        today = today or date.today()

        async with self._session_factory() as session:
            company = await session.get(Company, company_id)
            if company is None:
                raise NotFound(f"company {company_id}")
            service = SyncService(session, self._settings)
            if self.is_running(company_id) or await service.active_run(company_id):
                raise ConflictError(
                    "a sync is already running for this company",
                    user_message="A sync is already running for this company.",
                )
            books_from = await self._books_from(session, company)
            run = await service.create_backfill(company, books_from=books_from, today=today)
            await session.commit()
            run_id = run.id

        self._spawn(company_id, self._execute_backfill(run_id, company_id))
        return run_id

    async def ensure_backfill(self, company_id: str) -> str | None:
        """Start a backfill only if the company has no usable history yet.

        Called on the paths where a sync is a side effect rather than the
        request -- linking a company, resuming after a restart -- so it must be
        safe to call repeatedly and cheap when there is nothing to do.
        """
        if self.is_running(company_id):
            return None

        async with self._session_factory() as session:
            service = SyncService(session, self._settings)
            if await service.active_run(company_id) is not None:
                return None

            # Checked before "does it already have history?", because a backfill
            # that stopped at year three has both: a usable window *and* slices
            # nobody has read. Short-circuiting on coverage would leave those
            # years permanently unread, with the company looking fully synced.
            resumable = await self._resumable_run(session, company_id)
            if resumable is None:
                state = await session.get(CompanySyncState, company_id)
                if state is not None and state.has_history:
                    return None
            if resumable is not None:
                run_id = resumable.id
                revived = await self._revive_stalled_chunks(session, run_id)
                resumable.state = SyncState.PENDING
                resumable.cancel_requested = False
                resumable.heartbeat_at = utc_now()
                # Cleared so the app stops showing the failure that is about to
                # be retried. `_finish` will set them again if it fails anew.
                resumable.error = None
                resumable.user_message = None
                await session.commit()
                logger.info(
                    "resuming backfill %s for company %s (%d slice(s) requeued)",
                    run_id,
                    company_id,
                    revived,
                )
                self._spawn(company_id, self._execute_backfill(run_id, company_id))
                return run_id

        try:
            return await self.start_backfill(company_id)
        except AppError as exc:
            logger.info("backfill for company %s not started: %s", company_id, exc.message)
            return None

    def _spawn(self, company_id: str, coro: Any) -> None:
        existing = self._tasks.get(company_id)
        if existing is not None and not existing.done():
            coro.close()
            return
        task = asyncio.create_task(coro, name=f"sync:{company_id}")
        self._tasks[company_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(company_id, None))

    async def _resumable_run(self, session: AsyncSession, company_id: str) -> SyncRun | None:
        """A backfill that stopped part-way and still has work left.

        Resuming beats restarting by exactly the slices already done -- which,
        on the sync this whole module exists for, is several multi-minute
        exports the shop's Tally does not have to serve twice.
        """
        stmt = (
            select(SyncRun)
            .where(
                SyncRun.company_id == company_id,
                SyncRun.phase == SyncPhase.BACKFILL,
                SyncRun.state.in_(
                    [SyncState.PENDING, SyncState.RUNNING, SyncState.FAILED]
                ),
            )
            .order_by(SyncRun.started_at.desc())
            .limit(1)
        )
        run = (await session.execute(stmt)).scalar_one_or_none()
        if run is None:
            return None

        pending = await session.scalar(
            select(SyncChunk)
            .where(SyncChunk.run_id == run.id, SyncChunk.state != SyncState.SUCCEEDED)
            .limit(1)
        )
        return run if pending is not None else None

    async def _revive_stalled_chunks(self, session: AsyncSession, run_id: str) -> int:
        """Put a resumed run's unfinished slices back in the queue.

        Without this, resuming does nothing at all. ``_fail_chunk`` leaves a
        slice ``FAILED`` once it runs out of attempts, and ``_begin_next_chunk``
        only ever claims ``PENDING`` ones -- so a resumed run finds no work,
        ends immediately, and ``_finish`` marks it failed again with the same
        message. Observed on a real backfill stuck since 2026-08-05 with 7
        failed slices: every press of the app's "Continue reading" button
        spawned a run that did nothing and came straight back to the error
        screen, which is indistinguishable from the button being broken.

        ``RUNNING`` is revived too: that state belongs to a slice whose backend
        instance was killed mid-export, and nothing else ever clears it.

        Attempts are reset because this is a *deliberate* retry -- somebody
        pressed the button, or the company was relinked -- not the automatic
        one that ``sync_chunk_attempts`` caps. The abandon-the-rest behaviour on
        repeated failure is left exactly as it was.
        """
        result = await session.execute(
            update(SyncChunk)
            .where(
                SyncChunk.run_id == run_id,
                SyncChunk.state.in_([SyncState.FAILED, SyncState.RUNNING]),
            )
            .values(state=SyncState.PENDING, attempts=0, error=None)
        )
        return int(result.rowcount or 0)

    # -- the backfill ----------------------------------------------------

    async def _execute_backfill(self, run_id: str, company_id: str) -> None:
        """Read every outstanding slice, in order, one at a time."""
        try:
            while not self._stopping.is_set():
                chunk_id = await self._begin_next_chunk(run_id)
                if chunk_id is None:
                    break
                await self._run_chunk(run_id, company_id, chunk_id)
                await asyncio.sleep(self._settings.sync_chunk_pause_seconds)
            await self._finish(run_id, company_id)
        except asyncio.CancelledError:
            # A shutdown, not a failure. The run stays resumable and the next
            # instance to see it picks up at the slice that did not run.
            await self._mark_interrupted(run_id, "the backend restarted")
            raise
        except Exception:
            logger.exception("backfill %s failed unexpectedly", run_id)
            await self._mark_interrupted(run_id, "an unexpected error")

    async def _begin_next_chunk(self, run_id: str) -> str | None:
        """Claim the next outstanding slice, or ``None`` when there are none."""
        async with self._session_factory() as session:
            run = await session.get(SyncRun, run_id)
            if run is None or run.cancel_requested:
                return None

            chunk = await session.scalar(
                select(SyncChunk)
                .where(SyncChunk.run_id == run_id, SyncChunk.state == SyncState.PENDING)
                .order_by(SyncChunk.seq)
                .limit(1)
            )
            if chunk is None:
                return None

            chunk.state = SyncState.RUNNING
            chunk.attempts += 1
            run.state = SyncState.RUNNING
            run.current_label = chunk.label
            run.heartbeat_at = utc_now()
            await session.commit()
            return chunk.id

    async def _run_chunk(self, run_id: str, company_id: str, chunk_id: str) -> None:
        async with self._session_factory() as session:
            company = await session.get(Company, company_id)
            chunk = await session.get(SyncChunk, chunk_id)
            run = await session.get(SyncRun, run_id)
            if company is None or chunk is None or run is None:
                return

            include_inventory = chunk.to_date >= date.today() - timedelta(
                days=self._settings.sync_inventory_days
            )
            result = await self._hub.run(
                connector_id=company.connector_id,
                query=VOUCHERS,
                params={
                    "company": company.tally_name,
                    "from_date": chunk.from_date.isoformat(),
                    "to_date": chunk.to_date.isoformat(),
                    "include_inventory": include_inventory,
                },
                timeout_seconds=self._settings.sync_chunk_timeout_seconds,
                # Never coalesce a sync slice onto somebody's live refresh: the
                # two carry different windows and joining them would silently
                # ingest the wrong dates.
                coalesce=False,
            )

            if not result.ok:
                await self._fail_chunk(session, run, chunk, result)
                await session.commit()
                return

            payload = result.data() or []
            store = VoucherStore(session)
            # Authoritative: this is a complete read of the slice, so anything
            # stored inside it that Tally no longer returns has been deleted.
            ingest = await store.ingest(
                company.id,
                payload,
                window=(chunk.from_date, chunk.to_date),
            )

            chunk.state = SyncState.SUCCEEDED
            chunk.vouchers = len(payload)
            chunk.duration_ms = result.duration_ms
            chunk.error = None

            run.completed_chunks += 1
            run.vouchers_ingested += ingest.inserted + ingest.updated
            run.heartbeat_at = utc_now()

            await self._extend_coverage(session, run, company.id)
            # Before the commit, so the narrowed plan and the slice that
            # justified it land in one transaction. A crash between them would
            # leave a run whose remaining slices were sized by a measurement
            # nothing records.
            await self._narrow_remaining(session, run, chunk, len(payload))
            await session.commit()

            logger.info(
                "sync %s: %s ingested %d voucher(s) (%d new, %d updated, %d removed)",
                run_id,
                chunk.label,
                len(payload),
                ingest.inserted,
                ingest.updated,
                ingest.deleted,
            )

    async def _narrow_remaining(
        self,
        session: AsyncSession,
        run: SyncRun,
        chunk: SyncChunk,
        vouchers: int,
    ) -> None:
        """Re-cut the slices still to be read, using what this one measured.

        Calendar spans are a guess about a shop's size. This is the correction:
        a slice that came back over ``sync_chunk_max_vouchers`` proves the guess
        was wrong for *this* shop, and every slice still pending is re-cut to
        the span that density implies.

        Only ever narrows. Widening on a quiet slice would let one empty stretch
        of the books talk the planner into a huge window over a busy one, and
        the whole point is to bound the largest export rather than optimise the
        smallest.
        """
        limit = self._settings.sync_chunk_max_vouchers
        if limit <= 0 or vouchers <= limit:
            return

        span_days = max((chunk.to_date - chunk.from_date).days + 1, 1)
        per_day = vouchers / span_days
        target = max(
            self._settings.sync_chunk_min_days, int(limit / per_day) if per_day else span_days
        )
        if target >= span_days:
            # Already at or below what the measurement implies -- narrowing to a
            # wider slice would be worse than leaving it alone.
            return

        pending = (
            await session.execute(
                select(SyncChunk)
                .where(SyncChunk.run_id == run.id, SyncChunk.state == SyncState.PENDING)
                .order_by(SyncChunk.seq)
            )
        ).scalars().all()
        if not pending:
            return

        floor = min(c.from_date for c in pending)
        end = max(c.to_date for c in pending)
        windows = plan_backfill(
            books_from=floor,
            today=end,
            chunk_months=self._settings.sync_chunk_months,
            max_history_years=self._settings.sync_max_history_years,
            inventory_days=self._settings.sync_inventory_days,
            chunk_days=target,
        )
        if len(windows) <= len(pending):
            # No finer than what is already queued; leave the plan alone rather
            # than churn the progress bar for nothing.
            return

        for old_chunk in pending:
            await session.delete(old_chunk)
        await session.flush()

        seq = chunk.seq
        for window in windows:
            seq += 1
            session.add(
                SyncChunk(
                    run_id=run.id,
                    seq=seq,
                    from_date=window.from_date,
                    to_date=window.to_date,
                    label=window.label,
                )
            )
        # Honest rather than flattering: the bar grows because there is genuinely
        # more work than was planned, and a total that stayed put would make the
        # remaining slices look like they were finishing early.
        run.total_chunks = run.completed_chunks + len(windows)
        await session.flush()

        logger.info(
            "sync %s: %s returned %d voucher(s) over %d day(s); narrowing the "
            "remaining %d slice(s) to %d day(s) -> %d slice(s)",
            run.id,
            chunk.label,
            vouchers,
            span_days,
            len(pending),
            target,
            len(windows),
        )

    async def _fail_chunk(
        self, session: AsyncSession, run: SyncRun, chunk: SyncChunk, result: JobResult
    ) -> None:
        error = result.error
        chunk.error = error.message if error else "unknown failure"
        run.heartbeat_at = utc_now()

        retryable = error.retryable if error else True
        if retryable and chunk.attempts < self._settings.sync_chunk_attempts:
            # Back to PENDING so the loop picks it up again after the pause.
            chunk.state = SyncState.PENDING
            logger.info(
                "sync %s: %s failed (%s), will retry",
                run.id,
                chunk.label,
                error.code if error else "unknown",
            )
            return

        chunk.state = SyncState.FAILED
        # The rest of the plan is abandoned rather than attempted. Whatever
        # stopped this slice -- Tally closed, the PC asleep, an export that
        # cannot finish -- will stop the next one too, and hammering a
        # struggling Tally is how one slow report becomes an unusable one.
        run.state = SyncState.FAILED
        run.finished_at = utc_now()
        run.error = chunk.error
        run.user_message = (
            error.user_message
            if error
            else "Could not read from TallyPrime. The sync will continue later."
        )
        logger.warning("sync %s stopped at %s: %s", run.id, chunk.label, chunk.error)

    async def _extend_coverage(
        self, session: AsyncSession, run: SyncRun, company_id: str
    ) -> None:
        """Recompute the window the store can actually answer for.

        Taken from the *contiguous* run of finished slices starting at the
        newest, not from whichever ones happened to succeed. A slice that failed
        in the middle leaves a hole, and claiming to cover it would make reports
        silently short a few months of a year they said they had.
        """
        chunks = (
            (
                await session.execute(
                    select(SyncChunk).where(SyncChunk.run_id == run.id).order_by(SyncChunk.seq)
                )
            )
            .scalars()
            .all()
        )
        covered: list[SyncChunk] = []
        for chunk in chunks:
            if chunk.state is not SyncState.SUCCEEDED:
                break
            covered.append(chunk)
        if not covered:
            return

        service = SyncService(session, self._settings)
        state = await service.state_for(company_id)
        newest = covered[0].to_date
        oldest = covered[-1].from_date

        state.backfilled_to = max(state.backfilled_to or newest, newest)
        state.backfilled_from = min(state.backfilled_from or oldest, oldest)

        # The newest slice is itself an authoritative read of the recent window,
        # so it already did the reconcile's job. Not recording that would send
        # the very first delta off to re-read months the backfill just finished.
        #
        # Measured against the day the *plan* was built for, never the wall
        # clock. `covered[0]` is always seq 0 -- the loop above starts at the
        # lowest seq and stops at the first gap -- and the planner lays slices
        # out newest-first from the `today` it was given, so `covered[0].to_date`
        # *is* that day. Reading `date.today()` here compared the plan against a
        # different date than it was built from, so the condition silently
        # stopped firing as soon as the calendar moved on, and every first delta
        # paid for a full re-read of the reconcile window.
        #
        # A run interrupted and resumed much later can still claim a reconcile
        # its newest slice no longer covers. That is bounded by
        # `sync_reconcile_interval_seconds` -- one skipped cycle, not a
        # permanent one -- and a run only stays resumable for
        # `sync_run_stale_after_seconds`, so the window is small.
        plan_today = covered[0].to_date
        reconcile_start = plan_today - timedelta(days=self._settings.sync_reconcile_days)
        if covered[0].from_date <= reconcile_start:
            state.last_reconcile_at = utc_now()

    async def _finish(self, run_id: str, company_id: str) -> None:
        async with self._session_factory() as session:
            run = await session.get(SyncRun, run_id)
            if run is None or run.state.is_terminal:
                return

            outstanding = await session.scalar(
                select(SyncChunk)
                .where(SyncChunk.run_id == run_id, SyncChunk.state != SyncState.SUCCEEDED)
                .limit(1)
            )
            if run.cancel_requested:
                run.state = SyncState.CANCELLED
                run.user_message = "Sync stopped. The history read so far is kept."
            elif outstanding is not None:
                run.state = SyncState.FAILED
                run.user_message = run.user_message or (
                    "Could not finish reading your history. It will continue "
                    "the next time your Tally PC is reachable."
                )
            else:
                run.state = SyncState.SUCCEEDED
                run.current_label = None
                run.user_message = None
                run.error = None

            run.finished_at = utc_now()
            run.heartbeat_at = utc_now()
            await session.commit()

        # Whatever the outcome, publish what was read: a backfill that stopped
        # three slices in still made the dashboard better than it was.
        await self.materialise(company_id)

    async def _mark_interrupted(self, run_id: str, reason: str) -> None:
        async with self._session_factory() as session:
            run = await session.get(SyncRun, run_id)
            if run is None or run.state.is_terminal:
                return
            run.state = SyncState.FAILED
            run.error = reason
            run.user_message = "The sync was interrupted. It will pick up where it left off."
            run.finished_at = utc_now()
            await session.commit()

    # -- incremental -----------------------------------------------------

    async def delta(self, company_id: str, *, today: date | None = None) -> DeltaOutcome:
        """Bring a backfilled company up to date as cheaply as Tally allows.

        The order matters. Markers are read first because they are what makes
        the whole thing cheap: on a quiet company the answer is "nothing has
        changed" and the method returns having transferred a few hundred bytes.
        """
        today = today or date.today()
        outcome = DeltaOutcome()

        async with self._session_factory() as session:
            company = await session.get(Company, company_id)
            if company is None:
                return outcome
            state = await session.get(CompanySyncState, company_id)
            if state is None or not state.has_history:
                # Nothing to be incremental *about* yet.
                return outcome

            markers = await self._read_markers(company)
            if markers is None:
                outcome.error = "could not read change markers"
                return outcome

            outcome.ran = True
            state.supports_incremental = markers.supports_incremental
            if markers.books_from is not None:
                state.books_from = markers.books_from

            vouchers_moved = (
                not markers.supports_incremental
                or state.voucher_alter_id is None
                or markers.voucher_alter_id != state.voucher_alter_id
            )

            if vouchers_moved:
                changed, ledgers, items = await self._sync_vouchers(
                    session, company, state, markers, today
                )
                outcome.vouchers_changed = changed.inserted + changed.updated
                outcome.vouchers_removed = changed.deleted
                # Balances first, while we still know which masters moved. A
                # voucher changes a ledger's closing balance without touching
                # that ledger's AlterID, so nothing below would catch it.
                outcome.balances_refreshed = await self._refresh_touched_masters(
                    session, company, ledgers, items
                )
            else:
                outcome.unchanged = True

            if self._reconcile_due(state, markers):
                removed = await self._reconcile(session, company, state, today)
                outcome.vouchers_removed += removed
                outcome.reconciled = True

            if self._masters_moved(state, markers):
                outcome.masters_refreshed = await self._refresh_masters(
                    session, company, state
                )
                if outcome.masters_refreshed:
                    state.master_alter_id = markers.master_alter_id

            state.last_delta_at = utc_now()
            await session.commit()

        await self.materialise(company_id)
        return outcome

    async def _read_markers(self, company: Company) -> CompanyMarkers | None:
        result = await self._hub.run(
            connector_id=company.connector_id,
            query=MARKERS,
            params={"company": company.tally_name},
            timeout_seconds=self._settings.default_job_timeout_seconds,
        )
        if not result.ok:
            logger.info(
                "change markers unavailable for company %s: %s",
                company.id,
                result.error.code if result.error else "unknown",
            )
            return None
        try:
            return CompanyMarkers.model_validate(result.data())
        except Exception:  # noqa: BLE001 - an old connector may answer differently
            logger.warning("could not parse change markers for company %s", company.id)
            return None

    async def _sync_vouchers(
        self,
        session: AsyncSession,
        company: Company,
        state: CompanySyncState,
        markers: CompanyMarkers,
        today: date,
    ) -> tuple[Any, list[str], list[str]]:
        """Fetch and merge whatever changed since the last sync.

        Returns the ingest result plus the ledgers and stock items the changed
        vouchers named. Those names are the only way to know whose *balance*
        moved: Tally does not bump a master's AlterID when a voucher hits it, so
        without them the caller would have to re-read every ledger in the
        company to find the six that changed.
        """
        store = VoucherStore(session)
        assert state.backfilled_from is not None and state.backfilled_to is not None

        if markers.supports_incremental and state.voucher_alter_id is not None:
            # The cheap path: Tally filters, and only edits cross the wire.
            params = {
                "company": company.tally_name,
                "from_date": state.backfilled_from.isoformat(),
                "to_date": today.isoformat(),
                "include_inventory": True,
                "alter_id_min": state.voucher_alter_id,
            }
            window = None  # additive: a delta says nothing about deletions
        else:
            # No usable cursor: re-read a recent window in full instead. Correct,
            # just more expensive, and bounded so it never becomes the four-year
            # export this module exists to avoid.
            start = min(
                state.backfilled_to,
                today - timedelta(days=self._settings.sync_reconcile_days),
            )
            params = {
                "company": company.tally_name,
                "from_date": start.isoformat(),
                "to_date": today.isoformat(),
                "include_inventory": True,
            }
            window = (start, today)

        result = await self._hub.run(
            connector_id=company.connector_id,
            query=VOUCHERS,
            params=params,
            timeout_seconds=self._settings.heavy_job_timeout_seconds,
            coalesce=False,
        )
        if not result.ok:
            from .voucher_store import IngestResult

            return IngestResult(), [], []

        payload = result.data() or []
        ingest = await store.ingest(company.id, payload, window=window)
        ledgers, items = _touched_masters(payload)

        # Advance to the marker rather than to the highest AlterID returned. A
        # voucher created between reading the marker and reading the vouchers
        # arrives in this batch with a higher id; adopting it would step the
        # cursor past edits made in that same gap and lose them permanently.
        # Re-reading a handful next time is the cheap mistake to make.
        if markers.voucher_alter_id is not None:
            state.voucher_alter_id = markers.voucher_alter_id
        elif ingest.max_alter_id is not None:
            state.voucher_alter_id = ingest.max_alter_id

        state.backfilled_to = max(state.backfilled_to, today)
        return ingest, ledgers, items

    def _reconcile_due(self, state: CompanySyncState, markers: CompanyMarkers) -> bool:
        """Whether the recent window is owed a full, deletion-catching re-read."""
        if not markers.supports_incremental:
            # The non-incremental path already reads its window authoritatively.
            return False
        if state.last_reconcile_at is None:
            return True
        age = (utc_now() - as_utc(state.last_reconcile_at)).total_seconds()
        return age >= self._settings.sync_reconcile_interval_seconds

    async def _reconcile(
        self,
        session: AsyncSession,
        company: Company,
        state: CompanySyncState,
        today: date,
    ) -> int:
        start = today - timedelta(days=self._settings.sync_reconcile_days)
        result = await self._hub.run(
            connector_id=company.connector_id,
            query=VOUCHERS,
            params={
                "company": company.tally_name,
                "from_date": start.isoformat(),
                "to_date": today.isoformat(),
                # Identity only. This read answers one question -- which
                # vouchers still exist -- and carrying the lines to answer it
                # cost 13x the bytes (measured live 2026-08-29). The delta has
                # already brought every *edit* in this window up to date; the
                # only thing left to find is what vanished.
                "identity_only": True,
                "include_inventory": False,
            },
            timeout_seconds=self._settings.heavy_job_timeout_seconds,
            coalesce=False,
        )
        if not result.ok:
            return 0

        store = VoucherStore(session)
        # Never `ingest`: this payload has no lines, and ingesting it would
        # overwrite every stored voucher in the window with a lineless copy.
        deleted = await store.reconcile(company.id, result.data() or [], window=(start, today))
        state.last_reconcile_at = utc_now()
        return deleted

    def _masters_moved(self, state: CompanySyncState, markers: CompanyMarkers) -> bool:
        if markers.master_alter_id is None:
            # Unknown: leave the ordinary refresher to warm masters on age.
            return False
        return markers.master_alter_id != state.master_alter_id

    async def _refresh_masters(
        self, session: AsyncSession, company: Company, state: CompanySyncState
    ) -> bool:
        """Pick up masters that were *edited* -- created, renamed, regrouped.

        Only half of keeping masters current, and the cheaper half. A master's
        ``AlterID`` moves when its record is altered and stands still when a
        voucher moves its balance (verified live -- see CLAUDE.md), so this pass
        can never refresh a figure. :meth:`_refresh_touched_masters` does that.
        """
        from .reads import FetchMode

        reads = ReadService(session, self._hub, self._settings)
        refreshed = False
        for dataset in MASTER_DATASETS:
            cursor = state.master_alter_id if dataset in INCREMENTAL_MASTERS else None
            if cursor is not None:
                merged = await self._merge_masters(
                    session, company, dataset, {"alter_id_min": cursor}
                )
                if merged is not None:
                    refreshed = True
                    continue
                # No snapshot to merge into yet -- fall through to a full read,
                # which is what establishes one.
            try:
                await reads.fetch(company, dataset=dataset, mode=FetchMode.LIVE, heavy=True)
                refreshed = True
            except AppError as exc:
                logger.info("master refresh of %s skipped: %s", dataset, exc.message)
        return refreshed

    async def _refresh_touched_masters(
        self,
        session: AsyncSession,
        company: Company,
        ledgers: Sequence[str],
        items: Sequence[str],
    ) -> int:
        """Re-read the balances of the masters the changed vouchers named.

        This is the half of incremental master sync that no change id can do.
        A voucher moves a ledger's closing balance without touching that
        ledger's ``AlterID``, so the only sound way to refresh a balance without
        re-reading every ledger in the company is to name the ones the delta
        just told us about.

        Above :data:`MAX_NAME_FILTER` names the filter stops paying for itself --
        Tally walks the whole collection to evaluate it either way -- so a busy
        day falls back to the ordinary full read rather than building an
        enormous OR chain.
        """
        refreshed = 0
        for dataset, names in ((LEDGERS, ledgers), (STOCK_ITEMS, items)):
            unique = sorted({n for n in names if n})
            if not unique:
                continue
            if len(unique) > MAX_NAME_FILTER:
                logger.info(
                    "%d %s touched for company %s; re-reading all of them instead",
                    len(unique),
                    dataset,
                    company.id,
                )
                await self._merge_masters(session, company, dataset, {})
                refreshed += 1
                continue
            merged = await self._merge_masters(
                session, company, dataset, {"names": unique}
            )
            if merged:
                refreshed += merged
        return refreshed

    async def _merge_masters(
        self,
        session: AsyncSession,
        company: Company,
        dataset: str,
        params: dict[str, Any],
    ) -> int | None:
        """Read part of a master collection and merge it into the full snapshot.

        The merge is the whole point. Writing a partial read straight to the
        snapshot would replace a company's three thousand ledgers with the six
        that moved, and every screen reads the snapshot -- so the dashboard
        would lose the other 2,994 with no error anywhere.

        ``None`` means there is no snapshot to merge into yet, which the caller
        answers with a full read. ``0`` means the read succeeded and changed
        nothing.
        """
        reads = ReadService(session, self._hub, self._settings)
        existing = await reads.snapshot_payload(company, dataset=dataset)
        if not isinstance(existing, list):
            return None

        result = await self._hub.run(
            connector_id=company.connector_id,
            query=dataset,
            params={"company": company.tally_name, **params},
            timeout_seconds=self._settings.heavy_job_timeout_seconds,
            # Never coalesced onto somebody's live full read: the params differ,
            # and joining them would merge the wrong rows.
            coalesce=False,
        )
        if not result.ok:
            logger.info(
                "incremental %s read failed for company %s: %s",
                dataset,
                company.id,
                result.error.code if result.error else "unknown",
            )
            return 0

        incoming = result.data() or []
        merged = _merge_by_name(existing, incoming)
        await reads.put_snapshot(company, dataset=dataset, payload=merged)
        return len(incoming)

    # -- publishing ------------------------------------------------------

    async def materialise(self, company_id: str) -> bool:
        """Republish the dashboard's voucher window from the store.

        The store is the source of truth for history, but every existing screen
        reads :class:`~..db.models.Snapshot`. Writing the dashboard's window
        back into a snapshot after each sync is what makes the two agree without
        a screen having to know which mechanism produced its numbers.
        """
        from .dashboard import voucher_params, voucher_window

        today = date.today()
        start, end = voucher_window(today)

        async with self._session_factory() as session:
            company = await session.get(Company, company_id)
            state = await session.get(CompanySyncState, company_id)
            if company is None or state is None or not state.covers(start, end):
                # Not covered: leaving the existing snapshot alone is correct.
                # Publishing a partial window would quietly replace real figures
                # with a subset of themselves.
                return False

            store = VoucherStore(session)
            payload = await store.read(company.id, from_date=start, to_date=end)

            reads = ReadService(session, self._hub, self._settings)
            await reads.put_snapshot(
                company,
                dataset=VOUCHERS,
                params=voucher_params(today),
                payload=payload,
            )
            await session.commit()
            return True

    async def _books_from(self, session: AsyncSession, company: Company) -> date | None:
        """Ask Tally where the books start, falling back to what we know."""
        markers = await self._read_markers(company)
        if markers is not None:
            service = SyncService(session, self._settings)
            state = await service.state_for(company.id)
            state.supports_incremental = markers.supports_incremental
            if markers.voucher_alter_id is not None:
                # Recorded before the backfill rather than after it, so that a
                # voucher edited *during* the read is picked up by the next
                # delta instead of being skipped by a cursor set too high.
                state.voucher_alter_id = markers.voucher_alter_id
            if markers.master_alter_id is not None:
                state.master_alter_id = markers.master_alter_id
            if markers.books_from is not None:
                return markers.books_from
            if markers.financial_year_from is not None:
                return markers.financial_year_from

        if company.financial_year_from is not None:
            return company.financial_year_from.date()
        return None
