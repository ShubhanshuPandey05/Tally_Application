"""Runs job requests against Tally.

This is the whole of the connector's "business logic", and it is deliberately
thin: resolve a name in the registry, check the cache, call Tally, serialise.
Deciding *which* reads make up a dashboard belongs to the backend (CLAUDE.md:
"The connector should NOT contain business logic").
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from pydantic import BaseModel, ValidationError
from tally_core.tally import get_query
from tally_core.tally.errors import TallyError, UnknownQueryError

from .cache import ResponseCache, cache_key
from .pipeline import TallyPipeline
from .protocol import JobError, JobRequest, JobResult

logger = logging.getLogger(__name__)


def serialise(value: Any) -> Any:
    """Convert domain objects to JSON-safe structures.

    ``mode="json"`` matters: it renders ``Decimal`` as a string rather than a
    float, so a rupee amount survives the trip to the phone without picking up
    binary floating-point error.
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list | tuple):
        return [serialise(item) for item in value]
    if isinstance(value, dict):
        return {k: serialise(v) for k, v in value.items()}
    return value


class JobExecutor:
    """Executes :class:`JobRequest`s, with caching and single-flight.

    Nothing here reaches Tally directly. Every read goes through the
    :class:`~tally_connector.pipeline.TallyPipeline`, which is the only thing
    allowed to talk to the gateway and does so one request at a time.
    """

    def __init__(self, pipeline: TallyPipeline, cache: ResponseCache | None = None) -> None:
        self._pipeline = pipeline
        self._cache = cache or ResponseCache()
        #: Identical concurrent jobs share one Tally round trip.
        self._in_flight: dict[str, asyncio.Task[Any]] = {}

    @property
    def cache(self) -> ResponseCache:
        return self._cache

    @property
    def pipeline(self) -> TallyPipeline:
        return self._pipeline

    async def run(self, job: JobRequest) -> JobResult:
        """Execute a job, never raising -- failures come back as a JobResult.

        The session loop must keep serving other jobs no matter what one query
        does, so every error path terminates here.
        """
        started = time.monotonic()

        def elapsed_ms() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            query = get_query(job.query)
        except UnknownQueryError as exc:
            return JobResult.failure(job.job_id, _job_error(exc), duration_ms=elapsed_ms())

        try:
            params = query.validate_params(job.params)
        except ValidationError as exc:
            return JobResult.failure(
                job.job_id,
                JobError(
                    code="invalid_params",
                    message=f"invalid params for {job.query!r}: {exc.error_count()} problem(s)",
                    user_message="That request wasn't understood. Please update the app.",
                    retryable=False,
                ),
                duration_ms=elapsed_ms(),
            )

        key = cache_key(job.query, job.params)

        if job.cache_ttl_seconds > 0:
            cached = await self._cache.get(key)
            if cached is not None:
                logger.debug("cache hit for %s", key)
                return JobResult.success(
                    job.job_id, cached, duration_ms=elapsed_ms(), from_cache=True
                )

        try:
            data = await asyncio.wait_for(
                self._execute_once(key, query, params, job),
                timeout=job.deadline_seconds,
            )
            return JobResult.success(job.job_id, data, duration_ms=elapsed_ms())

        except TimeoutError:
            return await self._fallback(
                job,
                key,
                JobError(
                    code="deadline_exceeded",
                    message=f"job {job.job_id} exceeded {job.deadline_seconds}s",
                    user_message="TallyPrime is taking too long to respond.",
                    retryable=True,
                ),
                elapsed_ms(),
            )

        except TallyError as exc:
            logger.warning("job %s failed: %s", job.job_id, exc)
            return await self._fallback(job, key, _job_error(exc), elapsed_ms())

        except Exception as exc:  # noqa: BLE001 - the loop must survive anything
            logger.exception("unexpected failure running job %s", job.job_id)
            return JobResult.failure(
                job.job_id,
                JobError(
                    code="connector_error",
                    message=f"{type(exc).__name__}: {exc}",
                    user_message="Something went wrong on the Tally Connector.",
                    retryable=True,
                ),
                duration_ms=elapsed_ms(),
            )

    async def _execute_once(
        self,
        key: str,
        query: Any,
        params: BaseModel,
        job: JobRequest,
    ) -> Any:
        """Run the query, collapsing duplicate concurrent calls onto one task."""
        existing = self._in_flight.get(key)
        if existing is not None:
            logger.debug("joining in-flight request for %s", key)
            # Shielded: this waiter's deadline must not cancel the shared task
            # out from under the other waiters.
            return await asyncio.shield(existing)

        task = asyncio.create_task(self._call_tally(key, query, params, job))
        self._in_flight[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if self._in_flight.get(key) is task and task.done():
                del self._in_flight[key]

    async def _call_tally(self, key: str, query: Any, params: BaseModel, job: JobRequest) -> Any:
        # The deadline is handed to the pipeline rather than applied only here,
        # so a job whose caller has already given up is dropped *before* it
        # reaches Tally instead of after -- queue time is the expensive part.
        result = await self._pipeline.execute(
            query, params, deadline_seconds=job.deadline_seconds
        )
        data = serialise(result)
        # Always stored, even at ttl=0: a forced refresh should still leave
        # something behind for the offline path to fall back on.
        await self._cache.set(key, data, job.cache_ttl_seconds)
        self._in_flight.pop(key, None)
        return data

    async def _fallback(
        self,
        job: JobRequest,
        key: str,
        error: JobError,
        duration_ms: int,
    ) -> JobResult:
        """Serve stale data when Tally cannot be reached.

        Only for retryable errors: a rejected or malformed request will fail the
        same way next time, and answering it with old data would hide a real bug.
        """
        if not error.retryable:
            return JobResult.failure(job.job_id, error, duration_ms=duration_ms)

        entry = await self._cache.get_stale(key)
        if entry is None:
            return JobResult.failure(job.job_id, error, duration_ms=duration_ms)

        logger.info(
            "serving stale cache for job %s (age %.0fs) after %s",
            job.job_id,
            entry.age_seconds,
            error.code,
        )
        return JobResult.success(
            job.job_id, entry.value, duration_ms=duration_ms, from_cache=True
        )


def _job_error(exc: TallyError) -> JobError:
    return JobError(
        code=exc.code,
        message=str(exc),
        user_message=exc.user_message,
        retryable=exc.retryable,
    )
