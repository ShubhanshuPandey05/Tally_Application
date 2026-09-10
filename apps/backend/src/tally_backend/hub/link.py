"""One live WebSocket to one connector.

The link owns request/response correlation: jobs go out with a ``job_id`` and
results arrive asynchronously, out of order, on the same socket. A dict of
pending futures turns that back into something an HTTP handler can await.

Two invariants matter more than anything else here:

1. **Every pending future is always resolved.** A future abandoned when a socket
   dies leaks a request handler until its timeout, and at fleet scale that is how
   a backend runs out of workers during an internet outage.
2. **Concurrency per connector is capped.** TallyPrime serves one request at a
   time and blocks its own UI while exporting, so flooding a connector does not
   make anything faster -- it just freezes the shop's till.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from tally_core.protocol import (
    JobError,
    JobRequest,
    JobResult,
    LogBatch,
    Message,
    Ping,
    Pong,
    ServerMessage,
    StatusEvent,
    UpdateCommand,
)

logger = logging.getLogger(__name__)


class SocketLike(Protocol):
    """The slice of ``fastapi.WebSocket`` this module uses.

    Narrow on purpose: tests drive a link with a fake socket, and a real
    WebSocket in a unit test means an event loop, a server, and flaky timing.
    """

    async def send_text(self, data: str) -> None: ...
    async def receive_text(self) -> str: ...
    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class ReleaseView(Protocol):
    """What the link needs to know about published connector builds.

    Narrow on purpose. This module is transport plumbing; it should not know
    what a release manifest is, only how to ask "what should this connector be
    running?". ``services.releases.ConnectorReleaseView`` is the implementation.
    """

    @property
    def latest_version(self) -> str: ...
    @property
    def min_version(self) -> str: ...
    def outdated(self, version: str) -> tuple[str, bool]:
        """``(target_version, mandatory)``; ``("", False)`` when current."""
        ...


StatusCallback = Callable[["ConnectorLink"], Awaitable[None]]

#: ``(link, batch) -> None``. Synchronous by design: log ingestion sits on the
#: socket's receive loop, and an awaitable here would put a database round trip
#: between two frames on the connection a customer's reports come back on.
LogCallback = Callable[["ConnectorLink", LogBatch], None]

#: ``(link) -> None``. Called when a connector asks for a fresh roster, which is
#: somebody pressing Refresh on the local page of a shop PC. Awaitable, unlike
#: :data:`LogCallback`: this one has to read the database, and it is a rare
#: human-driven request rather than a frame that arrives on a timer.
RosterCallback = Callable[["ConnectorLink"], Awaitable[None]]


class LinkClosed(Exception):
    """The connector went away before its job could be answered."""


class ConnectorLink:
    """Tracks one connector's socket, its liveness, and its in-flight jobs."""

    def __init__(
        self,
        *,
        connector_id: str,
        org_id: str,
        socket: SocketLike,
        max_concurrent_jobs: int = 2,
        heartbeat_interval_seconds: float = 30.0,
        heartbeat_grace_seconds: float = 90.0,
        on_status_change: StatusCallback | None = None,
        on_logs: LogCallback | None = None,
        on_roster_request: RosterCallback | None = None,
        releases: ReleaseView | None = None,
        push_updates: bool = True,
    ) -> None:
        self.connector_id = connector_id
        self.org_id = org_id
        self.session_id = uuid.uuid4().hex
        self.connected_at = time.time()

        self._socket = socket
        self._heartbeat_interval = heartbeat_interval_seconds
        self._heartbeat_grace = heartbeat_grace_seconds
        self._on_status_change = on_status_change
        self._on_logs = on_logs
        self._on_roster_request = on_roster_request
        self._releases = releases
        self._push_updates = push_updates

        self._pending: dict[str, asyncio.Future[JobResult]] = {}
        self._slots = asyncio.Semaphore(max_concurrent_jobs)
        self._send_lock = asyncio.Lock()
        self._closed = asyncio.Event()

        self.tally_online: bool = False
        self.companies_open: list[str] = []
        self.capabilities: list[dict[str, Any]] = []
        self.host: dict[str, Any] = {}
        self.last_seen: float = time.time()
        #: Set once the connector answers a ping; before that we know it is
        #: connected but not whether Tally behind it is actually running.
        self.status_known: bool = False
        #: The build this connector reported on its most recent frame. Every
        #: frame carries it, so this is never more than one message stale.
        self.connector_version: str = ""
        #: The version we last told it to install, so a busy connector gets one
        #: instruction rather than one per job result. Cleared by a version
        #: change, which is what makes a *failed* update get re-ordered on the
        #: next reconnect instead of being silently forgotten.
        self._update_ordered: str = ""

    # -- state -----------------------------------------------------------

    @property
    def is_closed(self) -> bool:
        return self._closed.is_set()

    @property
    def in_flight(self) -> int:
        return len(self._pending)

    def snapshot(self) -> dict[str, Any]:
        """Serialisable view for the fleet-health endpoint."""
        return {
            "connector_id": self.connector_id,
            "org_id": self.org_id,
            "session_id": self.session_id,
            "connected_seconds": round(time.time() - self.connected_at, 1),
            "tally_online": self.tally_online,
            "companies_open": list(self.companies_open),
            "in_flight": self.in_flight,
            "host": self.host,
            "connector_version": self.connector_version,
        }

    def supports(self, query: str) -> bool:
        if not self.capabilities:
            return True
        return any(entry.get("name") == query for entry in self.capabilities)

    # -- sending ---------------------------------------------------------

    async def send(self, message: Message) -> None:
        if self.is_closed:
            raise LinkClosed(f"connector {self.connector_id} is not connected")
        message = self._stamp(message)
        # Serialised: two coroutines interleaving frames on one WebSocket
        # produces a corrupt stream that is very hard to diagnose later.
        async with self._send_lock:
            await self._socket.send_text(message.model_dump_json())

    def _stamp(self, message: Message) -> Message:
        """Attach the current release floor to any frame going out.

        Done here, once, rather than at each construction site: a ping built in
        the heartbeat loop and a job built in ``_await_result`` must carry the
        same answer, and the version to stamp is only known to the link. Missing
        it on one message type would mean an idle connector -- which sees nothing
        but pings -- never hearing about a release.

        Read fresh on every send rather than cached at construction, so a
        ``run.py publish`` reaches connectors that are already connected.
        """
        if self._releases is None or not isinstance(message, ServerMessage):
            return message
        latest = self._releases.latest_version
        if not latest:
            return message
        # A copy, never a mutation: callers own their message objects, and the
        # hub broadcasts one built object to several links.
        return message.model_copy(
            update={
                "latest_connector_version": latest,
                "min_connector_version": self._releases.min_version,
            }
        )

    async def run_job(
        self,
        *,
        query: str,
        params: dict[str, Any],
        timeout_seconds: float,
        cache_ttl_seconds: float = 0.0,
        job_id: str | None = None,
    ) -> JobResult:
        """Dispatch one job and wait for its result.

        The timeout covers *queueing as well as execution*. A job that spent 55
        of its 60 seconds waiting for a free slot has already lost the caller, so
        giving it a fresh 60 seconds on Tally would only burn the shop's CPU on
        an answer nobody will read.
        """
        job_id = job_id or uuid.uuid4().hex
        deadline = time.monotonic() + timeout_seconds

        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=timeout_seconds)
        except TimeoutError:
            return JobResult.failure(
                job_id,
                JobError(
                    code="connector_busy",
                    message=f"no free slot on connector {self.connector_id}",
                    user_message="Your Tally PC is busy. Please try again in a moment.",
                    retryable=True,
                ),
            )

        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return JobResult.failure(job_id, _timeout_error(job_id, timeout_seconds))
            return await self._await_result(
                job_id=job_id,
                query=query,
                params=params,
                cache_ttl_seconds=cache_ttl_seconds,
                remaining=remaining,
            )
        finally:
            self._slots.release()

    async def _await_result(
        self,
        *,
        job_id: str,
        query: str,
        params: dict[str, Any],
        cache_ttl_seconds: float,
        remaining: float,
    ) -> JobResult:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[JobResult] = loop.create_future()
        self._pending[job_id] = future

        try:
            await self.send(
                JobRequest(
                    job_id=job_id,
                    query=query,
                    params=params,
                    cache_ttl_seconds=cache_ttl_seconds,
                    # Tell the connector to stop early too. Otherwise it keeps a
                    # dead job on Tally after we have already given up, and the
                    # next real request queues behind it.
                    deadline_seconds=remaining,
                )
            )
            return await asyncio.wait_for(future, timeout=remaining)

        except TimeoutError:
            return JobResult.failure(job_id, _timeout_error(job_id, remaining))

        except LinkClosed:
            return JobResult.failure(
                job_id,
                JobError(
                    code="connector_offline",
                    message=f"connector {self.connector_id} disconnected",
                    user_message="Your Tally PC went offline.",
                    retryable=True,
                ),
            )
        finally:
            self._pending.pop(job_id, None)

    # -- receiving -------------------------------------------------------

    async def handle_message(self, message: dict[str, Any]) -> None:
        """Route one decoded inbound frame."""
        self.last_seen = time.time()
        await self._note_version(message)
        kind = message.get("type")

        if kind == "job_result":
            self._resolve(message)
        elif kind == "pong":
            await self._handle_pong(message)
        elif kind == "status":
            await self._handle_status(message)
        elif kind == "log_batch":
            self._handle_logs(message)
        elif kind == "roster_request":
            await self._handle_roster_request()
        else:
            # Forward compatibility: a newer connector may send frames this
            # backend predates. Ignoring beats dropping a working session.
            logger.debug("ignoring connector frame of type %r", kind)

    async def _note_version(self, message: dict[str, Any]) -> None:
        """Check the version on an inbound frame and order an update if needed.

        Runs on *every* frame, which is the whole point: a connector that stays
        up for a week used to be re-evaluated once, at handshake. Now the answer
        is refreshed on each heartbeat, so a release published mid-session
        reaches it in seconds rather than at its next six-hourly poll.

        Cheap enough to sit on this path -- a dict lookup, a tuple compare, and a
        stat() on a cached manifest -- and it sends at most one instruction per
        target version per session, so a connector returning fifty job results
        does not receive fifty update commands.
        """
        reported = message.get("connector_version")
        if not isinstance(reported, str) or not reported:
            # An older connector that predates the stamped field. Its handshake
            # still told us a version, so leave whatever we already know intact.
            return
        await self.review_version(reported)

    async def review_version(self, reported: str) -> None:
        """Record the build a connector is running; order an update if it is old.

        Public so the handshake can call it directly. Waiting for the first
        heartbeat instead would leave a connector that reconnects on an outdated
        build running it for another 30 seconds for no reason -- and on a flapping
        connection, possibly forever.
        """
        if not reported:
            return

        if reported != self.connector_version:
            if self.connector_version:
                # The interesting transition: it came back on a different build.
                logger.info(
                    "connector %s is now running %s (was %s)",
                    self.connector_id,
                    reported,
                    self.connector_version,
                )
            self.connector_version = reported
            # A version change means any previous instruction is spent, whether
            # it succeeded or not. Re-arming here is what gets a *failed* update
            # retried rather than remembered as done.
            self._update_ordered = ""
            self.host = {**self.host, "connector_version": reported}

        if self._releases is None or not self._push_updates:
            return

        target, mandatory = self._releases.outdated(reported)
        if not target or target == self._update_ordered:
            return

        self._update_ordered = target
        logger.info(
            "telling connector %s to install %s (running %s%s)",
            self.connector_id,
            target,
            reported,
            ", required" if mandatory else "",
        )
        # Failure is not worth propagating: the connector still has the version
        # stamped on every other frame and will act on that by itself, so a lost
        # command costs nothing but a slightly later install.
        with contextlib.suppress(Exception):
            await self.send(
                UpdateCommand(
                    version=target,
                    mandatory=mandatory,
                    reason="a newer connector build is published",
                )
            )

    def _resolve(self, message: dict[str, Any]) -> None:
        job_id = message.get("job_id")
        future = self._pending.get(job_id or "")
        if future is None or future.done():
            # Normal: the caller timed out and walked away before the answer
            # arrived. Not worth a warning -- it happens on every slow export.
            logger.debug("result for unknown or abandoned job %s", job_id)
            return
        try:
            future.set_result(JobResult.model_validate(message))
        except Exception as exc:  # noqa: BLE001 - a bad frame must not kill the loop
            future.set_exception(exc)

    def _handle_logs(self, message: dict[str, Any]) -> None:
        """Hand a pushed log batch to whoever is storing them.

        Every failure path here is a swallow, and that is the point: this is a
        diagnostic side-channel on a socket whose actual job is serving a
        customer's reports. A malformed batch, or a full ingest buffer, must
        cost the support view some lines and cost the customer nothing.
        """
        if self._on_logs is None:
            return
        try:
            batch = LogBatch.model_validate(message)
        except Exception as exc:  # noqa: BLE001 - a bad frame is not a dead session
            logger.debug("connector %s sent a malformed log batch: %s", self.connector_id, exc)
            return
        try:
            self._on_logs(self, batch)
        except Exception:  # noqa: BLE001
            logger.warning("could not accept logs from %s", self.connector_id, exc_info=True)

    async def _handle_roster_request(self) -> None:
        """Rebuild and push this connector's roster.

        Every failure is swallowed for the same reason as the log path: this
        serves a page on a shop PC, and a database hiccup while somebody looks
        at it must not drop the socket their reports come back on. The page
        keeps showing what it last had and says when that was.
        """
        if self._on_roster_request is None:
            return
        try:
            await self._on_roster_request(self)
        except Exception:  # noqa: BLE001 - a roster is never worth the session
            logger.warning(
                "could not answer roster request from %s", self.connector_id, exc_info=True
            )

    async def _handle_pong(self, message: dict[str, Any]) -> None:
        pong = Pong.model_validate(message)
        await self._update_status(pong.tally_online, pong.companies_open)

    async def _handle_status(self, message: dict[str, Any]) -> None:
        event = StatusEvent.model_validate(message)
        await self._update_status(event.tally_online, event.companies_open)

    async def _update_status(self, online: bool, companies: list[str]) -> None:
        changed = (online != self.tally_online) or not self.status_known
        self.tally_online = online
        self.status_known = True
        if companies:
            self.companies_open = companies
        if changed and self._on_status_change is not None:
            # Persisting status must never take the socket down with it.
            with contextlib.suppress(Exception):
                await self._on_status_change(self)

    # -- liveness --------------------------------------------------------

    async def heartbeat_loop(self) -> None:
        """Ping until the connector stops answering.

        A TCP connection dropped by a NAT box or a sleeping laptop stays 'open'
        from the socket's point of view and delivers nothing. Without this the
        backend would keep routing jobs into a black hole and every one of them
        would burn its full timeout.
        """
        while not self.is_closed:
            await asyncio.sleep(self._heartbeat_interval)
            if self.is_closed:
                return

            if time.time() - self.last_seen > self._heartbeat_grace:
                logger.warning(
                    "connector %s missed heartbeats for %.0fs; dropping",
                    self.connector_id,
                    time.time() - self.last_seen,
                )
                await self.close(code=1001, reason="heartbeat timeout")
                return

            try:
                await self.send(Ping(token=uuid.uuid4().hex))
            except Exception:  # noqa: BLE001 - the receive loop reports the cause
                logger.debug("heartbeat send failed for %s", self.connector_id)
                return

    async def close(self, *, code: int = 1000, reason: str = "") -> None:
        """Close the socket and fail every job still waiting on it."""
        if self.is_closed:
            return
        self._closed.set()

        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(LinkClosed(reason or "connector disconnected"))
        self._pending.clear()

        with contextlib.suppress(Exception):
            await self._socket.close(code=code, reason=reason)


def _timeout_error(job_id: str, seconds: float) -> JobError:
    return JobError(
        code="connector_timeout",
        message=f"job {job_id} timed out after {seconds:.0f}s",
        user_message="TallyPrime is taking too long to respond. Please try again.",
        retryable=True,
    )
