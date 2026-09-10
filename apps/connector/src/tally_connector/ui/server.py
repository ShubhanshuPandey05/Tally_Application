"""The socket the connector's window reads it through.

The window is a separate process -- a Flutter desktop build, launched from the
Start menu or by ``tally-connector ui`` -- and this is the only thing it talks
to. It is a client, never a host: the connector runs whether the window has been
opened, closed, killed or never installed, and nothing below this module knows
it exists.

Loopback HTTP rather than a named pipe, for one reason that outweighs the rest:
the window is written in Dart, which has no pipe support in its standard library
and would need an FFI shim to get one. A shim that must be right on the first
machine it ships to, to draw a status screen, is a poor trade against a socket
both sides already speak.

**It binds to loopback and nothing else, and that is not configurable.** The
product's first rule is that no inbound port is ever opened on a customer's
machine; a status service bound to ``0.0.0.0`` would be exactly that, on a
network the customer does not control, in front of the names of their companies
and their colleagues' email addresses. The phone never talks to this server --
it talks to the backend, and the backend talks to the connector over the socket
the connector dialled out on.

Two defences beyond the bind. Both are aimed at the same attack -- a page the
shop owner has open in a browser quietly driving this -- and both survive the
move away from serving a page, because a browser on this machine can still reach
this port whether or not we ever hand it HTML.

``Host`` **is checked.** A hostname that resolves to 127.0.0.1 is how DNS
rebinding turns a loopback server into a remote one, and the check costs a
string comparison.

**Every call must carry** ``X-TallyFlow-Local``. A cross-origin page cannot set
a custom header without a CORS preflight, and this server answers no CORS
headers at all -- so the preflight fails and the request is never sent. A form
post, which needs no preflight, cannot set the header either.
"""

from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .qr import matrix_rows
from .state import UiBridge

logger = logging.getLogger(__name__)

#: The only address this server will ever bind. Named rather than inlined so
#: that a change to it is a change to a constant with this comment attached.
LOOPBACK = "127.0.0.1"

#: Header that a cross-origin page cannot attach. Its presence is the whole
#: CSRF defence; see the module docstring.
LOCAL_HEADER = "X-TallyFlow-Local"

#: Bodies are a port number or an empty object. Anything larger is not a button
#: on this window, and reading it would be reading an attacker's memory budget.
MAX_BODY_BYTES = 4096

#: Buttons the window may press, mapped to actions the connector registered.
ACTIONS = {"restart", "refresh", "port", "new-code"}


class _Server(ThreadingHTTPServer):
    """The window's socket, with address reuse turned off.

    ``HTTPServer`` sets ``SO_REUSEADDR``, which on Unix only shortens the
    TIME_WAIT wait -- but on Windows lets a *second* process bind a port that is
    already being listened on, and take the connections. This is a Windows
    product serving a business's company names to a window that can restart its
    connector, so an option whose effect is "anything on this machine may
    quietly become the connector's status service" is the wrong default here.

    The cost is on the other side: a connector relaunched within the TIME_WAIT
    window may find the port still held and start without one. That is
    logged, it is recoverable by waiting a minute, and it is a far better
    failure than the one above.
    """

    allow_reuse_address = False
    daemon_threads = True


class _Handler(BaseHTTPRequestHandler):
    """Routes for one window. Anything not listed here is a 404."""

    # HTTP/1.1 so the window keeps the connection alive between polls; without
    # it every two-second poll is a fresh TCP connection to a local socket.
    protocol_version = "HTTP/1.1"
    server_version = "TallyFlowConnector"
    #: Suppresses the default banner, which advertises the Python version to
    #: anything that connects.
    sys_version = ""

    bridge: UiBridge
    allowed_hosts: frozenset[str]

    # -- plumbing --------------------------------------------------------

    def log_message(self, fmt: str, *args: Any) -> None:
        """Send access logs to the connector's logger, at DEBUG.

        The base class writes to stderr, which in the windowless service build
        is a handle nobody reads -- and at INFO it would put a line in the
        shipped log every two seconds for as long as somebody leaves the window
        open, evicting the lines support actually needs.
        """
        logger.debug("ui %s", fmt % args)

    def _reject_foreign_host(self) -> bool:
        host = (self.headers.get("Host") or "").strip().lower()
        if host in self.allowed_hosts:
            return False
        # 421 rather than 403: the request reached a server that does not serve
        # that name, which is exactly what this status means.
        self._send(HTTPStatus.MISDIRECTED_REQUEST, b"", "text/plain")
        return True

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # Nothing here may be cached: this is a live status display, and a
        # cached pairing code is a code that has already expired.
        self.send_header("Cache-Control", "no-store")
        # There is nothing to embed and nothing to frame. Both headers are one
        # line each and remove a whole class of "a browser tab did something
        # the owner did not ask for".
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json")

    # -- routes ----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        if self._reject_foreign_host():
            return
        if not self._require_local_header():
            return

        path = self.path.split("?", 1)[0]

        if path == "/api/state":
            self._send_json(self.bridge.snapshot())
            return

        if path == "/api/qr":
            payload = self.bridge.pairing_payload()
            # An empty grid rather than a 404: the window asks for this while a
            # pairing screen is on its way out, and an error there would have it
            # report a fault on the one transition that is working correctly.
            self._send_json(
                {"payload": payload, "rows": matrix_rows(payload) if payload else []}
            )
            return

        self._send(HTTPStatus.NOT_FOUND, b"", "text/plain")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        if self._reject_foreign_host():
            return
        if not self._require_local_header():
            return

        path = self.path.split("?", 1)[0]
        if not path.startswith("/api/"):
            self._send(HTTPStatus.NOT_FOUND, b"", "text/plain")
            return

        name = path[len("/api/") :]
        if name not in ACTIONS:
            self._send(HTTPStatus.NOT_FOUND, b"", "text/plain")
            return

        payload = self._read_body()
        if payload is None:
            self._send_json(
                {"ok": False, "message": "That request was not understood."},
                HTTPStatus.BAD_REQUEST,
            )
            return

        self._send_json(self.bridge.invoke(name, payload))

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        self.do_GET()

    # -- helpers ---------------------------------------------------------

    def _require_local_header(self) -> bool:
        if self.headers.get(LOCAL_HEADER):
            return True
        self._send(HTTPStatus.FORBIDDEN, b"", "text/plain")
        return False

    def _read_body(self) -> dict[str, Any] | None:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if length < 0 or length > MAX_BODY_BYTES:
            return None
        if length == 0:
            return {}
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        return body if isinstance(body, dict) else None


class LocalUiServer:
    """The window's server, owned by whoever started the connector."""

    def __init__(self, bridge: UiBridge, *, port: int) -> None:
        self._bridge = bridge
        self._port = port
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://{LOOPBACK}:{self._port}/"

    def start(self) -> bool:
        """Start listening. ``False`` if the port was taken.

        Never fatal, in either direction. A connector that refused to start
        because something else holds 9787 would be a connector taken off the air
        by a status window, and the whole point of the window is the machine
        still working.
        """
        handler = type(
            "BoundHandler",
            (_Handler,),
            {
                "bridge": self._bridge,
                "allowed_hosts": frozenset(
                    {
                        f"{LOOPBACK}:{self._port}",
                        f"localhost:{self._port}",
                        # A default-port URL omits it entirely, which is legal
                        # and would otherwise fail the Host check.
                        LOOPBACK,
                        "localhost",
                    }
                ),
            },
        )
        try:
            self._server = _Server((LOOPBACK, self._port), handler)
        except OSError as exc:
            logger.warning(
                "the local status service could not start on %s:%s (%s); "
                "the connector is running normally without it",
                LOOPBACK,
                self._port,
                exc,
            )
            return False

        # Daemon: this thread must never be the reason a connector shutdown
        # hangs. There is no state on it worth draining.
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="tallyflow-ui",
            daemon=True,
        )
        self._thread.start()
        logger.info("the connector window can reach this connector at %s", self.url)
        return True

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
