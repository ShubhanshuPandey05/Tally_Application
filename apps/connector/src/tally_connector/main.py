"""Connector entry point.

Subcommands:

``run``        connect to the backend and serve jobs (the service path)
``diagnose``   check Tally locally and print what is wrong, in plain language
``pair``       store the credentials the app's pairing wizard produced
``install``    pair, then register the connector to start at logon
``uninstall``  remove it from startup
``status``     report whether the background connector is registered and running
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

from tally_core.tally import TallyClient, get_query, registry_manifest
from tally_core.tally.errors import TallyError

from . import __version__
from . import install as autostart
from .config import ConnectorSettings, load_settings, save_pairing, save_settings
from .livecheck import livecheck, print_manifest
from .logging_setup import setup_logging
from .session import AuthenticationRejected, ConnectorSession

logger = logging.getLogger(__name__)


async def serve(settings: ConnectorSettings) -> int:
    if not settings.is_paired:
        logger.error(
            "This connector is not paired yet. Open the TallyFlow app, add this "
            "computer, then run: tally-connector pair --id <ID> --secret <SECRET>"
        )
        return 2

    session = ConnectorSession(settings, version=__version__)
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

    client = TallyClient(settings.tally_config())
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


def uninstall(_: argparse.Namespace) -> int:
    """Stop the background connector and remove it from startup.

    Leaves connector.json alone: the Windows uninstaller removes it, and a
    reinstall over the top should not silently unpair the machine.
    """
    try:
        autostart.unregister()
    except OSError as exc:
        print(f"Could not remove the startup task: {exc}")
        return 1
    print(f'Removed "{autostart.TASK_NAME}" from startup.')
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

    sub.add_parser("uninstall", help="stop the connector and remove it from startup")
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
        handler = serve if args.command == "run" else diagnose
        return asyncio.run(handler(settings))
    except KeyboardInterrupt:
        logger.info("interrupted")
        return 0


if __name__ == "__main__":
    sys.exit(run())
