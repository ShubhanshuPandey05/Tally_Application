"""The read path: snapshots, live refresh, and the rules for choosing.

This module carries the product's central performance decision, so it is worth
stating plainly.

A naive backend proxies every dashboard open straight through to Tally. That
cannot work here. TallyPrime serves one request at a time, takes seconds per
export, sits on a desktop behind home broadband, and is switched off at night. A
shop with four staff refreshing a dashboard would queue four multi-second
exports against the same PC the till is running on.

So reads are served from :class:`Snapshot` -- the last known good answer -- and
refreshed out of band. Consequences that fall out of that choice:

* The dashboard opens in milliseconds and works when Tally is closed, which is
  what CLAUDE.md's "within 10 seconds" and "offline friendly" both require.
* Load on the customer's PC is proportional to the number of *companies*, not
  the number of users. Ten staff cost the same as one.
* Every response carries ``refreshed_at``, and the app is expected to show it.
  Silently serving old numbers as current is the one unacceptable outcome in an
  accounting product.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tally_core.protocol import JobResult

from ..config import Settings
from ..core.errors import NoDataYet
from ..db.models import Company, JobStat, Snapshot, utc_now
from ..hub import ConnectorHub

logger = logging.getLogger(__name__)


class FetchMode(StrEnum):
    #: Snapshot if fresh enough, otherwise go to Tally. The default for screens.
    AUTO = "auto"
    #: Never touch Tally. Used by list screens and anything on a hot path.
    CACHED = "cached"
    #: Pull-to-refresh. Still rate-limited per company.
    LIVE = "live"


def params_key(params: dict[str, Any]) -> str:
    """Stable identity for a params dict.

    ``company`` is excluded because the snapshot is already scoped to a company
    row; including it would just make every key longer without distinguishing
    anything.
    """
    relevant = {k: v for k, v in sorted(params.items()) if k != "company"}
    if not relevant:
        return ""
    blob = json.dumps(relevant, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


@dataclass
class DataResult:
    """A dataset plus everything the UI needs to describe its freshness."""

    payload: Any
    refreshed_at: datetime
    from_snapshot: bool
    is_stale: bool
    connector_online: bool
    age_seconds: float
    error: str | None = None

    def meta(self) -> dict[str, Any]:
        return {
            "refreshed_at": self.refreshed_at.isoformat(),
            "age_seconds": round(self.age_seconds, 1),
            "from_snapshot": self.from_snapshot,
            "is_stale": self.is_stale,
            "connector_online": self.connector_online,
            "error": self.error,
        }


class ReadService:
    """Fetches datasets for a company, choosing between snapshot and live."""

    def __init__(self, session: AsyncSession, hub: ConnectorHub, settings: Settings) -> None:
        self._session = session
        self._hub = hub
        self._settings = settings
        #: Memoised per request. A dashboard asks for four datasets, and the
        #: connector's reachability cannot meaningfully change between them --
        #: but without this it would cost four Redis lookups to say so.
        self._online_cache: dict[str, bool] = {}

    async def _connector_online(self, connector_id: str) -> bool:
        """Whether the connector is actually reachable right now.

        Consulted whenever a response is served from a snapshot. Reporting
        ``connector_online`` without checking would be fabricated metadata, and
        this field drives whether the app shows an "offline" banner over the
        numbers -- getting it wrong means telling an owner their data is current
        while their PC is switched off.
        """
        if connector_id not in self._online_cache:
            try:
                self._online_cache[connector_id] = await self._hub.is_online(connector_id)
            except Exception:  # noqa: BLE001 - unknown reachability is not fatal
                logger.debug("could not determine reachability of %s", connector_id)
                self._online_cache[connector_id] = False
        return self._online_cache[connector_id]

    async def fetch(
        self,
        company: Company,
        *,
        dataset: str,
        params: dict[str, Any] | None = None,
        mode: FetchMode = FetchMode.AUTO,
        heavy: bool = False,
    ) -> DataResult:
        params = {"company": company.tally_name, **(params or {})}
        key = params_key(params)
        snapshot = await self._load_snapshot(company.id, dataset, key)

        if mode is FetchMode.CACHED:
            if snapshot is None:
                raise NoDataYet(f"no snapshot for {dataset} on company {company.id}")
            return self._from_snapshot(
                snapshot, connector_online=await self._connector_online(company.connector_id)
            )

        if mode is FetchMode.AUTO and snapshot is not None and not self._is_stale(snapshot):
            return self._from_snapshot(
                snapshot, connector_online=await self._connector_online(company.connector_id)
            )

        # Throttle deliberate refreshes. Without this, holding pull-to-refresh
        # is a one-tap denial of service against the shop's own Tally.
        if (
            mode is FetchMode.LIVE
            and snapshot is not None
            and snapshot.age_seconds < self._settings.min_refresh_interval_seconds
        ):
            logger.debug("refresh of %s throttled (%.0fs old)", dataset, snapshot.age_seconds)
            return self._from_snapshot(
                snapshot, connector_online=await self._connector_online(company.connector_id)
            )

        return await self._refresh(
            company, dataset=dataset, params=params, key=key, fallback=snapshot, heavy=heavy
        )

    async def _refresh(
        self,
        company: Company,
        *,
        dataset: str,
        params: dict[str, Any],
        key: str,
        fallback: Snapshot | None,
        heavy: bool,
    ) -> DataResult:
        timeout = (
            self._settings.heavy_job_timeout_seconds
            if heavy
            else self._settings.default_job_timeout_seconds
        )
        result = await self._hub.run(
            connector_id=company.connector_id,
            query=dataset,
            params=params,
            timeout_seconds=timeout,
        )
        await self._record_job(company, dataset, result)

        if result.ok:
            payload = result.data()
            snapshot = await self._store(company.id, dataset, key, payload, result)
            return DataResult(
                payload=payload,
                refreshed_at=snapshot.refreshed_at,
                from_snapshot=False,
                is_stale=False,
                connector_online=True,
                age_seconds=0.0,
            )

        error = result.error
        message = error.user_message if error else "Could not read from Tally."

        if fallback is not None:
            # Old-but-real numbers beat an error screen, as long as the response
            # says how old they are. The app renders a staleness banner over them.
            await self._mark_stale(fallback, message)
            logger.info(
                "serving stale %s for company %s (%.0fs old) after %s",
                dataset,
                company.id,
                fallback.age_seconds,
                error.code if error else "unknown",
            )
            return DataResult(
                payload=fallback.payload,
                refreshed_at=fallback.refreshed_at,
                from_snapshot=True,
                is_stale=True,
                connector_online=False,
                age_seconds=fallback.age_seconds,
                error=message,
            )

        raise NoDataYet(
            f"{dataset} unavailable and never snapshotted", user_message=message
        )

    # -- snapshot storage -------------------------------------------------

    async def _load_snapshot(self, company_id: str, dataset: str, key: str) -> Snapshot | None:
        stmt = select(Snapshot).where(
            Snapshot.company_id == company_id,
            Snapshot.dataset == dataset,
            Snapshot.params_key == key,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _store(
        self, company_id: str, dataset: str, key: str, payload: Any, result: JobResult
    ) -> Snapshot:
        snapshot = await self._load_snapshot(company_id, dataset, key)
        row_count = len(payload) if isinstance(payload, list) else 1

        if snapshot is None:
            snapshot = Snapshot(company_id=company_id, dataset=dataset, params_key=key)
            self._session.add(snapshot)

        snapshot.payload = payload
        snapshot.refreshed_at = utc_now()
        snapshot.is_stale = False
        snapshot.last_error = None
        snapshot.row_count = row_count
        snapshot.duration_ms = result.duration_ms
        await self._session.flush()
        return snapshot

    async def _mark_stale(self, snapshot: Snapshot, message: str) -> None:
        snapshot.is_stale = True
        snapshot.last_error = message[:1000]
        await self._session.flush()

    def _is_stale(self, snapshot: Snapshot) -> bool:
        return (
            snapshot.is_stale
            or snapshot.age_seconds > self._settings.snapshot_stale_after_seconds
        )

    def _from_snapshot(self, snapshot: Snapshot, *, connector_online: bool) -> DataResult:
        return DataResult(
            payload=snapshot.payload,
            refreshed_at=snapshot.refreshed_at,
            from_snapshot=True,
            is_stale=snapshot.is_stale,
            connector_online=connector_online,
            age_seconds=snapshot.age_seconds,
            error=snapshot.last_error,
        )

    async def _record_job(self, company: Company, dataset: str, result: JobResult) -> None:
        self._session.add(
            JobStat(
                connector_id=company.connector_id,
                company_id=company.id,
                query=dataset,
                ok=result.ok,
                from_cache=result.from_cache,
                error_code=result.error.code if result.error else None,
                duration_ms=result.duration_ms,
            )
        )
