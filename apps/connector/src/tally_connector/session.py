"""The connector's WebSocket session with the backend.

Responsibilities, in order of importance:

1. Stay connected. A shop's internet drops, the laptop sleeps, the backend
   deploys. Reconnection is exponential with jitter and never gives up -- the
   owner should never have to know the connector exists.
2. Never let one bad job take the session down.
3. Notice a dead-but-open socket. A NAT box that silently drops a connection
   leaves a socket that looks fine and delivers nothing, so a heartbeat
   watchdog forces a reconnect when pings stop arriving.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import random
import ssl
from typing import Any

import websockets
from pydantic import ValidationError
from tally_core.tally import TallyClient, registry_manifest
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .cache import ResponseCache
from .config import ConnectorSettings
from .executor import JobExecutor
from .pipeline import TallyPipeline
from .protocol import (
    PROTOCOL_VERSION,
    Hello,
    HelloAck,
    JobRequest,
    Message,
    Ping,
    Pong,
    QueryCapability,
    StatusEvent,
)

logger = logging.getLogger(__name__)

#: A full year's day book can be tens of megabytes; the library's 1 MiB default
#: would close the connection mid-transfer.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024


class AuthenticationRejected(Exception):
    """The backend refused the handshake.

    Retrying with the same credentials cannot succeed, so this stops the
    reconnect loop rather than hammering the backend forever.
    """


class ConnectorSession:
    """Owns one logical connection to the backend, across reconnects."""

    def __init__(
        self,
        settings: ConnectorSettings,
        *,
        version: str,
        tally: TallyClient | None = None,
    ) -> None:
        self._settings = settings
        self._version = version
        self._tally = tally or TallyClient(settings.tally_config())
        self._pipeline = TallyPipeline(
            self._tally,
            max_queue_depth=settings.tally_queue_max_depth,
            cooldown_seconds=settings.tally_cooldown_seconds,
            liveness_ttl_seconds=settings.tally_liveness_ttl_seconds,
        )
        self._executor = JobExecutor(
            self._pipeline,
            ResponseCache(
                max_entries=settings.cache_max_entries,
                stale_ttl_seconds=settings.cache_stale_ttl_seconds,
            ),
        )
        # Caps how many jobs are *accepted* at once. They still reach Tally one
        # at a time -- the pipeline sees to that -- but bounding acceptance keeps
        # a burst from parking dozens of tasks and cache lookups in memory.
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._jobs: set[asyncio.Task[None]] = set()
        self._pings: set[asyncio.Task[None]] = set()
        # Two coroutines interleaving frames on one WebSocket produce a corrupt
        # stream. Job results and pongs are now genuinely concurrent, so sends
        # have to be serialised.
        self._send_lock = asyncio.Lock()
        self._last_inbound = 0.0
        self._tally_online: bool | None = None
        self._stopping = asyncio.Event()

    # -- lifecycle ------------------------------------------------------

    async def run_forever(self) -> None:
        """Connect, serve, reconnect. Returns only on stop() or auth rejection."""
        delay = self._settings.reconnect_initial_seconds

        while not self._stopping.is_set():
            try:
                await self._run_once()
                delay = self._settings.reconnect_initial_seconds

            except AuthenticationRejected:
                raise

            except asyncio.CancelledError:
                raise

            except Exception as exc:  # noqa: BLE001 - reconnect on anything
                logger.warning("session ended (%s: %s)", type(exc).__name__, exc)

            if self._stopping.is_set():
                break

            # Jitter matters at scale: without it, a backend restart brings
            # every connector back in the same instant.
            sleep_for = min(delay, self._settings.reconnect_max_seconds)
            sleep_for *= 0.5 + random.random()
            logger.info("reconnecting in %.1fs", sleep_for)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=sleep_for)
            delay = min(delay * 2, self._settings.reconnect_max_seconds)

        await self._drain_jobs()

    async def stop(self) -> None:
        self._stopping.set()

    async def aclose(self) -> None:
        await self._drain_jobs()
        await self._pipeline.aclose()
        await self._tally.aclose()

    # -- one connection --------------------------------------------------

    async def _run_once(self) -> None:
        logger.info("connecting to %s", self._settings.backend_url)

        async with websockets.connect(
            self._settings.backend_url,
            ssl=self._ssl_context(),
            max_size=MAX_MESSAGE_BYTES,
            # Application-level heartbeat drives liveness, so the library's own
            # keepalive is left off to avoid two timers disagreeing.
            ping_interval=None,
            open_timeout=30,
            compression="deflate",
        ) as socket:
            ack = await self._handshake(socket)
            logger.info("session %s established", ack.session_id)

            self._last_inbound = asyncio.get_running_loop().time()
            watchdog = asyncio.create_task(self._watch_heartbeat(socket))
            try:
                await self._serve(socket)
            finally:
                watchdog.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watchdog

    def _ssl_context(self) -> ssl.SSLContext | None:
        if not self._settings.backend_url.startswith("wss://"):
            return None
        context = ssl.create_default_context()
        if not self._settings.verify_tls:
            # Development only. Loud, because shipping this would expose a
            # customer's books to anyone who can intercept the connection.
            logger.warning("TLS verification is DISABLED - never do this in production")
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    async def _handshake(self, socket: Any) -> HelloAck:
        hello = Hello.signed(
            connector_id=self._settings.connector_id,
            secret=self._settings.connector_secret,
            host=self._settings.host_info(self._version),
            capabilities=[QueryCapability(**entry) for entry in registry_manifest()],
        )
        await self._send(socket, hello)

        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=30)
        except TimeoutError as exc:
            raise ConnectionError("backend did not answer the handshake") from exc

        ack = HelloAck.model_validate_json(raw)
        if not ack.accepted:
            raise AuthenticationRejected(ack.reason or "handshake rejected")
        if ack.protocol_version != PROTOCOL_VERSION:
            raise AuthenticationRejected(
                f"backend speaks protocol v{ack.protocol_version}, "
                f"connector speaks v{PROTOCOL_VERSION}; please update the connector"
            )
        return ack

    async def _serve(self, socket: Any) -> None:
        async for raw in socket:
            self._last_inbound = asyncio.get_running_loop().time()
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning("discarding non-JSON frame")
                continue

            kind = message.get("type")
            if kind == "ping":
                # Answered on its own task, never inline. Awaiting the pong here
                # stalled this read loop for as long as the liveness check took,
                # and the liveness check queued behind whatever export was
                # running -- so a slow day-book read stopped the connector from
                # reading *any* frame, the backend saw no heartbeat, and it
                # dropped a connection that was working perfectly.
                self._start_ping(socket, message)
            elif kind == "job":
                self._start_job(socket, message)
            else:
                # Forward compatibility: a newer backend may send message types
                # this build predates. Ignoring beats disconnecting.
                logger.debug("ignoring unsupported message type %r", kind)

    # -- handlers --------------------------------------------------------

    def _start_ping(self, socket: Any, message: dict[str, Any]) -> None:
        task = asyncio.create_task(self._handle_ping(socket, message))
        self._pings.add(task)
        task.add_done_callback(self._pings.discard)

    async def _handle_ping(self, socket: Any, message: dict[str, Any]) -> None:
        try:
            ping = Ping.model_validate(message)
        except ValidationError:
            logger.warning("malformed ping")
            return

        # Through the pipeline, which answers from what it has just seen and only
        # probes Tally when it is idle. The heartbeat must never be the thing
        # that puts a request on a busy gateway.
        online = await self._pipeline.is_alive()
        try:
            await self._report_status_change(socket, online)
            await self._send(socket, Pong(token=ping.token, tally_online=online))
        except ConnectionClosed:
            logger.debug("connection closed before the pong could be sent")

    async def _report_status_change(self, socket: Any, online: bool) -> None:
        """Push an event only on transitions, not on every heartbeat."""
        if self._tally_online == online:
            return
        self._tally_online = online
        logger.info("TallyPrime is now %s", "reachable" if online else "unreachable")
        await self._send(socket, StatusEvent(tally_online=online))

    def _start_job(self, socket: Any, message: dict[str, Any]) -> None:
        try:
            job = JobRequest.model_validate(message)
        except ValidationError as exc:
            logger.warning("discarding malformed job request: %s", exc)
            return

        task = asyncio.create_task(self._run_job(socket, job))
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)

    async def _run_job(self, socket: Any, job: JobRequest) -> None:
        async with self._semaphore:
            logger.info("running job %s (%s)", job.job_id, job.query)
            result = await self._executor.run(job)

        try:
            await self._send(socket, result)
        except ConnectionClosed:
            # The backend will have timed the job out and the phone will retry;
            # holding the result would only serve it to nobody.
            logger.info("connection closed before job %s could be returned", job.job_id)

    # -- plumbing --------------------------------------------------------

    async def _send(self, socket: Any, message: Message) -> None:
        async with self._send_lock:
            await socket.send(message.model_dump_json())

    async def _watch_heartbeat(self, socket: Any) -> None:
        """Force a reconnect when the backend stops pinging.

        A silently dropped TCP connection stays 'open' from the socket's point
        of view, so without this the connector can sit there indefinitely
        looking healthy while serving nothing.
        """
        timeout = self._settings.heartbeat_timeout_seconds
        loop = asyncio.get_running_loop()
        while True:
            await asyncio.sleep(timeout / 3)
            if loop.time() - self._last_inbound > timeout:
                logger.warning("no heartbeat for %.0fs; dropping connection", timeout)
                await socket.close(code=1001, reason="heartbeat timeout")
                return

    async def _drain_jobs(self) -> None:
        pending = self._jobs | self._pings
        if not pending:
            return
        logger.info("waiting for %d in-flight task(s)", len(pending))
        await asyncio.gather(*pending, return_exceptions=True)

    # -- introspection ---------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Queue health, for logs and support calls."""
        return {"jobs_accepted": len(self._jobs), **self._pipeline.stats()}


def is_fatal_connect_error(exc: BaseException) -> bool:
    """Whether a connection failure is worth reporting loudly.

    A 401/403 from the backend means the connector was unpaired or revoked --
    the owner has to act, so it must not be buried in a retry loop.
    """
    return isinstance(exc, InvalidStatus) and exc.response.status_code in {401, 403}
