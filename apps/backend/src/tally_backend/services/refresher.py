"""Background snapshot refresher.

Keeps each company's datasets warm so that opening the app is a database read
rather than a trip to a desktop PC. Without this the first user of the morning
pays a multi-second cold read on every tile and concludes the app is slow.

Three properties matter at fleet scale:

* **Only refresh what is reachable.** A connector that is offline is skipped
  rather than retried, so an overnight sweep does not spend its whole budget
  timing out against switched-off PCs.
* **Bounded work per sweep.** ``refresh_batch_size`` caps how many companies one
  instance touches per tick, which stops a backend restart from stampeding the
  entire fleet at once.
* **Oldest first.** Ordering by ``refreshed_at`` means a company that keeps
  failing cannot starve the others -- it goes to the back of the queue after
  each attempt, successful or not.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..core.errors import AppError
from ..db.models import Company, CompanySyncState, Snapshot, as_utc, utc_now
from ..hub import ConnectorHub
from .dashboard import voucher_params
from .reads import FetchMode, ReadService
from .sync import SyncCoordinator

logger = logging.getLogger(__name__)

#: Datasets kept warm, with whether each is expensive for Tally to produce.
WARM_DATASETS: list[tuple[str, bool]] = [
    ("ledgers.list", False),
    # Cheap, and every voucher-based figure depends on it to tell a renamed
    # "Tax Invoice" from a Journal. Warmed alongside the vouchers it classifies
    # so the two are never more than one sweep apart.
    ("voucher_types.list", False),
    ("vouchers.list", True),
    ("outstanding.bills", True),
    ("stock_items.list", True),
]

#: Datasets the history sync takes over once a company has been backfilled.
#: Only vouchers: outstanding bills and stock are point-in-time balances with no
#: change id to sync against, so they stay on the ordinary warming path.
SYNC_OWNED_DATASETS = {"vouchers.list"}


class SnapshotRefresher:
    """Periodically refreshes stale snapshots for reachable connectors."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        hub: ConnectorHub,
        settings: Settings,
        sync: SyncCoordinator | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._hub = hub
        self._settings = settings
        #: Optional so a test can drive plain warming in isolation. In the app
        #: it is always present, and companies with a synced history take the
        #: incremental path instead of re-exporting their vouchers.
        self._sync = sync
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if not self._settings.refresh_worker_enabled:
            logger.info("snapshot refresher disabled")
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        # Stagger startup so that N instances coming up together do not all
        # sweep in the same second.
        await asyncio.sleep(random.uniform(0, self._settings.refresh_worker_interval_seconds))

        while not self._stopping.is_set():
            try:
                refreshed = await self.sweep()
                if refreshed:
                    logger.info("refreshed %d company dataset(s)", refreshed)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the worker must outlive any one error
                logger.exception("snapshot sweep failed")

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._stopping.wait(),
                    timeout=self._settings.refresh_worker_interval_seconds,
                )

    async def sweep(self) -> int:
        """One pass. Returns how many datasets were refreshed."""
        companies = await self._due_companies()
        if not companies:
            return 0

        refreshed = 0
        today = date.today()

        for company in companies:
            # Re-checked per company rather than once up front: a connector can
            # drop out partway through a sweep.
            if not await self._hub.is_online(company.connector_id):
                continue

            synced = await self._advance_sync(company)

            for dataset, heavy in WARM_DATASETS:
                if self._stopping.is_set():
                    return refreshed
                # A synced company's vouchers come from its history store, kept
                # current by an AlterID delta. Warming them here as well would
                # re-export the dashboard's whole window every sweep -- the
                # exact repeated cost the sync exists to remove.
                if synced and dataset in SYNC_OWNED_DATASETS:
                    continue
                if await self._refresh_one(company, dataset, heavy, today):
                    refreshed += 1

        return refreshed

    async def _advance_sync(self, company: Company) -> bool:
        """Move a company's history sync along. Returns whether it owns vouchers.

        Three states, and the sweep is where each one is noticed:

        * **Never backfilled** -- start (or resume) one. A company linked while
          its PC was asleep would otherwise sit without history forever.
        * **Backfill in progress** -- leave it alone. It is already running, and
          a second read against the same Tally is precisely what chunking avoids.
        * **Backfilled** -- run a delta, which on a quiet company costs one tiny
          request and returns "nothing changed".
        """
        if self._sync is None:
            return False

        async with self._session_factory() as session:
            state = await session.get(CompanySyncState, company.id)
            has_history = state is not None and state.has_history
            last_delta = state.last_delta_at if state is not None else None

        if self._sync.is_running(company.id):
            return True

        if not has_history:
            started = await self._sync.ensure_backfill(company.id)
            # A backfill in flight owns the vouchers even before it finishes:
            # its first slice will publish them.
            return started is not None

        if last_delta is not None:
            age = (utc_now() - as_utc(last_delta)).total_seconds()
            if age < self._settings.sync_delta_interval_seconds:
                return True

        try:
            await self._sync.delta(company.id)
        except Exception:  # noqa: BLE001 - one company must not stop the sweep
            logger.exception("delta sync failed for company %s", company.id)
        return True

    async def _refresh_one(
        self, company: Company, dataset: str, heavy: bool, today: date
    ) -> bool:
        params = _params_for(dataset, today)
        async with self._session_factory() as session:
            reads = ReadService(session, self._hub, self._settings)
            try:
                await reads.fetch(
                    company,
                    dataset=dataset,
                    params=params,
                    mode=FetchMode.LIVE,
                    heavy=heavy,
                )
                await session.commit()
                return True
            except AppError as exc:
                # Expected: Tally closed, connector asleep. Committing keeps the
                # staleness flag the read path just wrote.
                await session.commit()
                logger.debug("refresh of %s for %s skipped: %s", dataset, company.id, exc)
                return False
            except Exception:
                await session.rollback()
                logger.exception("refresh of %s for %s failed", dataset, company.id)
                return False

    async def _due_companies(self) -> list[Company]:
        """Active companies whose snapshots are missing or stale, oldest first."""
        cutoff_age = self._settings.snapshot_stale_after_seconds

        async with self._session_factory() as session:
            companies = (
                (
                    await session.execute(
                        select(Company).where(Company.is_active.is_(True))
                    )
                )
                .scalars()
                .all()
            )

            newest = {
                company_id: refreshed
                for company_id, refreshed in (
                    await session.execute(
                        select(Snapshot.company_id, Snapshot.refreshed_at).where(
                            Snapshot.dataset == "vouchers.list"
                        )
                    )
                ).all()
            }

        def age(company: Company) -> float:
            stamp = newest.get(company.id)
            if stamp is None:
                return float("inf")  # never fetched -- highest priority
            snapshot = Snapshot(refreshed_at=stamp)
            return snapshot.age_seconds

        due = [c for c in companies if age(c) > cutoff_age]
        due.sort(key=age, reverse=True)
        return due[: self._settings.refresh_batch_size]


def _params_for(dataset: str, today: date) -> dict[str, object] | None:
    """Match the params the dashboard asks with.

    ``voucher_params`` is imported from the dashboard rather than reimplemented:
    the params are part of a snapshot's identity, so any divergence would warm
    rows the dashboard never reads.
    """
    if dataset == "vouchers.list":
        return voucher_params(today)
    if dataset == "outstanding.bills":
        return {"as_of": today.isoformat()}
    return None
