#!/usr/bin/env python3
# Connector running command 
# TALLY_CONNECTOR_CONNECTOR_ID=2cb25e291c44404aaba2fb0128a5628e TALLY_CONNECTOR_CONNECTOR_SECRET=UpnLchNTrUkDC2alwVE0JnbzfvSDqD-IpKq2qjtNPbI TALLY_CONNECTOR_BACKEND_URL=ws://127.0.0.1:8000/v1/connector ./.venv/Scripts/python.exe -m tally_connector.main run

"""TallyFlow task runner: `python run.py <command>`.

One file, standard library only, so it works the same on a Windows laptop and on
a Linux deploy box. Run it with the system Python -- it finds the virtualenv
itself.

    python run.py                 # list commands

Development:
    init        Write apps/backend/.env with freshly generated secrets
    dev         Start the backend with auto-reload on 127.0.0.1:8000
    app         Run the Flutter app pointed at the local backend
    migrate     Apply database migrations
    check       Everything CI would run: tests, lint, analyze

Production:
    preflight   Refuse-to-deploy checks against the current environment
    image       Build the backend container image
    connector   Build the Windows connector installer (Windows only)
    release     Build the signed-ready app bundles
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "apps" / "backend"
MOBILE = ROOT / "apps" / "mobile"
CONNECTOR = ROOT / "apps" / "connector"
INSTALLER = CONNECTOR / "installer"
ENV_FILE = BACKEND / ".env"

WINDOWS = os.name == "nt"


# --------------------------------------------------------------------------
# Toolchain discovery
# --------------------------------------------------------------------------


def python_bin() -> str:
    """The virtualenv interpreter, or this one if there is no venv yet."""
    candidate = ROOT / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    return str(candidate) if candidate.exists() else sys.executable


def flutter_bin() -> str | None:
    return shutil.which("flutter")


def run(
    args: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None
) -> int:
    print(f"$ {' '.join(args)}", flush=True)
    merged = {**os.environ, **(env or {})}
    return subprocess.call(args, cwd=str(cwd), env=merged)


def need_flutter() -> str:
    flutter = flutter_bin()
    if flutter is None:
        sys.exit("flutter is not on PATH -- install it or open a shell that has it")
    return flutter


# --------------------------------------------------------------------------
# Development
# --------------------------------------------------------------------------


def cmd_init(_: list[str]) -> int:
    """Create apps/backend/.env with real secrets.

    Generated rather than left blank on purpose. Without a fixed
    TALLYFLOW_JWT_SECRET the backend invents one per process, so every restart
    signs you out *and* makes previously paired connector secrets undecryptable
    -- which reads as "the connector suddenly stopped authenticating".
    """
    if ENV_FILE.exists():
        print(f"{ENV_FILE} already exists; leaving it alone.")
        return 0

    example = (BACKEND / ".env.example").read_text(encoding="utf-8")
    filled = example.replace(
        "TALLYFLOW_JWT_SECRET=", f"TALLYFLOW_JWT_SECRET={secrets.token_urlsafe(48)}"
    ).replace(
        "TALLYFLOW_SECRET_KEYS=", f"TALLYFLOW_SECRET_KEYS={secrets.token_urlsafe(48)}"
    )
    ENV_FILE.write_text(filled, encoding="utf-8")

    print(f"Wrote {ENV_FILE} with generated secrets.")
    print("It is gitignored. Back up TALLYFLOW_SECRET_KEYS separately from the")
    print("database -- losing it means every connector has to be paired again.")
    return 0


def cmd_dev(argv: list[str]) -> int:
    """Backend with auto-reload, bound to localhost only."""
    if not ENV_FILE.exists():
        print("No apps/backend/.env yet -- creating one.")
        cmd_init([])

    port = argv[0] if argv else "8000"
    # 127.0.0.1, not 0.0.0.0: a dev server holding a Tally database should not
    # be reachable from the rest of the cafe wifi. The container CMD binds
    # 0.0.0.0 because there it is behind a proxy.
    #
    # cwd is apps/backend because Settings reads `.env` relative to the working
    # directory -- launching from the repo root would silently ignore the file
    # `init` just wrote and fall back to a per-process random JWT secret.
    return run(
        [
            python_bin(), "-m", "uvicorn",
            "tally_backend.main:create_app",
            "--factory",
            "--host", "0.0.0.0",
            "--port", port,
            "--reload",
            "--reload-dir", str(BACKEND / "src"),
            "--reload-dir", str(ROOT / "packages" / "tally_core" / "src"),
        ],
        cwd=BACKEND,
    )


def cmd_app(argv: list[str]) -> int:
    """Flutter app against the local backend.

    `10.0.2.2` is the host machine as seen from the Android emulator; using
    `localhost` there points the app at the emulator itself, which is the most
    common "why can't it reach my backend" question.
    """
    device = argv[0] if argv else None
    host = "127.0.0.1" if device in {"chrome", "web-server", "windows"} else "10.0.2.2"

    args = [need_flutter(), "run", f"--dart-define=TALLYFLOW_API_URL=http://localhost:8000"]
    if device:
        args += ["-d", device]
    return run(args, cwd=MOBILE)


def cmd_migrate(argv: list[str]) -> int:
    target = argv[0] if argv else "head"
    return run([python_bin(), "-m", "alembic", "upgrade", target], cwd=BACKEND)


def cmd_check(_: list[str]) -> int:
    """Everything that has to pass before a change ships."""
    steps: list[tuple[str, list[str], Path]] = [
        ("python tests", [python_bin(), "-m", "pytest", "packages", "apps/backend", "apps/connector", "-q"], ROOT),
        ("python lint", [python_bin(), "-m", "ruff", "check", "packages", "apps"], ROOT),
    ]
    if flutter_bin():
        steps += [
            ("dart analyze", [need_flutter(), "analyze"], MOBILE),
            ("dart tests", [need_flutter(), "test"], MOBILE),
        ]
    else:
        print("! flutter not found -- skipping the mobile checks\n")

    failed: list[str] = []
    for name, args, cwd in steps:
        print(f"\n--- {name} ---")
        if run(args, cwd=cwd) != 0:
            failed.append(name)

    print()
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    print("All checks passed.")
    return 0


# --------------------------------------------------------------------------
# Production
# --------------------------------------------------------------------------


def cmd_preflight(_: list[str]) -> int:
    """Check the environment this process would actually deploy with.

    Reads real environment variables, not a config file, because that is what a
    container gets. Every check here corresponds to a failure that is silent
    until it is expensive.
    """
    problems: list[str] = []
    warnings: list[str] = []

    def env(name: str) -> str:
        return os.environ.get(f"TALLYFLOW_{name}", "")

    if env("ENVIRONMENT") != "prod":
        warnings.append("TALLYFLOW_ENVIRONMENT is not 'prod'")

    if len(env("JWT_SECRET")) < 32:
        problems.append("TALLYFLOW_JWT_SECRET is missing or too short (needs 32+ chars)")

    if not env("SECRET_KEYS"):
        problems.append(
            "TALLYFLOW_SECRET_KEYS is not set -- connector secrets would be encrypted "
            "with the JWT secret, so rotating that key would orphan every connector"
        )

    database = env("DATABASE_URL")
    if not database:
        problems.append("TALLYFLOW_DATABASE_URL is not set")
    elif database.startswith("sqlite"):
        problems.append("TALLYFLOW_DATABASE_URL points at SQLite; production is Postgres")
    elif not database.startswith("postgresql+asyncpg"):
        problems.append("TALLYFLOW_DATABASE_URL must use the postgresql+asyncpg driver")

    if env("DEBUG").lower() in {"1", "true", "yes"}:
        problems.append("TALLYFLOW_DEBUG is on; it leaks internals in error responses")

    if not env("REDIS_URL"):
        warnings.append(
            "TALLYFLOW_REDIS_URL is unset -- fine for a single instance, but a "
            "second replica could not reach connectors held by the first"
        )

    if not env("CORS_ORIGINS"):
        warnings.append("TALLYFLOW_CORS_ORIGINS is empty; a web dashboard would be blocked")

    for line in warnings:
        print(f"warn  {line}")
    for line in problems:
        print(f"FAIL  {line}")

    if problems:
        print(f"\n{len(problems)} blocking problem(s). Not safe to deploy.")
        return 1

    print("\nEnvironment looks deployable.")
    print("Reminder: one uvicorn worker per container. Forked workers each hold")
    print("different connector sockets with no way to route between them.")
    return 0


def cmd_image(argv: list[str]) -> int:
    """Build the backend image.

    Context is the repo root, not apps/backend: the Dockerfile copies the shared
    `tally_core` package too.
    """
    tag = argv[0] if argv else "tallyflow-backend:latest"
    return run(
        ["docker", "build", "-f", str(BACKEND / "Dockerfile"), "-t", tag, str(ROOT)]
    )


def cmd_connector(_: list[str]) -> int:
    """Build the two connector executables, then the Windows installer.

    PyInstaller freezes for the platform it runs on, so this is Windows-only by
    nature -- there is no cross-compile to offer.
    """
    if not WINDOWS:
        return usage_error("the connector installer can only be built on Windows")

    version = connector_version()
    print(f"Building TallyFlow Connector {version}\n")

    # cwd matters: the spec refers to entrypoint.py and src/ relatively.
    if run([python_bin(), "-m", "PyInstaller", "connector.spec", "--clean", "--noconfirm"],
           cwd=CONNECTOR) != 0:
        return 1

    for name in ("tally-connector.exe", "tally-connector-service.exe"):
        if not (CONNECTOR / "dist" / name).is_file():
            return usage_error(f"PyInstaller reported success but {name} is missing")

    iscc = inno_compiler()
    if iscc is None:
        print("\nBuilt both executables in apps/connector/dist.")
        print("Inno Setup 6 is not installed, so the installer was not built.")
        print("Install it with:  winget install JRSoftware.InnoSetup")
        return 1

    if run([iscc, f"/DAppVersion={version}", str(INSTALLER / "tallyflow-connector.iss")],
           cwd=INSTALLER) != 0:
        return 1

    output = CONNECTOR / "dist" / "installer" / f"TallyFlowConnector-Setup-{version}.exe"
    print(f"\nInstaller: {output}")
    print("Unsigned -- SmartScreen will warn on first download until it is")
    print("code-signed. Sign it before sending the link to a customer.")
    return 0


def connector_version() -> str:
    """Read __version__ out of the connector package without importing it.

    Importing would need the venv on sys.path and pull in pydantic; a regex
    keeps this file standard-library-only, which is the point of run.py.
    """
    source = (CONNECTOR / "src" / "tally_connector" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\'](.+?)["\']', source, re.MULTILINE)
    if match is None:
        sys.exit("could not find __version__ in tally_connector/__init__.py")
    return match.group(1)


def inno_compiler() -> str | None:
    """Locate ISCC.exe, the Inno Setup command-line compiler.

    Inno 6.7 installs per-user under LOCALAPPDATA\\Programs by default and puts
    nothing on PATH, so checking Program Files alone finds nothing on a machine
    where it is plainly installed.
    """
    found = shutil.which("iscc")
    if found:
        return found

    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
        Path(r"C:\Program Files (x86)"),
        Path(r"C:\Program Files"),
    ]
    for root in roots:
        for version in ("Inno Setup 6", "Inno Setup 5"):
            candidate = root / version / "ISCC.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def cmd_release(argv: list[str]) -> int:
    """Build the app against a real API URL.

    The URL is required and has no default. A release build that silently
    pointed at a developer's laptop is the kind of mistake you find out about
    from users.
    """
    if not argv:
        return usage_error("release needs the public API URL, e.g. https://api.tallyflow.in")

    api_url = argv[0]
    if not api_url.startswith("https://"):
        return usage_error("the release API URL must be https -- this is accounting data")

    flutter = need_flutter()
    defines = [
        f"--dart-define=TALLYFLOW_API_URL={api_url}",
        "--dart-define=TALLYFLOW_ENV=prod",
    ]
    for target in (["appbundle"], ["web"]):
        if run([flutter, "build", *target, "--release", *defines], cwd=MOBILE) != 0:
            return 1
    print("\nBuilt build/app/outputs/bundle/release/ and build/web/ in apps/mobile.")
    return 0


# --------------------------------------------------------------------------


COMMANDS = {
    "init": cmd_init,
    "dev": cmd_dev,
    "app": cmd_app,
    "migrate": cmd_migrate,
    "check": cmd_check,
    "preflight": cmd_preflight,
    "image": cmd_image,
    "connector": cmd_connector,
    "release": cmd_release,
}


def usage_error(message: str) -> int:
    print(f"error: {message}\n", file=sys.stderr)
    return 2


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help", "help"}:
        print(__doc__)
        return 0

    command = sys.argv[1]
    handler = COMMANDS.get(command)
    if handler is None:
        print(__doc__)
        return usage_error(f"unknown command '{command}'")
    return handler(sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
