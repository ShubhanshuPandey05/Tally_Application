"""Keeps the connector on the current build without anyone visiting the shop.

The connector runs on a PC in a shop, behind whatever network the shop has, with
nobody who wants to think about it. An update that needs a person is an update
that does not happen -- and a fleet of connectors months apart in version is how
a wire change turns into twenty support calls at once.

So this is deliberately unattended, and every risk that creates is answered
here rather than left to the operator:

``A half-downloaded installer must never run.``
    The manifest carries a SHA-256 measured from the exact bytes being served
    (``run.py publish``). A download that does not match it is deleted, not
    retried in place -- a truncated 38 MB exe would otherwise be re-used
    forever, failing identically each time.

``An update must not interrupt a read.``
    Tally serves one caller at a time and an export can run for minutes.
    Installing means killing that, and the phone waiting on it sees a blank
    dashboard. The updater waits for the pipeline to go idle, however long that
    takes; there is no deadline worth breaking a report over.

``The connector cannot overwrite itself while running.``
    Windows holds an open executable locked. The installer stops the service
    task first (``PrepareToInstall`` in the .iss), but the *running* process is
    this one -- so it spawns setup detached and exits immediately, leaving the
    installer to replace the files and start the new build.

The path that actually matters is the one nobody tests: an upgrade that leaves
the connector installed but unregistered. ``install`` with no id or secret now
re-registers against the pairing already on disk (``main._reinstall``); before
that existed, a silent upgrade would have replaced the executables and never
started them again.

The six-hourly poll is no longer the primary trigger. Every frame the backend
sends carries the published version, so :meth:`UpdateManager.nudge` is called
from the socket loop the moment a release appears and the timer is only the
fallback for a connector that cannot reach the backend at all. That is the
difference between a fleet that converges in seconds and one that takes a
working day.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx
from tally_core.versioning import compare_versions, is_newer, parse_version

logger = logging.getLogger(__name__)

__all__ = [
    "DOWNLOAD_TIMEOUT_SECONDS",
    "IDLE_POLL_SECONDS",
    "MANIFEST_TIMEOUT_SECONDS",
    "Release",
    "UpdateError",
    "UpdateManager",
    "default_download_dir",
    "is_newer",
    "parse_version",
    "supported",
]

#: Give up on a manifest quickly. It is a few hundred bytes and nothing waits
#: on it; a shop on bad broadband should not have a background chore holding a
#: connection open for a minute.
MANIFEST_TIMEOUT_SECONDS = 20.0

#: The installer is tens of megabytes over a home connection, so this is
#: generous. It bounds a stall, not a slow download.
DOWNLOAD_TIMEOUT_SECONDS = 600.0

#: How often to re-check whether Tally has gone quiet, once an update is ready
#: and waiting for the pipeline to drain.
IDLE_POLL_SECONDS = 30.0


class UpdateError(Exception):
    """A step of the update failed. Always recoverable by trying again later."""


@dataclass(frozen=True)
class Release:
    """One platform's entry in the manifest."""

    version: str
    url: str
    sha256: str
    size_bytes: int
    file: str
    mandatory: bool = False
    min_supported_version: str = "0.0.0"
    notes: str = ""

    @classmethod
    def from_manifest(cls, entry: dict) -> Release:
        try:
            return cls(
                version=str(entry["version"]),
                url=str(entry["url"]),
                sha256=str(entry["sha256"]).lower(),
                size_bytes=int(entry["size_bytes"]),
                file=str(entry["file"]),
                mandatory=bool(entry.get("mandatory", False)),
                min_supported_version=str(entry.get("min_supported_version", "0.0.0")),
                notes=str(entry.get("notes", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise UpdateError(f"malformed manifest entry: {exc}") from exc

    def is_required_over(self, current: str) -> bool:
        """Whether ``current`` is past the point of being asked politely.

        Either the release is flagged mandatory, or ``current`` is below the
        published floor -- meaning this build can no longer serve reads correctly,
        which for a connector is worse than an interruption. A shop looking at
        subtly wrong figures does not know to complain.
        """
        if self.mandatory:
            return True
        return (
            bool(self.min_supported_version)
            and compare_versions(current, self.min_supported_version) < 0
        )


class UpdateManager:
    """Polls the manifest and installs new connector builds when Tally is idle."""

    def __init__(
        self,
        *,
        current_version: str,
        manifest_url: str,
        pipeline=None,
        auto_update: bool = True,
        check_interval_seconds: float = 6 * 3600,
        download_dir: Path | None = None,
        verify_tls: bool = True,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._current = current_version
        self._manifest_url = manifest_url
        self._pipeline = pipeline
        self._auto = auto_update
        self._interval = check_interval_seconds
        self._download_dir = download_dir or default_download_dir()
        self._verify_tls = verify_tls
        self._client = client
        self._owns_client = client is None
        self._task: asyncio.Task[None] | None = None
        #: Versions already attempted this session. A build that fails to
        #: verify or refuses to launch would otherwise be retried every six
        #: hours forever, re-downloading tens of megabytes each time.
        self._failed: set[str] = set()
        #: Cuts the wait short when the backend says a release exists.
        self._wake = asyncio.Event()
        #: True while a check is running. A nudge arriving mid-download must not
        #: queue a second pass -- frames arrive continuously, and every one of
        #: them carries a version, so without this a single release would trigger
        #: a check per heartbeat for as long as the install takes.
        self._checking = False

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Begin checking in the background. Safe to call more than once."""
        if not supported():
            logger.info("automatic updates are Windows-only; skipping the updater")
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="connector-updater")

    async def aclose(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:  # noqa: SIM105 - contextlib.suppress reads worse around await
                await task
            except asyncio.CancelledError:
                pass
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(verify=self._verify_tls, follow_redirects=True)
        return self._client

    def nudge(self, reason: str = "") -> None:
        """Check now instead of waiting out the interval.

        Called from the session loop when a server frame advertises a version
        newer than this build, and when the backend sends an explicit update
        command. Synchronous and non-blocking on purpose: it runs on the socket's
        read path, where awaiting a download would stall every frame behind it --
        including the heartbeat the backend uses to decide this connector is
        still alive.

        Always sets the flag, even mid-check. The event coalesces, so a hundred
        frames arriving during a four-minute download cost exactly one extra pass
        afterwards -- whereas dropping nudges while busy would lose the one that
        arrived a second after a check read the old manifest, and that connector
        would then wait out the full interval for a release it had been told
        about.
        """
        if self._task is None or self._task.done():
            return
        # Logged only when idle: the caller sees a version on every single frame,
        # so an unguarded line here would be the noisiest thing in the log.
        if reason and not self._checking:
            logger.info("update check triggered: %s", reason)
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            # Cleared *before* the check, not after: a nudge that arrives while
            # check_once is running has to survive it, because that check may
            # already have read the manifest by the time the release landed.
            self._wake.clear()
            self._checking = True
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a failed check must not stop the connector
                logger.warning("update check failed", exc_info=True)
            finally:
                self._checking = False

            # The interval is now a fallback, not the schedule: a nudge from the
            # socket normally gets here first. It still matters -- a connector
            # that cannot reach the backend at all is exactly the one that may
            # need a new build, and it will never be nudged.
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=self._interval)

    # -- the check -------------------------------------------------------

    async def check_once(self) -> Release | None:
        """Fetch the manifest and act on it. Returns the release, if any."""
        release = await self.available()
        if release is None:
            return None

        logger.info(
            "connector %s is available (running %s)%s",
            release.version,
            self._current,
            " -- required" if release.mandatory else "",
        )

        required = release.is_required_over(self._current)

        if not self._auto and not required:
            logger.info(
                "automatic updates are off; install it from %s when convenient", release.url
            )
            return release

        if not self._auto and required:
            # Deliberately overrides the operator's preference. `auto_update:
            # false` means "do not interrupt my shop for routine releases", not
            # "leave me on a build that reports the wrong figures" -- and a
            # connector below the floor has no way to tell the owner that the
            # numbers on their phone are wrong.
            logger.warning(
                "installing %s despite auto_update being off: %s",
                release.version,
                "flagged mandatory"
                if release.mandatory
                else f"below the supported floor {release.min_supported_version}",
            )

        try:
            await self.apply(release)
        except Exception as exc:  # noqa: BLE001 - see below
            # Remembered so the next check does not re-download a build that
            # has already proved unusable on this machine.
            #
            # Any exception, not just UpdateError. An unexpected one used to
            # escape to the caller's warning and leave the version unmarked, so
            # the next heartbeat tried the same install again -- and a failure
            # that happens after setup has been launched detached means a shop
            # PC running the installer on a loop. Whatever went wrong, this
            # build has proved it does not install here; that is the fact worth
            # remembering, and the reason is for the log to carry.
            self._failed.add(release.version)
            logger.error("could not install connector %s: %s", release.version, exc, exc_info=True)
        return release

    async def available(self) -> Release | None:
        """The release worth installing, or ``None``."""
        manifest = await self._fetch_manifest()
        entry = manifest.get("connector")
        if not isinstance(entry, dict):
            logger.debug("manifest has no connector entry")
            return None

        release = Release.from_manifest(entry)
        if release.version in self._failed:
            return None
        if not is_newer(release.version, self._current):
            return None
        return release

    async def _fetch_manifest(self) -> dict:
        client = self._ensure_client()
        try:
            response = await client.get(self._manifest_url, timeout=MANIFEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise UpdateError(f"could not fetch {self._manifest_url}: {exc}") from exc
        except ValueError as exc:
            raise UpdateError(f"{self._manifest_url} is not valid JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise UpdateError(f"{self._manifest_url} is not a manifest object")
        return payload

    # -- applying --------------------------------------------------------

    async def apply(self, release: Release) -> None:
        """Download, verify, wait for quiet, then hand over to the installer."""
        installer = await self._download(release)
        await self._wait_until_idle()
        self._launch(installer, release)

    async def _download(self, release: Release) -> Path:
        """Fetch the installer and prove it is the file the manifest describes."""
        self._download_dir.mkdir(parents=True, exist_ok=True)
        target = self._download_dir / release.file

        if target.is_file() and _digest(target) == release.sha256:
            logger.info("reusing already-downloaded %s", target.name)
            return target

        url = urljoin(self._manifest_url, release.url)
        logger.info("downloading %s (%.1f MB)", url, release.size_bytes / 1e6)

        client = self._ensure_client()
        # Written beside the target and renamed on success, so an interrupted
        # download can never be mistaken for a complete one by the branch above.
        partial = target.with_suffix(target.suffix + ".part")
        try:
            with partial.open("wb") as handle:
                async with client.stream("GET", url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
                    response.raise_for_status()
                    async for chunk in response.aiter_bytes(256 * 1024):
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            partial.unlink(missing_ok=True)
            raise UpdateError(f"download failed: {exc}") from exc

        actual = _digest(partial)
        if actual != release.sha256:
            partial.unlink(missing_ok=True)
            raise UpdateError(
                f"checksum mismatch for {release.file}: manifest says "
                f"{release.sha256[:16]}..., downloaded file is {actual[:16]}... "
                f"-- refusing to run it"
            )

        target.unlink(missing_ok=True)
        partial.rename(target)
        logger.info("verified %s", target.name)
        return target

    async def _wait_until_idle(self) -> None:
        """Block until nothing is being read from Tally.

        No timeout on purpose. An export that runs long is a shop with years of
        history, which is exactly the customer who least wants their report
        killed for a background upgrade. The update waits; the report does not.
        """
        if self._pipeline is None:
            return

        waited = 0.0
        while self._pipeline.busy or self._pipeline.depth > 0:
            if waited == 0.0:
                logger.info("update ready; waiting for TallyPrime to finish current work")
            await asyncio.sleep(IDLE_POLL_SECONDS)
            waited += IDLE_POLL_SECONDS

        if waited:
            logger.info("Tally went idle after %.0fs; installing now", waited)

    def _launch(self, installer: Path, release: Release) -> None:
        """Start setup detached and stop this process so it can be replaced.

        The exit is the point. Windows will not let the installer overwrite
        ``tally-connector-service.exe`` while this process is running it, and
        setup has already stopped the *task* -- so the last thing standing
        between the new build and the disk is us.

        The startup task is re-registered and started by the installer's
        ``install`` step, so the connector comes back on its own.
        """
        command = [str(installer), "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"]
        logger.info("installing connector %s and restarting", release.version)

        # DETACHED_PROCESS + new process group: the installer has to outlive
        # this process, and it is about to.
        creationflags = 0
        if os.name == "nt":  # pragma: no branch - guarded by supported()
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

        try:
            subprocess.Popen(  # noqa: S603 - a file we downloaded and hash-verified
                command,
                creationflags=creationflags,
                close_fds=True,
            )
        except OSError as exc:
            raise UpdateError(f"could not start {installer.name}: {exc}") from exc

        # os._exit rather than a clean shutdown: every remaining path in this
        # process holds a handle on a file the installer is about to replace,
        # and a graceful drain would race it. The backend sees the socket drop,
        # which is the same thing it sees on any connector restart.
        logger.info("exiting so the installer can replace this build")
        # Both streams are None in the windowed PyInstaller build -- a Windows
        # service has no console attached -- and until 0.2.3 this raised
        # AttributeError on the line before the exit. The installer had already
        # been started detached by then, so the machine was left with setup
        # running against a connector that never let go of its own files, and
        # the caller retried the whole thing on the next heartbeat.
        #
        # Nothing may stand between a launched installer and this exit. The
        # flush is a courtesy for the console build; the exit is the contract.
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                with contextlib.suppress(Exception):
                    stream.flush()
        os._exit(0)


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def supported() -> bool:
    """Whether this build can update itself.

    Two conditions, both required. Windows, because the artefact is an .exe --
    and frozen, because a developer running from source has a checkout, not an
    installation, and replacing it with an installer build would silently
    discard their working tree.
    """
    return os.name == "nt" and getattr(sys, "frozen", False)


def default_download_dir() -> Path:
    """Where installers are staged.

    Beside the logs under LOCALAPPDATA, not in the install directory: {app} is
    what setup is about to overwrite, and a per-user location needs no
    elevation. Falls back to the system temp directory on a machine with no
    LOCALAPPDATA, which is a developer, not a shop.
    """
    local = os.environ.get("LOCALAPPDATA")
    root = Path(local) if local else Path(tempfile.gettempdir())
    return root / "TallyFlow Connector" / "updates"
