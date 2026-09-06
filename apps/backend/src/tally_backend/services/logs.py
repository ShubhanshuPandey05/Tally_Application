"""Backend logs, kept where support can read them.

The portal is the only tool anybody has during an incident, and until now it
could show what the *fleet* was doing and nothing at all about what the server
was doing. Reading that meant `docker compose logs`, which means SSH, which
means the person who noticed cannot be the person who looks.

Two storage tiers, because the two questions are different:

**"What is happening right now?"** is answered from an in-memory ring
(:class:`ServerLogStore`). Every level lands there, it costs one deque append
per record, and it is lost on restart -- which is correct, because a live tail
of a process that no longer exists is not a thing.

**"What happened last Tuesday?"** is answered from ``server_logs``, which takes
WARNING and above only. An incident is nearly always reported after the
container that produced it has been replaced, so this half has to outlive the
process. INFO is excluded on volume: it is a row per request against the same
database that serves the customer's reports, and the value of a three-day-old
INFO line does not justify that.

The bridge between a synchronous logging handler and an async database is a
plain deque plus a writer task that drains it. Not an ``asyncio.Queue``:
``emit`` can be called from any thread (uvicorn's, a thread-pool executor's) and
from inside an exception handler, and a handler that needs a running event loop
to accept a record is a handler that raises during shutdown -- exactly when the
records are most worth having.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AuditLog, ConnectorLog, JobStat, LogLevel, ServerLog, as_utc, utc_now

logger = logging.getLogger(__name__)

#: Loggers never captured. ``sqlalchemy.engine`` at DEBUG echoes every statement
#: including the INSERT this module is about to run, which is a loop; the rest
#: are per-frame chatter that would drown everything worth reading.
_NEVER_CAPTURE = ("sqlalchemy.engine", "websockets", "uvicorn.access")

#: What ``level`` values a filter may ask for, weakest first. Used for the
#: "this level and above" semantics the portal's dropdown wants -- picking
#: WARNING to see errors too is what everybody expects, and a portal that showed
#: warnings *only* would hide the thing being looked for.
LEVEL_ORDER: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR")


def normalise_level(name: str) -> str:
    """Collapse Python's nine levels onto the four the UI offers.

    ``CRITICAL`` becoming ``ERROR`` is a deliberate loss: nothing in this
    codebase logs critical, and a filter with a bucket that is always empty
    teaches the reader that the filter is unreliable.
    """
    upper = (name or "").upper()
    if upper in LEVEL_ORDER:
        return upper
    if upper in {"CRITICAL", "FATAL"}:
        return LogLevel.ERROR.value
    if upper in {"WARN"}:
        return LogLevel.WARNING.value
    if upper in {"NOTSET", "TRACE"}:
        return LogLevel.DEBUG.value
    return LogLevel.INFO.value


def level_at_least(level: str, minimum: str) -> bool:
    try:
        return LEVEL_ORDER.index(normalise_level(level)) >= LEVEL_ORDER.index(
            normalise_level(minimum)
        )
    except ValueError:  # pragma: no cover - normalise_level guarantees membership
        return True


@dataclass(slots=True)
class LogRecordView:
    """One captured line, in the shape both the ring and the API speak."""

    #: Monotonically increasing within a process. The live tail resumes from a
    #: sequence number rather than a timestamp because two records can share a
    #: millisecond and a resumed tail must not replay or skip one.
    seq: int
    created_at: datetime
    level: str
    logger: str
    message: str
    instance_id: str = ""
    request_id: str | None = None
    traceback: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "created_at": self.created_at.isoformat(),
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
            "instance_id": self.instance_id,
            "request_id": self.request_id,
            "traceback": self.traceback,
        }

    def matches(self, *, level: str = "", logger_name: str = "", search: str = "") -> bool:
        if level and not level_at_least(self.level, level):
            return False
        if logger_name and logger_name.lower() not in self.logger.lower():
            return False
        if search:
            needle = search.lower()
            if needle not in self.message.lower() and needle not in self.logger.lower():
                return False
        return True


class ServerLogStore:
    """The ring, the persist queue, and the live-tail fan-out.

    One instance per process, held on ``app.state``. It is created before the
    logging handler is installed and outlives every request, so a tail opened
    during a deploy sees the shutdown lines too.
    """

    def __init__(
        self,
        *,
        capacity: int = 5000,
        instance_id: str = "",
        persist_from: str = "WARNING",
        persist_queue_max: int = 2000,
    ) -> None:
        self._ring: deque[LogRecordView] = deque(maxlen=capacity)
        self._pending: deque[LogRecordView] = deque(maxlen=persist_queue_max)
        self._subscribers: set[asyncio.Queue[LogRecordView]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._seq = 0
        self._instance_id = instance_id
        self._persist_from = normalise_level(persist_from)

    # -- capture ---------------------------------------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Remember the loop that may be woken to feed live subscribers.

        Records arrive on whatever thread logged them, and pushing onto an
        ``asyncio.Queue`` from the wrong thread is not safe. Captured once at
        startup rather than looked up per record, because ``get_running_loop``
        raises when called from a worker thread -- inside a logging handler,
        which must never raise.
        """
        self._loop = loop

    def capture(self, view: LogRecordView) -> None:
        """Accept one record. Called from the logging handler, on any thread."""
        self._seq += 1
        view.seq = self._seq
        view.instance_id = view.instance_id or self._instance_id
        self._ring.append(view)

        if level_at_least(view.level, self._persist_from):
            # Dropped silently when full rather than blocking a logging call.
            # A backend that stalls a request handler to record a warning about
            # a request handler is worse than a missing warning.
            self._pending.append(view)

        if self._subscribers and self._loop is not None:
            with contextlib.suppress(RuntimeError):
                self._loop.call_soon_threadsafe(self._fan_out, view)

    def _fan_out(self, view: LogRecordView) -> None:
        for queue in list(self._subscribers):
            # A full queue is a browser tab that stopped reading. Dropping its
            # backlog is right: the alternative is a slow reader holding records
            # in memory for as long as the tab stays open.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(view)

    # -- reading ---------------------------------------------------------

    def recent(
        self,
        *,
        level: str = "",
        logger_name: str = "",
        search: str = "",
        limit: int = 500,
        after_seq: int = 0,
    ) -> list[LogRecordView]:
        """Newest-first slice of the ring.

        Newest first because the reason anybody opens this screen is that
        something just happened.
        """
        found: list[LogRecordView] = []
        for view in reversed(self._ring):
            if view.seq <= after_seq:
                break
            if view.matches(level=level, logger_name=logger_name, search=search):
                found.append(view)
                if len(found) >= limit:
                    break
        return found

    @property
    def last_seq(self) -> int:
        return self._seq

    def clear(self) -> None:
        """Forget everything held in memory.

        The sequence counter deliberately keeps counting. A tail that resumed
        from a number this store had already handed out would silently skip
        every line up to it, so the numbers stay unique for the life of the
        process even when what they pointed at is gone.
        """
        self._ring.clear()
        self._pending.clear()

    @contextlib.contextmanager
    def subscribe(self, max_backlog: int = 500) -> Iterator[asyncio.Queue[LogRecordView]]:
        """A queue fed every captured record until the caller lets go.

        A context manager rather than an add/remove pair because the caller is a
        streaming HTTP response, and the way those end is a disconnected client
        raising somewhere in the middle of the generator.
        """
        queue: asyncio.Queue[LogRecordView] = asyncio.Queue(maxsize=max_backlog)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    # -- persistence -----------------------------------------------------

    def take_pending(self, limit: int = 200) -> list[LogRecordView]:
        taken: list[LogRecordView] = []
        while self._pending and len(taken) < limit:
            taken.append(self._pending.popleft())
        return taken


_TRACE_FORMATTER = logging.Formatter()


class StoreHandler(logging.Handler):
    """Feeds a :class:`ServerLogStore` from the root logger.

    Formats eagerly. A ``LogRecord`` holds references to whatever was passed as
    an argument, so keeping five thousand of them in a ring would keep five
    thousand ORM objects, response bodies and tracebacks alive with them.
    """

    def __init__(self, store: ServerLogStore, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._store = store

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.name.startswith(_NEVER_CAPTURE):
                return
            trace = None
            if record.exc_info:
                # formatException lives on Formatter, not Handler. Calling it on
                # self raised AttributeError inside emit, so every exception log
                # was swallowed into a "--- Logging error ---" dump with no row
                # stored -- the incident least likely to survive was the one the
                # store exists for.
                trace = _TRACE_FORMATTER.formatException(record.exc_info)
            self._store.capture(
                LogRecordView(
                    seq=0,
                    created_at=datetime.fromtimestamp(record.created, tz=UTC),
                    level=normalise_level(record.levelname),
                    logger=record.name,
                    message=record.getMessage(),
                    request_id=getattr(record, "request_id", None),
                    traceback=trace,
                )
            )
        except Exception:  # noqa: BLE001 - a logging handler must never raise
            self.handleError(record)


@dataclass
class LogWriter:
    """Drains the store's persist queue into ``server_logs`` and prunes it.

    A single task rather than a write per record: an error storm is precisely
    when the database is least able to take a row per line, and batching turns
    a thousand inserts into five.
    """

    session_factory: Callable[[], Any]
    store: ServerLogStore
    interval_seconds: float = 5.0
    retention_days: int = 2
    connector_retention_days: int = 2
    audit_retention_days: int = 2
    job_stat_retention_days: int = 2
    prune_interval_seconds: float = 3600.0
    _task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _stopping: asyncio.Event = field(default_factory=asyncio.Event, init=False, repr=False)
    _last_prune: float = field(default=0.0, init=False, repr=False)

    async def start(self) -> None:
        self.store.bind_loop(asyncio.get_running_loop())
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        # One last drain outside the task, so the lines explaining a shutdown
        # are not the ones that get lost to it.
        with contextlib.suppress(Exception):
            await self._flush()

    async def _run(self) -> None:
        while not self._stopping.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=self.interval_seconds)
            if self._stopping.is_set():
                return
            try:
                await self._flush()
                if time.monotonic() - self._last_prune > self.prune_interval_seconds:
                    self._last_prune = time.monotonic()
                    await self._prune()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                # Logged at DEBUG on purpose: a failure here at WARNING would
                # enqueue a record describing the failure to enqueue records.
                logger.debug("log writer pass failed", exc_info=True)

    async def _flush(self) -> None:
        views = self.store.take_pending()
        if not views:
            return
        async with self.session_factory() as session:
            session.add_all(
                ServerLog(
                    created_at=view.created_at,
                    level=view.level,
                    logger=view.logger[:120],
                    message=view.message,
                    instance_id=view.instance_id[:32],
                    request_id=view.request_id,
                    traceback=view.traceback,
                )
                for view in views
            )
            await session.commit()

    async def _prune(self) -> None:
        """Age out every table that grows with traffic rather than with customers.

        Time-based rather than count-based: "keep the last million rows" is a
        limit nobody can reason about, while "two days" is something a support
        person can be told. Run from the same task as the writer so a deploy
        cannot leave pruning running nowhere.

        Four tables, not two. The audit trail and the job statistics are written
        once per request and once per dispatched job respectively, which on a
        fleet of connectors polling all day is more rows than either log -- and
        neither had anything ageing it out at all before this.

        A retention of zero or less means "keep nothing older than now", which
        is a legitimate setting for a box that is short of disk; it is not read
        as "keep forever", because a retention knob whose zero means infinity is
        the setting somebody reaches for at 2am and gets backwards.
        """
        now = utc_now()
        async with self.session_factory() as session:
            for model, days in (
                (ServerLog, self.retention_days),
                (ConnectorLog, self.connector_retention_days),
                (AuditLog, self.audit_retention_days),
                (JobStat, self.job_stat_retention_days),
            ):
                await session.execute(
                    delete(model).where(model.created_at < now - timedelta(days=max(days, 0)))
                )
            await session.commit()


def install_capture(store: ServerLogStore, *, level: int = logging.INFO) -> StoreHandler:
    """Attach the store to the root logger, replacing any previous handler.

    Replacing matters in tests, which build several apps in one process; two
    handlers would double every line and make the ring's capacity a lie.
    """
    root = logging.getLogger()
    for existing in list(root.handlers):
        if isinstance(existing, StoreHandler):
            root.removeHandler(existing)
    handler = StoreHandler(store, level=level)
    root.addHandler(handler)
    return handler


# --------------------------------------------------------------------------
# Stored queries
# --------------------------------------------------------------------------


async def stored_server_logs(
    session: AsyncSession,
    *,
    level: str = "",
    logger_name: str = "",
    search: str = "",
    limit: int = 200,
) -> list[LogRecordView]:
    """The persisted half: WARNING and above, surviving restarts."""
    query = select(ServerLog).order_by(ServerLog.created_at.desc()).limit(limit)
    if level:
        wanted = [name for name in LEVEL_ORDER if level_at_least(name, level)]
        query = query.where(ServerLog.level.in_(wanted))
    if logger_name:
        query = query.where(ServerLog.logger.ilike(f"%{logger_name}%"))
    if search:
        query = query.where(ServerLog.message.ilike(f"%{search}%"))

    rows = (await session.execute(query)).scalars().all()
    return [
        LogRecordView(
            seq=0,
            created_at=as_utc(row.created_at),
            level=row.level,
            logger=row.logger,
            message=row.message,
            instance_id=row.instance_id,
            request_id=row.request_id,
            traceback=row.traceback,
        )
        for row in rows
    ]


async def purge_server_logs(session: AsyncSession, *, before: datetime | None = None) -> int:
    """Delete persisted backend log lines, optionally only those before a moment.

    ``before`` of ``None`` means everything. Separate from the writer's own
    pruning because the two answer different needs: pruning is the standing
    policy nobody thinks about, and this is somebody looking at a full disk who
    wants the space back now.
    """
    query = delete(ServerLog)
    if before is not None:
        query = query.where(ServerLog.created_at < before)
    result = await session.execute(query)
    await session.commit()
    return int(result.rowcount or 0)


async def table_counts(session: AsyncSession) -> dict[str, int]:
    """How many rows each diagnostic table is holding.

    Shown in the portal so "are these logs eating the disk?" is a question with
    an answer on screen rather than one that needs a psql session. Counted
    rather than estimated: these tables are pruned to days, so the count is
    small enough that an exact one costs nothing, and an estimate that says
    "about 8,000" invites a second opinion.
    """
    counts: dict[str, int] = {}
    for name, model in (
        ("server_logs", ServerLog),
        ("connector_logs", ConnectorLog),
        ("audit_logs", AuditLog),
    ):
        counts[name] = int((await session.execute(select(func.count(model.id)))).scalar() or 0)
    return counts
