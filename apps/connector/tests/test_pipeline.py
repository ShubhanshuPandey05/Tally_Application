"""Pipeline tests.

Tally's gateway serves one request at a time and can be wedged by a second one
arriving mid-export, so "never two at once" is a correctness property here, not
a performance tuning knob. Most of these tests are about what happens to work
that queued up behind a slow read -- which is where the connector used to lose
the day book, and with it every sales figure on the dashboard.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from tally_core.tally.errors import (
    TallyBusyError,
    TallyResponseError,
    TallyTimeoutError,
    TallyUnreachableError,
)

from tally_connector.protocol import JobRequest


@pytest.fixture
def pipeline(make_pipeline, ok_handler):
    return make_pipeline(ok_handler)


def sleeper(seconds: float, *, log: list[str] | None = None, name: str = "x"):
    """A unit of pipeline work that takes time and records that it ran."""

    async def run():
        if log is not None:
            log.append(name)
        await asyncio.sleep(seconds)
        return name

    return run


# --------------------------------------------------------------------------
# Serialisation
# --------------------------------------------------------------------------


async def test_requests_reach_tally_one_at_a_time(pipeline):
    concurrent = 0
    peak = 0

    async def run():
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.02)
        concurrent -= 1
        return True

    await asyncio.gather(
        *(pipeline.submit(run, label=f"q{i}", deadline_seconds=10) for i in range(6))
    )

    assert peak == 1


async def test_queue_is_first_in_first_out(pipeline):
    order: list[str] = []
    await asyncio.gather(
        *(
            pipeline.submit(
                sleeper(0.01, log=order, name=name), label=name, deadline_seconds=10
            )
            for name in ("a", "b", "c", "d")
        )
    )

    assert order == ["a", "b", "c", "d"]


async def test_results_go_back_to_the_right_caller(pipeline):
    async def run(value):
        await asyncio.sleep(0.01)
        return value

    results = await asyncio.gather(
        *(
            pipeline.submit(
                lambda v=i: run(v), label=f"q{i}", deadline_seconds=10
            )
            for i in range(5)
        )
    )

    assert results == [0, 1, 2, 3, 4]


# --------------------------------------------------------------------------
# Deadlines -- the reason this module exists
# --------------------------------------------------------------------------


async def test_expired_work_is_never_sent_to_tally(pipeline):
    """The bug this fixes: five dashboard reads queue, the phone gives up, and
    Tally is *still* asked to produce all five, one after another."""
    ran: list[str] = []

    slow = asyncio.create_task(
        pipeline.submit(sleeper(0.3, log=ran, name="slow"), label="slow", deadline_seconds=10)
    )
    await asyncio.sleep(0.01)  # let it take the wire

    with pytest.raises(TallyTimeoutError):
        await pipeline.submit(
            sleeper(0.01, log=ran, name="doomed"), label="doomed", deadline_seconds=0.05
        )

    await slow
    assert ran == ["slow"]


async def test_the_deadline_covers_time_spent_queueing(pipeline):
    started = asyncio.get_running_loop().time()

    slow = asyncio.create_task(
        pipeline.submit(sleeper(0.3), label="slow", deadline_seconds=10)
    )
    await asyncio.sleep(0.01)

    with pytest.raises(TallyTimeoutError):
        await pipeline.submit(sleeper(0.01), label="doomed", deadline_seconds=0.05)

    # It failed while waiting, not after the slow request finished.
    assert asyncio.get_running_loop().time() - started < 0.25
    await slow


async def test_a_caller_that_walks_away_does_not_spend_tally(pipeline):
    ran: list[str] = []

    slow = asyncio.create_task(
        pipeline.submit(sleeper(0.2, log=ran, name="slow"), label="slow", deadline_seconds=10)
    )
    await asyncio.sleep(0.01)

    abandoned = asyncio.create_task(
        pipeline.submit(sleeper(0.01, log=ran, name="gone"), label="gone", deadline_seconds=10)
    )
    await asyncio.sleep(0.01)
    abandoned.cancel()

    await slow
    await asyncio.sleep(0.05)
    assert ran == ["slow"]
    assert pipeline.stats()["abandoned"] == 1


# --------------------------------------------------------------------------
# Backpressure
# --------------------------------------------------------------------------


async def test_a_full_queue_is_refused_immediately(make_pipeline, ok_handler):
    pipeline = make_pipeline(ok_handler, max_queue_depth=2)

    running = asyncio.create_task(
        pipeline.submit(sleeper(0.3), label="running", deadline_seconds=10)
    )
    await asyncio.sleep(0.01)

    queued = [
        asyncio.create_task(pipeline.submit(sleeper(0.01), label=f"q{i}", deadline_seconds=10))
        for i in range(2)
    ]
    await asyncio.sleep(0.01)

    with pytest.raises(TallyBusyError):
        await pipeline.submit(sleeper(0.01), label="overflow", deadline_seconds=10)

    assert pipeline.stats()["rejected"] == 1
    await asyncio.gather(running, *queued)


async def test_busy_is_retryable_so_the_caller_can_fall_back_to_a_snapshot():
    assert TallyBusyError("full").retryable is True


# --------------------------------------------------------------------------
# Cooldown
# --------------------------------------------------------------------------


async def test_a_timeout_pauses_the_pipeline(make_pipeline, ok_handler):
    pipeline = make_pipeline(ok_handler, cooldown_seconds=0.2)

    async def times_out():
        raise TallyTimeoutError("Tally did not answer")

    with pytest.raises(TallyTimeoutError):
        await pipeline.submit(times_out, label="slow", deadline_seconds=10)

    started = asyncio.get_running_loop().time()
    await pipeline.submit(sleeper(0), label="next", deadline_seconds=10)

    assert asyncio.get_running_loop().time() - started >= 0.15


async def test_a_rejected_envelope_does_not_pause_the_pipeline(make_pipeline, ok_handler):
    """Tally answering "no" proves it is healthy; only silence is a symptom."""
    pipeline = make_pipeline(ok_handler, cooldown_seconds=5.0)

    async def rejected():
        raise TallyResponseError("bad envelope")

    with pytest.raises(TallyResponseError):
        await pipeline.submit(rejected, label="bad", deadline_seconds=10)

    started = asyncio.get_running_loop().time()
    await pipeline.submit(sleeper(0), label="next", deadline_seconds=10)

    assert asyncio.get_running_loop().time() - started < 0.5


async def test_work_that_cannot_outlast_the_cooldown_fails_fast(make_pipeline, ok_handler):
    pipeline = make_pipeline(ok_handler, cooldown_seconds=5.0)
    ran: list[str] = []

    async def unreachable():
        raise TallyUnreachableError("connection refused")

    with pytest.raises(TallyUnreachableError):
        await pipeline.submit(unreachable, label="probe", deadline_seconds=10)

    with pytest.raises(TallyTimeoutError):
        await pipeline.submit(
            sleeper(0, log=ran, name="short"), label="short", deadline_seconds=0.1
        )

    assert ran == []


# --------------------------------------------------------------------------
# Liveness
# --------------------------------------------------------------------------


async def test_liveness_never_queues_behind_a_running_export(make_pipeline):
    """The heartbeat used to block here, which took the whole session down."""
    probes = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal probes
        probes += 1
        return httpx.Response(200, text="ok")

    pipeline = make_pipeline(handler)
    export = asyncio.create_task(
        pipeline.submit(sleeper(0.3), label="vouchers.list", deadline_seconds=10)
    )
    await asyncio.sleep(0.01)

    started = asyncio.get_running_loop().time()
    assert await pipeline.is_alive() is True

    assert asyncio.get_running_loop().time() - started < 0.05
    assert probes == 0
    await export


async def test_a_recent_success_stands_in_for_a_probe(make_pipeline):
    probes = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal probes
        probes += 1
        return httpx.Response(200, text="ok")

    pipeline = make_pipeline(handler, liveness_ttl_seconds=30.0)
    await pipeline.submit(sleeper(0), label="work", deadline_seconds=10)

    assert await pipeline.is_alive() is True
    assert probes == 0


async def test_an_idle_pipeline_probes_when_the_observation_is_stale(make_pipeline):
    probes = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal probes
        probes += 1
        return httpx.Response(200, text="ok")

    pipeline = make_pipeline(handler, liveness_ttl_seconds=0.0)

    assert await pipeline.is_alive() is True
    assert probes == 1


async def test_liveness_reports_false_when_tally_is_gone(make_pipeline):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    pipeline = make_pipeline(handler, liveness_ttl_seconds=0.0)
    assert await pipeline.is_alive() is False


async def test_a_fresh_failure_outranks_a_busy_queue(make_pipeline, ok_handler):
    """Work can be queueing up precisely *because* every request is failing."""
    pipeline = make_pipeline(ok_handler, liveness_ttl_seconds=30.0)

    async def unreachable():
        raise TallyUnreachableError("connection refused")

    with pytest.raises(TallyUnreachableError):
        await pipeline.submit(unreachable, label="doomed", deadline_seconds=10)

    queued = asyncio.create_task(
        pipeline.submit(sleeper(0.2), label="next", deadline_seconds=10)
    )
    await asyncio.sleep(0.01)

    assert await pipeline.is_alive() is False
    await queued


# --------------------------------------------------------------------------
# Resilience
# --------------------------------------------------------------------------


async def test_one_bad_ticket_does_not_kill_the_worker(pipeline):
    async def explodes():
        raise RuntimeError("boom")

    with pytest.raises(Exception):  # noqa: B017 - any failure is acceptable here
        await pipeline.submit(explodes, label="bad", deadline_seconds=10)

    assert await pipeline.submit(sleeper(0), label="good", deadline_seconds=10) == "x"


async def test_closing_fails_whatever_is_still_queued(pipeline):
    running = asyncio.create_task(
        pipeline.submit(sleeper(0.2), label="running", deadline_seconds=10)
    )
    await asyncio.sleep(0.01)
    queued = asyncio.create_task(
        pipeline.submit(sleeper(0.01), label="queued", deadline_seconds=10)
    )
    await asyncio.sleep(0.01)

    await pipeline.aclose()

    with pytest.raises(TallyBusyError):
        await queued
    running.cancel()


async def test_submitting_after_close_is_refused(pipeline):
    await pipeline.aclose()
    with pytest.raises(TallyBusyError):
        await pipeline.submit(sleeper(0), label="late", deadline_seconds=10)


# --------------------------------------------------------------------------
# Through the executor
# --------------------------------------------------------------------------


async def test_executor_reads_are_serialised(make_executor, companies_xml):
    """Whatever the backend throws at it, Tally sees one request at a time."""
    concurrent = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.02)
        concurrent -= 1
        return httpx.Response(200, text=companies_xml)

    executor = make_executor(handler)
    jobs = [
        JobRequest(job_id=f"j{i}", query="ledgers.list", params={"company": f"Co {i}"})
        for i in range(5)
    ]
    results = await asyncio.gather(*(executor.run(j) for j in jobs))

    assert peak == 1
    assert all(r.ok for r in results)
