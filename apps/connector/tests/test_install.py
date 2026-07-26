"""Windows autostart registration.

Nothing here runs schtasks for real -- the runner is injected. What is worth
testing is the parts that fail silently on a customer's machine weeks later:
the task settings we override, the file encoding schtasks demands, and the
parsing of the credentials the installer hands over.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest

from tally_connector import install
from tally_connector.config import save_settings

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def ok(_: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


class RecordingRunner:
    """Captures schtasks invocations and replays scripted results."""

    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.calls: list[list[str]] = []
        self._result = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def __call__(self, args) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(args))
        return self._result


def task_xml(**overrides) -> ElementTree.Element:
    defaults = {"command": r"C:\Apps\tally-connector-service.exe", "user_id": "SHOP-PC\\owner"}
    return ElementTree.fromstring(install.build_task_xml(**{**defaults, **overrides}))


def setting(root: ElementTree.Element, name: str) -> str | None:
    node = root.find(f"t:Settings/t:{name}", NS)
    return None if node is None else node.text


# --------------------------------------------------------------------------
# Task definition
# --------------------------------------------------------------------------


def test_task_xml_is_well_formed_and_names_the_executable() -> None:
    root = task_xml()
    assert root.findtext("t:Actions/t:Exec/t:Command", namespaces=NS) == (
        r"C:\Apps\tally-connector-service.exe"
    )
    assert root.findtext("t:Triggers/t:LogonTrigger/t:UserId", namespaces=NS) == "SHOP-PC\\owner"


def test_execution_time_limit_is_unlimited() -> None:
    # schtasks' own default is 72 hours, which would kill a connector that is
    # supposed to run forever -- three days after anyone was watching.
    assert setting(task_xml(), "ExecutionTimeLimit") == "PT0S"


def test_battery_settings_do_not_stop_the_connector_on_a_laptop() -> None:
    root = task_xml()
    assert setting(root, "DisallowStartIfOnBatteries") == "false"
    assert setting(root, "StopIfGoingOnBatteries") == "false"


def test_idle_does_not_stop_the_connector() -> None:
    # A shop PC left alone is the normal case, not a reason to stop reporting.
    root = task_xml()
    assert root.findtext("t:Settings/t:IdleSettings/t:StopOnIdleEnd", namespaces=NS) == "false"
    assert setting(root, "RunOnlyIfIdle") == "false"


def test_task_restarts_itself_after_a_crash() -> None:
    root = task_xml()
    assert root.findtext("t:Settings/t:RestartOnFailure/t:Count", namespaces=NS) == "3"


def test_task_runs_without_elevation() -> None:
    # The install is per-user; asking for admin here would make the task fail
    # to start for exactly the users who installed without it.
    root = task_xml()
    assert root.findtext("t:Principals/t:Principal/t:RunLevel", namespaces=NS) == "LeastPrivilege"
    assert root.findtext("t:Principals/t:Principal/t:LogonType", namespaces=NS) == (
        "InteractiveToken"
    )


def test_arguments_and_working_directory_are_omitted_when_empty() -> None:
    # An empty <Arguments/> element is not the same as no element: Task
    # Scheduler passes it through as an empty argument.
    root = task_xml()
    assert root.find("t:Actions/t:Exec/t:Arguments", NS) is None
    assert root.find("t:Actions/t:Exec/t:WorkingDirectory", NS) is None


def test_paths_with_xml_significant_characters_are_escaped() -> None:
    root = task_xml(command=r"C:\Program Files\A & B\tally.exe", arguments="run --tag <live>")
    assert root.findtext("t:Actions/t:Exec/t:Command", namespaces=NS) == (
        r"C:\Program Files\A & B\tally.exe"
    )
    assert root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=NS) == "run --tag <live>"


# --------------------------------------------------------------------------
# schtasks calls
# --------------------------------------------------------------------------


def test_register_writes_utf16_because_schtasks_requires_it(monkeypatch) -> None:
    seen: dict[str, bytes] = {}

    def runner(args):
        xml_path = Path(args[args.index("/XML") + 1])
        seen["content"] = xml_path.read_bytes()
        return ok(args)

    install.register(command="C:\\x.exe", user_id="PC\\owner", runner=runner)

    # The document declares encoding="UTF-16"; handing schtasks UTF-8 bytes
    # under that declaration makes it reject the file.
    assert seen["content"].startswith(b"\xff\xfe")
    assert 'encoding="UTF-16"' in seen["content"].decode("utf-16")


def test_register_replaces_an_existing_task() -> None:
    runner = RecordingRunner()
    install.register(command="C:\\x.exe", user_id="PC\\owner", runner=runner)

    # /F, or reinstalling over an existing install fails with "already exists".
    assert runner.calls[0][:3] == ["/Create", "/TN", install.TASK_NAME]
    assert "/F" in runner.calls[0]


def test_register_deletes_the_temp_xml_even_when_schtasks_fails() -> None:
    captured: list[Path] = []

    def runner(args):
        captured.append(Path(args[args.index("/XML") + 1]))
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="ERROR: nope")

    with pytest.raises(install.TaskError, match="nope"):
        install.register(command="C:\\x.exe", user_id="PC\\owner", runner=runner)

    assert not captured[0].exists()


def test_unregister_stops_before_deleting() -> None:
    runner, killer = RecordingRunner(), RecordingRunner()
    install.unregister(runner, killer)

    # Deleting a running task leaves the process orphaned, still holding the
    # backend socket, with no task left to stop it.
    assert [call[0] for call in runner.calls] == ["/End", "/Delete"]


def test_stop_force_kills_the_service_image() -> None:
    # Regression: a silent uninstall deleted the task, then found the exe still
    # locked by a connector that outlived `schtasks /End` -- leaving a running,
    # unmanageable process and a half-removed install.
    runner, killer = RecordingRunner(), RecordingRunner()
    install.stop(runner, killer)

    assert killer.calls == [["/F", "/T", "/IM", install.SERVICE_EXE_NAME]]


def test_stop_never_targets_the_cli_that_is_running_it() -> None:
    # `tally-connector.exe uninstall` calls this. If the kill matched the CLI's
    # own image name it would terminate itself mid-uninstall.
    runner, killer = RecordingRunner(), RecordingRunner()
    install.stop(runner, killer)

    assert install.CLI_EXE_NAME not in killer.calls[0]


def test_unregister_tolerates_a_task_that_was_never_registered() -> None:
    runner = RecordingRunner(returncode=1, stderr="ERROR: The system cannot find the file")
    killer = RecordingRunner(returncode=128, stderr='ERROR: process "x" not found')
    install.unregister(runner, killer)  # must not raise -- uninstall has to be idempotent


def test_task_status_reads_the_state() -> None:
    runner = RecordingRunner(
        stdout="Folder: \\\nTaskName:  \\TallyFlow Connector\nStatus:    Running\n"
    )
    assert install.task_status(runner) == "Running"


def test_task_status_is_none_when_not_registered() -> None:
    runner = RecordingRunner(returncode=1, stderr="ERROR: The system cannot find the file")
    assert install.task_status(runner) is None


# --------------------------------------------------------------------------
# Pairing hand-off
# --------------------------------------------------------------------------


def test_reads_the_installers_pairing_file(tmp_path: Path) -> None:
    path = tmp_path / "pairing.txt"
    path.write_text(
        "id=cn_123\nsecret=abc123\nbackend_url=wss://api.tallyflow.app/v1/connector\n",
        encoding="utf-8",
    )

    assert install.read_pairing_file(path) == {
        "id": "cn_123",
        "secret": "abc123",
        "backend_url": "wss://api.tallyflow.app/v1/connector",
    }


def test_pairing_values_may_contain_equals_signs(tmp_path: Path) -> None:
    # Base64 padding. Splitting on every '=' would silently truncate the secret
    # and produce an authentication failure with no clue as to why.
    path = tmp_path / "pairing.txt"
    path.write_text("secret=abc==\n", encoding="utf-8")

    assert install.read_pairing_file(path)["secret"] == "abc=="


def test_pairing_file_tolerates_a_bom(tmp_path: Path) -> None:
    # Inno Setup's SaveStringsToFile writes one in Unicode mode; without
    # utf-8-sig the first key becomes "\ufeffid" and the id looks missing.
    path = tmp_path / "pairing.txt"
    path.write_bytes("id=cn_123\n".encode("utf-8-sig"))

    assert install.read_pairing_file(path)["id"] == "cn_123"


def test_pairing_file_ignores_blank_lines_and_comments(tmp_path: Path) -> None:
    path = tmp_path / "pairing.txt"
    path.write_text("\n# written by setup\nid = cn_123 \n\n", encoding="utf-8")

    assert install.read_pairing_file(path) == {"id": "cn_123"}


# --------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------


def test_logs_go_to_the_users_local_appdata(monkeypatch, tmp_path: Path) -> None:
    # Not the install directory: a machine-wide install lands in Program Files,
    # where a limited user cannot create a log file.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert install.default_log_dir() == tmp_path / "TallyFlow Connector" / "logs"


def test_prefers_the_windowless_executable(tmp_path: Path) -> None:
    (tmp_path / install.SERVICE_EXE_NAME).write_text("")
    (tmp_path / install.CLI_EXE_NAME).write_text("")

    command, arguments = install.service_command(tmp_path)
    assert command.endswith(install.SERVICE_EXE_NAME)
    assert arguments == ""


def test_falls_back_to_the_console_executable_with_a_run_argument(tmp_path: Path) -> None:
    (tmp_path / install.CLI_EXE_NAME).write_text("")

    command, arguments = install.service_command(tmp_path)
    assert command.endswith(install.CLI_EXE_NAME)
    assert arguments == "run"


# --------------------------------------------------------------------------
# Config merge
# --------------------------------------------------------------------------


def test_install_settings_do_not_discard_hand_edits(tmp_path: Path) -> None:
    # Support routinely edits tally_port or log_level in place; a reinstall
    # must not quietly revert that.
    path = tmp_path / "connector.json"
    save_settings({"tally_port": 9002, "log_level": "DEBUG"}, path)
    save_settings({"connector_id": "cn_1", "connector_secret": "s"}, path)

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["tally_port"] == 9002
    assert written["log_level"] == "DEBUG"
    assert written["connector_id"] == "cn_1"


def test_none_values_leave_the_existing_setting_alone(tmp_path: Path) -> None:
    path = tmp_path / "connector.json"
    save_settings({"backend_url": "ws://localhost:8000/v1/connector"}, path)
    save_settings({"connector_id": "cn_1", "backend_url": None}, path)

    assert json.loads(path.read_text(encoding="utf-8"))["backend_url"] == (
        "ws://localhost:8000/v1/connector"
    )


def test_paths_are_written_as_strings(tmp_path: Path) -> None:
    # json.dumps cannot serialise a Path, and log_dir arrives as one.
    path = tmp_path / "connector.json"
    save_settings({"log_dir": tmp_path / "logs"}, path)

    assert json.loads(path.read_text(encoding="utf-8"))["log_dir"] == str(tmp_path / "logs")
