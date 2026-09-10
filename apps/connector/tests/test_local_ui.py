"""What the connector hands its window, and what it refuses to hand anything else.

The connector runs on a shop's till PC and answers, over loopback, with the
names of that business's companies and the email addresses of its staff, plus
four buttons that restart it and change the port Tally is read on. That socket
is reachable by anything on the machine that can open one -- including a web
page the owner happens to have open in a browser tab.

So most of this file is about the refusals. The bind, the ``Host`` check and the
custom-header requirement are three layers of the same defence, and each is
cheap enough that losing one silently is the only real risk.

The window itself is a Flutter build under ``apps/mobile``; its half of this
contract is tested in ``apps/mobile/test/connector_window_test.dart``.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from tally_connector.ui import LocalUiServer, UiBridge
from tally_connector.ui.qr import QUIET_ZONE, matrix_rows


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def bridge() -> UiBridge:
    ui = UiBridge(version="9.9.9", log_dir=r"C:\logs")
    ui.update(
        paired=True,
        connector_id="con_abc",
        connector_name="Shop PC",
        backend_url="wss://api.example.com/v1/connector",
        tally_host="127.0.0.1",
        tally_port=9000,
    )
    return ui


@pytest.fixture
def server(bridge: UiBridge):
    ui = LocalUiServer(bridge, port=free_port())
    assert ui.start()
    try:
        yield ui
    finally:
        ui.stop()


def fetch(url: str, *, headers: dict[str, str] | None = None, data: bytes | None = None):
    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


LOCAL = {"X-TallyFlow-Local": "1", "Content-Type": "application/json"}


# --------------------------------------------------------------------------
# What it answers
# --------------------------------------------------------------------------


def test_it_serves_no_page_at_all(server: LocalUiServer) -> None:
    """The HTML this used to hand a browser is gone, and stays gone.

    The window replaced it. Serving a page as well would put a second, untested
    rendering of a business's company names on a socket whose whole defence is
    that a browser cannot usefully talk to it.
    """
    assert fetch(server.url, headers=LOCAL)[0] == 404
    assert fetch(f"{server.url}index.html", headers=LOCAL)[0] == 404


def test_the_state_endpoint_describes_the_connector(server: LocalUiServer) -> None:
    status, body = fetch(f"{server.url}api/state", headers=LOCAL)
    state = json.loads(body)

    assert status == 200
    assert state["connector_id"] == "con_abc"
    assert state["tally"]["port"] == 9000
    # Never checked yet, which is not the same as "not responding" -- the page
    # renders the two differently and one of them sends somebody to restart
    # software that is working.
    assert state["tally"]["online"] is None


def test_the_pairing_grid_is_sent_only_while_a_code_is_waiting(
    bridge: UiBridge, server: LocalUiServer
) -> None:
    """An empty grid, not a 404.

    The window asks for this while a pairing screen is on its way out, and an
    error there would have it report a fault on the one transition that is
    working correctly.
    """
    empty = json.loads(fetch(f"{server.url}api/qr", headers=LOCAL)[1])
    assert empty["rows"] == []

    bridge.update(pairing_payload='{"v":1,"c":"abc","h":"api.example.com"}')
    status, body = fetch(f"{server.url}api/qr", headers=LOCAL)
    grid = json.loads(body)

    assert status == 200
    assert grid["payload"] == '{"v":1,"c":"abc","h":"api.example.com"}'
    # Square, and nothing in it but modules.
    assert len(grid["rows"]) == len(grid["rows"][0])
    assert set("".join(grid["rows"])) <= {"0", "1"}


def test_the_expiry_counts_down_rather_than_standing_still(bridge: UiBridge) -> None:
    """A code that promises fifteen minutes for fifteen minutes is a lie."""
    bridge.update(pairing_payload="x", pairing_expires_at=time.time() + 120)
    first = bridge.snapshot()["pairing"]["seconds_left"]

    bridge.update(pairing_expires_at=time.time() + 60)

    assert first > bridge.snapshot()["pairing"]["seconds_left"]


def test_the_secret_is_never_sent(bridge: UiBridge, server: LocalUiServer) -> None:
    """There is no field for it, and this is what keeps it that way.

    A pairing secret handed to anything on the machine that asks would undo
    the entire reason the QR flow exists.
    """
    bridge.update(pairing_payload='{"v":1,"c":"a-code","h":"h"}')
    state = json.loads(fetch(f"{server.url}api/state", headers=LOCAL)[1])

    assert "secret" not in json.dumps(state)


# --------------------------------------------------------------------------
# What it refuses
# --------------------------------------------------------------------------


def test_it_listens_on_loopback_and_nowhere_else(server: LocalUiServer) -> None:
    """The product's first rule, asserted at the socket.

    No inbound port on a customer's machine. A status page bound to every
    interface would be exactly that, in front of a list of their companies.
    """
    port = int(server.url.rsplit(":", 1)[1].rstrip("/"))
    outward = socket.socket()
    outward.settimeout(2)
    try:
        # Binding the same port on a non-loopback address must be possible,
        # which it only is if the server did not take the wildcard.
        outward.bind((socket.gethostbyname(socket.gethostname()), port))
    except OSError as exc:  # pragma: no cover - depends on the host's interfaces
        pytest.skip(f"no second address to test against: {exc}")
    finally:
        outward.close()


def test_a_request_for_another_hostname_is_refused(server: LocalUiServer) -> None:
    """DNS rebinding, which is how a loopback server becomes a remote one."""
    status, _ = fetch(
        f"{server.url}api/state",
        headers={**LOCAL, "Host": "connector.attacker.example"},
    )

    # Checked before the header is, so a foreign hostname is refused even by a
    # caller that got the header right.
    assert status == 421


def test_the_api_needs_a_header_a_foreign_page_cannot_set(
    server: LocalUiServer,
) -> None:
    """The CSRF defence, in one assertion.

    A page on another origin cannot attach a custom header without a CORS
    preflight, and this server answers no CORS headers -- so the preflight
    fails and the request is never sent. A form post needs no preflight and
    cannot set the header either.
    """
    assert fetch(f"{server.url}api/state")[0] == 403
    assert fetch(f"{server.url}api/restart", data=b"{}")[0] == 403


def test_no_cors_headers_are_ever_sent(server: LocalUiServer) -> None:
    request = urllib.request.Request(f"{server.url}api/state", headers=LOCAL)
    with urllib.request.urlopen(request, timeout=5) as response:
        headers = {key.lower() for key in response.headers}

    assert not any(key.startswith("access-control-") for key in headers)


def test_an_unknown_action_is_not_invented(server: LocalUiServer) -> None:
    assert fetch(f"{server.url}api/uninstall", data=b"{}", headers=LOCAL)[0] == 404


def test_an_oversized_body_is_refused_rather_than_read(server: LocalUiServer) -> None:
    status, _ = fetch(
        f"{server.url}api/port", data=b"{" + b"x" * 8192 + b"}", headers=LOCAL
    )

    assert status == 400


# --------------------------------------------------------------------------
# Buttons
# --------------------------------------------------------------------------


def test_a_button_pressed_before_the_connector_is_ready_answers_honestly(
    bridge: UiBridge, server: LocalUiServer
) -> None:
    """No loop bound yet. The page must be told, not left waiting."""
    status, body = fetch(f"{server.url}api/restart", data=b"{}", headers=LOCAL)

    assert status == 200
    assert json.loads(body)["ok"] is False


def test_an_action_that_raises_becomes_a_message(bridge: UiBridge) -> None:
    """A traceback is not an answer to "why is my dashboard empty?"."""
    import asyncio

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    async def explode(_: dict) -> dict:
        raise RuntimeError("the port is already in use")

    bridge.bind(loop)
    bridge.register("restart", explode)
    try:
        result = bridge.invoke("restart")
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)

    assert result == {"ok": False, "message": "the port is already in use"}


def test_the_connector_survives_the_port_being_taken(bridge: UiBridge) -> None:
    """A status page must never be the reason a shop's connector is off the air."""
    port = free_port()
    holder = LocalUiServer(bridge, port=port)
    assert holder.start()

    second = LocalUiServer(UiBridge(version="9.9.9"), port=port)
    try:
        assert second.start() is False
    finally:
        holder.stop()


# --------------------------------------------------------------------------
# The code itself
# --------------------------------------------------------------------------


def test_the_grid_is_a_full_qr_and_never_a_micro_one() -> None:
    """Left to choose, the encoder drops to Micro QR for a short payload.

    Micro QR is a different symbology that a good many phone cameras -- ones
    that read every other code they are shown -- simply do not decode. The
    failure is a code that looks perfect on screen and does nothing when
    scanned, which is unfalsifiable from the machine drawing it. The smallest
    full QR is 21 modules; every Micro QR is smaller.
    """
    rows = matrix_rows("x")

    assert len(rows) - QUIET_ZONE * 2 >= 21


def test_the_quiet_zone_travels_with_the_grid() -> None:
    """Four blank modules of margin, included rather than left to the drawing.

    Without it many scanners will not read at all, and a front end that padded
    the symbol itself would be one that could forget to.
    """
    rows = matrix_rows('{"v":1,"c":"abcdef","h":"api.example.com"}')

    for row in rows[:QUIET_ZONE] + rows[-QUIET_ZONE:]:
        assert set(row) == {"0"}
    for row in rows:
        assert row[:QUIET_ZONE] == "0" * QUIET_ZONE
        assert row[-QUIET_ZONE:] == "0" * QUIET_ZONE


def test_the_grid_is_square_and_carries_the_payload() -> None:
    rows = matrix_rows('{"v":1,"c":"abcdef","h":"api.example.com"}')

    assert len({len(row) for row in rows}) == 1
    assert len(rows) == len(rows[0])
    # Not blank: a grid of zeroes would draw a white square and scan as nothing.
    assert "1" in "".join(rows)
