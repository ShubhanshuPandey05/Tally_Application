"""What the local window shows, and how a click on it reaches the event loop.

Two threads meet here and neither may block the other. The connector's work
happens on an asyncio loop; the window polls a small threaded HTTP server from
its own process, because a shop PC should not have a web framework installed on
it to answer one status screen. So:

* **Reading** is a snapshot under a lock. The window never touches live objects,
  which is what stops a poll from being able to observe the session halfway
  through a reconnect.
* **Writing** is a coroutine posted onto the loop and waited for with a timeout.
  A button that hangs is worse than a button that says it could not do it -- the
  person pressing it is already looking at a machine they think is broken.

The bridge also satisfies the observer protocol in :mod:`tally_connector.session`,
which is how the session reports connection and Tally state without importing
anything about a user interface. That direction matters: the connector must run
perfectly with ``ui_enabled`` off, and it does, because nothing below this
module knows the window exists.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from tally_core.protocol import Roster

logger = logging.getLogger(__name__)

#: Longest a button press may take before the window is told it failed. Chosen
#: against the slowest action -- a restart, which tears down a socket and stands
#: a new session up -- rather than against the fastest.
ACTION_TIMEOUT_SECONDS = 20.0

#: ``(payload) -> result``. Runs on the connector's event loop.
Action = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat()


@dataclass
class UiState:
    """Everything the window draws, in one object with one owner.

    Deliberately flat and JSON-shaped. The window is the only reader, it renders
    what it is given, and a snapshot that needed assembling from three live
    objects would be a snapshot that could disagree with itself.
    """

    version: str = ""
    started_at: float = field(default_factory=time.time)

    # -- pairing ---------------------------------------------------------
    paired: bool = False
    connector_id: str = ""
    connector_name: str = ""
    #: Set while this machine is waiting to be scanned. Its presence is what the
    #: window keys the whole pairing screen off.
    pairing_payload: str = ""
    #: When the code on screen stops working, as a wall-clock moment rather than
    #: a countdown. A stored "seconds left" would be whatever it was when the
    #: claim was opened, and the window would cheerfully promise fourteen minutes
    #: for the whole fourteen minutes.
    pairing_expires_at: float | None = None
    pairing_detail: str = ""

    # -- backend ---------------------------------------------------------
    backend_url: str = ""
    backend_connected: bool = False
    backend_detail: str = ""
    session_id: str = ""
    connected_since: float | None = None

    # -- Tally -----------------------------------------------------------
    tally_host: str = ""
    tally_port: int = 0
    #: ``None`` before the first probe. Not ``False``: "we have not looked yet"
    #: and "TallyPrime is not answering" are different things to tell somebody
    #: standing in front of the machine, and the second one sends them to
    #: restart software that is running perfectly.
    tally_online: bool | None = None
    companies_open: list[str] = field(default_factory=list)

    # -- roster ----------------------------------------------------------
    organisation: str = ""
    org_status: str = ""
    companies: list[dict[str, Any]] = field(default_factory=list)
    users: list[dict[str, Any]] = field(default_factory=list)
    roster_at: datetime | None = None

    log_dir: str = ""

    def as_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "uptime_seconds": int(time.time() - self.started_at),
            "paired": self.paired,
            "connector_id": self.connector_id,
            "connector_name": self.connector_name,
            "pairing": {
                "waiting": bool(self.pairing_payload),
                "payload": self.pairing_payload,
                "seconds_left": (
                    0
                    if self.pairing_expires_at is None
                    else max(0, int(self.pairing_expires_at - time.time()))
                ),
                "detail": self.pairing_detail,
            },
            "backend": {
                "url": self.backend_url,
                "connected": self.backend_connected,
                "detail": self.backend_detail,
                "session_id": self.session_id,
                "connected_seconds": (
                    None
                    if self.connected_since is None
                    else int(time.time() - self.connected_since)
                ),
            },
            "tally": {
                "host": self.tally_host,
                "port": self.tally_port,
                "online": self.tally_online,
                "companies_open": list(self.companies_open),
            },
            "account": {
                "organisation": self.organisation,
                "status": self.org_status,
                "companies": list(self.companies),
                "users": list(self.users),
                "as_of": _iso(self.roster_at),
            },
            "log_dir": self.log_dir,
        }


class UiBridge:
    """The window's view of the connector, and its handful of levers."""

    def __init__(self, *, version: str, log_dir: str = "") -> None:
        self._lock = threading.Lock()
        self._state = UiState(version=version, log_dir=log_dir)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._actions: dict[str, Action] = {}

    # -- wiring ----------------------------------------------------------

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Attach the loop that actions will be posted onto.

        Called from the loop's own thread when the connector starts. Before it,
        the window still renders -- it just reports that nothing can be pressed
        yet, which is true.
        """
        self._loop = loop

    def register(self, name: str, action: Action) -> None:
        self._actions[name] = action

    # -- reading ---------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._state.as_json()

    def pairing_payload(self) -> str:
        with self._lock:
            return self._state.pairing_payload

    def update(self, **fields: Any) -> None:
        """Set fields on the state. Unknown names are ignored on purpose.

        This is called from the connector's hot paths -- a status transition, a
        roster arriving -- and an attribute typo there must cost a wrong window,
        never a dropped session.
        """
        with self._lock:
            for key, value in fields.items():
                if hasattr(self._state, key):
                    setattr(self._state, key, value)
                else:
                    logger.debug("ignoring unknown UI field %r", key)

    # -- the session observer protocol -----------------------------------

    def connection_changed(
        self, *, connected: bool, session_id: str = "", detail: str = ""
    ) -> None:
        self.update(
            backend_connected=connected,
            session_id=session_id,
            backend_detail=detail,
            connected_since=time.time() if connected else None,
        )

    def tally_changed(
        self, *, online: bool | None, companies_open: list[str] | None = None
    ) -> None:
        fields: dict[str, Any] = {"tally_online": online}
        # An empty list from a heartbeat means "this frame carried no company
        # list", not "no company is open" -- overwriting on it would blank the
        # one line that explains a stale dashboard.
        if companies_open:
            fields["companies_open"] = list(companies_open)
        self.update(**fields)

    def roster_received(self, roster: Roster) -> None:
        fields: dict[str, Any] = {
            "organisation": roster.organisation,
            "org_status": roster.org_status,
            "companies": [company.model_dump(mode="json") for company in roster.companies],
            "users": [user.model_dump(mode="json") for user in roster.users],
            "roster_at": datetime.now(UTC),
        }
        # Only when the roster actually carries one. Reading the current value
        # to fall back on would mean taking the lock inside the argument list of
        # a call that takes it again -- which works today only because the two
        # do not overlap, and is a deadlock the first time somebody tidies it.
        if roster.connector_name:
            fields["connector_name"] = roster.connector_name
        self.update(**fields)

    # -- writing ---------------------------------------------------------

    def invoke(self, name: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run a registered action on the event loop and wait for its answer.

        Called from an HTTP worker thread. Every failure comes back as a result
        the window can render, never as an exception across the thread boundary --
        a traceback in a status window is not an answer to "why is my dashboard
        empty?".
        """
        action = self._actions.get(name)
        loop = self._loop
        if action is None:
            return {"ok": False, "message": f"{name} is not something this build can do."}
        if loop is None or loop.is_closed():
            return {"ok": False, "message": "The connector is still starting up."}

        future = asyncio.run_coroutine_threadsafe(action(payload or {}), loop)
        try:
            return future.result(timeout=ACTION_TIMEOUT_SECONDS)
        except TimeoutError:
            # Deliberately not cancelled. A restart that is merely slow will
            # still finish, and cancelling it halfway would leave the connector
            # in the one state nothing else recovers from.
            return {
                "ok": False,
                "message": "That is taking longer than expected. Check back in a moment.",
            }
        except Exception as exc:  # noqa: BLE001 - the window must always get an answer
            logger.warning("UI action %s failed", name, exc_info=True)
            return {"ok": False, "message": str(exc)}
