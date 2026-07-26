@echo off
rem Start Menu shortcut target.
rem
rem The connector CLI prints and exits; launched straight from a shortcut the
rem window would close before anyone could read it, so hold it open.
rem
rem Takes the subcommand as an argument ("diagnose" when the shortcut passes
rem none), which lets one script back both the "Check Tally Connection" and
rem "Connector Status" shortcuts.

setlocal
set "COMMAND=%~1"
if "%COMMAND%"=="" set "COMMAND=diagnose"

title TallyFlow Connector - %COMMAND%
"%~dp0tally-connector.exe" %COMMAND%

echo.
echo ----------------------------------------------------------------
echo Press any key to close this window.
pause >nul
