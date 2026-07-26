# PyInstaller spec for the Windows connector.
#
# Build:  pyinstaller apps/connector/connector.spec --clean
# Output: apps/connector/dist/tally-connector.exe          (console CLI)
#         apps/connector/dist/tally-connector-service.exe  (windowless)
#
# Two executables from one codebase:
#
#   tally-connector.exe          what a person runs -- diagnose, pair, install,
#                                status. Console, so it can print.
#   tally-connector-service.exe  what the logon task runs. No console, because
#                                a black window sitting on the shop counter's
#                                desktop all day gets closed by someone.
#
# They cannot be the same file: the console subsystem is a link-time property of
# the executable, not a runtime flag.

from PyInstaller.utils.hooks import collect_submodules

# Query modules are pulled in by decorator side effect, not by a direct import
# PyInstaller can follow, so the whole package must be collected explicitly.
# Miss this and the exe starts fine and then reports "unknown query" for
# everything -- a failure that does not reproduce in development.
hiddenimports = collect_submodules("tally_core.tally.queries")

EXCLUDES = ["tkinter", "matplotlib", "numpy", "pytest"]


def analyse(script):
    # entrypoint.py, not main.py: PyInstaller runs its target as a top-level
    # script, where main.py's relative imports have no package to resolve
    # against.
    return Analysis(
        [script],
        pathex=["src"],
        binaries=[],
        datas=[],
        hiddenimports=hiddenimports,
        hookspath=[],
        runtime_hooks=[],
        excludes=EXCLUDES,
        noarchive=False,
    )


cli_analysis = analyse("entrypoint.py")
cli_exe = EXE(
    PYZ(cli_analysis.pure),
    cli_analysis.scripts,
    cli_analysis.binaries,
    cli_analysis.datas,
    [],
    name="tally-connector",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

service_analysis = analyse("service_entrypoint.py")
service_exe = EXE(
    PYZ(service_analysis.pure),
    service_analysis.scripts,
    service_analysis.binaries,
    service_analysis.datas,
    [],
    name="tally-connector-service",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    # An unhandled exception in a windowed build would otherwise pop a modal
    # dialog on an unattended PC and wedge the process until someone clicks it.
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
