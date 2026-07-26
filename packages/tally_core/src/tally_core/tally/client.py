"""HTTP transport to a local TallyPrime instance.

This is the only module in ``tally_core`` that performs I/O. It runs inside the
connector, on the customer's own machine, and always talks to loopback -- Tally
is never exposed to the network (CLAUDE.md: "Never expose Tally directly to the
internet").
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, TypeVar
from xml.etree import ElementTree as ET

import httpx
from pydantic import BaseModel

from .codec import parse_xml
from .errors import (
    TallyError,
    TallyParseError,
    TallyResponseError,
    TallyTimeoutError,
    TallyUnreachableError,
)
from .query import TallyQuery

logger = logging.getLogger(__name__)

ResultT = TypeVar("ResultT")


@dataclass(frozen=True)
class TallyConfig:
    """Connection settings for the local Tally gateway."""

    host: str = "127.0.0.1"
    port: int = 9000
    #: Generous by default: a full-year day book export on a slow desktop can
    #: legitimately take minutes, and a spurious timeout looks like data loss.
    timeout_seconds: float = 60.0
    heavy_timeout_seconds: float = 300.0
    max_attempts: int = 3
    retry_backoff_seconds: float = 1.5

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"


class TallyClient:
    """Executes registered queries against Tally.

    Tally's HTTP gateway is strictly single-threaded: concurrent POSTs are not
    merely slow, they can return one request's body to another's connection. An
    internal lock serialises every call, so callers may share one client freely.
    """

    def __init__(
        self,
        config: TallyConfig | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config or TallyConfig()
        self._client = client
        self._owns_client = client is None
        self._lock = asyncio.Lock()

    @property
    def config(self) -> TallyConfig:
        return self._config

    async def __aenter__(self) -> TallyClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers={"Content-Type": "text/xml; charset=utf-8"}
            )
        return self._client

    async def is_alive(self) -> bool:
        """Cheap liveness probe used by the connector heartbeat."""
        try:
            client = self._ensure_client()
            async with self._lock:
                response = await client.get(self._config.url, timeout=5.0)
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    async def execute(
        self,
        query: TallyQuery[Any, ResultT],
        params: BaseModel,
    ) -> ResultT:
        """Build, send, parse and map a query in one call."""
        envelope = query.build(params)  # type: ignore[arg-type]
        timeout = (
            self._config.heavy_timeout_seconds
            if query.heavy
            else self._config.timeout_seconds
        )
        root = await self.post_xml(
            envelope,
            timeout=timeout,
            label=query.name,
            # A heavy export that ran out of time was not a blip: Tally is busy,
            # wedged, or showing a dialog. Retrying twice more costs the shop ten
            # minutes of a gateway that serves one caller at a time, and the
            # connector has a stale snapshot to fall back on regardless.
            retry_on_timeout=not query.heavy,
        )
        try:
            return query.parse(root, params)  # type: ignore[arg-type]
        except TallyError:
            raise
        except Exception as exc:
            raise TallyParseError(
                f"failed to map response for query {query.name!r}: {exc}"
            ) from exc

    async def post_xml(
        self,
        envelope: str,
        *,
        timeout: float | None = None,
        label: str = "raw",
        retry_on_timeout: bool = True,
    ) -> ET.Element:
        """POST a prepared envelope, retrying transport-level failures."""
        client = self._ensure_client()
        effective_timeout = timeout or self._config.timeout_seconds
        last_error: TallyError | None = None
        retryable = True

        for attempt in range(1, self._config.max_attempts + 1):
            try:
                async with self._lock:
                    response = await client.post(
                        self._config.url,
                        content=envelope.encode("utf-8"),
                        timeout=effective_timeout,
                    )
                response.raise_for_status()
                return parse_xml(response.content)

            except httpx.TimeoutException:
                last_error = TallyTimeoutError(
                    f"query {label!r} timed out after {effective_timeout}s"
                )
                retryable = retry_on_timeout
            except httpx.ConnectError as exc:
                last_error = TallyUnreachableError(
                    f"cannot connect to Tally at {self._config.url}: {exc}"
                )
            except httpx.HTTPStatusError as exc:
                # Tally answers 4xx/5xx for malformed envelopes. Retrying an
                # envelope Tally already rejected just wastes the user's time.
                raise TallyResponseError(
                    f"Tally returned HTTP {exc.response.status_code} for query {label!r}"
                ) from exc
            except httpx.HTTPError as exc:
                last_error = TallyUnreachableError(f"transport error for {label!r}: {exc}")
            except (TallyResponseError, TallyParseError):
                raise

            if attempt < self._config.max_attempts and last_error.retryable and retryable:
                delay = self._config.retry_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "tally query %s failed (attempt %d/%d): %s; retrying in %.1fs",
                    label,
                    attempt,
                    self._config.max_attempts,
                    last_error,
                    delay,
                )
                await asyncio.sleep(delay)
            else:
                break

        assert last_error is not None
        raise last_error
