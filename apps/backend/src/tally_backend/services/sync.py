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
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tally_core.domain.masters import CompanyMarkers
from tally_core.protocol import JobResult

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
MASTER_DATASETS = ("ledgers.list", "voucher_types.list", "stock_items.list")


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
) -> list[Window]:
    """Cut a company's history into readable slices, newest first.

    The floor is whichever is *later* of the books' start and the history
    ceiling: a shop that opened last year gets one slice, not four years of
    empty exports, and a shop with a decade of books is not asked for all of it.
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
        start = max(floor, _shift_months(end, -chunk_months) + timedelta(days=1))
        # Absorb a short tail into this slice rather than leaving it as its own.
        # Whole-month arithmetic against a fixed floor otherwise ends a plan on
        # a one-day window: a whole extra export, a whole extra retry budget,
        # and a progress bar with a chunk that finishes instantly at the end.
        if 0 < (start - floor).days <= _TAIL_ABSORB_DAYS:
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
        today = date.today()
        reconcile_start = today - timedelta(days=self._settings.sync_reconcile_days)
        if covered[0].from_date <= reconcile_start and covered[0].to_date >= today:
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
                changed = await self._sync_vouchers(session, company, state, markers, today)
                outcome.vouchers_changed = changed.inserted + changed.updated
                outcome.vouchers_removed = changed.deleted
            else:
                outcome.unchanged = True

            if self._reconcile_due(state, markers):
                removed = await self._reconcile(session, company, state, today)
                outcome.vouchers_removed += removed
                outcome.reconciled = True

            if self._masters_moved(state, markers):
                outcome.masters_refreshed = await self._refresh_masters(session, company)
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
    ) -> Any:
        """Fetch and merge whatever changed since the last sync."""
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

            return IngestResult()

        ingest = await store.ingest(company.id, result.data() or [], window=window)

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
        return ingest

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
                "include_inventory": True,
            },
            timeout_seconds=self._settings.heavy_job_timeout_seconds,
            coalesce=False,
        )
        if not result.ok:
            return 0

        store = VoucherStore(session)
        ingest = await store.ingest(company.id, result.data() or [], window=(start, today))
        state.last_reconcile_at = utc_now()
        return ingest.deleted

    def _masters_moved(self, state: CompanySyncState, markers: CompanyMarkers) -> bool:
        if markers.master_alter_id is None:
            # Unknown: leave the ordinary refresher to warm masters on age.
            return False
        return markers.master_alter_id != state.master_alter_id

    async def _refresh_masters(self, session: AsyncSession, company: Company) -> bool:
        """Re-read the master datasets, because something in them changed."""
        from .reads import FetchMode

        reads = ReadService(session, self._hub, self._settings)
        refreshed = False
        for dataset in MASTER_DATASETS:
            try:
                await reads.fetch(company, dataset=dataset, mode=FetchMode.LIVE, heavy=True)
                refreshed = True
            except AppError as exc:
                logger.info("master refresh of %s skipped: %s", dataset, exc.message)
        return refreshed

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
