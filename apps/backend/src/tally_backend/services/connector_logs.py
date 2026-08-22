"""Ingest and query the logs pushed by customers' connectors.

Why this exists: when a shop rings up, the machine that knows what went wrong is
in their back office and the person who can read it is not. The connector
already holds a socket open to us, so the log comes down it.

Ingestion has one hard rule: **a bad log batch must never cost the connector its
session.** Everything here is best-effort. A malformed frame, a database blip,
a connector shouting a hundred thousand lines a minute -- each of those degrades
what support can see, and none of them may drop a socket that is otherwise
serving a customer's reports.

That is also why writes are batched through a background task rather than
awaited on the socket's receive loop. Writing inline would put a database round
trip between two frames on the connection Tally reports come back on, so a slow
database would present as slow reports.

Reads are scoped by ``org_id`` at the query, never filtered afterwards, for the
same reason ``deps.get_company`` is the only place tenant isolation lives: a
rule applied per handler is a rule the next handler ships without.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tally_core.protocol import MAX_LOG_ENTRIES_PER_BATCH, MAX_LOG_MESSAGE_CHARS

from ..db.models import ConnectorLog, as_utc, utc_now
from .logs import LEVEL_ORDER, level_at_least, normalise_level

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PendingLine:
    """One line waiting to be written, already reduced to column values."""

    org_id: str
    connector_id: str
    session_id: str
    logged_at: datetime
    level: str
    logger: str
    message: str
    dropped_before: int = 0


class ConnectorLogIngest:
    """Buffers pushed log lines and writes them in batches.

    The buffer is bounded and drops the *newest* line when full, which is the
    opposite of the connector's own buffer and deliberately so. The connector
    drops its oldest because it is trying to preserve what is happening now; the
    backend drops the newest because a full buffer here means one connector is
    flooding, and letting it evict every other customer's lines would turn one
    broken machine into a fleet-wide blind spot.
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        capacity: int = 20_000,
        interval_seconds: float = 2.0,
        batch_size: int = 500,
    ) -> None:
        self._session_factory = session_factory
        self._buffer: deque[PendingLine] = deque()
        self._capacity = capacity
        self._interval = interval_seconds
        self._batch_size = batch_size
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self._refused = 0

    @property
    def buffered(self) -> int:
        return len(self._buffer)

    @property
    def refused(self) -> int:
        """Lines the backend itself dropped because its buffer was full."""
        return self._refused

    async def start(self) -> None:
        self._stopping.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        with contextlib.suppress(Exception):
            await self.flush()

    def submit(
        self,
        *,
        org_id: str,
        connector_id: str,
        session_id: str,
        entries: list[Any],
        dropped: int = 0,
    ) -> None:
        """Queue one batch. Synchronous, never raises, never blocks the socket."""
        if not entries:
            # A batch that is nothing but a drop count still matters: it is the
            # only record that the gap exists. Kept as a marker line so the
            # support view can render it in place rather than as a footnote.
            if dropped > 0:
                self._append(
                    PendingLine(
                        org_id=org_id,
                        connector_id=connector_id,
                        session_id=session_id,
                        logged_at=utc_now(),
                        level="WARNING",
                        logger="tally_connector.remote_logs",
                        message=f"{dropped} log lines were dropped before this point.",
                        dropped_before=dropped,
                    )
                )
            return

        # Capped here as well as at the connector, because the cap is a
        # protection against a connector that is not behaving and a
        # misbehaving connector is exactly the one that would ignore it.
        for index, entry in enumerate(entries[:MAX_LOG_ENTRIES_PER_BATCH]):
            self._append(
                PendingLine(
                    org_id=org_id,
                    connector_id=connector_id,
                    session_id=session_id,
                    logged_at=_entry_time(entry),
                    level=normalise_level(getattr(entry, "level", "INFO")),
                    logger=str(getattr(entry, "logger", ""))[:120],
                    message=str(getattr(entry, "message", ""))[:MAX_LOG_MESSAGE_CHARS],
                    # Attached to the first line of the batch only, so the gap
                    # is reported once rather than on every row it precedes.
                    dropped_before=dropped if index == 0 else 0,
                )
            )

    def _append(self, line: PendingLine) -> None:
        if len(self._buffer) >= self._capacity:
            self._refused += 1
            return
        self._buffer.append(line)

    async def _run(self) -> None:
        while not self._stopping.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)
            if self._stopping.is_set():
                return
            try:
                await self.flush()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never let a write failure kill the task
                logger.warning("connector log flush failed", exc_info=True)

    async def flush(self) -> None:
        """Write whatever is buffered. Safe to call when there is nothing."""
        while self._buffer:
            batch: list[PendingLine] = []
            while self._buffer and len(batch) < self._batch_size:
                batch.append(self._buffer.popleft())
            async with self._session_factory() as session:
                session.add_all(
                    ConnectorLog(
                        org_id=line.org_id,
                        connector_id=line.connector_id,
                        session_id=line.session_id,
                        logged_at=line.logged_at,
                        level=line.level,
                        logger=line.logger,
                        message=line.message,
                        dropped_before=line.dropped_before,
                    )
                    for line in batch
                )
                await session.commit()


def _entry_time(entry: Any) -> datetime:
    """The connector's own timestamp, or ours when it did not send a usable one.

    Never trusted for ordering -- ``created_at`` is what queries sort by -- but
    kept, because a machine whose clock is hours out is a real finding and one
    that is invisible if we quietly overwrite what it claimed.
    """
    value = getattr(entry, "logged_at", None)
    if isinstance(value, datetime):
        return value
    return utc_now()


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


async def connector_logs(
    session: AsyncSession,
    *,
    org_ids: list[str] | None = None,
    connector_id: str = "",
    level: str = "",
    logger_name: str = "",
    search: str = "",
    before: datetime | None = None,
    limit: int = 200,
) -> list[ConnectorLog]:
    """Newest-first lines for the accounts the caller may see.

    ``org_ids`` of ``None`` means "no restriction" and is reachable only for a
    portal owner. A partner always arrives here with an explicit list, and an
    empty list returns nothing rather than everything -- the failure mode of the
    other convention is a partner seeing the whole platform.
    """
    query = select(ConnectorLog).order_by(ConnectorLog.created_at.desc()).limit(limit)
    if org_ids is not None:
        if not org_ids:
            return []
        query = query.where(ConnectorLog.org_id.in_(org_ids))
    if connector_id:
        query = query.where(ConnectorLog.connector_id == connector_id)
    if level:
        query = query.where(
            ConnectorLog.level.in_([name for name in LEVEL_ORDER if level_at_least(name, level)])
        )
    if logger_name:
        query = query.where(ConnectorLog.logger.ilike(f"%{logger_name}%"))
    if search:
        query = query.where(ConnectorLog.message.ilike(f"%{search}%"))
    if before is not None:
        # Strict, so paging with the last row's timestamp cannot loop forever
        # on a batch that shares one.
        query = query.where(ConnectorLog.created_at < before)

    return list((await session.execute(query)).scalars().all())


async def log_activity(
    session: AsyncSession, connector_ids: list[str]
) -> dict[str, tuple[datetime | None, int]]:
    """``connector_id -> (newest line, error count)``, in one query per figure.

    Shown beside each connector in the picker so the choice is informed: a PC
    that has logged nothing for a week and one that logged forty errors this
    morning look identical otherwise.
    """
    if not connector_ids:
        return {}

    newest = dict(
        (
            await session.execute(
                select(ConnectorLog.connector_id, func.max(ConnectorLog.created_at))
                .where(ConnectorLog.connector_id.in_(connector_ids))
                .group_by(ConnectorLog.connector_id)
            )
        ).all()
    )
    errors = dict(
        (
            await session.execute(
                select(ConnectorLog.connector_id, func.count(ConnectorLog.id))
                .where(
                    ConnectorLog.connector_id.in_(connector_ids),
                    ConnectorLog.level == "ERROR",
                )
                .group_by(ConnectorLog.connector_id)
            )
        ).all()
    )
    return {
        connector_id: (
            as_utc(newest[connector_id]) if newest.get(connector_id) else None,
            errors.get(connector_id, 0),
        )
        for connector_id in connector_ids
    }
