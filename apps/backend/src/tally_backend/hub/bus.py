"""Cross-instance job routing.

The problem this solves is the central one in scaling a connector fleet. A
connector holds **one** WebSocket to **one** backend instance, but the phone's
HTTP request lands on whichever instance the load balancer picked. Without
routing, "show me today's sales" fails whenever the two do not coincide -- which,
with N instances, is (N-1)/N of the time.

Two implementations:

* :class:`LocalBus` -- no external dependency, correct for a single process.
  This is the default so that a fresh checkout runs with nothing but Python, and
  it is genuinely sufficient for a few hundred connectors on one box.
* :class:`RedisBus` -- a directory of ``connector -> instance`` plus a pub/sub
  inbox per instance. Adding Redis is the only change needed to run many
  replicas; no application code moves.

The directory entry carries a TTL and is refreshed while the socket is alive, so
an instance that is killed uncleanly stops attracting traffic on its own rather
than blackholing every request until someone notices.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

logger = logging.getLogger(__name__)

#: Refreshed every ``REGISTRY_REFRESH_SECONDS``; if an instance dies its entries
#: expire within this window rather than pointing at a dead process forever.
REGISTRY_TTL_SECONDS = 90
REGISTRY_REFRESH_SECONDS = 30

RemoteHandler = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]
"""Called on the owning instance: (connector_id, job) -> serialised JobResult."""


class BusUnavailable(Exception):
    """The owning instance could not be reached."""


class JobBus(Protocol):
    """Routes a job to the instance holding the connector's socket."""

    @property
    def is_distributed(self) -> bool: ...

    async def start(self, handler: RemoteHandler) -> None: ...
    async def stop(self) -> None: ...
    async def register(self, connector_id: str) -> None: ...
    async def unregister(self, connector_id: str) -> None: ...
    async def locate(self, connector_id: str) -> str | None: ...
    async def forward(
        self, instance_id: str, connector_id: str, job: dict[str, Any], timeout: float
    ) -> dict[str, Any]: ...


class LocalBus:
    """Single-process routing: a connector is reachable only if it is local."""

    def __init__(self, instance_id: str) -> None:
        self.instance_id = instance_id
        self._local: set[str] = set()

    @property
    def is_distributed(self) -> bool:
        return False

    async def start(self, handler: RemoteHandler) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def register(self, connector_id: str) -> None:
        self._local.add(connector_id)

    async def unregister(self, connector_id: str) -> None:
        self._local.discard(connector_id)

    async def locate(self, connector_id: str) -> str | None:
        return self.instance_id if connector_id in self._local else None

    async def forward(
        self, instance_id: str, connector_id: str, job: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        raise BusUnavailable("local bus cannot forward to another instance")


class RedisBus:
    """Redis-backed directory and inbox.

    Pub/sub, not a work queue, and that is the right primitive here: a job is
    addressed to exactly one instance that holds a specific socket. A queue with
    competing consumers would deliver it to an instance that cannot serve it.

    A dropped message therefore means a lost job. Acceptable because the caller
    always has a timeout and a stale-snapshot fallback -- and the alternative,
    persistent per-instance queues, would leave undelivered work accumulating for
    instances that no longer exist.
    """

    def __init__(self, redis_url: str, instance_id: str) -> None:
        self.instance_id = instance_id
        self._url = redis_url
        self._redis: Any = None
        self._pubsub: Any = None
        self._handler: RemoteHandler | None = None
        self._listener: asyncio.Task[None] | None = None
        self._refresher: asyncio.Task[None] | None = None
        self._registered: set[str] = set()
        self._waiters: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._workers: set[asyncio.Task[None]] = set()

    @property
    def is_distributed(self) -> bool:
        return True

    @property
    def inbox(self) -> str:
        return f"tf:inbox:{self.instance_id}"

    @staticmethod
    def _directory_key(connector_id: str) -> str:
        return f"tf:conn:{connector_id}"

    async def start(self, handler: RemoteHandler) -> None:
        import redis.asyncio as aioredis

        self._handler = handler
        self._redis = aioredis.from_url(self._url, decode_responses=True)
        self._pubsub = self._redis.pubsub(ignore_subscribe_messages=True)
        await self._pubsub.subscribe(self.inbox)
        self._listener = asyncio.create_task(self._listen())
        self._refresher = asyncio.create_task(self._refresh_registrations())
        logger.info("job bus listening on %s", self.inbox)

    async def stop(self) -> None:
        for task in (self._listener, self._refresher):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        for task in list(self._workers):
            task.cancel()

        # Drop directory entries on the way out so a rolling deploy does not
        # leave traffic pointed at a process that is already gone.
        if self._redis is not None and self._registered:
            with contextlib.suppress(Exception):
                await self._redis.delete(
                    *[self._directory_key(cid) for cid in self._registered]
                )
        if self._pubsub is not None:
            with contextlib.suppress(Exception):
                await self._pubsub.aclose()
        if self._redis is not None:
            with contextlib.suppress(Exception):
                await self._redis.aclose()

    async def register(self, connector_id: str) -> None:
        self._registered.add(connector_id)
        await self._redis.set(
            self._directory_key(connector_id), self.instance_id, ex=REGISTRY_TTL_SECONDS
        )

    async def unregister(self, connector_id: str) -> None:
        self._registered.discard(connector_id)
        # Only clear the entry if it is still ours: the connector may already
        # have reconnected to a different instance, and deleting that instance's
        # claim would make a live connector unreachable.
        current = await self._redis.get(self._directory_key(connector_id))
        if current == self.instance_id:
            await self._redis.delete(self._directory_key(connector_id))

    async def locate(self, connector_id: str) -> str | None:
        return await self._redis.get(self._directory_key(connector_id))

    async def forward(
        self, instance_id: str, connector_id: str, job: dict[str, Any], timeout: float
    ) -> dict[str, Any]:
        correlation_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._waiters[correlation_id] = future

        try:
            delivered = await self._redis.publish(
                f"tf:inbox:{instance_id}",
                json.dumps(
                    {
                        "kind": "job",
                        "correlation_id": correlation_id,
                        "reply_to": self.instance_id,
                        "connector_id": connector_id,
                        "job": job,
                        "timeout": timeout,
                    }
                ),
            )
            if not delivered:
                # Nobody is subscribed: the owning instance died since the
                # directory lookup. Fail immediately instead of burning the
                # caller's full timeout waiting for a reply that cannot come.
                raise BusUnavailable(f"instance {instance_id} is not listening")

            return await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            raise BusUnavailable(f"instance {instance_id} did not reply in time") from exc
        finally:
            self._waiters.pop(correlation_id, None)

    # -- internals -------------------------------------------------------

    async def _listen(self) -> None:
        while True:
            try:
                async for raw in self._pubsub.listen():
                    if raw.get("type") != "message":
                        continue
                    try:
                        message = json.loads(raw["data"])
                    except (TypeError, ValueError):
                        logger.warning("discarding malformed bus frame")
                        continue
                    self._dispatch(message)

            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect on anything
                # Losing the subscription silently would strand every connector
                # on this instance, so keep retrying rather than exiting.
                logger.warning("bus listener error (%s); resubscribing", exc)
                await asyncio.sleep(1.0)
                with contextlib.suppress(Exception):
                    await self._pubsub.subscribe(self.inbox)

    def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message.get("kind")

        if kind == "reply":
            future = self._waiters.get(message.get("correlation_id", ""))
            if future is not None and not future.done():
                future.set_result(message.get("result", {}))
            return

        if kind == "job":
            task = asyncio.create_task(self._serve(message))
            self._workers.add(task)
            task.add_done_callback(self._workers.discard)

    async def _serve(self, message: dict[str, Any]) -> None:
        """Run a job another instance routed to us, and publish the result back."""
        if self._handler is None:
            return
        try:
            result = await self._handler(message["connector_id"], message["job"])
        except Exception as exc:  # noqa: BLE001 - always answer the caller
            logger.exception("failed serving forwarded job")
            result = {
                "type": "job_result",
                "job_id": message.get("job", {}).get("job_id", ""),
                "ok": False,
                "error": {
                    "code": "connector_error",
                    "message": f"{type(exc).__name__}: {exc}",
                    "user_message": "Something went wrong reading from Tally.",
                    "retryable": True,
                },
            }

        with contextlib.suppress(Exception):
            await self._redis.publish(
                f"tf:inbox:{message['reply_to']}",
                json.dumps(
                    {
                        "kind": "reply",
                        "correlation_id": message["correlation_id"],
                        "result": result,
                    }
                ),
            )

    async def _refresh_registrations(self) -> None:
        """Keep this instance's directory claims alive while its sockets are."""
        while True:
            await asyncio.sleep(REGISTRY_REFRESH_SECONDS)
            for connector_id in list(self._registered):
                with contextlib.suppress(Exception):
                    await self._redis.set(
                        self._directory_key(connector_id),
                        self.instance_id,
                        ex=REGISTRY_TTL_SECONDS,
                    )


def build_bus(redis_url: str | None, instance_id: str) -> JobBus:
    if redis_url:
        return RedisBus(redis_url, instance_id)
    logger.info("no TALLYFLOW_REDIS_URL set; running single-instance job routing")
    return LocalBus(instance_id)
