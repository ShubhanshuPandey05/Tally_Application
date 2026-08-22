"""The connector's half of the version exchange.

Two directions, both riding on frames that already exist:

* Outbound -- every frame the connector sends is stamped with the build it is
  running, so the backend can re-evaluate it on any traffic rather than only at
  handshake. The failure this prevents is a connector that stays connected for a
  week and is therefore checked once in a week.
* Inbound -- every frame the backend sends carries the published version, and
  seeing a newer one cuts the six-hourly wait short.

The nudge is deliberately cheap and idempotent, because it runs on the socket's
read path. A version arrives on *every* frame, so anything expensive here would
be paid per heartbeat, and anything that blocked would stall the very heartbeat
the backend uses to decide this connector is alive.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

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
    UpdateCommand,
    verify_handshake,
)
from tally_connector.session import ConnectorSession

SECRET = "test-secret"
CONNECTOR_ID = "con_test"
VERSION = "0.3.0"


class VersionBackend:
    """A backend that stamps a chosen version on everything it sends."""

    def __init__(self, latest: str = "", *, script: list[dict] | None = None) -> None:
        self.latest = latest
        self.script = script or []
        self.frames: list[dict] = []
        self.pongs: list[dict] = []
        self.done = asyncio.Event()
        #: How many connector frames to wait for before releasing the test.
        self.expect = 1

    def _stamp(self, frame: dict) -> dict:
        if self.latest:
            frame["latest_connector_version"] = self.latest
        return frame

    async def handler(self, socket) -> None:
        hello = Hello.model_validate_json(await socket.recv())
        self.frames.append(json.loads(hello.model_dump_json()))
        if not verify_handshake(hello, secret=SECRET):
            await socket.send(HelloAck(accepted=False, reason="bad signature").model_dump_json())
            return

        ack = json.loads(HelloAck(accepted=True, session_id="s1").model_dump_json())
        await socket.send(json.dumps(self._stamp(ack)))

        for frame in self.script:
            await socket.send(json.dumps(self._stamp(dict(frame))))

        async for raw in socket:
            message = json.loads(raw)
            self.frames.append(message)
            if message.get("type") == "pong":
                self.pongs.append(message)
            if len(self.frames) >= self.expect:
                self.done.set()


@contextlib.asynccontextmanager
async def running(backend: VersionBackend):
    async with serve(backend.handler, "127.0.0.1", 0) as server:
        yield f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"


def make_settings(url: str, **overrides) -> ConnectorSettings:
    return ConnectorSettings(
        connector_id=CONNECTOR_ID,
        connector_secret=SECRET,
        backend_url=url,
        reconnect_initial_seconds=0.01,
        reconnect_max_seconds=0.05,
        **overrides,
    )


def fake_tally() -> TallyClient:
    companies = (
        "<ENVELOPE><BODY><DATA><COLLECTION>"
        "<COMPANY NAME='Acme'><NAME>Acme</NAME></COMPANY>"
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )
    return TallyClient(
        TallyConfig(retry_backoff_seconds=0.0, max_attempts=1),
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, text=companies))
        ),
    )


class RecordingUpdater:
    """Captures nudges instead of downloading anything."""

    def __init__(self) -> None:
        self.nudges: list[str] = []
        self.started = False

    def start(self) -> None:
        self.started = True

    def nudge(self, reason: str = "") -> None:
        self.nudges.append(reason)

    async def aclose(self) -> None:
        return None


async def run_session(settings, backend, *, timeout=5.0) -> RecordingUpdater:
    """Run one session with the updater replaced by a recorder."""
    session = ConnectorSession(settings, version=VERSION, tally=fake_tally())
    updater = RecordingUpdater()
    session._updater = updater  # noqa: SLF001 - the seam this test exists to drive
    runner = asyncio.create_task(session.run_forever())
    try:
        await asyncio.wait_for(backend.done.wait(), timeout=timeout)
    finally:
        await session.stop()
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner
        await session.aclose()
    return updater


# --------------------------------------------------------------------------
# Outbound: stamping our own version
# --------------------------------------------------------------------------


async def test_the_handshake_carries_the_running_version() -> None:
    # Scripted with a ping so the session has a second frame to exchange; the
    # backend only releases the test once it has seen the connector answer.
    backend = VersionBackend(script=[json.loads(Ping(token="t").model_dump_json())])
    backend.expect = 2

    async with running(backend) as url:
        await run_session(make_settings(url), backend)

    assert backend.frames[0]["type"] == "hello"
    assert backend.frames[0]["connector_version"] == VERSION


async def test_a_pong_carries_the_running_version() -> None:
    """The heartbeat is the frame an idle connector sends, so it is the one that
    keeps the backend's view of its version fresh."""
    backend = VersionBackend(script=[json.loads(Ping(token="t").model_dump_json())])
    backend.expect = 2

    async with running(backend) as url:
        await run_session(make_settings(url), backend)

    assert backend.pongs
    assert backend.pongs[0]["connector_version"] == VERSION


async def test_a_job_result_carries_the_running_version() -> None:
    backend = VersionBackend(
        script=[json.loads(JobRequest(job_id="j1", query="companies.list").model_dump_json())]
    )
    backend.expect = 2

    async with running(backend) as url:
        await run_session(make_settings(url), backend)

    results = [f for f in backend.frames if f.get("type") == "job_result"]
    assert results
    assert results[0]["connector_version"] == VERSION


# --------------------------------------------------------------------------
# Inbound: reacting to the published version
# --------------------------------------------------------------------------


async def test_a_newer_version_on_a_ping_triggers_a_check() -> None:
    backend = VersionBackend(
        latest="0.9.0", script=[json.loads(Ping(token="t").model_dump_json())]
    )
    backend.expect = 2

    async with running(backend) as url:
        updater = await run_session(make_settings(url), backend)

    assert updater.nudges
    assert "0.9.0" in updater.nudges[0]


async def test_the_ack_alone_is_enough_to_trigger_a_check() -> None:
    """No need to wait for the first heartbeat: the handshake reply already says."""
    backend = VersionBackend(
        latest="0.9.0", script=[json.loads(Ping(token="t").model_dump_json())]
    )
    backend.expect = 2

    async with running(backend) as url:
        updater = await run_session(make_settings(url), backend)

    # The ack arrives before the ping, so the first nudge is attributable to it.
    assert len(updater.nudges) >= 1


async def test_the_same_version_triggers_nothing() -> None:
    backend = VersionBackend(
        latest=VERSION, script=[json.loads(Ping(token="t").model_dump_json())]
    )
    backend.expect = 2

    async with running(backend) as url:
        updater = await run_session(make_settings(url), backend)

    assert updater.nudges == []


async def test_an_older_published_version_never_triggers_a_downgrade() -> None:
    """A rolled-back manifest must not talk the fleet backwards."""
    backend = VersionBackend(
        latest="0.1.0", script=[json.loads(Ping(token="t").model_dump_json())]
    )
    backend.expect = 2

    async with running(backend) as url:
        updater = await run_session(make_settings(url), backend)

    assert updater.nudges == []


async def test_a_backend_that_stamps_nothing_triggers_nothing() -> None:
    """An older backend, or one with no manifest. The interval poll still covers it."""
    backend = VersionBackend(script=[json.loads(Ping(token="t").model_dump_json())])
    backend.expect = 2

    async with running(backend) as url:
        updater = await run_session(make_settings(url), backend)

    assert updater.nudges == []


async def test_an_explicit_update_command_triggers_a_check() -> None:
    backend = VersionBackend(
        script=[
            json.loads(UpdateCommand(version="0.9.0", reason="security fix").model_dump_json()),
            json.loads(Ping(token="t").model_dump_json()),
        ]
    )
    backend.expect = 2

    async with running(backend) as url:
        updater = await run_session(make_settings(url), backend)

    assert any("0.9.0" in reason for reason in updater.nudges)


async def test_a_stale_update_command_is_ignored() -> None:
    """A command naming a version we already run, or an older one."""
    backend = VersionBackend(
        script=[
            json.loads(UpdateCommand(version="0.1.0").model_dump_json()),
            json.loads(Ping(token="t").model_dump_json()),
        ]
    )
    backend.expect = 2

    async with running(backend) as url:
        updater = await run_session(make_settings(url), backend)

    assert updater.nudges == []


async def test_a_malformed_update_command_does_not_kill_the_session() -> None:
    backend = VersionBackend(
        script=[
            {"type": "update"},  # no version
            json.loads(Ping(token="t").model_dump_json()),
        ]
    )
    backend.expect = 2

    async with running(backend) as url:
        updater = await run_session(make_settings(url), backend)

    # The session survived to answer the ping that followed.
    assert backend.pongs
    assert updater.nudges == []


async def test_an_unknown_frame_type_still_has_its_version_read() -> None:
    """A newer backend sending a type this build predates is precisely the case
    where noticing that an update exists matters most."""
    backend = VersionBackend(
        latest="0.9.0",
        script=[
            {"type": "something_from_the_future", "protocol_version": 1},
            json.loads(Ping(token="t").model_dump_json()),
        ],
    )
    backend.expect = 2

    async with running(backend) as url:
        updater = await run_session(make_settings(url), backend)

    assert updater.nudges
    assert backend.pongs  # and it did not disconnect over it


# --------------------------------------------------------------------------
# The nudge itself
# --------------------------------------------------------------------------


async def test_a_nudge_before_start_is_a_no_op() -> None:
    """Called from the socket path, which can be live before the loop is."""
    from tally_connector.updater import UpdateManager

    manager = UpdateManager(
        current_version="0.1.0",
        manifest_url="https://example.test/downloads/manifest.json",
    )
    manager.nudge("nothing running yet")  # must not raise

    await manager.aclose()


async def test_a_nudge_cuts_the_interval_short() -> None:
    """The point of the whole mechanism: no waiting out six hours."""
    from tally_connector.updater import UpdateManager

    checks = asyncio.Event()
    calls = 0

    class Nudgeable(UpdateManager):
        async def check_once(self):  # noqa: ANN201
            nonlocal calls
            calls += 1
            if calls >= 2:
                checks.set()
            return None

    manager = Nudgeable(
        current_version="0.1.0",
        manifest_url="https://example.test/downloads/manifest.json",
        # Long enough that reaching a second check by waiting is impossible.
        check_interval_seconds=3600,
    )
    # Bypasses the Windows/frozen guard in `start()`; the loop is what is under
    # test, not where it is allowed to run.
    manager._task = asyncio.create_task(manager._loop())  # noqa: SLF001

    try:
        await asyncio.sleep(0)
        manager.nudge("test")
        await asyncio.wait_for(checks.wait(), timeout=2.0)
    finally:
        await manager.aclose()

    assert calls >= 2


@pytest.mark.parametrize("nudges", [1, 25])
async def test_many_nudges_during_one_check_cost_one_extra_pass(nudges: int) -> None:
    """A release seen on every heartbeat must not mean a download per heartbeat."""
    from tally_connector.updater import UpdateManager

    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    class Slow(UpdateManager):
        async def check_once(self):  # noqa: ANN201
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await release.wait()
            return None

    manager = Slow(
        current_version="0.1.0",
        manifest_url="https://example.test/downloads/manifest.json",
        check_interval_seconds=3600,
    )
    manager._task = asyncio.create_task(manager._loop())  # noqa: SLF001

    try:
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        for _ in range(nudges):
            manager.nudge("frame")
        release.set()
        # Give the loop room to run the coalesced second pass and settle.
        for _ in range(50):
            await asyncio.sleep(0.01)
            if calls >= 2:
                break
    finally:
        await manager.aclose()

    assert calls == 2, "nudges during a check should coalesce into exactly one pass"
