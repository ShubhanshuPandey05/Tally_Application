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

    def __init__(self, settings: Settings, bus: JobBus | None = None) -> None:
        self._settings = settings
        self._bus = bus or build_bus(settings.redis_url, settings.instance_id)
        self._links: dict[str, ConnectorLink] = {}
        #: Identical concurrent reads share one trip to the shop's PC.
        self._in_flight: dict[str, asyncio.Task[JobResult]] = {}

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
