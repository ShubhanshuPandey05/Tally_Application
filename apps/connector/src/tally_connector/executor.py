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
from tally_core.tally import get_mutation, get_query
from tally_core.tally.errors import (
    CompanyNotLoadedError,
    TallyBusyError,
    TallyError,
    TallyResponseError,
    TallyUnreachableError,
    UnknownQueryError,
)

from .cache import ResponseCache, cache_key
from .loaded import LoadedCompanies
from .pipeline import TallyPipeline
from .protocol import JobError, JobRequest, JobResult, MutationRequest

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

    def __init__(
        self,
        pipeline: TallyPipeline,
        cache: ResponseCache | None = None,
        loaded: LoadedCompanies | None = None,
        voucher_entry_mode: str = "optional",
    ) -> None:
        self._pipeline = pipeline
        #: How entries sent from a phone arrive in Tally. Read from this
        #: machine's own settings rather than taken from the request, so the
        #: computer next to the till decides it and no client can talk its way
        #: past the choice.
        self._voucher_entry_mode = voucher_entry_mode
        self._cache = cache or ResponseCache()
        #: Refuses reads for a company TallyPrime does not have open. Without
        #: it those reads come back empty and are indistinguishable from a
        #: company with no data -- see :mod:`tally_connector.loaded`.
        self._loaded = loaded or LoadedCompanies(pipeline)
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
        # Before the request, not after: a voucher read for a company that is
        # not open crashes TallyPrime outright, so there is no reply left to
        # inspect. Every other read would come back empty and be reported as a
        # successful read of a company with no sales.
        await self._loaded.ensure(getattr(params, "company", ""))

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

    @property
    def voucher_entry_mode(self) -> str:
        return self._voucher_entry_mode

    def set_voucher_entry_mode(self, mode: str) -> None:
        """Applied to the next write, without restarting anything."""
        self._voucher_entry_mode = mode

    async def write(self, request: MutationRequest) -> JobResult:
        """Execute one write, never raising -- failures come back as a JobResult.

        Structurally similar to :meth:`run` and deliberately separate from it.
        Three things a read does are *absent* here, and each one is a way a
        voucher could otherwise be booked twice or silently:

        * **No cache lookup and no cache write.** There is no answer to reuse,
          and an entry keyed like a read would let a retry report the first
          attempt's receipt as though it were its own.
        * **No single-flight.** Two identical reads are one read; two identical
          receipts are two receipts, and collapsing them would quietly drop a
          real second payment from the same customer for the same amount.
        * **No stale fallback.** There is nothing older to serve, and answering
          a failed write out of the cache would report a success that never
          happened.
        """
        started = time.monotonic()

        def elapsed_ms() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            mutation = get_mutation(request.mutation)
        except UnknownQueryError as exc:
            return JobResult.failure(
                request.job_id, _job_error(exc), duration_ms=elapsed_ms()
            )

        # This machine's policy, layered on before validation so it is part of
        # what gets checked rather than something applied afterwards.
        params_raw = dict(request.params)
        params_raw["force_optional"] = self._voucher_entry_mode == "optional"

        try:
            params = mutation.validate_params(params_raw)
        except ValidationError as exc:
            return JobResult.failure(
                request.job_id,
                JobError(
                    code="invalid_params",
                    message=(
                        f"invalid params for {request.mutation!r}: "
                        f"{exc.error_count()} problem(s)"
                    ),
                    user_message="That entry wasn't understood. Please update the app.",
                    retryable=False,
                ),
                duration_ms=elapsed_ms(),
            )

        try:
            envelope = mutation.build(params)
        except ValueError as exc:
            # An entry that does not balance, refused before it is queued: it
            # would fail the same way after waiting behind a five-minute
            # export, and the person gets told what is wrong with it instead.
            return JobResult.failure(
                request.job_id,
                JobError(
                    code="unbalanced_voucher",
                    message=str(exc),
                    user_message="That entry does not balance, so it was not saved.",
                    retryable=False,
                ),
                duration_ms=elapsed_ms(),
            )

        try:
            posted = await asyncio.wait_for(
                self._call_tally_write(mutation, envelope, params, request),
                timeout=request.deadline_seconds,
            )
            return JobResult.success(
                request.job_id, serialise(posted), duration_ms=elapsed_ms()
            )

        except TimeoutError:
            # Emphatically not retryable, and the message says so in words the
            # person can act on. Tally can finish an import and lose the reply,
            # so "it may already be saved" is the truth -- telling them to try
            # again is how a receipt gets entered twice.
            logger.warning("write %s passed its deadline", request.job_id)
            return JobResult.failure(
                request.job_id,
                JobError(
                    code="write_deadline_exceeded",
                    message=f"write {request.job_id} exceeded {request.deadline_seconds}s",
                    user_message=(
                        "TallyPrime did not answer in time. The entry may still "
                        "have been saved - check the Day Book in TallyPrime "
                        "before entering it again."
                    ),
                    retryable=False,
                ),
                duration_ms=elapsed_ms(),
            )

        except TallyError as exc:
            logger.warning("write %s failed: %s", request.job_id, exc)
            # Tally's own words are the most useful thing we have, and they
            # arrive here as the message. What is recomputed is whether trying
            # again is safe -- see `_nothing_was_sent`.
            error = _job_error(exc).model_copy(
                update={"retryable": _nothing_was_sent(exc)}
            )
            if isinstance(exc, TallyResponseError):
                # Tally's own sentence, shown as-is. "Ledger 'Ram Traders' does
                # not exist!" tells somebody what to do next; the generic
                # response message is written for a failed *report* and says
                # nothing useful about an entry that was refused.
                error = error.model_copy(update={"user_message": str(exc)})
            return JobResult.failure(
                request.job_id, error, duration_ms=elapsed_ms()
            )

        except Exception as exc:  # noqa: BLE001 - the loop must survive anything
            logger.exception("unexpected failure writing %s", request.job_id)
            return JobResult.failure(
                request.job_id,
                JobError(
                    code="connector_error",
                    message=f"{type(exc).__name__}: {exc}",
                    user_message="Something went wrong on the Tally Connector.",
                    retryable=False,
                ),
                duration_ms=elapsed_ms(),
            )

    async def _call_tally_write(
        self,
        mutation: Any,
        envelope: str,
        params: BaseModel,
        request: MutationRequest,
    ) -> Any:
        # Same guard as a read, and it matters more here. A company that is not
        # open in Tally answers an import with a cheerful "created 0" rather
        # than an error, so without this the phone is told the entry was refused
        # for no stated reason when the real answer is "open the company".
        await self._loaded.ensure(getattr(params, "company", ""))

        # Through the same single-worker pipeline as every read, so a write
        # queues behind whatever Tally is doing instead of interrupting the
        # till. `submit` rather than `execute`, because that one takes a query.
        return await self._pipeline.submit(
            lambda: self._write_once(mutation, envelope, params),
            label=mutation.name,
            deadline_seconds=request.deadline_seconds,
        )

    async def _write_once(self, mutation: Any, envelope: str, params: BaseModel) -> Any:
        root = await self._pipeline.tally.post_xml(
            envelope,
            label=mutation.name,
            # The whole reason a mutation carries this flag. A retried import is
            # a duplicated voucher, and Tally offers no way to tell afterwards
            # which of the two attempts landed.
            retry_on_timeout=mutation.retry_on_timeout,
        )
        return mutation.parse(root, params)

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



#: Failures that prove the import never reached TallyPrime.
#:
#: This is the whole retry question for a write, and it is a question about
#: *evidence*, not about how annoying the failure was. Retrying is safe exactly
#: when we can show nothing was written; everywhere else the honest answer is
#: "go and look", because Tally can finish an import and lose the reply and
#: nothing afterwards distinguishes that from a write that never arrived.
#:
#: Each of these is refused before or instead of a POST:
#:
#: * :class:`CompanyNotLoadedError` -- the loaded-company guard stopped it, so
#:   no envelope was built into a request at all.
#: * :class:`TallyUnreachableError` -- the TCP connection was never
#:   established, so no bytes left this machine.
#: * :class:`TallyBusyError` -- the pipeline refused to queue it.
#:
#: Deliberately *not* here: a timeout and :class:`TallyCrashedError`. Both mean
#: the request was on the wire when things went wrong, which is precisely the
#: ambiguous case. A response error is also absent, for a different reason --
#: nothing was written, but the same envelope will be refused the same way, so
#: inviting a retry would send somebody round a loop.
_SENT_NOTHING = (CompanyNotLoadedError, TallyUnreachableError, TallyBusyError)


def _nothing_was_sent(exc: TallyError) -> bool:
    return isinstance(exc, _SENT_NOTHING)


def _job_error(exc: TallyError) -> JobError:
    return JobError(
        code=exc.code,
        message=str(exc),
        user_message=exc.user_message,
        retryable=exc.retryable,
    )
