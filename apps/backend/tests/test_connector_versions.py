"""Version exchange over the connector socket.

The mechanism is symmetric and rides on traffic that already exists: every frame
the backend sends carries the published version, every frame the connector sends
carries the one it is running. Nothing polls.

What is worth testing is the restraint rather than the plumbing. A connector
returning fifty job results reports its version fifty times, and the failure mode
that matters is a backend that answers each one with an install order -- fifty
commands to a machine that is already downloading. So the assertions here are
mostly about *how few* messages come back.
"""

from __future__ import annotations

import json

from tally_core.protocol import JobResult, Ping, Pong, StatusEvent

from tally_backend.hub.link import ConnectorLink


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed = False

    async def send_text(self, data: str) -> None:
        if self.closed:
            raise RuntimeError("socket is closed")
        self.sent.append(json.loads(data))

    async def receive_text(self) -> str:  # pragma: no cover - never read here
        raise AssertionError("unreachable")

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = True


class FakeReleases:
    """A stand-in for ``ConnectorReleaseView`` with a settable answer."""

    def __init__(self, latest: str = "", minimum: str = "", *, mandatory: bool = False) -> None:
        self.latest_version = latest
        self.min_version = minimum
        self.mandatory = mandatory
        self.queries: list[str] = []

    def outdated(self, version: str) -> tuple[str, bool]:
        self.queries.append(version)
        if not self.latest_version or not version:
            return "", False
        if version == self.latest_version:
            return "", False
        return self.latest_version, self.mandatory


def make_link(releases: FakeReleases | None = None, **kwargs) -> tuple[ConnectorLink, FakeSocket]:
    socket = FakeSocket()
    link = ConnectorLink(
        connector_id="conn-1",
        org_id="org-1",
        socket=socket,
        releases=releases,
        **kwargs,
    )
    return link, socket


def pong(version: str = "0.1.0") -> dict:
    frame = json.loads(Pong(token="t", tally_online=True).model_dump_json())
    frame["connector_version"] = version
    return frame


def updates_in(socket: FakeSocket) -> list[dict]:
    return [frame for frame in socket.sent if frame.get("type") == "update"]


# --------------------------------------------------------------------------
# Stamping outbound frames
# --------------------------------------------------------------------------


async def test_every_server_frame_carries_the_published_version() -> None:
    link, socket = make_link(FakeReleases("0.4.0", "0.2.0"))

    await link.send(Ping(token="t"))

    assert socket.sent[0]["latest_connector_version"] == "0.4.0"
    assert socket.sent[0]["min_connector_version"] == "0.2.0"


async def test_the_version_is_read_fresh_on_each_send() -> None:
    """A `run.py publish` has to reach connectors that are already connected."""
    releases = FakeReleases("0.4.0", "0.2.0")
    link, socket = make_link(releases)

    await link.send(Ping(token="a"))
    releases.latest_version = "0.5.0"
    await link.send(Ping(token="b"))

    assert socket.sent[0]["latest_connector_version"] == "0.4.0"
    assert socket.sent[1]["latest_connector_version"] == "0.5.0"


async def test_a_backend_with_no_manifest_stamps_nothing() -> None:
    """Empty has to mean "no opinion" -- not version zero, and not a downgrade."""
    link, socket = make_link(FakeReleases())

    await link.send(Ping(token="t"))

    assert socket.sent[0]["latest_connector_version"] == ""


async def test_stamping_does_not_mutate_the_caller_s_message() -> None:
    """The hub builds one message and sends it to several links."""
    link, _ = make_link(FakeReleases("0.4.0", "0.2.0"))
    message = Ping(token="t")

    await link.send(message)

    assert message.latest_connector_version == ""


# --------------------------------------------------------------------------
# Reacting to inbound frames
# --------------------------------------------------------------------------


async def test_an_outdated_connector_is_told_to_update() -> None:
    link, socket = make_link(FakeReleases("0.4.0", "0.2.0"))

    await link.handle_message(pong("0.3.0"))

    commands = updates_in(socket)
    assert len(commands) == 1
    assert commands[0]["version"] == "0.4.0"
    assert commands[0]["mandatory"] is False


async def test_a_connector_below_the_floor_is_told_it_is_required() -> None:
    link, socket = make_link(FakeReleases("0.4.0", "0.2.0", mandatory=True))

    await link.handle_message(pong("0.1.0"))

    assert updates_in(socket)[0]["mandatory"] is True


async def test_a_current_connector_is_left_alone() -> None:
    link, socket = make_link(FakeReleases("0.4.0", "0.2.0"))

    await link.handle_message(pong("0.4.0"))

    assert updates_in(socket) == []


async def test_a_busy_connector_gets_one_command_not_one_per_frame() -> None:
    """The reason this test exists: fifty job results must not mean fifty orders."""
    link, socket = make_link(FakeReleases("0.4.0", "0.2.0"))

    for _ in range(50):
        await link.handle_message(pong("0.3.0"))

    assert len(updates_in(socket)) == 1


async def test_a_connector_that_comes_back_still_outdated_is_told_again() -> None:
    """A failed install has to be retried, not remembered as done.

    The re-arm is keyed on the reported version changing, which is what happens
    across a reconnect -- so an update that did not take is ordered again rather
    than being silently dropped for the life of the session.
    """
    link, socket = make_link(FakeReleases("0.4.0", "0.2.0"))

    await link.handle_message(pong("0.3.0"))
    # Reports a different build, then the old one again: two version changes.
    await link.handle_message(pong("0.3.1"))
    await link.handle_message(pong("0.3.0"))

    assert len(updates_in(socket)) == 3


async def test_the_reported_version_is_recorded_for_the_fleet_view() -> None:
    link, _ = make_link(FakeReleases("0.4.0", "0.2.0"))

    await link.handle_message(pong("0.3.0"))

    assert link.connector_version == "0.3.0"
    assert link.snapshot()["connector_version"] == "0.3.0"
    assert link.host["connector_version"] == "0.3.0"


async def test_an_older_connector_that_stamps_nothing_is_not_disturbed() -> None:
    """A build predating the stamped field still has to work."""
    releases = FakeReleases("0.4.0", "0.2.0")
    link, socket = make_link(releases)
    link.connector_version = "0.3.0"

    frame = json.loads(Pong(token="t", tally_online=True).model_dump_json())
    frame.pop("connector_version", None)
    await link.handle_message(frame)

    assert updates_in(socket) == []
    assert link.connector_version == "0.3.0"


async def test_pushing_updates_can_be_switched_off() -> None:
    """Leaves the connector on its own interval poll, which still works."""
    link, socket = make_link(FakeReleases("0.4.0", "0.2.0"), push_updates=False)

    await link.handle_message(pong("0.1.0"))

    assert updates_in(socket) == []
    # Still recorded -- the fleet view needs it even when nothing is pushed.
    assert link.connector_version == "0.1.0"


async def test_a_job_result_reports_its_version_like_any_other_frame() -> None:
    link, socket = make_link(FakeReleases("0.4.0", "0.2.0"))

    frame = json.loads(JobResult.success("job-1", {"ok": True}).model_dump_json())
    frame["connector_version"] = "0.3.0"
    await link.handle_message(frame)

    assert link.connector_version == "0.3.0"
    assert len(updates_in(socket)) == 1


async def test_a_status_event_reports_its_version_too() -> None:
    link, socket = make_link(FakeReleases("0.4.0", "0.2.0"))

    frame = json.loads(StatusEvent(tally_online=False).model_dump_json())
    frame["connector_version"] = "0.3.0"
    await link.handle_message(frame)

    assert len(updates_in(socket)) == 1


async def test_a_link_with_no_catalogue_behaves_exactly_as_before() -> None:
    """The whole feature has to be absent-safe, not just fail-open."""
    link, socket = make_link(None)

    await link.send(Ping(token="t"))
    await link.handle_message(pong("0.1.0"))

    assert socket.sent[0]["latest_connector_version"] == ""
    assert updates_in(socket) == []
    assert link.connector_version == "0.1.0"


async def test_a_closed_socket_does_not_raise_out_of_the_receive_loop() -> None:
    """An update command is best-effort; losing it must not kill the session."""
    link, socket = make_link(FakeReleases("0.4.0", "0.2.0"))
    socket.closed = True

    # Would raise LinkClosed if the failure were propagated.
    await link.handle_message(pong("0.1.0"))

    assert link.connector_version == "0.1.0"
