"""Connector configuration.

Resolution order, lowest priority first: defaults, then ``connector.json`` next
to the executable, then ``TALLY_CONNECTOR_*`` environment variables. The file is
what a non-technical shop owner gets from the pairing wizard; the env vars are
for development and support.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from tally_core.tally import TallyConfig

from .protocol import HostInfo

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "connector.json"

#: Backend hosts that were renamed, old name to new.
#:
#: This exists because an upgrade cannot repoint a machine any other way. The
#: installer writes ``connector.json`` on a *first* install only -- on an
#: upgrade the pairing and the address it was paired with are already on disk
#: and must survive -- so a build that merely carries a new default reaches
#: every shop PC and changes nothing. Without the rewrite below, retiring an
#: old hostname would mean visiting every customer's machine.
#:
#: Rewriting it here makes the update that carries this build also the thing
#: that moves the machine, which is what lets the old DNS record be retired at
#: all: once no connector dials the old name, nothing does.
#:
#: This is a one-time correction, not a feature. Every entry is a name we have
#: promised to keep answering on until the fleet has moved off it -- so delete
#: an entry once that is true, and delete the map when it is empty.
RENAMED_HOSTS = {
    "api-tallyflow.theshubhanshu.dev": "api-tallyflow.jsrprimesolution.com",
    "uat-tallyflow.theshubhanshu.dev": "uat-tallyflow.jsrprimesolution.com",
}


def install_dir() -> Path:
    """Directory the connector runs from.

    Under PyInstaller ``sys.executable`` is the frozen exe, which is where the
    pairing wizard writes ``connector.json``; in development it is the venv's
    python, so fall back to the source tree.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parents[3]


class ConnectorSettings(BaseSettings):
    """Everything the connector needs to run."""

    model_config = SettingsConfigDict(
        env_prefix="TALLY_CONNECTOR_",
        env_file=".env",
        extra="ignore",
    )

    # --- Identity -------------------------------------------------------
    connector_id: str = ""
    #: Shared secret from pairing. Never logged, never sent -- only used to sign.
    connector_secret: str = ""

    # --- Backend --------------------------------------------------------
    backend_url: str = "wss://api-tallyflow.jsrprimesolution.com/v1/connector"
    #: Only ever disabled for local development against a plaintext backend.
    verify_tls: bool = True
    reconnect_initial_seconds: float = 1.0
    reconnect_max_seconds: float = 60.0
    heartbeat_timeout_seconds: float = 90.0

    # --- Tally ----------------------------------------------------------
    tally_host: str = "127.0.0.1"
    tally_port: int = 9000
    tally_timeout_seconds: float = 60.0
    tally_heavy_timeout_seconds: float = 300.0

    # --- Behaviour ------------------------------------------------------
    #: How many jobs the connector will *accept* at once. They still reach Tally
    #: strictly one at a time; this only bounds how much sits in memory waiting.
    max_concurrent_jobs: int = 4
    #: Depth of the queue in front of Tally. Beyond this, requests are refused
    #: immediately with a retryable error instead of being accepted into a queue
    #: they would time out in -- the caller falls back to its snapshot, which is
    #: a far better answer than a spinner.
    tally_queue_max_depth: int = 16
    #: Pause after Tally times out or refuses a connection, before the next
    #: request goes out. A wedged gateway or an open modal dialog in Tally does
    #: not recover any faster for being hammered.
    tally_cooldown_seconds: float = 5.0
    #: How long a successful (or failed) Tally call stands in for a fresh
    #: liveness probe. Keeps the 30-second heartbeat from putting a request on
    #: the gateway when real work has just proved it is up.
    tally_liveness_ttl_seconds: float = 20.0
    cache_max_entries: int = 256
    cache_stale_ttl_seconds: float = 24 * 3600
    log_level: str = "INFO"
    log_dir: Path | None = None

    # --- Local window ---------------------------------------------------
    #: Serve the small status page on this machine. It is what somebody
    #: standing at the shop's PC can open to see whether the connector is
    #: working, which companies it feeds, and to pair it in the first place.
    #:
    #: Off is a supported way to run: everything the page does is also a CLI
    #: command, and a machine locked down by an IT department should be able to
    #: refuse the extra socket.
    ui_enabled: bool = True
    #: Loopback only, and not configurable to anything else -- see
    #: ``ui.server``. The port is settable because a shop PC is somebody else's
    #: machine and something may already be on this one.
    ui_port: int = 9787

    # --- Remote diagnostics ---------------------------------------------
    #: Ship log lines to the backend over the socket that is already open, so
    #: support can read them without asking a shop owner to find a file. Off
    #: leaves the connector logging to disk only, which is what a customer who
    #: does not want their machine talking about itself should get.
    remote_logs: bool = True
    #: Floor for what is shipped. INFO because the questions this exists to
    #: answer -- "did the sync run?", "was Tally reachable at 11am?" -- are
    #: answered by INFO lines; a WARNING-only feed shows the crash and not the
    #: half hour that led to it.
    remote_log_level: str = "INFO"
    #: How often the buffer is drained onto the socket. Batched rather than per
    #: line because these machines sit on asymmetric broadband where upload is
    #: already the bottleneck for report exports.
    remote_log_interval_seconds: float = 10.0
    #: Lines held while the backend is unreachable. Bounded because an internet
    #: outage must not turn into an out-of-memory kill on a shop's till; the
    #: oldest are dropped and the count is reported in the next batch.
    remote_log_buffer: int = 2000

    # --- Updates --------------------------------------------------------
    #: Install new builds without being asked. Off turns the updater into a
    #: reporter: it still logs that a version is available, but the shop runs
    #: the installer itself. Worth turning off on a machine where an unexpected
    #: connector restart would be noticed.
    #:
    #: Does *not* apply to a release flagged mandatory, or to this build being
    #: below the published floor. Those install regardless, because the setting
    #: means "do not interrupt my shop for routine releases" -- not "leave me
    #: serving figures I cannot compute correctly", which a shop owner has no way
    #: to notice for themselves.
    auto_update: bool = True
    #: Fallback interval for re-reading the manifest, no longer the main trigger.
    #:
    #: The backend stamps the published version on every frame it sends, so a
    #: connected connector normally hears about a release within one heartbeat
    #: and checks immediately. This interval covers the case that mechanism
    #: cannot: a connector that cannot reach the backend at all is exactly the
    #: one that may need a new build, and it will never be told.
    update_check_interval_seconds: float = 6 * 3600
    #: Overrides where the manifest is fetched from. Empty means "derive it from
    #: backend_url", which keeps a UAT connector pointed at UAT artefacts
    #: without a second setting to get wrong.
    update_manifest_url: str = ""

    @property
    def api_base_url(self) -> str:
        """The backend's HTTP origin, derived from the socket address.

        Same derivation as :attr:`manifest_url` and for the same reason: there
        is one address to configure on a shop PC, and a second setting for the
        same host is a second thing to get wrong on a support call. Pairing and
        the update manifest both ride on it.
        """
        base = self.backend_url
        base = "https://" + base[6:] if base.startswith("wss://") else "http://" + base[5:]
        return base.split("/v1/", 1)[0].rstrip("/")

    @property
    def manifest_url(self) -> str:
        """Where to look for the update manifest.

        Derived from ``backend_url`` so there is one address to configure. The
        websocket scheme maps to its HTTP equivalent, and the API path is
        dropped: the manifest is served by the same host off /downloads.
        """
        if self.update_manifest_url:
            return self.update_manifest_url
        base = self.backend_url
        base = "https://" + base[6:] if base.startswith("wss://") else "http://" + base[5:]
        host = base.split("/v1/", 1)[0].rstrip("/")
        return f"{host}/downloads/manifest.json"

    @field_validator("backend_url")
    @classmethod
    def _require_websocket_url(cls, value: str) -> str:
        if not value.startswith(("ws://", "wss://")):
            raise ValueError("backend_url must be a ws:// or wss:// URL")
        return value

    @field_validator("log_level", "remote_log_level")
    @classmethod
    def _valid_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"invalid log level: {value}")
        return level

    @property
    def is_paired(self) -> bool:
        return bool(self.connector_id and self.connector_secret)

    def tally_config(self) -> TallyConfig:
        return TallyConfig(
            host=self.tally_host,
            port=self.tally_port,
            timeout_seconds=self.tally_timeout_seconds,
            heavy_timeout_seconds=self.tally_heavy_timeout_seconds,
        )

    def host_info(self, connector_version: str) -> HostInfo:
        return HostInfo(
            hostname=platform.node(),
            os=f"{platform.system()} {platform.release()}",
            connector_version=connector_version,
            python_version=platform.python_version(),
        )

    def redacted(self) -> dict[str, object]:
        """Config safe to write to a log or a diagnostics bundle."""
        data = self.model_dump(mode="json")
        if data.get("connector_secret"):
            data["connector_secret"] = "***redacted***"
        return data


def repointed_url(url: str) -> str | None:
    """``url`` under its host's new name, or ``None`` if the host has not moved.

    Only the host is replaced. The scheme, port and path are left exactly as
    they are: a support engineer who set ``ws://`` against a plaintext test
    backend, or a non-standard path, meant it, and a rename is no reason to
    overrule them.
    """
    parsed = urlsplit(url)
    new_host = RENAMED_HOSTS.get(parsed.hostname or "")
    if new_host is None:
        return None
    # netloc, not hostname, so a userinfo or an explicit :port survives.
    return urlunsplit(parsed._replace(netloc=parsed.netloc.replace(parsed.hostname, new_host, 1)))


def load_settings(config_path: Path | None = None) -> ConnectorSettings:
    """Load settings from disk, then let the environment override."""
    path = config_path or install_dir() / CONFIG_FILENAME
    file_values: dict[str, object] = {}

    if path.is_file():
        try:
            file_values = json.loads(path.read_text(encoding="utf-8"))
            logger.info("loaded connector config from %s", path)
        except (OSError, json.JSONDecodeError) as exc:
            # A corrupt config must not stop the connector from starting; it can
            # still come up unpaired and be re-paired from the app.
            logger.error("could not read %s: %s; falling back to defaults", path, exc)

    # Applied to the file value rather than to the loaded settings, so an
    # operator's TALLY_CONNECTOR_BACKEND_URL still wins and is never written to
    # disk -- an env override is deliberately not this machine's configuration.
    moved = repointed_url(str(file_values.get("backend_url", "")))
    if moved is not None:
        logger.warning("backend host renamed; this connector now dials %s", moved)
        file_values["backend_url"] = moved
        try:
            save_settings({"backend_url": moved}, path)
        except OSError as exc:
            # Not fatal, and specifically not a reason to refuse to start. The
            # value in memory is already right, so this run reaches the new
            # host; only the persistence is lost, and the next start retries.
            logger.error("could not record the new backend address in %s: %s", path, exc)

    # BaseSettings already layers env over these, so passing file values as
    # explicit kwargs gives file < env precedence for free.
    known = set(ConnectorSettings.model_fields)
    return ConnectorSettings(**{k: v for k, v in file_values.items() if k in known})


def save_settings(values: Mapping[str, object], config_path: Path | None = None) -> Path:
    """Merge ``values`` into connector.json, leaving every other key untouched.

    Read-modify-write rather than overwrite because the installer and the
    pairing command each own only a couple of keys, and a support engineer may
    well have hand-edited ``tally_port`` or ``log_level`` in the same file.
    """
    path = config_path or install_dir() / CONFIG_FILENAME
    existing: dict[str, object] = {}
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            existing = {}

    # `None` means "leave whatever is there alone", so a caller can pass an
    # optional field through without having to branch.
    existing.update(
        {k: str(v) if isinstance(v, Path) else v for k, v in values.items() if v is not None}
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(existing, indent=2), encoding="utf-8")

    # The file holds a credential that grants read access to the company's
    # books, so keep it owner-only where the platform supports it.
    if os.name != "nt":
        path.chmod(0o600)
    return path


def save_pairing(connector_id: str, connector_secret: str, config_path: Path | None = None) -> Path:
    """Persist pairing credentials, preserving any other settings in the file."""
    return save_settings(
        {"connector_id": connector_id, "connector_secret": connector_secret}, config_path
    )
