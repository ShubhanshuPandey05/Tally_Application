"""Session integration tests against a real (in-process) backend WebSocket server.

These drive the actual websockets stack rather than a mock, because the parts
most likely to break in production -- handshake ordering, reconnect, surviving a
dropped socket -- only exist at that level.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

import httpx
import pytest
from tally_core.tally import TallyClient, TallyConfig
from websockets.asyncio.server import serve

from tally_connector.config import ConnectorSettings
from tally_connector.protocol import (
    Hello,
    HelloAck,
    JobRequest,
    Ping,
    verify_handshake,
)
from tally_connector.remote_logs import RemoteLogHandler
from tally_connector.session import AuthenticationRejected, ConnectorSession

SECRET = "test-secret"
CONNECTOR_ID = "con_test"


class FakeBackend:
    """Minimal backend: verifies the handshake and scripts a conversation."""

    def __init__(self, *, accept: bool = True, reject_reason: str | None = None) -> None:
        self.accept = accept
        self.reject_reason = reject_reason
        self.hellos: list[Hello] = []
        self.results: list[dict] = []
        self.pongs: list[dict] = []
        self.status_events: list[dict] = []
        self.log_batches: list[dict] = []
        #: Frame kinds in arrival order, for tests about what blocks what.
        self.order: list[str] = []
        self.connections = 0
        self.script: list[dict] = []
        #: Gap between scripted frames. Needed when a test cares about what the
        #: connector was *already doing* when the next frame arrived.
        self.script_gap = 0.0
        #: Set once the server has seen everything a test is waiting for.
        self.done = asyncio.Event()
        self.expected_results = 0
        self.expected_log_batches = 0
        self.drop_after_handshake = False

    async def handler(self, socket) -> None:
        self.connections += 1

        hello = Hello.model_validate_json(await socket.recv())
        self.hellos.append(hello)

        if not self.accept or not verify_handshake(hello, secret=SECRET):
            reason = self.reject_reason or "bad signature"
            await socket.send(HelloAck(accepted=False, reason=reason).model_dump_json())
            await socket.close()
            return

        await socket.send(
            HelloAck(accepted=True, session_id=f"sess-{self.connections}").model_dump_json()
        )

        if self.drop_after_handshake:
            await socket.close(code=1011, reason="simulated backend restart")
            return

        for index, frame in enumerate(self.script):
            if index and self.script_gap:
                await asyncio.sleep(self.script_gap)
            await socket.send(json.dumps(frame))

        async for raw in socket:
            message = json.loads(raw)
            kind = message.get("type")
            self.order.append(kind)
            if kind == "job_result":
                self.results.append(message)
            elif kind == "pong":
                self.pongs.append(message)
            elif kind == "status":
                self.status_events.append(message)
            elif kind == "log_batch":
                self.log_batches.append(message)

            if len(self.results) >= self.expected_results and self.expected_results:
                self.done.set()
            if (
                self.expected_log_batches
                and len(self.log_batches) >= self.expected_log_batches
            ):
                self.done.set()


@contextlib.asynccontextmanager
async def running(backend: FakeBackend):
    async with serve(backend.handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}"


def make_settings(url: str, **overrides) -> ConnectorSettings:
    return ConnectorSettings(
        connector_id=CONNECTOR_ID,
        connector_secret=SECRET,
        backend_url=url,
        reconnect_initial_seconds=0.01,
        reconnect_max_seconds=0.05,
        **overrides,
    )


def fake_tally(handler=None) -> TallyClient:
    companies = (
        "<ENVELOPE><BODY><DATA><COLLECTION>"
        "<COMPANY NAME='Acme'><NAME>Acme</NAME></COMPANY>"
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )
    handler = handler or (lambda request: httpx.Response(200, text=companies))
    return TallyClient(
        TallyConfig(retry_backoff_seconds=0.0, max_attempts=1),
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def run_session(
    settings, backend, *, timeout=5.0, tally=None, log_handler=None
) -> ConnectorSession:
    """Run a session until the backend says it has what it needs."""
    session = ConnectorSession(
        settings,
        version="0.1.0-test",
        tally=tally or fake_tally(),
        log_handler=log_handler,
    )
    runner = asyncio.create_task(session.run_forever())
    try:
        await asyncio.wait_for(backend.done.wait(), timeout=timeout)
    finally:
        await session.stop()
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner
        await session.aclose()
    return session


# --------------------------------------------------------------------------
# Handshake
# --------------------------------------------------------------------------


async def test_handshake_is_signed_and_advertises_capabilities():
    backend = FakeBackend()
    backend.expected_results = 1
    backend.script = [
        JobRequest(job_id="j1", query="companies.list").model_dump(mode="json")
    ]

    async with running(backend) as url:
        await run_session(make_settings(url), backend)

    hello = backend.hellos[0]
    assert hello.connector_id == CONNECTOR_ID
    assert verify_handshake(hello, secret=SECRET)
    assert SECRET not in hello.model_dump_json()

    names = {c.name for c in hello.capabilities}
    assert "companies.list" in names
    assert "vouchers.list" in names


async def test_rejected_handshake_stops_the_reconnect_loop():
    """A revoked connector must not hammer the backend forever."""
    backend = FakeBackend(accept=False, reject_reason="connector revoked")

    async with running(backend) as url:
        session = ConnectorSession(
            make_settings(url), version="0.1.0-test", tally=fake_tally()
        )
        with pytest.raises(AuthenticationRejected, match="connector revoked"):
            await asyncio.wait_for(session.run_forever(), timeout=5.0)
        await session.aclose()

    assert backend.connections == 1


async def test_wrong_secret_is_refused_by_the_backend():
    backend = FakeBackend()

    async with running(backend) as url:
        settings = make_settings(url)
        settings = settings.model_copy(update={"connector_secret": "wrong-secret"})
        session = ConnectorSession(settings, version="0.1.0-test", tally=fake_tally())
        with pytest.raises(AuthenticationRejected):
            await asyncio.wait_for(session.run_forever(), timeout=5.0)
        await session.aclose()


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


async def test_job_request_is_executed_and_returned():
    backend = FakeBackend()
    backend.expected_results = 1
    backend.script = [
        JobRequest(job_id="j1", query="companies.list").model_dump(mode="json")
    ]

    async with running(backend) as url:
        await run_session(make_settings(url), backend)

    result = backend.results[0]
    assert result["job_id"] == "j1"
    assert result["ok"] is True
    assert result["payload"][0]["name"] == "Acme"


async def test_multiple_jobs_are_all_answered():
    backend = FakeBackend()
    backend.expected_results = 3
    backend.script = [
        JobRequest(job_id=f"j{i}", query="companies.list").model_dump(mode="json")
        for i in range(3)
    ]

    async with running(backend) as url:
        await run_session(make_settings(url), backend)

    assert {r["job_id"] for r in backend.results} == {"j0", "j1", "j2"}
    assert all(r["ok"] for r in backend.results)


async def test_failing_job_is_answered_and_session_survives():
    """One bad query must not take down the connection."""
    backend = FakeBackend()
    backend.expected_results = 2
    backend.script = [
        JobRequest(job_id="bad", query="does.not.exist").model_dump(mode="json"),
        JobRequest(job_id="good", query="companies.list").model_dump(mode="json"),
    ]

    async with running(backend) as url:
        await run_session(make_settings(url), backend)

    by_id = {r["job_id"]: r for r in backend.results}
    assert by_id["bad"]["ok"] is False
    assert by_id["bad"]["error"]["code"] == "unknown_query"
    assert by_id["good"]["ok"] is True


async def test_malformed_frames_are_ignored():
    """Garbage on the wire must not kill a session that is otherwise healthy."""
    backend = FakeBackend()
    backend.expected_results = 1
    backend.script = [
        {"type": "job"},  # missing required fields
        {"type": "from_the_future", "data": 1},  # unknown type
        JobRequest(job_id="j1", query="companies.list").model_dump(mode="json"),
    ]

    async with running(backend) as url:
        await run_session(make_settings(url), backend)

    assert len(backend.results) == 1
    assert backend.results[0]["job_id"] == "j1"


# --------------------------------------------------------------------------
# Liveness and reconnect
# --------------------------------------------------------------------------


async def test_ping_is_answered_with_tally_status():
    backend = FakeBackend()
    backend.expected_results = 1
    backend.script = [
        Ping(token="tok-1").model_dump(mode="json"),
        JobRequest(job_id="j1", query="companies.list").model_dump(mode="json"),
    ]

    async with running(backend) as url:
        await run_session(make_settings(url), backend)

    assert backend.pongs[0]["token"] == "tok-1"
    assert backend.pongs[0]["tally_online"] is True


async def test_tally_status_transition_is_pushed_once():
    backend = FakeBackend()
    backend.expected_results = 1
    backend.script = [
        Ping(token="a").model_dump(mode="json"),
        Ping(token="b").model_dump(mode="json"),
        JobRequest(job_id="j1", query="companies.list").model_dump(mode="json"),
    ]

    async with running(backend) as url:
        await run_session(make_settings(url), backend)

    assert len(backend.pongs) == 2
    # Two pings, one transition (unknown -> online): status is edge-triggered.
    assert len(backend.status_events) == 1
    assert backend.status_events[0]["tally_online"] is True


async def test_heartbeat_is_answered_while_a_slow_export_is_running():
    """The regression that lost the day book -- and with it every sales figure.

    The ping used to be handled inline in the socket read loop, and its liveness
    check queued behind whatever was already on Tally. A slow ``vouchers.list``
    therefore stopped the connector from reading *any* frame for the duration:
    the backend saw no heartbeat for 90 seconds, dropped a connection that was
    working perfectly, and the voucher result was thrown away on arrival. Every
    sweep hit the same wall, so the dashboard's sales section never filled in.
    """
    companies = (
        "<ENVELOPE><BODY><DATA><COLLECTION>"
        "<COMPANY NAME='Acme'><NAME>Acme</NAME></COMPANY>"
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.6)
        return httpx.Response(200, text=companies)

    backend = FakeBackend()
    backend.expected_results = 1
    # The gap is the point: the ping has to arrive while the export is genuinely
    # on the wire, which is the situation the old code could not survive.
    backend.script_gap = 0.15
    backend.script = [
        JobRequest(job_id="slow", query="companies.list").model_dump(mode="json"),
        Ping(token="tok-1").model_dump(mode="json"),
    ]

    async with running(backend) as url:
        await run_session(make_settings(url), backend, tally=fake_tally(slow))

    assert backend.order.index("pong") < backend.order.index("job_result")
    # And it answered honestly: Tally is demonstrably talking to us.
    assert backend.pongs[0]["tally_online"] is True


async def test_reconnects_after_the_backend_drops_the_connection():
    backend = FakeBackend()
    backend.drop_after_handshake = True

    async with running(backend) as url:
        session = ConnectorSession(
            make_settings(url), version="0.1.0-test", tally=fake_tally()
        )
        runner = asyncio.create_task(session.run_forever())

        # Give the backoff loop room for several attempts.
        for _ in range(100):
            if backend.connections >= 3:
                break
            await asyncio.sleep(0.02)

        await session.stop()
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner
        await session.aclose()

    assert backend.connections >= 3, "connector should keep retrying after a drop"


# --------------------------------------------------------------------------
# Remote logs
# --------------------------------------------------------------------------


async def test_buffered_log_lines_are_shipped_over_the_socket():
    """The support view's whole supply of connector logs."""
    handler = RemoteLogHandler(capacity=100)
    handler.setFormatter(logging.Formatter("%(message)s"))
    for index in range(3):
        handler.emit(
            logging.LogRecord(
                "tally_connector.session", logging.INFO, __file__, 1,
                "line %d", (index,), None,
            )
        )

    backend = FakeBackend()
    backend.expected_log_batches = 1

    async with running(backend) as url:
        await run_session(
            make_settings(url, remote_log_interval_seconds=0.05),
            backend,
            log_handler=handler,
        )

    batch = backend.log_batches[0]
    assert [entry["message"] for entry in batch["entries"]] == ["line 0", "line 1", "line 2"]
    # Stamped like every other client frame, so the backend can re-evaluate the
    # build on a connector that sends nothing but logs.
    assert batch["connector_version"] == "0.1.0-test"


async def test_a_reconnect_does_not_lose_buffered_lines():
    """The lines explaining why a connection dropped must survive it.

    The buffer is owned by `main`, not by the session, precisely so that a
    dropped socket does not take the explanation with it.
    """
    handler = RemoteLogHandler(capacity=100)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.emit(
        logging.LogRecord(
            "tally_connector.session", logging.ERROR, __file__, 1,
            "the socket died", (), None,
        )
    )

    backend = FakeBackend()
    backend.expected_log_batches = 1
    # First connection is dropped immediately; the shipper on it never runs.
    backend.drop_after_handshake = True

    async with running(backend) as url:
        session = ConnectorSession(
            make_settings(url, remote_log_interval_seconds=0.05),
            version="0.1.0-test",
            tally=fake_tally(),
            log_handler=handler,
        )
        runner = asyncio.create_task(session.run_forever())
        await asyncio.sleep(0.15)
        backend.drop_after_handshake = False
        try:
            await asyncio.wait_for(backend.done.wait(), timeout=5.0)
        finally:
            await session.stop()
            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner
            await session.aclose()

    messages = [
        entry["message"]
        for batch in backend.log_batches
        for entry in batch["entries"]
    ]
    assert "the socket died" in messages


async def test_a_session_without_a_log_handler_ships_nothing():
    """Remote logging off is a connector that simply never sends the frame."""
    backend = FakeBackend()
    backend.expected_results = 1
    backend.script = [
        JobRequest(job_id="j1", query="companies.list").model_dump(mode="json")
    ]

    async with running(backend) as url:
        await run_session(
            make_settings(url, remote_log_interval_seconds=0.05), backend, log_handler=None
        )

    assert backend.log_batches == []
