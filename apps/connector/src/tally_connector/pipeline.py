"""One queue in front of TallyPrime. Every request to Tally goes through it.

Tally's HTTP gateway serves **one request at a time**, and it does not merely
queue the second one -- a concurrent POST arriving while an export is running
can wedge the gateway until Tally is restarted. :class:`TallyClient` already
holds a lock around the socket, but a lock is not a pipeline, and the difference
is what this module exists to fix:

``Deadlines were being spent in the wrong place.``
    A job blocked on the lock burned its own deadline while waiting. The
    dashboard fires five reads; the day-book export takes minutes; the other
    four expired in the queue and were *still* sent to Tally afterwards, one
    after another, long after the phone had given up. Here a ticket's deadline
    is checked immediately before dispatch, so work nobody is waiting for is
    never handed to Tally at all.

``A wedged Tally was hammered harder.``
    Every timeout was retried, and each retry queued behind the last. After a
    failure the pipeline now pauses briefly before touching Tally again.

``Liveness probes queued behind exports.``
    The heartbeat's ``is_alive()`` took the same lock, so a ping arriving during
    a five-minute export blocked -- and with it the socket read loop, which is
    what actually took the connector offline mid-export. The pipeline answers
    liveness from what it has just observed, and only probes when it is idle.

There is exactly one worker. That is the entire concurrency model, and it is
deliberate: correctness against a single-threaded gateway is worth more than
parallelism the gateway cannot honour anyway.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel
from tally_core.tally import TallyClient
from tally_core.tally.errors import (
    TallyBusyError,
    TallyCrashedError,
    TallyError,
    TallyTimeoutError,
    TallyUnreachableError,
)
from tally_core.tally.query import TallyQuery

logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")

#: Errors that say something about Tally's health rather than about the request.
#: A malformed envelope tells us nothing -- Tally answered, it just said no.
#:
#: A crash belongs here for the strongest reason of the three: Tally is not
#: merely slow, it is *gone*, and the next request cannot be served until the
#: operator reopens it. Without the cooldown the queue simply re-sent the
#: envelope that had just killed it.
_LIVENESS_FAILURES = (TallyUnreachableError, TallyTimeoutError, TallyCrashedError)

#: How long to leave a crashed Tally alone, as a multiple of the ordinary
#: cooldown. Restarting TallyPrime and reloading a company takes an operator
#: tens of seconds at best; probing through that window only produces refused
#: connections and burns the deadlines of jobs that could have been served
#: from the snapshot instead.
_CRASH_COOLDOWN_MULTIPLIER = 6.0


@dataclass
class _Ticket:
    """One queued request to Tally."""

    label: str
    run: Callable[[], Awaitable[Any]]
    #: Absolute, on the event loop's clock.
    deadline: float
    queued_at: float
    future: asyncio.Future[Any]
    #: Fires at ``deadline`` while the ticket is still waiting. Cancelled the
    #: moment it goes on the wire -- past that point the deadline is the HTTP
    #: client's business, and killing a half-read export helps nobody.
    timer: asyncio.TimerHandle | None = None
    #: Distinguishes "ran out of time" from "the caller hung up", which are the
    #: same thing to :attr:`future` but very different on a support call.
    expired: bool = False

    @property
    def settled(self) -> bool:
        """Whether this ticket already has an answer, one way or another."""
        return self.future.done()

    def disarm(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None


class TallyPipeline:
    """Serialises every request to Tally onto a single bounded FIFO queue."""

    def __init__(
        self,
        tally: TallyClient,
        *,
        max_queue_depth: int = 16,
        cooldown_seconds: float = 5.0,
        liveness_ttl_seconds: float = 20.0,
        probe_deadline_seconds: float = 10.0,
    ) -> None:
        self._tally = tally
        self._queue: asyncio.Queue[_Ticket] = asyncio.Queue(maxsize=max_queue_depth)
        self._cooldown_seconds = cooldown_seconds
        self._liveness_ttl = liveness_ttl_seconds
        self._probe_deadline = probe_deadline_seconds

        self._worker: asyncio.Task[None] | None = None
        self._closed = False

        # -- observed state, all on the loop clock -------------------------
        self._running: str | None = None
        self._cooldown_until = 0.0
        self._online: bool | None = None
        self._observed_at: float | None = None

        # -- counters, for `status` and support calls ----------------------
        self._completed = 0
        self._failed = 0
        self._rejected = 0
        self._expired = 0
        self._crashes = 0
        self._abandoned = 0
        self._peak_depth = 0
        self._peak_wait = 0.0

    # -- lifecycle -------------------------------------------------------

    @property
    def tally(self) -> TallyClient:
        return self._tally

    async def aclose(self) -> None:
        """Stop the worker and fail anything still queued.

        A request already on the wire is aborted, which is the same thing Tally
        sees on any client timeout. :meth:`ConnectorSession.aclose` drains its
        jobs first, so in practice the worker is idle by the time we get here.
        """
        self._closed = True
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.cancel()
            try:  # noqa: SIM105 - contextlib.suppress reads worse around await
                await worker
            except asyncio.CancelledError:
                pass

        while not self._queue.empty():
            ticket = self._queue.get_nowait()
            self._fail(ticket, TallyBusyError("the connector is shutting down"))

    def _ensure_worker(self) -> None:
        """Started lazily so the pipeline can be constructed outside a loop."""
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run(), name="tally-pipeline")

    # -- submission ------------------------------------------------------

    async def execute(
        self,
        query: TallyQuery[Any, ResultT],
        params: BaseModel,
        *,
        deadline_seconds: float,
    ) -> ResultT:
        """Run one registered query, in turn."""
        return await self.submit(
            lambda: self._tally.execute(query, params),
            label=query.name,
            deadline_seconds=deadline_seconds,
        )

    async def submit(
        self,
        run: Callable[[], Awaitable[Any]],
        *,
        label: str,
        deadline_seconds: float,
    ) -> Any:
        """Queue ``run`` and wait for its turn at Tally.

        ``deadline_seconds`` covers the wait as well as the work, because from
        the phone's point of view they are the same second.
        """
        if self._closed:
            raise TallyBusyError("the connector is shutting down")

        if self._queue.full():
            self._rejected += 1
            depth = self._queue.qsize()
            logger.warning("refusing %s: %d request(s) already queued for Tally", label, depth)
            raise TallyBusyError(f"{depth} request(s) already queued for Tally")

        loop = asyncio.get_running_loop()
        now = loop.time()
        ticket = _Ticket(
            label=label,
            run=run,
            deadline=now + deadline_seconds,
            queued_at=now,
            future=loop.create_future(),
        )

        # Armed here rather than checked only at dispatch, so a caller stuck
        # behind a five-minute export is told at *its* deadline instead of at
        # the export's. The worker then finds the ticket already settled and
        # never puts it on the wire, which is the half that matters to Tally.
        ticket.timer = loop.call_at(ticket.deadline, self._expire, ticket)

        self._ensure_worker()
        self._queue.put_nowait(ticket)

        depth = self._queue.qsize()
        self._peak_depth = max(self._peak_depth, depth)
        if depth > 1 or self._running is not None:
            logger.info(
                "queued %s for Tally (%d waiting, running %s)",
                label,
                depth,
                self._running or "nothing",
            )

        # A cancelled waiter resolves the future, which is exactly the signal the
        # worker uses to skip the ticket rather than spend Tally on it.
        return await ticket.future

    # -- liveness --------------------------------------------------------

    async def is_alive(self) -> bool:
        """Whether Tally is responding, without ever queueing behind an export.

        Four answers, in order of precedence:

        1. **Recently failed** -- a fresh timeout or refused connection outranks
           everything below it. Requests can be queueing up precisely *because*
           each one is failing.
        2. **Busy** -- something is on the wire right now, so Tally is by
           definition talking to us. Probing would only queue behind it and time
           out, which used to report a healthy gateway as offline.
        3. **Recently succeeded** -- a real request settled the question inside
           the TTL. Reusing that costs Tally nothing.
        4. **Idle and stale** -- send an actual probe, through the queue like
           everything else.
        """
        loop = asyncio.get_running_loop()
        fresh = (
            self._observed_at is not None
            and loop.time() - self._observed_at <= self._liveness_ttl
        )

        if fresh and not self._online:
            return False

        if self._running is not None or not self._queue.empty():
            return True

        if fresh:
            return True

        try:
            await self.submit(
                self._probe, label="liveness", deadline_seconds=self._probe_deadline
            )
        except TallyError:
            return False
        return True

    async def _probe(self) -> bool:
        """``is_alive`` as a raising call, so the worker records it like any other."""
        if await self._tally.is_alive():
            return True
        raise TallyUnreachableError("TallyPrime did not answer the liveness probe")

    # -- the worker ------------------------------------------------------

    async def _run(self) -> None:
        while True:
            ticket = await self._queue.get()
            try:
                await self._dispatch(ticket)
            except asyncio.CancelledError:
                self._fail(ticket, TallyBusyError("the connector is shutting down"))
                raise
            except Exception:  # noqa: BLE001 - the worker must outlive any one ticket
                logger.exception("tally pipeline worker failed on %s", ticket.label)
                self._fail(ticket, TallyError(f"pipeline failure running {ticket.label}"))
            finally:
                self._queue.task_done()

    async def _dispatch(self, ticket: _Ticket) -> None:
        loop = asyncio.get_running_loop()

        if self._skip(ticket) or not await self._hold(ticket):
            return

        waited = loop.time() - ticket.queued_at
        self._peak_wait = max(self._peak_wait, waited)
        if waited > 1.0:
            logger.info("%s waited %.1fs for Tally", ticket.label, waited)

        ticket.disarm()
        self._running = ticket.label
        try:
            result = await ticket.run()
        except TallyError as exc:
            self._failed += 1
            # A rejected envelope still proves Tally is up and answering; only a
            # timeout or a refused connection says anything about its health.
            unhealthy = isinstance(exc, _LIVENESS_FAILURES)
            self._observe(not unhealthy)
            if unhealthy:
                self._begin_cooldown(exc)
            self._fail(ticket, exc)
        else:
            self._completed += 1
            self._observe(True)
            self._resolve(ticket, result)
        finally:
            self._running = None

    def _skip(self, ticket: _Ticket) -> bool:
        """Whether this ticket already has an answer and must not reach Tally."""
        if not ticket.settled:
            return False
        if not ticket.expired:
            self._abandoned += 1
            logger.info("skipping %s: the caller stopped waiting", ticket.label)
        return True

    async def _hold(self, ticket: _Ticket) -> bool:
        """Wait out any cooldown. ``False`` means give up on this ticket.

        Checked here, immediately before the request goes on the wire, because
        that is the only moment at which "is anyone still waiting for this?" has
        a useful answer.
        """
        loop = asyncio.get_running_loop()
        pause = self._cooldown_until - loop.time()
        if pause <= 0:
            return True

        if loop.time() + pause >= ticket.deadline:
            # Honest rather than hopeful: Tally has just timed out or refused,
            # and this ticket cannot outlast the settling period anyway.
            self._expire(ticket, reason="TallyPrime was recovering")
            return False

        logger.info("holding %s for %.1fs to let TallyPrime settle", ticket.label, pause)
        await asyncio.sleep(pause)
        return not self._skip(ticket)

    def _expire(self, ticket: _Ticket, *, reason: str = "") -> None:
        """Give up on a ticket that ran out of time before reaching Tally."""
        if ticket.settled:
            return
        ticket.expired = True
        ticket.disarm()
        self._expired += 1

        waited = asyncio.get_running_loop().time() - ticket.queued_at
        detail = reason or f"it waited {waited:.1f}s for a free turn"
        logger.info(
            "dropping %s before it reached Tally: %s, so nobody is waiting for the answer",
            ticket.label,
            detail,
        )
        self._fail(ticket, TallyTimeoutError(f"{ticket.label} expired in the queue: {detail}"))

    def _begin_cooldown(self, exc: TallyError) -> None:
        """Give a struggling Tally a moment before the next request.

        A timeout usually means Tally is busy, wedged, or showing a modal dialog.
        Firing the next queued export at it immediately is how one slow report
        turns into a connector that never recovers.
        """
        loop = asyncio.get_running_loop()
        pause = self._cooldown_seconds
        if isinstance(exc, TallyCrashedError):
            pause *= _CRASH_COOLDOWN_MULTIPLIER
            self._crashes += 1
            logger.error(
                "TallyPrime crashed while running a query (%d time(s) this session). "
                "This is a fault inside tally.exe, not a connector error -- see "
                "tallyerr.log next to tally.exe. Pausing %.0fs for it to be reopened.",
                self._crashes,
                pause,
            )
        self._cooldown_until = loop.time() + pause
        logger.warning(
            "pausing Tally requests for %.0fs after %s: %s",
            pause,
            exc.code,
            exc,
        )

    # -- bookkeeping -----------------------------------------------------

    def _observe(self, online: bool) -> None:
        if self._online is not None and self._online != online:
            logger.info("TallyPrime is now %s", "responding" if online else "not responding")
        self._online = online
        self._observed_at = asyncio.get_running_loop().time()

    @staticmethod
    def _resolve(ticket: _Ticket, value: Any) -> None:
        ticket.disarm()
        if not ticket.settled:
            ticket.future.set_result(value)

    @staticmethod
    def _fail(ticket: _Ticket, exc: BaseException) -> None:
        ticket.disarm()
        if not ticket.settled:
            ticket.future.set_exception(exc)

    # -- introspection ---------------------------------------------------

    @property
    def depth(self) -> int:
        """Requests waiting for their turn, excluding the one on the wire."""
        return self._queue.qsize()

    @property
    def busy(self) -> bool:
        return self._running is not None

    def stats(self) -> dict[str, Any]:
        """What a support call needs: is it moving, and is anything piling up?"""
        return {
            "running": self._running,
            "queued": self._queue.qsize(),
            "completed": self._completed,
            "failed": self._failed,
            # Distinct on purpose. `rejected` means the queue was full, which is
            # a capacity problem; `expired` means work outlived its deadline in
            # the queue, which is a Tally-is-slow problem; `abandoned` means the
            # backend gave up first, which is usually the network.
            "rejected": self._rejected,
            "expired": self._expired,
            # Nonzero means TallyPrime itself died mid-query. That is a Tally
            # bug, not a connector one, and it is the first number to ask for
            # when a shop reports "the dashboard keeps going blank".
            "crashes": self._crashes,
            "abandoned": self._abandoned,
            "peak_queued": self._peak_depth,
            "peak_wait_seconds": round(self._peak_wait, 1),
            "tally_online": self._online,
        }
