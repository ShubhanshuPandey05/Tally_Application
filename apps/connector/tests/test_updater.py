"""The self-updater.

An unattended updater on a machine nobody visits has two ways to be worse than
no updater at all: installing something that is not what the manifest described,
and killing a Tally export that a phone is waiting on. Both are pinned here.

``_launch`` cannot be run end to end -- it calls ``os._exit`` by design, so a
test that reached it would take the test runner with it -- but the handover is
covered with ``os._exit`` and ``Popen`` stubbed. It is tested because the four
lines that were once dismissed as too small to break shipped 0.2.2 with an
``AttributeError`` between a launched installer and the exit it depends on.
"""

from __future__ import annotations

import asyncio
import hashlib

import httpx
import pytest

from tally_connector.config import ConnectorSettings
from tally_connector.updater import (
    Release,
    UpdateError,
    UpdateManager,
    is_newer,
    parse_version,
)

INSTALLER_BYTES = b"MZ" + b"pretend installer" * 100
INSTALLER_SHA = hashlib.sha256(INSTALLER_BYTES).hexdigest()

MANIFEST_URL = "https://example.test/downloads/manifest.json"


def manifest(version: str = "0.2.0", *, sha: str | None = None, **extra) -> dict:
    entry = {
        "version": version,
        "file": f"TallyFlowConnector-Setup-{version}.exe",
        "url": f"/downloads/TallyFlowConnector-Setup-{version}.exe",
        "sha256": sha if sha is not None else INSTALLER_SHA,
        "size_bytes": len(INSTALLER_BYTES),
        "mandatory": False,
        "min_supported_version": "0.1.0",
        "notes": "",
    }
    entry.update(extra)
    return {"schema": 1, "connector": entry}


def serving(payload, *, body: bytes = INSTALLER_BYTES):
    """A fake CDN. Returns the handler and a log of what was requested."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("manifest.json"):
            if isinstance(payload, int):
                return httpx.Response(payload)
            return httpx.Response(200, json=payload)
        return httpx.Response(200, content=body)

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def manager_for(handler, tmp_path, *, current: str = "0.1.0", **kwargs) -> UpdateManager:
    return UpdateManager(
        current_version=current,
        manifest_url=MANIFEST_URL,
        download_dir=tmp_path,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


# --------------------------------------------------------------------------
# Version comparison
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("candidate", "current", "expected"),
    [
        ("0.2.0", "0.1.0", True),
        ("0.1.0", "0.1.0", False),
        ("0.1.0", "0.2.0", False),
        # The release at which a string comparison silently stops offering
        # upgrades: "0.10.0" sorts before "0.9.0" as text.
        ("0.10.0", "0.9.0", True),
        ("1.0.0", "0.99.99", True),
        ("0.2.0-rc1", "0.2.0", False),
    ],
)
def test_version_comparison(candidate, current, expected):
    assert is_newer(candidate, current) is expected


def test_versions_parse_to_numbers():
    assert parse_version("0.10.2") == (0, 10, 2)


# --------------------------------------------------------------------------
# Deciding whether to update
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_newer_build_is_offered(tmp_path):
    manager = manager_for(serving(manifest("0.2.0")), tmp_path)
    release = await manager.available()
    assert release is not None
    assert release.version == "0.2.0"
    await manager.aclose()


@pytest.mark.asyncio
async def test_the_running_version_is_not_offered_to_itself(tmp_path):
    manager = manager_for(serving(manifest("0.1.0")), tmp_path)
    assert await manager.available() is None
    await manager.aclose()


@pytest.mark.asyncio
async def test_a_rolled_back_manifest_does_not_downgrade(tmp_path):
    """Otherwise the fleet flaps between two versions forever."""
    manager = manager_for(serving(manifest("0.0.9")), tmp_path)
    assert await manager.available() is None
    await manager.aclose()


@pytest.mark.asyncio
async def test_an_unreachable_manifest_is_an_error_not_a_silent_no(tmp_path):
    manager = manager_for(serving(503), tmp_path)
    with pytest.raises(UpdateError):
        await manager.available()
    await manager.aclose()


@pytest.mark.asyncio
async def test_a_manifest_missing_the_connector_entry_is_not_an_error(tmp_path):
    """A manifest that only publishes the app is normal, not broken."""
    manager = manager_for(serving({"schema": 1, "android": {}}), tmp_path)
    assert await manager.available() is None
    await manager.aclose()


@pytest.mark.asyncio
async def test_a_malformed_entry_is_refused_rather_than_guessed(tmp_path):
    manager = manager_for(serving({"connector": {"version": "0.2.0"}}), tmp_path)
    with pytest.raises(UpdateError):
        await manager.available()
    await manager.aclose()


# --------------------------------------------------------------------------
# Downloading: the installer must be exactly what the manifest described
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_verified_download_is_kept(tmp_path):
    manager = manager_for(serving(manifest("0.2.0")), tmp_path)
    release = await manager.available()
    path = await manager._download(release)
    assert path.read_bytes() == INSTALLER_BYTES
    await manager.aclose()


@pytest.mark.asyncio
async def test_a_corrupted_download_is_refused_and_deleted(tmp_path):
    """A truncated 38 MB exe must never be run, and must never be kept."""
    handler = serving(manifest("0.2.0"), body=b"MZ" + b"corrupted")
    manager = manager_for(handler, tmp_path)
    release = await manager.available()

    with pytest.raises(UpdateError, match="checksum mismatch"):
        await manager._download(release)

    assert list(tmp_path.iterdir()) == []
    await manager.aclose()


@pytest.mark.asyncio
async def test_an_already_verified_installer_is_not_downloaded_twice(tmp_path):
    handler = serving(manifest("0.2.0"))
    manager = manager_for(handler, tmp_path)
    release = await manager.available()

    await manager._download(release)
    downloads = [p for p in handler.seen if p.endswith(".exe")]
    await manager._download(release)

    assert [p for p in handler.seen if p.endswith(".exe")] == downloads
    await manager.aclose()


@pytest.mark.asyncio
async def test_a_build_that_failed_is_not_retried_every_cycle(tmp_path):
    """Re-downloading tens of megabytes on a loop is its own outage."""
    handler = serving(manifest("0.2.0"), body=b"corrupted")
    manager = manager_for(handler, tmp_path)

    await manager.check_once()
    assert await manager.available() is None
    await manager.aclose()


# --------------------------------------------------------------------------
# The handover to the installer
# --------------------------------------------------------------------------


class _Exited(Exception):
    """Stands in for ``os._exit``, which a test process cannot survive."""


def _stub_handover(monkeypatch, *, console: bool):
    """Replace the two calls that would end the test run, and the console."""
    started: list[list[str]] = []
    monkeypatch.setattr("subprocess.Popen", lambda cmd, **kw: started.append(cmd))

    def fake_exit(code: int) -> None:
        raise _Exited(code)

    monkeypatch.setattr("os._exit", fake_exit)

    class _Stream:
        def flush(self) -> None:
            pass

    # None is what a windowed PyInstaller build actually has here; a Windows
    # service has no console attached. The console build has a real stream, and
    # both have to end the same way.
    monkeypatch.setattr("sys.stdout", _Stream() if console else None)
    monkeypatch.setattr("sys.stderr", _Stream() if console else None)
    return started


@pytest.mark.parametrize("console", [True, False], ids=["console", "windowed"])
def test_the_installer_launch_always_reaches_the_exit(tmp_path, monkeypatch, console):
    """Nothing may stand between a launched installer and ``os._exit``.

    0.2.2 shipped with a bare ``sys.stdout.flush()`` here. In the service build
    that is ``None.flush()``, so setup ran detached against a connector that
    never exited and never released the files setup had to replace -- and the
    caller then retried the whole install on the next heartbeat.
    """
    started = _stub_handover(monkeypatch, console=console)
    manager = manager_for(serving(manifest()), tmp_path)
    installer = tmp_path / "TallyFlowConnector-Setup-0.2.0.exe"
    installer.write_bytes(INSTALLER_BYTES)

    with pytest.raises(_Exited) as exit_code:
        manager._launch(installer, Release.from_manifest(manifest()["connector"]))

    assert exit_code.value.args[0] == 0
    assert started and started[0][0] == str(installer)


@pytest.mark.asyncio
async def test_an_unexpected_install_failure_is_not_retried_forever(tmp_path, monkeypatch):
    """A shop PC must not run the installer on a loop because of a stray bug.

    ``UpdateError`` was already remembered. Anything else escaped, left the
    version unmarked, and came round again on the next heartbeat.
    """
    manager = manager_for(serving(manifest("0.2.0")), tmp_path)

    async def boom(release):
        raise AttributeError("'NoneType' object has no attribute 'flush'")

    monkeypatch.setattr(manager, "apply", boom)

    await manager.check_once()
    assert await manager.available() is None
    await manager.aclose()


# --------------------------------------------------------------------------
# Never interrupt a read
# --------------------------------------------------------------------------


class FakePipeline:
    def __init__(self, busy: bool, depth: int = 0) -> None:
        self.busy = busy
        self.depth = depth


@pytest.mark.asyncio
async def test_an_idle_pipeline_installs_immediately(tmp_path):
    manager = manager_for(serving(manifest()), tmp_path, pipeline=FakePipeline(False))
    await manager._wait_until_idle()  # returns rather than hanging
    await manager.aclose()


@pytest.mark.asyncio
async def test_a_running_export_holds_the_install_back(tmp_path):
    """The report wins. An upgrade can wait; a killed export cannot be undone."""
    pipeline = FakePipeline(True)
    manager = manager_for(serving(manifest()), tmp_path, pipeline=pipeline)

    waiter = asyncio.ensure_future(manager._wait_until_idle())
    await asyncio.sleep(0)
    assert not waiter.done()

    waiter.cancel()
    await manager.aclose()


@pytest.mark.asyncio
async def test_queued_work_counts_as_busy(tmp_path):
    """`busy` is only what is on the wire; the queue behind it matters too."""
    manager = manager_for(serving(manifest()), tmp_path, pipeline=FakePipeline(False, depth=3))

    waiter = asyncio.ensure_future(manager._wait_until_idle())
    await asyncio.sleep(0)
    assert not waiter.done()

    waiter.cancel()
    await manager.aclose()


# --------------------------------------------------------------------------
# Reporting mode
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_update_off_reports_without_installing(tmp_path):
    manager = manager_for(serving(manifest("0.2.0")), tmp_path, auto_update=False)
    release = await manager.check_once()
    assert release is not None and release.version == "0.2.0"
    # Nothing downloaded: reporting must not touch the customer's bandwidth.
    assert list(tmp_path.iterdir()) == []
    await manager.aclose()


# --------------------------------------------------------------------------
# Where the manifest is looked for
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        (
            "wss://uat.example.test/v1/connector",
            "https://uat.example.test/downloads/manifest.json",
        ),
        (
            "ws://127.0.0.1:8000/v1/connector",
            "http://127.0.0.1:8000/downloads/manifest.json",
        ),
    ],
)
def test_the_manifest_url_follows_the_backend(backend, expected):
    """One address to configure: a UAT connector must not poll production."""
    assert ConnectorSettings(backend_url=backend).manifest_url == expected


def test_an_explicit_manifest_url_wins():
    settings = ConnectorSettings(
        backend_url="wss://uat.example.test/v1/connector",
        update_manifest_url="https://cdn.example.test/m.json",
    )
    assert settings.manifest_url == "https://cdn.example.test/m.json"


def test_a_relative_release_url_resolves_against_the_manifest():
    """Manifest URLs are relative so one file works behind any hostname."""
    from urllib.parse import urljoin

    release = Release.from_manifest(manifest("0.2.0")["connector"])
    assert urljoin(MANIFEST_URL, release.url) == (
        "https://example.test/downloads/TallyFlowConnector-Setup-0.2.0.exe"
    )
