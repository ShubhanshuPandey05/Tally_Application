"""Changing an installed connector's server address or credentials.

Both genuinely change after a working install: a backend moves, or the app
reissues a secret because the shop lost the old one. Without a way to edit them
in place the only route is a reinstall, and re-pairing from scratch is what
makes a company appear twice in the app.

Nothing here touches the real Task Scheduler -- the autostart calls are stubbed.
A test that started the machine's actual connector would be a test that fights
the connector the developer is running.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tally_connector import install as autostart
from tally_connector import main
from tally_connector.config import load_settings, save_settings


@pytest.fixture
def config(tmp_path: Path) -> Path:
    path = tmp_path / "connector.json"
    save_settings(
        {
            "connector_id": "abc123",
            "connector_secret": "old-secret",
            "backend_url": "wss://old.example.com/v1/connector",
            "tally_port": 9001,
        },
        path,
    )
    return path


@pytest.fixture(autouse=True)
def no_scheduler(monkeypatch) -> list[str]:
    """Stub autostart so the suite never drives the real logon task."""
    calls: list[str] = []
    monkeypatch.setattr(autostart, "task_status", lambda *a, **k: None)
    monkeypatch.setattr(autostart, "stop", lambda *a, **k: calls.append("stop"))
    monkeypatch.setattr(autostart, "start", lambda *a, **k: calls.append("start"))
    return calls


def run(config: Path, *args: str) -> int:
    return main.run(["--config", str(config), "configure", *args])


def contents(config: Path) -> dict:
    return json.loads(config.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Changing things
# --------------------------------------------------------------------------


async def test_the_backend_url_can_be_changed(config: Path) -> None:
    """The failure this exists for: a server address that is no longer reachable."""
    assert run(config, "--backend-url", "wss://new.example.com/v1/connector") == 0

    assert load_settings(config).backend_url == "wss://new.example.com/v1/connector"


async def test_the_secret_can_be_changed_without_re_pairing(config: Path) -> None:
    """Matches the backend's rotate endpoint: same id, new credential."""
    assert run(config, "--secret", "brand-new-secret") == 0

    settings = load_settings(config)
    assert settings.connector_secret == "brand-new-secret"
    assert settings.connector_id == "abc123", "rotating a secret must not re-identify the PC"


async def test_unrelated_settings_are_left_alone(config: Path) -> None:
    """A support engineer may have hand-edited this file."""
    run(config, "--backend-url", "wss://new.example.com/v1/connector")

    assert contents(config)["tally_port"] == 9001
    assert contents(config)["connector_secret"] == "old-secret"


async def test_credentials_can_come_from_a_file(config: Path, tmp_path: Path) -> None:
    """Command lines are readable by any process on the machine; files are not."""
    handover = tmp_path / "pairing.txt"
    handover.write_text(
        "id=xyz789\nsecret=file-secret\nbackend_url=wss://from-file.example.com/v1/connector\n",
        encoding="utf-8",
    )

    assert run(config, "--from-file", str(handover)) == 0

    settings = load_settings(config)
    assert settings.connector_id == "xyz789"
    assert settings.connector_secret == "file-secret"
    assert settings.backend_url == "wss://from-file.example.com/v1/connector"


# --------------------------------------------------------------------------
# Refusing bad input
# --------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["http://api.example.com", "api.example.com", "https://x/y"])
async def test_a_url_that_is_not_websocket_is_refused(config: Path, url: str) -> None:
    """Caught here rather than at startup.

    The connector validates this when it boots, so an unchecked typo produces a
    command that succeeds and a connector that silently never dials home again.
    """
    assert run(config, "--backend-url", url) == 2


async def test_a_rejected_change_leaves_the_file_untouched(config: Path) -> None:
    before = contents(config)

    run(config, "--backend-url", "http://not-websocket")

    assert contents(config) == before


async def test_doing_nothing_is_an_error_not_a_silent_success(config: Path) -> None:
    """Otherwise a mistyped flag reads as "configured" and nothing changed."""
    assert run(config) == 2


async def test_the_secret_is_never_printed(config: Path, capsys) -> None:
    """This output ends up in screenshots and support tickets."""
    run(config, "--secret", "super-secret-value")

    printed = capsys.readouterr()
    assert "super-secret-value" not in printed.out
    assert "super-secret-value" not in printed.err


# --------------------------------------------------------------------------
# Making the change take effect
# --------------------------------------------------------------------------


async def test_a_running_connector_is_restarted(config: Path, monkeypatch, no_scheduler) -> None:
    """It holds the old settings in memory until something restarts it."""
    monkeypatch.setattr(autostart, "task_status", lambda *a, **k: "Running")

    assert run(config, "--backend-url", "wss://new.example.com/v1/connector") == 0
    assert no_scheduler == ["stop", "start"]


async def test_nothing_is_restarted_when_no_task_is_registered(
    config: Path, no_scheduler
) -> None:
    assert run(config, "--backend-url", "wss://new.example.com/v1/connector") == 0
    assert no_scheduler == []


async def test_a_failed_restart_still_reports_the_saved_change(
    config: Path, monkeypatch, capsys
) -> None:
    """The setting is on disk either way; saying otherwise sends them re-running it."""
    monkeypatch.setattr(autostart, "task_status", lambda *a, **k: "Running")

    def boom(*a, **k):
        raise autostart.TaskError("access denied")

    monkeypatch.setattr(autostart, "stop", boom)

    assert run(config, "--backend-url", "wss://new.example.com/v1/connector") == 1
    assert load_settings(config).backend_url == "wss://new.example.com/v1/connector"
    assert "saved" in capsys.readouterr().out.lower()


# --------------------------------------------------------------------------
# Uninstalling
# --------------------------------------------------------------------------


@pytest.fixture
def unregisterable(monkeypatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(autostart, "unregister", lambda *a, **k: calls.append("unregister"))
    return calls


def uninstall(config: Path, *args: str) -> int:
    return main.run(["--config", str(config), "uninstall", *args])


async def test_a_plain_uninstall_keeps_the_pairing(
    config: Path, unregisterable, monkeypatch, tmp_path: Path
) -> None:
    """The Windows installer runs this during an *upgrade*.

    Unpairing here would make every version bump a re-pair for every shop.
    """
    monkeypatch.setattr(autostart, "data_dir", lambda: tmp_path / "data")

    assert uninstall(config) == 0
    assert unregisterable == ["unregister"]
    assert config.is_file(), "an upgrade must not unpair the machine"


async def test_purge_removes_the_credential_and_the_logs(
    config: Path, unregisterable, monkeypatch, tmp_path: Path
) -> None:
    data = tmp_path / "data"
    (data / "logs").mkdir(parents=True)
    (data / "logs" / "connector.log").write_text("noise", encoding="utf-8")
    monkeypatch.setattr(autostart, "data_dir", lambda: data)

    assert uninstall(config, "--purge") == 0

    assert not config.exists(), "the secret must not survive an explicit purge"
    assert not data.exists()


async def test_purging_an_unpaired_machine_is_not_an_error(
    config: Path, unregisterable, monkeypatch, tmp_path: Path
) -> None:
    """A half-finished install still has to be removable."""
    config.unlink()
    monkeypatch.setattr(autostart, "data_dir", lambda: tmp_path / "never-created")

    assert uninstall(config, "--purge") == 0


async def test_purge_reports_what_it_deleted(
    config: Path, unregisterable, monkeypatch, tmp_path: Path, capsys
) -> None:
    """"Deleted" has to mean deleted -- this is the last word before support."""
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(autostart, "data_dir", lambda: data)

    uninstall(config, "--purge")

    out = capsys.readouterr().out
    assert str(config) in out
    assert str(data) in out
    assert "no longer paired" in out.lower()
