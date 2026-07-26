"""Hub, link and routing behaviour under failure and load.

Most of these describe things that only happen at scale or during an outage:
sockets that die with jobs in flight, connectors that never answer, requests
arriving faster than a single-threaded Tally can serve them. They are the
failure modes that turn into "the app is broken" support calls.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from tally_core.protocol import JobResult, Ping, Pong

from tally_backend.config import Settings
from tally_backend.hub import ConnectorHub, LinkClosed, LocalBus
from tally_backend.hub.link import ConnectorLink


class FakeSocket:
    """Records what was sent and lets a test inject replies."""

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False
        self.close_code: int | None = None

    async def send_text(self, data: str) -> None:
        if self.closed:
            raise RuntimeError("socket is closed")
        self.sent.append(json.loads(data))

    async def receive_text(self) -> str:
        await asyncio.sleep(3600)
        raise AssertionError("unreachable")

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True
        self.close_code = code


def make_link(**kwargs) -> tuple[ConnectorLink, FakeSocket]:
    socket = FakeSocket()
    link = ConnectorLink(
        connector_id="conn-1",
        org_id="org-1",
        socket=socket,
        max_concurrent_jobs=kwargs.pop("max_concurrent_jobs", 2),
        heartbeat_interval_seconds=kwargs.pop("heartbeat_interval_seconds", 30.0),
        heartbeat_grace_seconds=kwargs.pop("heartbeat_grace_seconds", 90.0),
        **kwargs,
    )
    return link, socket


async def answer(link: ConnectorLink, socket: FakeSocket, payload) -> None:
    """Reply to the most recent job as the connector would."""
    for _ in range(100):
        if socket.sent:
            break
        await asyncio.sleep(0.01)
    job_id = socket.sent[-1]["job_id"]
    await link.handle_message(
        json.loads(JobResult.success(job_id, payload).model_dump_json())
    )


# --------------------------------------------------------------------------
# Correlation
# --------------------------------------------------------------------------


async def test_a_job_result_is_matched_to_its_request() -> None:
    link, socket = make_link()

    task = asyncio.create_task(
        link.run_job(query="ledgers.list", params={}, timeout_seconds=5)
    )
    await answer(link, socket, [{"name": "Cash"}])
    result = await task

    assert result.ok
    assert result.data() == [{"name": "Cash"}]


async def test_out_of_order_results_go_to_the_right_waiters() -> None:
    """Two jobs in flight, answered backwards, must not swap answers."""
    link, socket = make_link(max_concurrent_jobs=2)

    first = asyncio.create_task(
        link.run_job(query="ledgers.list", params={}, timeout_seconds=5, job_id="A")
    )
    second = asyncio.create_task(
        link.run_job(query="stock_items.list", params={}, timeout_seconds=5, job_id="B")
    )
    await asyncio.sleep(0.05)

    await link.handle_message(json.loads(JobResult.success("B", "second").model_dump_json()))
    await link.handle_message(json.loads(JobResult.success("A", "first").model_dump_json()))

    assert (await first).data() == "first"
    assert (await second).data() == "second"


async def test_a_result_for_an_abandoned_job_is_discarded_quietly() -> None:
    link, _ = make_link()
    # No waiter for this id; must not raise.
    await link.handle_message(
        json.loads(JobResult.success("never-asked", "x").model_dump_json())
    )


# --------------------------------------------------------------------------
# Failure modes
# --------------------------------------------------------------------------


async def test_closing_the_link_fails_every_pending_job() -> None:
    """A leaked future holds a request handler open until its timeout.

    Across a fleet during an internet outage, that is how a backend runs out of
    workers -- so closing must resolve every waiter immediately.
    """
    link, _ = make_link(max_concurrent_jobs=4)

    jobs = [
        asyncio.create_task(
            link.run_job(query="ledgers.list", params={"i": i}, timeout_seconds=30)
        )
        for i in range(4)
    ]
    await asyncio.sleep(0.05)
    assert link.in_flight == 4

    await link.close(reason="connector disconnected")
    results = await asyncio.gather(*jobs)

    assert all(not r.ok for r in results)
    assert all(r.error.code == "connector_offline" for r in results)
    assert link.in_flight == 0


async def test_a_job_that_is_never_answered_times_out() -> None:
    link, _ = make_link()
    result = await link.run_job(query="ledgers.list", params={}, timeout_seconds=0.1)

    assert not result.ok
    assert result.error.code == "connector_timeout"
    assert result.error.retryable is True


async def test_the_deadline_sent_to_the_connector_shrinks_with_queueing() -> None:
    """A job that waited must not get a fresh full timeout on Tally."""
    link, socket = make_link(max_concurrent_jobs=1)

    first = asyncio.create_task(
        link.run_job(query="a", params={}, timeout_seconds=5, job_id="A")
    )
    await asyncio.sleep(0.05)

    queued = asyncio.create_task(
        link.run_job(query="b", params={}, timeout_seconds=5, job_id="B")
    )
    await asyncio.sleep(0.2)

    await link.handle_message(json.loads(JobResult.success("A", "done").model_dump_json()))
    await first
    await asyncio.sleep(0.05)

    sent_b = next(m for m in socket.sent if m["job_id"] == "B")
    assert sent_b["deadline_seconds"] < 5.0

    await link.handle_message(json.loads(JobResult.success("B", "done").model_dump_json()))
    await queued


async def test_concurrency_is_capped_per_connector() -> None:
    """TallyPrime serves one request at a time; flooding it helps nobody."""
    link, socket = make_link(max_concurrent_jobs=2)

    jobs = [
        asyncio.create_task(
            link.run_job(query="q", params={"i": i}, timeout_seconds=5, job_id=str(i))
        )
        for i in range(5)
    ]
    await asyncio.sleep(0.1)

    assert len(socket.sent) == 2, "more jobs were dispatched than the connector allows"

    for i in range(5):
        await link.handle_message(
            json.loads(JobResult.success(str(i), "ok").model_dump_json())
        )
        await asyncio.sleep(0.02)

    assert all(r.ok for r in await asyncio.gather(*jobs))


async def test_a_saturated_connector_reports_busy_rather_than_hanging() -> None:
    link, _ = make_link(max_concurrent_jobs=1)

    blocker = asyncio.create_task(
        link.run_job(query="slow", params={}, timeout_seconds=5, job_id="blocker")
    )
    await asyncio.sleep(0.05)

    result = await link.run_job(query="q", params={}, timeout_seconds=0.1)
    assert not result.ok
    assert result.error.code == "connector_busy"

    await link.handle_message(
        json.loads(JobResult.success("blocker", "ok").model_dump_json())
    )
    await blocker


async def test_sending_on_a_closed_link_raises_link_closed() -> None:
    link, _ = make_link()
    await link.close()
    with pytest.raises(LinkClosed):
        await link.send(Ping(token="x"))


# --------------------------------------------------------------------------
# Status
# --------------------------------------------------------------------------


async def test_status_changes_fire_the_callback_only_on_transitions() -> None:
    """Persisting on every heartbeat would be the busiest query in the system."""
    changes: list[bool] = []

    async def on_change(link: ConnectorLink) -> None:
        changes.append(link.tally_online)

    link, _ = make_link(on_status_change=on_change)

    for online in (True, True, True, False, False, True):
        await link.handle_message(
            json.loads(Pong(token="t", tally_online=online).model_dump_json())
        )

    assert changes == [True, False, True]


async def test_unknown_message_types_are_ignored() -> None:
    """A newer connector must not be able to crash an older backend."""
    link, _ = make_link()
    await link.handle_message({"type": "something_from_the_future", "data": 1})
    assert not link.is_closed


# --------------------------------------------------------------------------
# Hub routing
# --------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return Settings(
        jwt_secret="hub-test-secret-long-enough-for-validation",
        secret_keys=["k"],
        database_url="sqlite+aiosqlite:///:memory:",
    )


async def test_an_unknown_connector_fails_fast(settings: Settings) -> None:
    hub = ConnectorHub(settings, bus=LocalBus("instance-1"))
    result = await hub.run(connector_id="nobody", query="ledgers.list", params={})

    assert not result.ok
    assert result.error.code == "connector_offline"


async def test_reconnecting_replaces_the_previous_session(settings: Settings) -> None:
    """A slept laptop leaves a half-open socket; the fresh one must win."""
    hub = ConnectorHub(settings, bus=LocalBus("instance-1"))
    old, old_socket = make_link()
    new, _ = make_link()

    await hub.attach(old)
    await hub.attach(new)

    assert old_socket.closed is True
    assert hub.local_link("conn-1") is new


async def test_detaching_a_stale_link_does_not_strand_the_live_one(
    settings: Settings,
) -> None:
    hub = ConnectorHub(settings, bus=LocalBus("instance-1"))
    old, _ = make_link()
    new, _ = make_link()

    await hub.attach(old)
    await hub.attach(new)
    await hub.detach(old)  # the old session's handler finishing, late

    assert hub.local_link("conn-1") is new


async def test_a_connector_that_lacks_a_query_says_so(settings: Settings) -> None:
    """Better than a mystery failure: the app can prompt for an update."""
    hub = ConnectorHub(settings, bus=LocalBus("instance-1"))
    link, _ = make_link()
    link.capabilities = [{"name": "ledgers.list", "version": 1, "heavy": False}]
    await hub.attach(link)

    result = await hub.run(connector_id="conn-1", query="a.brand.new.report", params={})
    assert not result.ok
    assert result.error.code == "connector_outdated"
    assert result.error.retryable is False


async def test_coalescing_distinguishes_different_params(settings: Settings) -> None:
    """Collapsing two genuinely different reads would answer one with the other."""
    from tally_backend.hub.hub import coalesce_key

    a = coalesce_key("c", "vouchers.list", {"from_date": "2026-01-01"})
    b = coalesce_key("c", "vouchers.list", {"from_date": "2026-02-01"})
    assert a != b

    # Key order must not matter, or coalescing silently never fires.
    assert coalesce_key("c", "q", {"x": 1, "y": 2}) == coalesce_key("c", "q", {"y": 2, "x": 1})


async def test_local_bus_reports_itself_as_single_instance(settings: Settings) -> None:
    bus = LocalBus("instance-1")
    assert bus.is_distributed is False

    await bus.register("conn-1")
    assert await bus.locate("conn-1") == "instance-1"

    await bus.unregister("conn-1")
    assert await bus.locate("conn-1") is None
