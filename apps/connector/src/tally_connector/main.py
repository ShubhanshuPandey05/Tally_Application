"""Connector entry point.

Subcommands:

``run``        connect to the backend and serve jobs (the service path)
``diagnose``   check Tally locally and print what is wrong, in plain language
``pair``       store the credentials the app's pairing wizard produced
``install``    pair, then register the connector to start at logon
``configure``  change the server address or the credentials of an installed one
``uninstall``  remove it from startup (``--purge`` also unpairs the machine)
``status``     report whether the background connector is registered and running
``update``     check for a newer build (``--apply`` installs it)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import signal
import sys
from pathlib import Path

from pydantic import ValidationError
from tally_core.tally import TallyClient, get_query, registry_manifest
from tally_core.tally.errors import TallyError

from . import __version__, remote_logs
from . import install as autostart
from .alterid import baseline_path, run_probe
from .config import ConnectorSettings, load_settings, save_pairing, save_settings
from .livecheck import livecheck, print_manifest
from .loaded import GuardedClient
from .logging_setup import setup_logging
from .session import AuthenticationRejected, ConnectorSession
from .updater import UpdateError, UpdateManager
from .updater import supported as updater_supported

logger = logging.getLogger(__name__)


async def serve(settings: ConnectorSettings) -> int:
    if not settings.is_paired:
        logger.error(
            "This connector is not paired yet. Open the TallyFlow app, add this "
            "computer, then run: tally-connector pair --id <ID> --secret <SECRET>"
        )
        return 2

    # Installed before the session so the lines describing a *failed* first
    # connection are already buffered when the socket finally comes up.
    #
    # Never below `log_level`: the root logger filters before any handler is
    # consulted, so a remote level of INFO under a file level of WARNING would
    # not ship INFO -- it would ship nothing and look like remote logging was
    # broken. Remote can be quieter than the file, never noisier.
    log_handler = (
        remote_logs.install(
            level=remote_logs.effective_level(settings.log_level, settings.remote_log_level),
            capacity=settings.remote_log_buffer,
        )
        if settings.remote_logs
        else None
    )

    session = ConnectorSession(settings, version=__version__, log_handler=log_handler)
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError, AttributeError):
            # Windows ProactorEventLoop has no add_signal_handler; the
            # KeyboardInterrupt path in run() covers Ctrl+C there.
            loop.add_signal_handler(sig, stop.set)

    runner = asyncio.create_task(session.run_forever())
    stopper = asyncio.create_task(stop.wait())

    done, _ = await asyncio.wait({runner, stopper}, return_when=asyncio.FIRST_COMPLETED)

    if stopper in done:
        logger.info("shutting down")
        await session.stop()
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner

    stopper.cancel()
    await session.aclose()

    if runner in done and not runner.cancelled():
        exc = runner.exception()
        if isinstance(exc, AuthenticationRejected):
            logger.error("Backend rejected this connector: %s", exc)
            logger.error("Re-pair this computer from the TallyFlow app.")
            return 3
        if exc is not None:
            logger.error("connector stopped: %s", exc)
            return 1
    return 0


async def diagnose(settings: ConnectorSettings) -> int:
    """Local health check that answers "why isn't my data showing?".

    Runs entirely against Tally, so it works before pairing and while the
    backend is unreachable.
    """
    print(f"TallyFlow Connector {__version__}")
    print(f"Tally endpoint : {settings.tally_config().url}")
    print(f"Backend        : {settings.backend_url}")
    print(f"Paired         : {'yes' if settings.is_paired else 'no'}")
    print(f"Queries        : {len(registry_manifest())} registered")
    print()

    # Guarded like every other read path, even though the only query below is
    # company discovery -- which the guard passes straight through, being the
    # one read that is not scoped to a company. It costs nothing today and means
    # a scoped read added here later is guarded by default rather than by
    # someone remembering that a closed company crashes Tally.
    client = GuardedClient(TallyClient(settings.tally_config()))
    try:
        if not await client.is_alive():
            print("[FAIL] TallyPrime is not responding.")
            print()
            print("  1. Is TallyPrime open on this computer?")
            print("  2. In Tally: F1 > Settings > Connectivity > Client/Server")
            print("     configuration, set 'TallyPrime acts as' to 'Both'.")
            print(f"  3. Confirm the port there matches {settings.tally_port}.")
            print("  4. Close any open dialog box in Tally - it blocks requests.")
            return 1

        print("[ OK ] TallyPrime is responding.")

        query = get_query("companies.list")
        companies = await client.execute(query, query.validate_params({}))
        if not companies:
            print("[WARN] No company is open in Tally. Open one to see data.")
            return 1

        print(f"[ OK ] {len(companies)} company/companies open:")
        for company in companies:
            fy = company.financial_year_from
            suffix = f"  (books from {fy:%d-%b-%Y})" if fy else ""
            print(f"         - {company.name}{suffix}")
        return 0

    except TallyError as exc:
        print(f"[FAIL] {exc.user_message}")
        print(f"       Detail: {exc}")
        return 1
    finally:
        await client.aclose()


async def verify(settings: ConnectorSettings, company: str | None) -> int:
    """Run the live accounting verification against TallyPrime."""
    print(f"TallyFlow Connector {__version__} - live verification")
    print(f"Tally endpoint : {settings.tally_config().url}")
    print_manifest()

    client = TallyClient(settings.tally_config())
    try:
        failures = await livecheck(client, company)
    finally:
        await client.aclose()

    print()
    if failures == 0:
        print("PASS - Tally integration verified")
    else:
        print(f"FAIL - {failures} check(s) failed")
    return 1 if failures else 0


async def alterid_probe(settings: ConnectorSettings, company: str | None) -> int:
    """Settle whether a master's AlterID moves when a voucher moves its balance.

    Run either side of one real voucher. The whole incremental-master design
    depends on the answer, and it differs between TallyPrime builds -- see
    :mod:`tally_connector.alterid`.
    """
    print(f"TallyFlow Connector {__version__} - master AlterID probe")
    print(f"Tally endpoint : {settings.tally_config().url}")
    print()

    # Always the connector's own data directory, never the working directory:
    # the two runs are minutes apart and often in different shells, and a
    # baseline that lands wherever the operator happened to be standing is a
    # baseline the second run does not find.
    path = baseline_path(autostart.data_dir())
    client = TallyClient(settings.tally_config())
    try:
        return await run_probe(client, company, path)
    except TallyError as exc:
        print(f"[FAIL] {exc.user_message}")
        print(f"       Detail: {exc}")
        return 1
    finally:
        await client.aclose()


async def update(settings: ConnectorSettings, *, apply: bool) -> int:
    """Report -- or install -- the current connector build.

    The manual half of the updater. Support needs a way to answer "what version
    is that shop on, and why hasn't it moved?" without waiting out the six-hour
    check, and a shop with automatic updates turned off needs a way to take one.
    """
    manager = UpdateManager(
        current_version=__version__,
        manifest_url=settings.manifest_url,
        auto_update=False,
        verify_tls=settings.verify_tls,
    )
    print(f"Installed : {__version__}")
    print(f"Manifest  : {settings.manifest_url}")

    try:
        release = await manager.available()
    except UpdateError as exc:
        print(f"\n[FAIL] {exc}")
        return 1
    finally:
        await manager.aclose()

    if release is None:
        print("\nUp to date.")
        return 0

    print(f"\nAvailable : {release.version}{'  (required)' if release.mandatory else ''}")
    if release.notes:
        print(f"Notes     : {release.notes}")
    print(f"Size      : {release.size_bytes / 1e6:.1f} MB")

    if not apply:
        print("\nInstall it with: tally-connector update --apply")
        return 0

    if not updater_supported():
        print("\n[FAIL] Self-update needs the installed Windows build.")
        print("       Download and run the installer instead.")
        return 1

    # Recreated with auto_update on; the reporting instance above is closed.
    installer = UpdateManager(
        current_version=__version__,
        manifest_url=settings.manifest_url,
        verify_tls=settings.verify_tls,
    )
    try:
        # Does not return: apply() hands over to setup and exits this process.
        await installer.apply(release)
    except UpdateError as exc:
        print(f"\n[FAIL] {exc}")
        return 1
    finally:
        await installer.aclose()
    return 0


def pair(args: argparse.Namespace) -> int:
    path = save_pairing(args.id, args.secret, args.config)
    print(f"Pairing saved to {path}")
    print("Start the connector with: tally-connector run")
    return 0


def _pairing_values(args: argparse.Namespace) -> dict[str, str]:
    """Pairing details from ``--from-file`` first, then explicit flags."""
    values: dict[str, str] = {}
    if args.from_file:
        values.update(autostart.read_pairing_file(args.from_file))
    for flag, key in (("id", "id"), ("secret", "secret"), ("backend_url", "backend_url")):
        supplied = getattr(args, flag, None)
        if supplied:
            values[key] = supplied
    return values


def install(args: argparse.Namespace) -> int:
    """Pair this computer and register the connector to start at logon.

    This is what the Windows installer runs. It deliberately does not touch
    Tally: the shop owner may well be installing this before opening TallyPrime
    for the day, and a failed connection check must not fail the install.
    """
    values = _pairing_values(args)
    connector_id, secret = values.get("id", ""), values.get("secret", "")

    # An upgrade has no credentials to offer: the secret is shown once, in the
    # app, at pairing time. Reusing what is already on disk is what makes an
    # unattended re-install possible at all -- without it a silent upgrade
    # leaves the connector installed but never registered to start, which looks
    # to the shop exactly like the connector dying for no reason.
    if not connector_id and not secret and load_settings(args.config).is_paired:
        return _reinstall(args)

    if not connector_id or not secret:
        print("error: both a connector id and a secret are required", file=sys.stderr)
        return 2

    config_path = save_settings(
        {
            "connector_id": connector_id,
            "connector_secret": secret,
            "backend_url": values.get("backend_url") or None,
            # A background process with no console has nowhere else to report
            # itself, so file logging is not optional for an installed connector.
            "log_dir": autostart.default_log_dir(),
        },
        args.config,
    )
    print(f"Paired as {connector_id}; settings written to {config_path}")

    if args.no_autostart:
        print("Skipping startup registration (--no-autostart).")
        return 0

    command, arguments = autostart.service_command()
    try:
        autostart.register(
            command=command, arguments=arguments, working_dir=str(autostart.install_root())
        )
        autostart.start()
    except (autostart.TaskError, RuntimeError, OSError) as exc:
        # The pairing is already on disk and valid, so this is recoverable by
        # hand -- say so instead of leaving a half-finished install.
        print(f"Could not register the startup task: {exc}")
        print("The connector is paired; start it manually with: tally-connector run")
        return 1

    print(f'Registered "{autostart.TASK_NAME}" to start at logon, and started it now.')
    print(f"Logs: {autostart.default_log_dir()}")
    return 0


def _reinstall(args: argparse.Namespace) -> int:
    """Re-register an already-paired connector after its files were replaced.

    The upgrade path, reached two ways: the installer running over an existing
    install, and the connector updating itself. Both have already stopped the
    old build and overwritten the executables; all that is left is to point the
    startup task at them again and start it.

    Deliberately does not touch connector.json. The credentials in it are the
    ones being preserved, and rewriting them from empty inputs is the failure
    this whole branch exists to prevent.
    """
    settings = load_settings(args.config)
    print(f"Upgrading in place; keeping the existing pairing ({settings.connector_id}).")

    if getattr(args, "no_autostart", False):
        print("Skipping startup registration (--no-autostart).")
        return 0

    command, arguments = autostart.service_command()
    try:
        autostart.register(
            command=command, arguments=arguments, working_dir=str(autostart.install_root())
        )
        autostart.start()
    except (autostart.TaskError, RuntimeError, OSError) as exc:
        print(f"Could not register the startup task: {exc}")
        print("The connector is still paired; start it manually with: tally-connector run")
        return 1

    print(f'Re-registered "{autostart.TASK_NAME}" and started it.')
    return 0


def configure(args: argparse.Namespace) -> int:
    """Change where this PC connects, or which credentials it uses.

    The reason this exists as its own command: both values genuinely change
    after a working install. A backend that moves address, or a secret reissued
    from the app because the old one was lost, would otherwise mean uninstalling
    and reinstalling a shop's connector -- and re-pairing from scratch is how a
    company ends up linked twice.

    Nothing is written until the merged result validates, so a mistyped URL is
    an error message rather than a connector that starts and never dials home.
    """
    values = _pairing_values(args)
    if not values and args.log_level is None:
        print("error: nothing to change; pass --backend-url, --id, --secret "
              "or --log-level", file=sys.stderr)
        return 2

    updates: dict[str, object] = {
        "connector_id": values.get("id"),
        "connector_secret": values.get("secret"),
        "backend_url": values.get("backend_url"),
        "log_level": args.log_level,
    }
    updates = {key: value for key, value in updates.items() if value is not None}

    current = load_settings(args.config)
    candidate = current.model_dump()
    candidate.update(updates)
    try:
        ConnectorSettings(
            **{k: v for k, v in candidate.items() if k in ConnectorSettings.model_fields}
        )
    except ValidationError as exc:
        # One line per problem: this is read by a shop owner on a phone call,
        # not by someone who will go and read a stack trace.
        for error in exc.errors():
            field = ".".join(str(part) for part in error["loc"]) or "value"
            print(f"error: {field}: {error['msg']}", file=sys.stderr)
        return 2

    path = save_settings(updates, args.config)
    # Never the secret itself -- this output lands in screenshots and tickets.
    changed = ", ".join(sorted("secret" if k == "connector_secret" else k for k in updates))
    print(f"Updated {changed} in {path}")

    if autostart.task_status() is None:
        print("Start the connector with: tally-connector run")
        return 0

    # A running connector holds the old settings in memory, so a change nobody
    # restarts is a change that appears not to have worked.
    try:
        autostart.stop()
        autostart.start()
    except (autostart.TaskError, OSError) as exc:
        print(f"Settings saved, but the background connector could not be restarted: {exc}")
        print("Restart it from Task Scheduler, or log out and back in.")
        return 1
    print("Restarted the background connector so the change takes effect.")
    return 0


def uninstall(args: argparse.Namespace) -> int:
    """Stop the background connector and remove it from startup.

    Without ``--purge`` this leaves connector.json alone, because the same
    command runs during an *upgrade*: the Windows installer stops the old build
    before overwriting it, and unpairing a working shop mid-upgrade would be a
    support call for every customer.
    """
    try:
        autostart.unregister()
    except OSError as exc:
        print(f"Could not remove the startup task: {exc}")
        return 1
    print(f'Removed "{autostart.TASK_NAME}" from startup.')

    if not getattr(args, "purge", False):
        return 0

    removed = autostart.purge(args.config)
    if removed:
        for path in removed:
            print(f"Deleted {path}")
        print("This computer is no longer paired.")
    else:
        print("Nothing left to delete; this computer was not paired.")
    return 0


def status(args: argparse.Namespace) -> int:
    """What a support call starts with: is it installed, paired, and running?"""
    settings = load_settings(args.config)
    state = autostart.task_status()

    print(f"TallyFlow Connector {__version__}")
    print(f"Paired    : {'yes (' + settings.connector_id + ')' if settings.is_paired else 'no'}")
    print(f"Backend   : {settings.backend_url}")
    print(f"Startup   : {state or 'not registered'}")
    print(f"Logs      : {settings.log_dir or '(console only)'}")

    if not settings.is_paired:
        print("\nThis computer is not paired. Re-run the installer, or use: "
              "tally-connector pair --id <ID> --secret <SECRET>")
        return 1
    if state is None:
        print("\nThe connector will not start on its own. Re-run the installer.")
        return 1
    if state != "Running":
        print(f'\nThe startup task exists but is {state}. Start it with: '
              f'schtasks /Run /TN "{autostart.TASK_NAME}"')
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tally-connector",
        description="TallyFlow connector - the bridge between TallyPrime and TallyFlow.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, help="path to connector.json")
    parser.add_argument("--log-level", help="DEBUG, INFO, WARNING, ERROR")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="connect to the backend and serve requests")
    sub.add_parser("diagnose", help="check the local TallyPrime connection")
    sub.add_parser("capabilities", help="print the queries this build supports")

    live = sub.add_parser(
        "livecheck",
        help="run every query against the live Tally and verify the accounting",
    )
    live.add_argument("--company", help="company to test (default: first one open)")

    probe = sub.add_parser(
        "alterid-probe",
        help="check whether master AlterIDs move when a voucher changes a balance",
    )
    probe.add_argument("--company", help="company to probe (default: first one open)")

    update_cmd = sub.add_parser("update", help="check for a newer connector build")
    update_cmd.add_argument(
        "--apply", action="store_true", help="download and install it now"
    )

    pair_cmd = sub.add_parser("pair", help="save pairing credentials")
    pair_cmd.add_argument("--id", required=True, help="connector id from the app")
    pair_cmd.add_argument("--secret", required=True, help="connector secret from the app")

    install_cmd = sub.add_parser(
        "install", help="pair this computer and start the connector at logon"
    )
    install_cmd.add_argument("--id", help="connector id from the app")
    install_cmd.add_argument("--secret", help="connector secret from the app")
    install_cmd.add_argument("--backend-url", dest="backend_url", help="ws:// or wss:// endpoint")
    install_cmd.add_argument(
        "--from-file",
        type=Path,
        help="key=value file holding id/secret/backend_url (used by the Windows installer)",
    )
    install_cmd.add_argument(
        "--no-autostart", action="store_true", help="pair only; do not register a startup task"
    )

    configure_cmd = sub.add_parser(
        "configure", help="change the server address or the pairing credentials"
    )
    configure_cmd.add_argument(
        "--backend-url", dest="backend_url", help="ws:// or wss:// endpoint"
    )
    configure_cmd.add_argument("--id", help="connector id from the app")
    configure_cmd.add_argument(
        "--secret",
        help=(
            "connector secret from the app; prefer --from-file, since command "
            "lines are readable by other processes on this machine"
        ),
    )
    configure_cmd.add_argument(
        "--from-file", type=Path, help="key=value file holding id/secret/backend_url"
    )
    configure_cmd.add_argument("--log-level", dest="log_level", help="DEBUG, INFO, WARNING, ERROR")

    uninstall_cmd = sub.add_parser(
        "uninstall", help="stop the connector and remove it from startup"
    )
    uninstall_cmd.add_argument(
        "--purge",
        action="store_true",
        help="also delete the saved credentials and logs, unpairing this computer",
    )

    sub.add_parser("status", help="report whether the background connector is running")

    return parser


def run(argv: list[str] | None = None, *, fallback_log_dir: Path | None = None) -> int:
    """Dispatch a command.

    ``fallback_log_dir`` is supplied by the windowless service build, which has
    nowhere to print. It is passed in rather than sniffed from ``sys.stdout``
    because a GUI-subsystem executable started from an open terminal inherits
    that terminal's handles -- so the process that most needs a log file is the
    one that looks least like it needs one.
    """
    args = build_parser().parse_args(argv)

    # These write config and exit. They run before setup_logging on purpose:
    # the secret is in memory here, and there is no handler that could put it
    # in a file.
    if args.command == "pair":
        return pair(args)
    if args.command == "install":
        return install(args)
    if args.command == "configure":
        return configure(args)
    if args.command == "uninstall":
        return uninstall(args)
    if args.command == "status":
        return status(args)

    settings = load_settings(args.config)
    setup_logging(args.log_level or settings.log_level, settings.log_dir or fallback_log_dir)

    if args.command == "capabilities":
        print(json.dumps(registry_manifest(), indent=2))
        return 0

    try:
        if args.command == "livecheck":
            return asyncio.run(verify(settings, args.company))
        if args.command == "alterid-probe":
            return asyncio.run(alterid_probe(settings, args.company))
        if args.command == "update":
            return asyncio.run(update(settings, apply=args.apply))
        handler = serve if args.command == "run" else diagnose
        return asyncio.run(handler(settings))
    except KeyboardInterrupt:
        logger.info("interrupted")
        return 0


if __name__ == "__main__":
    sys.exit(run())
