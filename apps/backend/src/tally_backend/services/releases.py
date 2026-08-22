"""What the newest published build is, for every client that asks.

The backend did not used to know this at all: ``run.py publish`` wrote
``manifest.json``, Caddy served it, and each client polled it on its own clock.
That is why an update took up to six hours to reach a connector and a full app
restart to reach a phone.

Now the backend reads the same manifest and answers the question inline -- on
every HTTP response to the app, and on every WebSocket frame to a connector.
Nothing polls, and a deploy reaches the fleet within one heartbeat.

The manifest stays the single source of truth on purpose. It is generated from
the bytes actually staged for download, so its checksums cannot disagree with
the files being served; duplicating the version numbers into environment
variables would create a second place to forget on release day, and the symptom
-- a floor that locks out clients over a build that was never published -- locks
people out of their own accounting data.

**This service fails open, everywhere.** No manifest, unreadable manifest,
malformed manifest, unknown platform: every one of those yields "no opinion",
never "you are out of date". A missing config file must not be able to brick a
fleet, and getting that backwards would be the single most damaging bug in the
update path.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from tally_core.versioning import compare_versions, is_newer

logger = logging.getLogger(__name__)

#: Where the manifest lives when nothing is configured. Checked in order.
#:
#: The container path comes first because that is the deployment that matters;
#: the repo-relative path is what makes a developer checkout work without any
#: environment variable at all.
DEFAULT_MANIFEST_PATHS = (
    Path("/srv/downloads/manifest.json"),
    Path(__file__).resolve().parents[5] / "deploy" / "uat" / "downloads" / "manifest.json",
)


class UpdateAction(StrEnum):
    """What a client should do about the release it was told about."""

    NONE = "none"
    #: A newer build exists. Mention it; do not interrupt anyone.
    OPTIONAL = "optional"
    #: Below the floor, or the release is flagged mandatory. The client must not
    #: carry on as though nothing is wrong.
    REQUIRED = "required"


@dataclass(frozen=True)
class Release:
    """One platform's entry, as published."""

    platform: str
    version: str
    url: str
    sha256: str
    size_bytes: int
    file: str
    mandatory: bool = False
    min_supported_version: str = ""
    notes: str = ""
    build_number: int | None = None

    @classmethod
    def from_entry(cls, platform: str, entry: dict) -> Release | None:
        """Parse one manifest entry, or ``None`` if it is not usable.

        Returning ``None`` rather than raising: one malformed platform entry
        must not take the other platform's release down with it, and a manifest
        half-written by a concurrent ``publish`` is a transient state worth
        surviving.
        """
        try:
            version = str(entry["version"])
            url = str(entry["url"])
        except (KeyError, TypeError):
            logger.warning("release manifest entry for %r has no version or url", platform)
            return None

        if not version:
            return None

        build = entry.get("build_number")
        return cls(
            platform=platform,
            version=version,
            url=url,
            sha256=str(entry.get("sha256", "")).lower(),
            size_bytes=int(entry.get("size_bytes") or 0),
            file=str(entry.get("file", "")),
            mandatory=bool(entry.get("mandatory", False)),
            # Defaults to empty, not to the version being published. A missing
            # floor has to mean "no floor" -- defaulting it to the latest build
            # would make every client below the newest one *required*, turning a
            # routine release into a fleet-wide lockout.
            min_supported_version=str(entry.get("min_supported_version", "") or ""),
            notes=str(entry.get("notes", "")),
            build_number=int(build) if isinstance(build, int | float | str) and build else None,
        )

    def as_client_payload(self) -> dict[str, object]:
        """The subset an out-of-date client needs to fetch and verify the build."""
        return {
            "version": self.version,
            "url": self.url,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "file": self.file,
            "mandatory": self.mandatory,
            "min_supported_version": self.min_supported_version,
            "notes": self.notes,
            **({"build_number": self.build_number} if self.build_number is not None else {}),
        }


@dataclass(frozen=True)
class Verdict:
    """The answer to "is this client current?"."""

    action: UpdateAction
    current_version: str
    release: Release | None = None

    @property
    def is_required(self) -> bool:
        return self.action is UpdateAction.REQUIRED

    @property
    def has_update(self) -> bool:
        return self.action is not UpdateAction.NONE

    @property
    def latest_version(self) -> str:
        return self.release.version if self.release else ""

    @property
    def min_version(self) -> str:
        return self.release.min_supported_version if self.release else ""


#: Used whenever we have nothing to say, which is the common case in dev and the
#: safe case everywhere else.
NO_OPINION = Verdict(action=UpdateAction.NONE, current_version="")


class ReleaseCatalogue:
    """Reads ``manifest.json`` and answers version questions from it.

    Re-reads when the file's mtime or size changes, so ``run.py publish``
    followed by a file copy is picked up without restarting the API. Cached
    otherwise, because this is consulted on *every* request and every heartbeat
    -- a stat() per call is affordable, a JSON parse per call is not.

    Thread-safe by lock rather than by asyncio primitives: the middleware calls
    it from the request path and the hub from the socket loop, and Starlette's
    ``BaseHTTPMiddleware`` may run either on a worker thread.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._configured = Path(path) if path else None
        self._lock = threading.Lock()
        self._releases: dict[str, Release] = {}
        self._stamp: tuple[float, int] | None = None
        self._resolved: Path | None = None
        #: Logged once, not per call: an absent manifest in dev is normal and a
        #: warning on every request would drown the log it is trying to inform.
        self._warned_missing = False

    # -- resolution ------------------------------------------------------

    @property
    def path(self) -> Path | None:
        """The manifest actually in use, once found."""
        return self._resolved

    def _locate(self) -> Path | None:
        if self._configured is not None:
            return self._configured if self._configured.is_file() else None
        for candidate in DEFAULT_MANIFEST_PATHS:
            if candidate.is_file():
                return candidate
        return None

    def _load(self) -> dict[str, Release]:
        """Return the parsed manifest, re-reading only when the file changed."""
        path = self._locate()
        if path is None:
            if not self._warned_missing:
                self._warned_missing = True
                logger.info(
                    "no release manifest found; version checks are disabled "
                    "(set TALLYFLOW_RELEASE_MANIFEST_PATH to enable them)"
                )
            self._releases = {}
            self._stamp = None
            return self._releases

        try:
            stat = path.stat()
        except OSError:
            return self._releases

        stamp = (stat.st_mtime, stat.st_size)
        if stamp == self._stamp and self._resolved == path:
            return self._releases

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            # Keep serving the last good copy. A publish that is mid-write, or a
            # manifest someone hand-edited into invalid JSON, should not flip the
            # whole fleet to "unknown version".
            logger.warning("could not read release manifest %s: %s", path, exc)
            return self._releases

        if not isinstance(payload, dict):
            logger.warning("release manifest %s is not an object", path)
            return self._releases

        releases: dict[str, Release] = {}
        for platform, entry in payload.items():
            if not isinstance(entry, dict):
                continue  # `schema`, `generated_at`, `_comment`
            release = Release.from_entry(platform, entry)
            if release is not None:
                releases[platform] = release

        first_load = self._stamp is None
        self._releases = releases
        self._stamp = stamp
        self._resolved = path
        self._warned_missing = False
        logger.info(
            "%s release manifest %s: %s",
            "loaded" if first_load else "reloaded",
            path,
            ", ".join(f"{k}={v.version}" for k, v in sorted(releases.items())) or "empty",
        )
        return self._releases

    # -- queries ---------------------------------------------------------

    def latest(self, platform: str) -> Release | None:
        with self._lock:
            return self._load().get(platform)

    def evaluate(self, platform: str, current_version: str) -> Verdict:
        """Decide what ``current_version`` of ``platform`` should do.

        ``mandatory`` and ``min_supported_version`` are checked independently
        because they say different things: mandatory is "take this one", the
        floor is "the one you have no longer works". A client can be below the
        floor of a release that is not mandatory, and that still has to block.
        """
        if not current_version:
            # A client that did not identify itself. Nothing useful to say, and
            # guessing would mean blocking every request from an older build
            # that predates the header -- including its login.
            return NO_OPINION

        release = self.latest(platform)
        if release is None:
            return Verdict(action=UpdateAction.NONE, current_version=current_version)

        if not is_newer(release.version, current_version):
            # Already current, or ahead of it (a tester's build). Never report an
            # update in either case: offering a downgrade puts a client in a loop
            # between two versions.
            return Verdict(action=UpdateAction.NONE, current_version=current_version)

        below_floor = bool(release.min_supported_version) and (
            compare_versions(current_version, release.min_supported_version) < 0
        )
        return Verdict(
            action=UpdateAction.REQUIRED
            if (below_floor or release.mandatory)
            else UpdateAction.OPTIONAL,
            current_version=current_version,
            release=release,
        )


class ConnectorReleaseView:
    """The connector-shaped slice of the catalogue, for the hub to hold.

    Exists so ``hub.link`` -- which is transport plumbing and knows nothing about
    manifests -- can stamp versions and spot an outdated connector without
    importing this module or hard-coding the ``"connector"`` platform key. The
    hub sees a two-method object; swapping the catalogue for a database table
    later changes nothing on that side.
    """

    PLATFORM = "connector"

    def __init__(self, catalogue: ReleaseCatalogue) -> None:
        self._catalogue = catalogue

    @property
    def latest_version(self) -> str:
        release = self._catalogue.latest(self.PLATFORM)
        return release.version if release else ""

    @property
    def min_version(self) -> str:
        release = self._catalogue.latest(self.PLATFORM)
        return release.min_supported_version if release else ""

    def outdated(self, version: str) -> tuple[str, bool]:
        """``(target_version, mandatory)`` for a connector that should update.

        ``("", False)`` when it is current, unknown, or ahead of us -- the three
        cases that must produce no instruction at all.
        """
        verdict = self._catalogue.evaluate(self.PLATFORM, version)
        if not verdict.has_update or verdict.release is None:
            return "", False
        return verdict.release.version, verdict.is_required


def build_catalogue(path: str | None) -> ReleaseCatalogue:
    """Factory used at startup, so ``main`` need not know the default paths."""
    return ReleaseCatalogue(path or None)
