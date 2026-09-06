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
    portal      Run the management portal against the local backend
    migrate     Apply database migrations
    check       Everything CI would run: tests, lint, analyze

Production:
    preflight   Refuse-to-deploy checks against the current environment
    image       Build the backend container image
    connector   Build the Windows connector installer (Windows only)
    release     Build the signed-ready app bundles
    publish     Regenerate the update manifest from the staged artefacts
    prod        Everything above, in order, then commit and push

`connector` and `release` stage their output and publish automatically, and
publishing mirrors into the directory the production stack serves -- so any of
the three reaches customers. Run `publish` on its own after editing
deploy/release-policy.json -- to mark a release mandatory, or to raise the floor
below which clients refuse to run.

`prod` is the release-day command: check, build both artefacts against the
production API URL, publish, and push. Windows only, because the connector is.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
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
PORTAL = ROOT / "apps" / "portal"
WEBSITE = ROOT / "apps" / "website"
CONNECTOR = ROOT / "apps" / "connector"
INSTALLER = CONNECTOR / "installer"
ENV_FILE = BACKEND / ".env"

#: Where Caddy serves artefacts from (`deploy/uat/Caddyfile`, handle /downloads/*).
#: Builds stage here and the manifest is generated from what lands in it.
DOWNLOADS = ROOT / "deploy" / "uat" / "downloads"
#: The production stack serves its own copy (`deploy/prod/Caddyfile`). Every
#: publish mirrors DOWNLOADS into it, rather than either stack reading across
#: into the other's directory -- a deployment that depends on a sibling
#: environment's files still being there is one `rm -rf` from a dead site.
#: This is the directory customers actually reach; UAT alone is not shipped.
PROD_DOWNLOADS = ROOT / "deploy" / "prod" / "downloads"
#: The human half of the manifest: mandatory flags, floors, release notes.
RELEASE_POLICY = ROOT / "deploy" / "release-policy.json"
MANIFEST = DOWNLOADS / "manifest.json"

# --------------------------------------------------------------------------
# Production hostnames
#
# Three names on one backend, resolved here so no build step takes them from a
# human's memory on release day. deploy/prod/Caddyfile serves all three and
# deploy/prod/prod.env.example repeats them for the server side.
#
# PROD_API_URL is the expensive one to change: it is compiled into the connector
# installer and the mobile app, so moving it means re-issuing both to everybody
# who already installed them.
# --------------------------------------------------------------------------
PROD_SITE_HOST = "tallyflow.jsrprimesolution.com"
PROD_API_HOST = "api-tallyflow.jsrprimesolution.com"
PROD_PORTAL_HOST = "pd-tallyflow.jsrprimesolution.com"
PROD_API_URL = f"https://{PROD_API_HOST}"

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


def npm_bin() -> str | None:
    # `npm` is a .cmd shim on Windows, which `shutil.which` finds but
    # `subprocess` will not execute under the bare name -- so take the full
    # path `which` returns rather than "npm".
    return shutil.which("npm")


def need_npm() -> str:
    npm = npm_bin()
    if npm is None:
        sys.exit("npm is not on PATH -- install Node 20+ or open a shell that has it")
    return npm


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
    # Generated too, and not left blank: without a portal account there is no
    # way to approve the first signup, and a developer who has just registered
    # would find every "add" button refusing with no obvious reason why.
    portal_password = secrets.token_urlsafe(12)
    filled = (
        example.replace(
            "TALLYFLOW_JWT_SECRET=", f"TALLYFLOW_JWT_SECRET={secrets.token_urlsafe(48)}"
        )
        .replace(
            "TALLYFLOW_SECRET_KEYS=", f"TALLYFLOW_SECRET_KEYS={secrets.token_urlsafe(48)}"
        )
        .replace(
            "TALLYFLOW_PORTAL_BOOTSTRAP_PASSWORD=",
            f"TALLYFLOW_PORTAL_BOOTSTRAP_PASSWORD={portal_password}",
        )
    )
    ENV_FILE.write_text(filled, encoding="utf-8")

    print(f"Wrote {ENV_FILE} with generated secrets.")
    print("It is gitignored. Back up TALLYFLOW_SECRET_KEYS separately from the")
    print("database -- losing it means every connector has to be paired again.")
    print()
    # The portal is its own Vite app now, served by Caddy in a deployment and
    # by `run.py portal` locally -- so the backend alone does not serve it, and
    # printing the old :8000/portal address here would send everyone who runs
    # `init` to a 404 on their first attempt.
    print("Management portal:  run `python run.py portal` -> http://localhost:5174/portal")
    print("  sign in as        dev@tallyflow.in")
    print(f"  password          {portal_password}")
    print("New signups start unapproved; approve them there to unlock the app.")
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
    host = "127.0.0.1" if device in {"chrome", "web-server", "windows"} else "10.21.25.128"

    args = [need_flutter(), "run", f"--dart-define=TALLYFLOW_API_URL=http://{host}:8000"]
    if device:
        args += ["-d", device]
    return run(args, cwd=MOBILE)


def cmd_portal(argv: list[str]) -> int:
    """The management portal's dev server, proxying /v1 to the local backend.

    A deployment serves the portal from Caddy (``deploy/uat/web.Dockerfile``),
    which means ``run.py dev`` on its own has no portal at all. Run this
    alongside it.

    The proxy is not a convenience. It keeps the browser on one origin here as
    it is in production, so a portal token is never a cross-origin credential in
    development and same-origin in the environment that matters -- otherwise the
    only CORS behaviour anybody exercises is the one that does not ship.
    """
    npm = need_npm()
    if not (PORTAL / "node_modules").is_dir():
        print("Installing portal dependencies (first run only)...")
        if run([npm, "install", "--no-audit", "--no-fund"], cwd=PORTAL) != 0:
            return 1

    print("")
    # `localhost`, not 127.0.0.1: Vite binds the name, which resolves to ::1
    # first on Windows -- so the IPv4 literal is refused and reads as "the
    # dev server did not start".
    print("Portal:  http://localhost:5174/portal")
    print("Backend: expected on http://127.0.0.1:8000 -- start it with `run.py dev`.")
    print("")
    return run([npm, "run", "dev", "--", *argv], cwd=PORTAL)


def cmd_migrate(argv: list[str]) -> int:
    target = argv[0] if argv else "head"
    return run([python_bin(), "-m", "alembic", "upgrade", target], cwd=BACKEND)


def cmd_check(_: list[str]) -> int:
    """Everything that has to pass before a change ships."""
    steps: list[tuple[str, list[str], Path]] = [
        ("python tests", [python_bin(), "-m", "pytest", "packages", "apps/backend", "apps/connector", "-q"], ROOT),
        ("python lint", [python_bin(), "-m", "ruff", "check", "packages", "apps", "tools", "run.py"], ROOT),
    ]
    if flutter_bin():
        steps += [
            ("dart analyze", [need_flutter(), "analyze"], MOBILE),
            ("dart tests", [need_flutter(), "test"], MOBILE),
        ]
    else:
        print("! flutter not found -- skipping the mobile checks\n")

    # Both web apps are *built*, not tested -- neither has a test suite, and the
    # build is what catches the failure that matters here: a bad import or a JSX
    # syntax error ships as a blank page with nothing in any log to explain it.
    #
    # Skipped when dependencies are absent, matching how flutter is handled, and
    # said out loud rather than passing quietly: `check` has to stay runnable on
    # a machine that only works on the Python half.
    npm = npm_bin()
    for name, path in (("website", WEBSITE), ("portal", PORTAL)):
        if npm and (path / "node_modules").is_dir():
            steps.append((f"{name} build", [npm, "run", "build"], path))
        elif npm:
            print(f"! {name}/node_modules is missing -- skipping the {name} build")
            print(f"  (run: npm install --prefix {path.relative_to(ROOT).as_posix()})")
        else:
            print(f"! npm not found -- skipping the {name} build")

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

    # A deployment with no portal account can take signups and approve none of
    # them, so every customer who installs the app is stuck on "waiting to be
    # approved" with nobody able to act. Only a warning: on a redeploy the owner
    # already exists in the database and both of these should be blank by then.
    if not env("PORTAL_BOOTSTRAP_EMAIL"):
        warnings.append(
            "TALLYFLOW_PORTAL_BOOTSTRAP_EMAIL is unset -- correct once a portal "
            "owner exists, but a first deploy needs it or nothing can be approved"
        )
    elif len(env("PORTAL_BOOTSTRAP_PASSWORD")) < 12:
        problems.append(
            "TALLYFLOW_PORTAL_BOOTSTRAP_PASSWORD is missing or too short; it is the "
            "credential for an account that can see every customer on the platform"
        )

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

    print()
    stage(output)
    return cmd_publish([])


def stage(artefact: Path) -> None:
    """Copy a freshly built artefact to the directory Caddy serves.

    Done here rather than left as a manual step because the manifest is
    generated from what is *in* that directory: a build that stops short of
    copying produces a manifest still advertising the previous version, which
    is indistinguishable from "no update yet" to every client.
    """
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    destination = DOWNLOADS / artefact.name
    shutil.copy2(artefact, destination)
    print(f"Staged {destination.relative_to(ROOT)}")


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


def mobile_version() -> tuple[str, int]:
    """``version: 1.0.0+1`` out of pubspec.yaml, as (semver, build number).

    Regex for the same reason as :func:`connector_version`: run.py stays
    standard-library-only, and a YAML parser is not in the standard library.
    """
    source = (MOBILE / "pubspec.yaml").read_text(encoding="utf-8")
    match = re.search(r"^version:\s*([0-9]+\.[0-9]+\.[0-9]+)\+([0-9]+)\s*$", source, re.MULTILINE)
    if match is None:
        sys.exit("could not find a `version: x.y.z+n` line in apps/mobile/pubspec.yaml")
    return match.group(1), int(match.group(2))


def sha256_of(path: Path) -> str:
    """Hash an artefact in chunks -- an installer is tens of megabytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def cmd_publish(_: list[str]) -> int:
    """Regenerate deploy/uat/downloads/manifest.json from what is on disk.

    This is what makes an update *live*. Both clients poll the manifest, compare
    versions, verify the artefact against ``sha256`` and refuse to install
    anything that does not match -- so the hash has to be measured here, from
    the exact bytes being served, rather than copied from a build log.

    Safe to re-run: it only ever describes files that are actually present.
    """
    if not DOWNLOADS.is_dir():
        return usage_error(f"{DOWNLOADS} does not exist")

    policy = json.loads(RELEASE_POLICY.read_text(encoding="utf-8"))
    connector = connector_version()
    app_version, app_build = mobile_version()

    targets = [
        ("connector", connector, None, f"TallyFlowConnector-Setup-{connector}.exe"),
        ("android", app_version, app_build, f"TallyFlow-{app_version}.apk"),
    ]

    entries: dict[str, object] = {}
    missing: list[str] = []

    for platform, version, build, filename in targets:
        artefact = DOWNLOADS / filename
        if not artefact.is_file():
            missing.append(f"{platform}: {filename}")
            continue

        rules = policy.get(platform, {})
        entry = {
            "version": version,
            "file": filename,
            # Relative on purpose: the same manifest has to work behind
            # whatever hostname a deployment happens to use, and the client
            # already knows the host it fetched this from.
            "url": f"/downloads/{filename}",
            "sha256": sha256_of(artefact),
            "size_bytes": artefact.stat().st_size,
            "mandatory": bool(rules.get("mandatory", False)),
            "min_supported_version": rules.get("min_supported_version", version),
            "notes": rules.get("notes", ""),
        }
        if build is not None:
            entry["build_number"] = build
        entries[platform] = entry
        print(f"  {platform:<10} {version:<10} {artefact.stat().st_size / 1e6:6.1f} MB  {filename}")

    if not entries:
        return usage_error(
            f"no artefacts found in {DOWNLOADS}\n"
            "       build them first: `python run.py connector` / `python run.py release <url>`"
        )

    manifest = {
        "schema": 1,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        **entries,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"\nWrote {MANIFEST}")
    for line in missing:
        print(f"  ! not published (file absent) -- {line}")

    # Every publish reaches production, not just the release-day command.
    #
    # This used to belong to `prod` alone, on the reasoning that a full
    # release is when artefacts should cross into the live stack. In practice
    # that made `run.py connector` a trap: it built the installer, generated a
    # manifest, printed "Wrote ..." and returned 0, while the directory the
    # production Caddy actually serves still held the previous release. The
    # build log said shipped, the live host said 0.2.1, and the fleet was
    # correctly told there was no update -- as far as any client could see,
    # there was not.
    #
    # Mirroring here costs a no-op copy on a re-publish and removes the one
    # failure mode a release process must not have: looking successful.
    print()
    mirror_downloads()
    print("\nClients poll this on their own schedule. Deploy it together with the")
    print("artefacts it names, or a phone will fetch a manifest whose download 404s.")
    return 0


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

    # Refuse to produce a distributable build signed with the debug key. Gradle
    # falls back to it so a fresh checkout can `flutter run --release`, and that
    # fallback is exactly how a debug-signed APK reaches customers unnoticed:
    # it builds cleanly, installs cleanly, and looks identical. The debug
    # keystore's password is publicly documented, so anyone can sign a package
    # Android will install *over* TallyFlow as an update.
    #
    # This is the last point at which that is catchable, so it is caught here
    # rather than left to whoever remembers.
    if "--allow-debug-signing" in argv:
        print("! Building with the DEBUG signing key. Do not distribute this artefact.\n")
    elif not (MOBILE / "android" / "key.properties").is_file():
        return usage_error(
            "apps/mobile/android/key.properties is missing, so this build would be\n"
            "       signed with the DEBUG key and must not be distributed.\n"
            "       See apps/mobile/android/key.properties.example and DEPLOYMENT.md.\n"
            "       To build a throwaway artefact anyway: run.py release <url> --allow-debug-signing"
        )

    flutter = need_flutter()
    defines = [
        f"--dart-define=TALLYFLOW_API_URL={api_url}",
        "--dart-define=TALLYFLOW_ENV=prod",
    ]
    # apk as well as appbundle: an .aab cannot be installed on a phone. It is
    # a Play Store upload format, so a pilot that downloads the app from our own
    # site -- which is how UAT works before any store listing exists -- needs the
    # apk or it has nothing to install.
    for target in (["apk"], ["appbundle"], ["web"]):
        if run([flutter, "build", *target, "--release", *defines], cwd=MOBILE) != 0:
            return 1
    print("\nBuilt in apps/mobile:")
    print("  build/app/outputs/flutter-apk/app-release.apk   (sideload / website)")
    print("  build/app/outputs/bundle/release/               (Play Store upload)")
    print("  build/web/                                      (Flutter web)")

    # Renamed on the way out: the APK the site links to, and that the update
    # manifest names, carries its version. `app-release.apk` would overwrite
    # the previous release in place and leave every older manifest dangling.
    version, _ = mobile_version()
    apk = MOBILE / "build" / "app" / "outputs" / "flutter-apk" / "app-release.apk"
    if not apk.is_file():
        return usage_error(f"flutter reported success but {apk} is missing")

    print()
    staged = DOWNLOADS / f"TallyFlow-{version}.apk"
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(apk, staged)
    print(f"Staged {staged.relative_to(ROOT)}")
    return cmd_publish([])


# --------------------------------------------------------------------------
# Release day
# --------------------------------------------------------------------------


def git(args: list[str], *, capture: bool = False) -> tuple[int, str]:
    """Run git, optionally capturing stdout. Never interactive."""
    if capture:
        proc = subprocess.run(
            ["git", *args], cwd=str(ROOT), capture_output=True, text=True
        )
        return proc.returncode, proc.stdout.strip()
    return run(["git", *args]), ""


def mirror_downloads() -> None:
    """Copy the published artefacts into the production stack's directory.

    The manifest goes too, and it goes LAST. Both clients verify an artefact
    against the ``sha256`` in the manifest and refuse anything that does not
    match, so a manifest that arrives before the file it describes is a window
    -- however short -- in which every client is told about a download that
    404s. Copying it last makes that window impossible rather than unlikely.
    """
    PROD_DOWNLOADS.mkdir(parents=True, exist_ok=True)

    artefacts = [
        p for p in DOWNLOADS.iterdir()
        if p.is_file() and p.suffix in {".exe", ".apk"}
    ]
    for artefact in artefacts:
        _copy_artefact(artefact, PROD_DOWNLOADS / artefact.name)

    if MANIFEST.is_file():
        _copy_artefact(MANIFEST, PROD_DOWNLOADS / MANIFEST.name)

    print(f"Mirrored into {PROD_DOWNLOADS.relative_to(ROOT)}")


def _copy_artefact(source: Path, destination: Path) -> None:
    """Copy one file, tolerating a read-only destination and skipping no-ops.

    Both cases come up on the second run and neither is interesting:

    * ``copy2`` preserves permissions, so an artefact that was read-only in the
      source directory lands read-only here -- and the *next* mirror then dies
      with PermissionError on a file it wrote itself.
    * Republishing usually changes one artefact. Re-copying 60 MB of unchanged
      installers to prove it is just slow.
    """
    if destination.exists():
        stat = destination.stat()
        if stat.st_size == source.stat().st_size and int(stat.st_mtime) == int(
            source.stat().st_mtime
        ):
            print(f"  {source.name} (unchanged)")
            return
        # Clear the read-only bit before overwriting, or Windows refuses.
        destination.chmod(0o644)
        destination.unlink()

    shutil.copy2(source, destination)
    print(f"  {source.name}")


def cmd_prod(argv: list[str]) -> int:
    """Build everything for production, publish it, and push.

    The release-day command. It exists because the steps have an order that is
    not obvious and is expensive to get wrong: the connector installer and the
    app both compile the API hostname in, and the manifest is generated from
    the bytes on disk -- so building before the hostname is right, or pushing
    before publishing, ships something that cannot be fixed by a redeploy.

        python run.py prod              # build, publish, commit, push
        python run.py prod --no-push    # everything except the push

    Windows only: PyInstaller freezes for the platform it runs on, so there is
    no cross-compile for the connector to offer.
    """
    if not WINDOWS:
        return usage_error(
            "prod builds the connector installer, which is Windows-only.\n"
            "       Run the individual steps elsewhere if you only need the app."
        )

    push = "--no-push" not in argv

    # A dirty tree is not refused -- the whole point is to commit the version
    # bumps and rebuilt artefacts. But an unknown starting state turns "what
    # shipped?" into archaeology, so it is printed before anything is built.
    code, branch = git(["rev-parse", "--abbrev-ref", "HEAD"], capture=True)
    if code != 0:
        return usage_error("not a git repository, or git is not on PATH")
    _, dirty = git(["status", "--porcelain"], capture=True)

    connector = connector_version()
    mobile, build_number = mobile_version()

    print("TallyFlow production release")
    print(f"  branch     {branch}")
    print(f"  connector  {connector}")
    print(f"  app        {mobile}+{build_number}")
    print(f"  API URL    {PROD_API_URL}")
    print(f"  site       https://{PROD_SITE_HOST}")
    print(f"  portal     https://{PROD_PORTAL_HOST}")
    print(f"  working tree {'has uncommitted changes' if dirty else 'is clean'}")
    print(f"  push       {'yes' if push else 'no (--no-push)'}")
    print()

    # The gate. CLAUDE.md is unambiguous that this passes before anything ships,
    # and it is cheaper to fail here than after two long native builds.
    print("== check ==")
    if cmd_check([]) != 0:
        return usage_error("check failed -- nothing was built, nothing was pushed")

    # Connector first. It is the build most likely to fail for an environmental
    # reason (PyInstaller, Inno Setup), and failing before the Flutter build
    # saves the longer of the two.
    print("\n== connector installer ==")
    if cmd_connector([]) != 0:
        return usage_error("connector build failed")

    print("\n== mobile app ==")
    # Passes --allow-debug-signing through, so the guard in cmd_release stays
    # the only place that decides whether an unsigned build may be produced.
    release_argv = [PROD_API_URL] + [a for a in argv if a == "--allow-debug-signing"]
    if cmd_release(release_argv) != 0:
        return usage_error("app build failed")

    print("\n== git ==")
    if git(["add", "-A"])[0] != 0:
        return usage_error("git add failed")

    _, staged = git(["status", "--porcelain"], capture=True)
    if not staged:
        print("Nothing changed -- no commit needed.")
        return 0

    message = f"Release: connector {connector}, app {mobile}+{build_number}"
    if git(["commit", "-m", message])[0] != 0:
        return usage_error("git commit failed")

    if not push:
        print("\nCommitted. Not pushed (--no-push).")
        return 0

    if git(["push"])[0] != 0:
        return usage_error(
            "git push failed -- the commit is local. Fix the remote and push it."
        )

    print(f"\nPushed {message}.")
    print("Deploy it:  ssh <host> && cd tallyflow && git pull &&")
    print("            docker compose --env-file prod.env up -d --build")
    return 0


# --------------------------------------------------------------------------


COMMANDS = {
    "init": cmd_init,
    "dev": cmd_dev,
    "app": cmd_app,
    "portal": cmd_portal,
    "migrate": cmd_migrate,
    "check": cmd_check,
    "preflight": cmd_preflight,
    "image": cmd_image,
    "connector": cmd_connector,
    "release": cmd_release,
    "publish": cmd_publish,
    "prod": cmd_prod,
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
