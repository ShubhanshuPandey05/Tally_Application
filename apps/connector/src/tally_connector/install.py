"""Background-service registration for Windows.

The connector runs as a **per-user Scheduled Task triggered at logon**, not as a
Windows Service. That choice is deliberate:

* TallyPrime is a desktop application. It only exists while a human is logged
  in, so a service that starts at boot would spend every night reconnecting to
  a Tally that is not there, and would need an admin install to gain nothing.
* A logon task needs no administrator rights, which means the whole install can
  run per-user with no UAC prompt -- the difference between a shop owner
  installing this themselves and having to call someone.
* Task Scheduler already provides restart-on-crash and start-on-demand, so
  there is no service wrapper to ship and keep alive.

Everything here shells out to ``schtasks.exe`` rather than binding the COM
Task Scheduler API, so there is no pywin32 dependency in the frozen exe.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from xml.sax.saxutils import escape

logger = logging.getLogger(__name__)

TASK_NAME = "TallyFlow Connector"
CLI_EXE_NAME = "tally-connector.exe"
SERVICE_EXE_NAME = "tally-connector-service.exe"

#: Keeps schtasks from flashing a console window when we are called from the
#: windowless service exe or from a hidden installer step.
_CREATE_NO_WINDOW = 0x08000000

Runner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


# --------------------------------------------------------------------------
# Locations
# --------------------------------------------------------------------------


def data_dir() -> Path:
    """Per-user directory for logs and the response cache.

    Not the install directory: a machine-wide install would put that under
    Program Files where a limited user cannot write, and logs must keep working
    in that case too.
    """
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "TallyFlow Connector"
    return Path.home() / ".tallyflow-connector"


def default_log_dir() -> Path:
    return data_dir() / "logs"


def install_root() -> Path:
    """Directory holding the connector executables."""
    return Path(sys.executable).parent


def service_command(root: Path | None = None) -> tuple[str, str]:
    """``(command, arguments)`` for the scheduled task's action.

    Prefers the windowless build. Falling back to the console exe keeps
    ``install`` usable on a developer machine, at the cost of a visible window.
    """
    base = root or install_root()

    service = base / SERVICE_EXE_NAME
    if service.is_file():
        return str(service), ""

    cli = base / CLI_EXE_NAME
    if cli.is_file():
        return str(cli), "run"

    # Source checkout: no exe anywhere, so drive the module directly.
    return str(Path(sys.executable)), "-m tally_connector.main run"


# --------------------------------------------------------------------------
# Task definition
# --------------------------------------------------------------------------


def current_user() -> str:
    """``DOMAIN\\user`` for the account the task should run as."""
    user = os.environ.get("USERNAME", "")
    if not user:
        raise RuntimeError("USERNAME is not set; cannot decide who the task runs as")
    domain = os.environ.get("USERDOMAIN") or platform.node()
    return f"{domain}\\{user}"


def build_task_xml(
    *,
    command: str,
    arguments: str = "",
    working_dir: str = "",
    user_id: str,
    start_delay: str = "PT30S",
) -> str:
    """Task Scheduler 1.2 XML for the logon task.

    Element order inside ``<Settings>`` follows the schema's sequence exactly --
    Task Scheduler rejects the document outright if it does not, with an error
    that names no element.

    Several defaults here are the opposite of what ``schtasks /Create /SC
    ONLOGON`` would give us, and each one is a bug that only shows up days
    later:

    ``ExecutionTimeLimit``
        Defaults to 72 hours. The connector is meant to run forever, so the
        default silently kills it every third day.
    ``StopIfGoingOnBatteries`` / ``DisallowStartIfOnBatteries``
        Default true. On a laptop that means the dashboard goes cold the moment
        the charger is unplugged.
    ``StopOnIdleEnd``
        Default true, so an idle shop PC stops reporting.
    """
    arguments_xml = f"\n      <Arguments>{escape(arguments)}</Arguments>" if arguments else ""
    working_xml = (
        f"\n      <WorkingDirectory>{escape(working_dir)}</WorkingDirectory>" if working_dir else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Keeps TallyPrime data available to the TallyFlow mobile app. \
Reads only; never writes to Tally.</Description>
    <URI>\\{escape(TASK_NAME)}</URI>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user_id)}</UserId>
      <Delay>{escape(start_delay)}</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user_id)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>3</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>{arguments_xml}{working_xml}
    </Exec>
  </Actions>
</Task>
"""


# --------------------------------------------------------------------------
# schtasks
# --------------------------------------------------------------------------


def _windows_tool(name: str) -> Runner:
    def call(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603
            [name, *args],
            capture_output=True,
            text=True,
            # Windows console tools emit OEM codepage text; replace rather than
            # raise, since we only ever show this in an error message.
            errors="replace",
            creationflags=_CREATE_NO_WINDOW if os.name == "nt" else 0,
        )

    return call


_schtasks = _windows_tool("schtasks")
_taskkill = _windows_tool("taskkill")


class TaskError(RuntimeError):
    """schtasks refused an operation."""


def _require(result: subprocess.CompletedProcess[str], what: str) -> None:
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise TaskError(f"{what} failed: {detail or f'schtasks exited {result.returncode}'}")


def register(
    *,
    command: str,
    arguments: str = "",
    working_dir: str = "",
    user_id: str | None = None,
    runner: Runner = _schtasks,
) -> None:
    """Create or replace the logon task."""
    xml = build_task_xml(
        command=command,
        arguments=arguments,
        working_dir=working_dir,
        user_id=user_id or current_user(),
    )

    # schtasks reads the file from disk, and it wants UTF-16 with a BOM to match
    # the declared encoding; Python's "utf-16" codec writes the BOM for us.
    descriptor, name = tempfile.mkstemp(suffix=".xml")
    os.close(descriptor)
    path = Path(name)
    try:
        path.write_text(xml, encoding="utf-16", newline="")
        _require(
            runner(["/Create", "/TN", TASK_NAME, "/XML", str(path), "/F"]),
            "registering the startup task",
        )
    finally:
        path.unlink(missing_ok=True)


def start(runner: Runner = _schtasks) -> None:
    _require(runner(["/Run", "/TN", TASK_NAME]), "starting the connector")


def stop(runner: Runner = _schtasks, killer: Runner = _taskkill) -> None:
    """Stop the running connector. Not an error if it was not running.

    ``schtasks /End`` alone is not enough, and this was only visible end to
    end. It returns as soon as Task Scheduler has been *asked* to stop the
    task, and a PyInstaller one-file build is two processes -- a bootloader and
    the child it extracts and launches. The child outlives the request often
    enough that an uninstall a moment later finds the executable still locked,
    deletes what it can, and leaves a running connector behind with no task to
    stop it.

    So follow up with taskkill by image name. It is idempotent (nothing to kill
    is exit code 128, which we ignore) and it can only ever match our own
    executable -- the CLI running this code has a different image name, so
    ``uninstall`` cannot kill itself.
    """
    runner(["/End", "/TN", TASK_NAME])
    killer(["/F", "/T", "/IM", SERVICE_EXE_NAME])


def unregister(runner: Runner = _schtasks, killer: Runner = _taskkill) -> None:
    """Remove the task. Safe to call when nothing is registered."""
    stop(runner, killer)
    runner(["/Delete", "/TN", TASK_NAME, "/F"])


def task_status(runner: Runner = _schtasks) -> str | None:
    """``Running`` / ``Ready`` / ``Disabled``, or ``None`` when unregistered."""
    result = runner(["/Query", "/TN", TASK_NAME, "/FO", "LIST"])
    if result.returncode != 0:
        return None
    for line in (result.stdout or "").splitlines():
        # Localised Windows translates the label, so match on position: the
        # status is the only field whose value is one of the known words.
        _, _, value = line.partition(":")
        candidate = value.strip()
        if candidate in {"Running", "Ready", "Disabled", "Queued"}:
            return candidate
    return None


# --------------------------------------------------------------------------
# Pairing hand-off
# --------------------------------------------------------------------------


def read_pairing_file(path: Path) -> dict[str, str]:
    """Parse the ``key=value`` file the Windows installer hands us.

    The credentials travel through a file rather than the command line on
    purpose: every process on the machine can read another process's command
    line, so ``pair --secret <secret>`` would briefly publish the shop's
    credential to anything that happens to be polling.
    """
    values: dict[str, str] = {}
    # utf-8-sig: Inno Setup's SaveStringsToFile writes a BOM in Unicode mode.
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if sep:
            # Split on the *first* '=' only; base64 padding lives in values.
            values[key.strip().lower()] = value.strip()
    return values
