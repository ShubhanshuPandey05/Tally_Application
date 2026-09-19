"""The connector hub: the backend's single entry point for reading from Tally.

Everything above this line (services, routers) asks the hub for a query result
and never learns whether the answer came from this process, from a sibling
instance, or from a coalesced request someone else started.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from tally_core.protocol import JobError, JobRequest, JobResult

from ..config import Settings
from .bus import BusUnavailable, JobBus, build_bus
from .link import ConnectorLink

logger = logging.getLogger(__name__)


def coalesce_key(connector_id: str, query: str, params: dict[str, Any]) -> str:
    """Identity of a read, for request collapsing.

    ``sort_keys`` matters: two dicts with the same content but different
    insertion order must produce the same key or coalescing silently never fires.
    """
    blob = json.dumps(
        {"c": connector_id, "q": query, "p": params}, sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


class ConnectorHub:
    """Owns local connector links and routes jobs to them."""

    #: Called with a connector id once it is registered and reachable.
    #:
    #: A plain attribute rather than a constructor argument because the thing
    #: that wants it -- the voucher queue -- needs the hub to exist first. Kept
    #: to one callback: this is a notification, not an extension point, and a
    #: list of them would turn attach into a place where slow work accumulates
    #: on the handshake path.
    on_attach: Callable[[str], Awaitable[Any]] | None = None

    def __init__(self, settings: Settings, bus: JobBus | None = None) -> None:
        self._settings = settings
        self._bus = bus or build_bus(settings.redis_url, settings.instance_id)
        self._links: dict[str, ConnectorLink] = {}
        #: Identical concurrent reads share one trip to the shop's PC.
        self._in_flight: dict[str, asyncio.Task[JobResult]] = {}
        #: Held so a drain scheduled on attach is not garbage collected
        #: mid-flight, which is how a queued entry silently never goes.
        self._attach_tasks: set[asyncio.Task[None]] = set()

    @property
    def bus(self) -> JobBus:
        return self._bus

    async def start(self) -> None:
        await self._bus.start(self._serve_forwarded)

    async def stop(self) -> None:
        await self._bus.stop()
        for link in list(self._links.values()):
            await link.close(code=1001, reason="backend shutting down")
        self._links.clear()

    # -- link lifecycle --------------------------------------------------

    async def attach(self, link: ConnectorLink) -> None:
        """Register a newly handshaken connector.

        A second connection for the same connector replaces the first. That is
        the common case after a laptop sleeps: the old socket is a half-open
        connection the OS has not reaped, and preferring the fresh one gets the
        customer working again immediately.
        """
        existing = self._links.get(link.connector_id)
        if existing is not None and existing is not link:
            logger.info("replacing existing session for connector %s", link.connector_id)
            await existing.close(code=1012, reason="replaced by a newer session")

        self._links[link.connector_id] = link
        with contextlib.suppress(Exception):
            await self._bus.register(link.connector_id)

        # Scheduled, never awaited. The handshake must finish and the socket
        # start serving whatever else this does, and draining a queue means
        # talking to a TallyPrime that may take a minute to answer.
        if self.on_attach is not None:
            task = asyncio.create_task(self._notify_attached(link.connector_id))
            self._attach_tasks.add(task)
            task.add_done_callback(self._attach_tasks.discard)

    async def _notify_attached(self, connector_id: str) -> None:
        try:
            await self.on_attach(connector_id)  # type: ignore[misc]
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a reconnect must survive this
            logger.exception("attach callback failed for %s", connector_id)

    async def detach(self, link: ConnectorLink) -> None:
        # Only if it is still the current one -- a reconnect may already have
        # installed a newer link, and removing that would strand a live socket.
        if self._links.get(link.connector_id) is link:
            del self._links[link.connector_id]
            with contextlib.suppress(Exception):
                await self._bus.unregister(link.connector_id)
        await link.close()

    def local_link(self, connector_id: str) -> ConnectorLink | None:
        link = self._links.get(connector_id)
        if link is not None and link.is_closed:
            return None
        return link

    def local_links(self) -> list[ConnectorLink]:
        return list(self._links.values())

    async def is_online(self, connector_id: str) -> bool:
        if self.local_link(connector_id) is not None:
            return True
        with contextlib.suppress(Exception):
            return await self._bus.locate(connector_id) is not None
        return False

    # -- dispatch --------------------------------------------------------

    async def run(
        self,
        *,
        connector_id: str,
        query: str,
        params: dict[str, Any],
        timeout_seconds: float | None = None,
        cache_ttl_seconds: float = 0.0,
        coalesce: bool = True,
    ) -> JobResult:
        """Execute a query on a connector, wherever it happens to be connected.

        Never raises for expected failures -- an offline connector or a timeout
        comes back as a failed :class:`JobResult`, so callers have exactly one
        code path to handle instead of a mix of exceptions and return values.
        """
        timeout = timeout_seconds or self._settings.default_job_timeout_seconds

        if not coalesce:
            return await self._dispatch(connector_id, query, params, timeout, cache_ttl_seconds)

        key = coalesce_key(connector_id, query, params)
        existing = self._in_flight.get(key)
        if existing is not None:
            logger.debug("joining in-flight read %s", key)
            # Shielded so this waiter's cancellation cannot abort the shared task
            # and strip the result from everyone else waiting on it.
            return await asyncio.shield(existing)

        task = asyncio.create_task(
            self._dispatch(connector_id, query, params, timeout, cache_ttl_seconds)
        )
        self._in_flight[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if self._in_flight.get(key) is task and task.done():
                del self._in_flight[key]

    async def write(
        self,
        *,
        connector_id: str,
        mutation: str,
        params: dict[str, Any],
        timeout_seconds: float | None = None,
    ) -> JobResult:
        """Perform a write on a connector, wherever it is connected.

        Deliberately **not** a flag on :meth:`run`, and deliberately without its
        coalescing. Two identical reads are one read; two identical receipts are
        two receipts, and folding them together would silently drop a real
        second payment from the same customer for the same amount on the same
        day. There is no key under which a write may join another write.

        Like :meth:`run` it never raises for expected failures, so callers have
        one code path. Unlike it, every failure it returns is non-retryable:
        this side cannot tell a write that never left from one that posted and
        lost its reply, so re-sending is never the automatic answer.
        """
        timeout = timeout_seconds or self._settings.default_write_timeout_seconds
        job_id = uuid.uuid4().hex

        link = self.local_link(connector_id)
        if link is None:
            # Not routed across instances. A write has a person waiting on it
            # and must not be re-sent, so the extra hop -- which can fail after
            # the frame was delivered -- buys an ambiguity we refuse to create.
            # Reads coalesce and retry; a voucher does neither.
            owner = None
            with contextlib.suppress(Exception):
                owner = await self._bus.locate(connector_id)
            if owner is not None and owner != self._settings.instance_id:
                logger.info(
                    "refusing to route write for %s to instance %s", connector_id, owner
                )
                return JobResult.failure(job_id, _write_elsewhere_error(connector_id))
            return JobResult.failure(job_id, _write_offline_error(connector_id))

        if not link.can_write(mutation):
            return JobResult.failure(job_id, _cannot_write_error(mutation))

        return await link.run_write(
            mutation=mutation,
            params=params,
            timeout_seconds=timeout,
            job_id=job_id,
        )

    async def _dispatch(
        self,
        connector_id: str,
        query: str,
        params: dict[str, Any],
        timeout: float,
        cache_ttl: float,
    ) -> JobResult:
        job_id = uuid.uuid4().hex

        link = self.local_link(connector_id)
        if link is not None:
            if not link.supports(query):
                return JobResult.failure(job_id, _outdated_error(query))
            return await link.run_job(
                query=query,
                params=params,
                timeout_seconds=timeout,
                cache_ttl_seconds=cache_ttl,
                job_id=job_id,
            )

        owner = None
        with contextlib.suppress(Exception):
            owner = await self._bus.locate(connector_id)

        if owner is None or owner == self._settings.instance_id:
            # No socket here and the directory agrees, or points back at us --
            # either way there is nothing to route to.
            return JobResult.failure(job_id, _offline_error(connector_id))

        try:
            raw = await self._bus.forward(
                owner,
                connector_id,
                JobRequest(
                    job_id=job_id,
                    query=query,
                    params=params,
                    cache_ttl_seconds=cache_ttl,
                    deadline_seconds=timeout,
                ).model_dump(mode="json"),
                # A little longer than the job itself, so the owning instance's
                # own timeout fires first and returns a specific error rather
                # than this one reporting a vague routing failure.
                timeout=timeout + 5,
            )
            return JobResult.model_validate(raw)
        except BusUnavailable as exc:
            logger.warning("routing to %s failed: %s", owner, exc)
            return JobResult.failure(job_id, _offline_error(connector_id))

    async def _serve_forwarded(self, connector_id: str, job: dict[str, Any]) -> dict[str, Any]:
        """Run a job a sibling instance routed here because we hold the socket."""
        request = JobRequest.model_validate(job)
        link = self.local_link(connector_id)
        if link is None:
            return JobResult.failure(
                request.job_id, _offline_error(connector_id)
            ).model_dump(mode="json")

        result = await link.run_job(
            query=request.query,
            params=request.params,
            timeout_seconds=request.deadline_seconds,
            cache_ttl_seconds=request.cache_ttl_seconds,
            job_id=request.job_id,
        )
        return result.model_dump(mode="json")

    def fleet_snapshot(self) -> dict[str, Any]:
        return {
            "instance_id": self._settings.instance_id,
            "distributed": self._bus.is_distributed,
            "connectors": [link.snapshot() for link in self._links.values()],
        }


def _offline_error(connector_id: str) -> JobError:
    return JobError(
        code="connector_offline",
        message=f"connector {connector_id} is not connected",
        user_message="Your Tally PC is offline. Please check it is switched on and connected.",
        retryable=True,
    )


def _outdated_error(query: str) -> JobError:
    return JobError(
        code="connector_outdated",
        message=f"connector does not support query {query!r}",
        user_message="Your Tally Connector needs updating to show this.",
        retryable=False,
    )


def _write_offline_error(connector_id: str) -> JobError:
    """Offline, and definitely nothing was written.

    Worth its own message rather than reusing the read one: "please try again"
    is safe here precisely because the entry never left this building.
    """
    return JobError(
        code="connector_offline",
        message=f"connector {connector_id} is not connected",
        user_message=(
            "Your Tally PC is offline, so nothing was saved. Check it is "
            "switched on and connected, then try again."
        ),
        # The entry never left this building, so re-sending it cannot duplicate
        # anything. This is the one write failure that is unambiguously safe.
        retryable=True,
    )


def _write_elsewhere_error(connector_id: str) -> JobError:
    return JobError(
        code="connector_elsewhere",
        message=f"connector {connector_id} is held by another backend instance",
        user_message="Your Tally PC is reconnecting. Nothing was saved -- please try again.",
        retryable=True,
    )


def _cannot_write_error(mutation: str) -> JobError:
    return JobError(
        code="connector_outdated",
        message=f"connector does not support mutation {mutation!r}",
        user_message=(
            "The Tally Connector on your PC is too old to create entries. "
            "Update it and try again."
        ),
        retryable=False,
    )
