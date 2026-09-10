"""Keeping a connector running, across pairings and restarts.

``ConnectorSession`` owns one logical connection to the backend. This owns the
thing above it: the machine. It decides whether this PC is in a state to connect
at all, stands a session up when it is, tears one down and builds another when
somebody asks for a restart, and drops into the pairing screen when the account
says this computer is no longer trusted.

Three states, and the loop only ever moves between them:

**Pairing** -- no credentials, or credentials the account has disowned: this
computer was removed, or an admin asked for it to be paired again. A code is on
the local window waiting to be scanned, and nothing talks to the backend except
the poll that collects the answer.

**Running** -- a session is up, or trying to reconnect, which is the session's
own business and not this module's.

**Stopping** -- a signal, or the service shutting down.

The restart is deliberately a *session* restart rather than a process one.
Killing and relaunching the executable is the obvious reading of the button, and
it cannot work from inside: the page that would report the result dies with the
process, the scheduled task cannot be told to relaunch something it is currently
running, and on a developer's console there is no task at all. Tearing the
session down and building a new one from freshly loaded settings does everything
a process restart would -- a fresh socket, a re-read ``connector.json``, a new
Tally client on whatever port was just saved -- and it can answer the person who
pressed it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import Any

from tally_core.tally import TallyClient

from .config import ConnectorSettings, load_settings, save_pairing, save_settings
from .pairing import PairingClient, PairingError, PendingClaim
from .remote_logs import RemoteLogHandler
from .session import AuthenticationRejected, ConnectorSession
from .ui import LocalUiServer, UiBridge

logger = logging.getLogger(__name__)

#: How often TallyPrime is probed while this machine is waiting to be paired.
#: The session's own watcher does this once there is one; during pairing there
#: is no session, and "is Tally even talking to this PC?" is the question the
#: owner is standing there asking.
PAIRING_TALLY_PROBE_SECONDS = 15.0

#: Rejections that mean "ask to be paired again" rather than "something is
#: wrong". Both can only be produced by somebody deciding it -- this computer
#: was removed, or an admin asked for it to be paired again -- which is what
#: separates them from every other refusal. A signature check that fails for a
#: reason nobody has diagnosed, or a backend that cannot decrypt its own
#: secrets, leaves the stored pairing exactly where it is: that failure is
#: fleet-wide, and acting on it would take every customer offline at once and
#: leave each machine needing a visit.
PAIRING_REJECTIONS = frozenset({"revoked", "repairing"})

#: Exit codes. ``2`` and ``3`` predate this module and are what the installer's
#: documentation and support notes already refer to.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_UNPAIRED = 2
EXIT_REJECTED = 3


class ConnectorRunner:
    """The supervisor: one per process, for the life of the process."""

    def __init__(
        self,
        *,
        config_path: Path | None,
        version: str,
        log_handler: RemoteLogHandler | None = None,
    ) -> None:
        self._config_path = config_path
        self._version = version
        self._log_handler = log_handler

        self._stop = asyncio.Event()
        #: Set by the restart button. Distinct from ``_stop`` because the loop
        #: has to be able to tell "come back" from "go away".
        self._restart = asyncio.Event()
        #: Set when the current pairing wait should be abandoned -- a shutdown,
        #: or somebody asking for a fresh code.
        self._interrupt = asyncio.Event()
        #: True once the backend has disowned this machine's credentials --
        #: removed, or awaiting a re-pair. It survives into the next iteration
        #: of the loop, which is what turns a rejection into a pairing screen
        #: rather than an exit.
        self._needs_pairing = False

        self._session: ConnectorSession | None = None
        self._bridge: UiBridge | None = None
        self._ui: LocalUiServer | None = None

    # -- lifecycle -------------------------------------------------------

    async def stop(self) -> None:
        self._stop.set()
        self._interrupt.set()
        session = self._session
        if session is not None:
            await session.stop()

    async def run(self) -> int:
        settings = load_settings(self._config_path)

        if settings.ui_enabled:
            self._start_ui(settings)
        elif not settings.is_paired:
            # No page to show a code on, so there is nothing this process can do
            # but say how to pair from a command line.
            logger.error(
                "This connector is not paired yet. Open the TallyFlow app, add this "
                "computer, then run: tally-connector pair --id <ID> --secret <SECRET>"
            )
            return EXIT_UNPAIRED

        try:
            return await self._loop()
        finally:
            if self._ui is not None:
                self._ui.stop()

    async def _loop(self) -> int:
        while not self._stop.is_set():
            # Cleared here rather than where a session starts, because pairing
            # is interrupted by the same button and a flag left set would spin
            # this loop instead of drawing a new code.
            self._restart.clear()

            # Re-read every time round, not once at startup. A restart exists to
            # pick up a changed port or a new pairing, and settings held from
            # before the change would make the button appear to do nothing.
            settings = load_settings(self._config_path)
            self._publish(settings)

            if self._needs_pairing or not settings.is_paired:
                if not settings.ui_enabled:
                    return EXIT_UNPAIRED
                await self._pair(settings)
                continue

            code = await self._serve(settings)
            if code is not None:
                return code

        return EXIT_OK

    # -- running ---------------------------------------------------------

    async def _serve(self, settings: ConnectorSettings) -> int | None:
        """Run one session. ``None`` means "go round again"."""
        session = ConnectorSession(
            settings,
            version=self._version,
            log_handler=self._log_handler,
            observer=self._bridge,
        )
        self._session = session

        runner = asyncio.create_task(session.run_forever())
        waiter = asyncio.create_task(self._until_interrupted())

        done, _ = await asyncio.wait({runner, waiter}, return_when=asyncio.FIRST_COMPLETED)

        if waiter in done:
            # A stop or a restart. Either way this session is finished; the
            # difference is decided by the loop condition, not here.
            await session.stop()
            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner
        waiter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await waiter

        await session.aclose()
        self._session = None

        if runner in done and not runner.cancelled():
            return self._explain(runner.exception())
        return None

    def _explain(self, exc: BaseException | None) -> int | None:
        """What a finished session means for the process."""
        if exc is None:
            return None

        if isinstance(exc, AuthenticationRejected):
            logger.error("Backend rejected this connector: %s", exc)
            if exc.reason_code in PAIRING_REJECTIONS:
                # The one rejection that is an instruction rather than a fault:
                # this machine was removed or re-paired from the app, so it
                # stops offering a credential that cannot work and asks for a
                # new one. The old pairing is left on disk untouched until a new
                # one replaces it -- there is nothing to gain from deleting it,
                # and a connector that erases its own credentials on any server
                # answer is one bad deploy away from a fleet-wide outage.
                logger.info("This computer needs to be paired again.")
                self._needs_pairing = True
                self._report_pairing(
                    "This computer was removed from the account."
                    if exc.reason_code == "revoked"
                    else "This computer is being paired again."
                )
                return None
            logger.error("Re-pair this computer from the TallyFlow app.")
            return EXIT_REJECTED

        logger.error("connector stopped: %s", exc)
        return EXIT_FAILED

    async def _until_interrupted(self) -> None:
        """Resolves when somebody asks this session to end."""
        stopper = asyncio.create_task(self._stop.wait())
        restarter = asyncio.create_task(self._restart.wait())
        try:
            await asyncio.wait({stopper, restarter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (stopper, restarter):
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    # -- pairing ---------------------------------------------------------

    async def _pair(self, settings: ConnectorSettings) -> None:
        """Show a code until somebody scans it, or until we are told to stop.

        Returns when the machine is paired, when it is shutting down, or when a
        restart has been asked for. The caller goes round the loop either way,
        so there is no state to carry back.
        """
        client = PairingClient(
            api_base_url=settings.api_base_url, verify_tls=settings.verify_tls
        )
        probe = asyncio.create_task(self._probe_tally_while_pairing(settings))
        try:
            while not self._stop.is_set() and not self._restart.is_set():
                claim = await self._open_claim(client, settings)
                if claim is None:
                    # Could not reach the backend. Wait and try again rather
                    # than give up: the shop's internet coming back is the most
                    # likely next event, and nobody is going to restart this.
                    if await self._sleep_or_interrupted(10.0):
                        return
                    continue

                self._interrupt.clear()
                self._report_pairing("", claim=claim)
                pairing = await client.wait_for_pairing(claim, stop=self._interrupt)

                if pairing is None:
                    # Expired, interrupted, or a new code was asked for. All
                    # three are answered by drawing another one.
                    continue

                save_pairing(pairing.connector_id, pairing.secret, self._config_path)
                logger.info("paired as %s (%s)", pairing.name, pairing.connector_id)
                self._needs_pairing = False
                self._report_pairing("")
                if self._bridge is not None:
                    self._bridge.update(
                        paired=True,
                        connector_id=pairing.connector_id,
                        connector_name=pairing.name,
                    )
                return
        finally:
            probe.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await probe
            await client.aclose()

    async def _open_claim(
        self, client: PairingClient, settings: ConnectorSettings
    ) -> PendingClaim | None:
        host = settings.host_info(self._version)
        try:
            return await client.open_claim(
                hostname=host.hostname,
                os_name=host.os,
                connector_version=host.connector_version,
            )
        except PairingError as exc:
            logger.warning("could not get a pairing code: %s", exc)
            self._report_pairing(f"Could not reach TallyFlow: {exc}")
            return None

    async def _probe_tally_while_pairing(self, settings: ConnectorSettings) -> None:
        """Keep the Tally line on the page honest before there is a session.

        A machine that is not paired yet is one somebody is setting up, and half
        of what goes wrong at that moment is Tally's connectivity settings
        rather than ours. Telling them now saves the round trip of pairing
        successfully and then finding nothing works.
        """
        if self._bridge is None:
            return
        client = TallyClient(settings.tally_config())
        try:
            while True:
                with contextlib.suppress(Exception):
                    self._bridge.tally_changed(online=await client.is_alive())
                await asyncio.sleep(PAIRING_TALLY_PROBE_SECONDS)
        finally:
            await client.aclose()

    # -- the local window ------------------------------------------------

    def _start_ui(self, settings: ConnectorSettings) -> None:
        bridge = UiBridge(
            version=self._version, log_dir=str(settings.log_dir or "")
        )
        bridge.bind(asyncio.get_running_loop())
        bridge.register("restart", self._action_restart)
        bridge.register("refresh", self._action_refresh)
        bridge.register("port", self._action_port)
        bridge.register("new-code", self._action_new_code)

        server = LocalUiServer(bridge, port=settings.ui_port)
        self._bridge = bridge
        # Kept even if it could not bind, so every later update is a no-op
        # rather than a branch: the connector runs the same either way.
        self._ui = server if server.start() else None

    def _publish(self, settings: ConnectorSettings) -> None:
        if self._bridge is None:
            return
        self._bridge.update(
            paired=settings.is_paired and not self._needs_pairing,
            connector_id=settings.connector_id,
            backend_url=settings.backend_url,
            tally_host=settings.tally_host,
            tally_port=settings.tally_port,
            log_dir=str(settings.log_dir or ""),
        )

    def _report_pairing(self, detail: str, *, claim: PendingClaim | None = None) -> None:
        """Put a code, or the reason there isn't one, in front of the owner.

        Deliberately does not touch ``paired``. That answer comes from the
        settings on disk and from whether the backend has disowned this machine
        -- never from whether a code happens to be on screen, which would make
        "we could not reach TallyFlow to get a code" render as "paired".
        """
        if self._bridge is None:
            return
        self._bridge.update(
            pairing_payload=claim.payload() if claim else "",
            pairing_expires_at=(
                time.time() + claim.seconds_left if claim is not None else None
            ),
            pairing_detail=detail,
        )

    # -- what the buttons do ---------------------------------------------

    async def _action_restart(self, _: dict[str, Any]) -> dict[str, Any]:
        self._restart.set()
        self._interrupt.set()
        session = self._session
        if session is not None:
            await session.stop()
        return {"ok": True, "message": "Restarting the connector..."}

    async def _action_refresh(self, _: dict[str, Any]) -> dict[str, Any]:
        session = self._session
        if session is None:
            return {
                "ok": False,
                "message": "Not connected to TallyFlow, so there is nothing to refresh yet.",
            }
        if await session.request_roster():
            return {"ok": True, "message": "Refreshed."}
        return {"ok": False, "message": "Could not reach TallyFlow. Trying again shortly."}

    async def _action_port(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            port = int(payload.get("port"))
        except (TypeError, ValueError):
            return {"ok": False, "message": "That is not a port number."}
        if not 1 <= port <= 65535:
            return {"ok": False, "message": "A port must be between 1 and 65535."}

        # Written before the restart, never after: the restart re-reads the file,
        # so the order here is the whole mechanism rather than tidiness.
        save_settings({"tally_port": port}, self._config_path)
        logger.info("Tally port changed to %d from the connector window", port)
        if self._bridge is not None:
            self._bridge.update(tally_port=port, tally_online=None)
        await self._action_restart({})
        return {"ok": True, "message": f"Now using port {port}. Restarting..."}

    async def _action_new_code(self, _: dict[str, Any]) -> dict[str, Any]:
        if not self._interrupt.is_set():
            # Ends the current wait; the pairing loop opens a fresh claim, which
            # also abandons the old one -- a code that is still on somebody's
            # camera roll must stop working the moment it is replaced on screen.
            self._interrupt.set()
        return {"ok": True, "message": "Showing a new code..."}

    # -- helpers ---------------------------------------------------------

    async def _sleep_or_interrupted(self, seconds: float) -> bool:
        """Sleep, unless somebody asks for something. ``True`` if they did.

        Waits on ``_interrupt`` rather than ``_stop`` because every button that
        should cut a wait short sets it -- a shutdown, a restart, and "show me a
        new code" from a page whose owner is tired of looking at an error.
        """
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._interrupt.wait(), timeout=seconds)
        return self._stop.is_set() or self._restart.is_set()
