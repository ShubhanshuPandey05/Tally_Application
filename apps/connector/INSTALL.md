# TallyFlow Connector — Installer Guide

How to build, run, verify, and remove the Windows connector installer.

The connector is the only component that talks to TallyPrime. It runs on the
shop PC where Tally is installed and dials **out** to the backend — Tally is
never exposed to the internet.

---

## 1. Build the installer (developer machine, Windows only)

```powershell
python run.py connector
```

That does three things:

1. PyInstaller freezes both executables into `apps\connector\dist\`
   - `tally-connector.exe` — the console CLI a person runs
   - `tally-connector-service.exe` — windowless, what the startup task runs
2. Flutter builds the window into
   `apps\mobile\build\windows\x64\runner\Release\`
   - `tally-connector-window.exe` — what a person at the shop's PC looks at.
     It needs a Visual Studio install with the C++ tools **and ATL**; the build
     finds one with `vswhere` and says so if there is none. See
     [`NATIVE-UI.md`](NATIVE-UI.md).
3. Inno Setup compiles all of it into:

```
apps\connector\dist\installer\TallyFlowConnector-Setup-0.1.0.exe
```

The version comes from `tally_connector.__version__`, so the file name can
never drift from the build.

### Prerequisites

| Requirement | Notes |
|---|---|
| Windows | PyInstaller freezes for the platform it runs on — no cross-compile |
| Python venv at repo root | `run.py` finds `.venv\Scripts\python.exe` itself |
| PyInstaller | installed with `apps\connector[dev]` |
| Inno Setup 6 | `winget install JRSoftware.InnoSetup` |

`run.py` locates `ISCC.exe` on PATH, then under `%LOCALAPPDATA%\Programs` and
both Program Files roots. If it isn't found, the two `.exe` files are still
built and only the installer step is skipped.

> The output is **unsigned**. SmartScreen will warn on first download until it
> is code-signed. Sign it before sending a link to a customer.

---

## 2. Run the installer (customer machine)

Double-click `TallyFlowConnector-Setup-0.1.0.exe`.

**No administrator rights needed.** It is a per-user install
(`PrivilegesRequired=lowest`), so there is no UAC prompt and the shop owner can
run it themselves.

**The wizard asks for nothing.** Pairing happens afterwards, from the app's
camera — the connector shows a QR code and the phone reads it. The step that
used to live there, typing a connector id and a 43-character secret, is the step
this replaced.

Setup:

1. Copies both executables to `%LOCALAPPDATA%\Programs\TallyFlow Connector`,
   and the window into a `window\` folder beside them
2. Writes the server address to a temp file (never onto a command line — any
   process on the machine can read another process's arguments)
3. Runs `tally-connector.exe install --from-file …`, which writes
   `connector.json` and registers the **"TallyFlow Connector"** scheduled task
4. Starts the connector immediately, and at every logon thereafter
5. Offers to open the connector's own window, which is where the code is

Then, on the phone: **Tally PCs → Add a PC → Scan the code on that PC**. The
credentials go from the server to that machine over TLS; nobody sees a secret.

### Silent / bulk install

For a business rolling this out to twenty shop PCs, where nobody is going to
walk between them with a phone:

```powershell
TallyFlowConnector-Setup-0.1.0.exe /VERYSILENT /ID=<id> /SECRET=<secret>
```

Add `/SERVER=wss://…` to override the backend. Supplying `/ID` or `/SECRET` is
the only thing that brings the credentials page back — an interactive install
never sees it, because scanning is the path.

Without them the install is still valid: the machine comes up unpaired, shows a
code in its own window, and is paired whenever somebody gets to it.

**Each machine still needs its own pairing.** One connector row per PC is what
lets the app say *which* shop went offline.

---

## 3. Verify it works

Two Start Menu shortcuts under **TallyFlow**:

- **Check Tally Connection** → runs `tally-connector diagnose`
- **Connector Status** → runs `tally-connector status`

Or from a terminal, in the install directory:

```powershell
cd "$env:LOCALAPPDATA\Programs\TallyFlow Connector"
.\tally-connector.exe status      # paired? backend? startup task? logs?
.\tally-connector.exe diagnose    # can it actually reach TallyPrime?
.\tally-connector.exe livecheck   # run every query against live Tally and verify the accounting
```

A healthy `status` reports `Paired: yes (…)`, `Startup: Running`. It exits
non-zero on any problem, so it also works as a monitoring check.

`diagnose` requires TallyPrime to be **open** with a company loaded and its
HTTP interface listening on `localhost:9000`
(*F1 → Settings → Connectivity → Client/Server configuration →
TallyPrime acts as: **Both***).

The `install` step deliberately does **not** touch Tally — a shop owner may
install this before opening Tally for the day, and a failed connection check
must not fail the install.

---

## 4. Where things land

| What | Path |
|---|---|
| Executables | `%LOCALAPPDATA%\Programs\TallyFlow Connector\` |
| Pairing config | `…\TallyFlow Connector\connector.json` (next to the exe) |
| Logs & cache | `%LOCALAPPDATA%\TallyFlow Connector\` |
| Startup task | Task Scheduler → `\TallyFlow Connector` (logon trigger, 30s delay) |

The task runs as the installing user with least privilege, never expires
(`ExecutionTimeLimit` off), keeps running on battery, and restarts up to 3
times a minute apart on failure.

---

## 5. Upgrading

Run the newer installer over the top. It has a fixed `AppId`, so Windows treats
it as an upgrade rather than a second parallel install. Before copying files it
runs `tally-connector.exe uninstall` to stop the running service — an upgrade
cannot overwrite a locked executable. **Pairing survives**: `uninstall` only
touches Task Scheduler, not `connector.json`.

---

## 6. Uninstalling

Settings → Apps → **TallyFlow Connector** → Uninstall.

Removes the scheduled task, the process, `connector.json` (it holds the pairing
secret, so removing the product removes the credential), and
`%LOCALAPPDATA%\TallyFlow Connector` including logs.

---

## 7. Running without the installer (development)

From a source checkout, skipping the frozen build entirely:

```powershell
.\.venv\Scripts\python.exe -m tally_connector.main pair --id <ID> --secret <SECRET>
.\.venv\Scripts\python.exe -m tally_connector.main run
```

To pair *and* register the startup task the way the installer does, but without
building an installer:

```powershell
.\.venv\Scripts\python.exe -m tally_connector.main install --id <ID> --secret <SECRET> --backend-url ws://localhost:8000/v1/connector
```

Add `--no-autostart` to write the config only and leave Task Scheduler alone.

---

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Installer says the server address is invalid | It must start with `wss://` or `ws://` |
| `status`: *"This computer is not paired"* | Re-run the installer, or `tally-connector pair --id … --secret …` |
| `status`: *"will not start on its own"* | Startup task missing — re-run the installer |
| Task exists but is `Ready`, not `Running` | `schtasks /Run /TN "TallyFlow Connector"` |
| *"Could not register the startup task"* | Pairing already saved and valid; start by hand with `tally-connector run` |
| `diagnose` cannot reach Tally | TallyPrime not open, no company loaded, or port 9000 not enabled |
| One dashboard section (usually sales) stays empty while the rest works | That section's read is overrunning its budget. Run `livecheck` and look at the **Timing** table — anything marked `SLOW` cannot reach the dashboard live |
| Logs show `dropping <query> before it reached Tally` | Normal under load: work expired in the queue and was not spent on Tally. Persistent lines mean reads are slower than the backend's timeout |
| Logs show `refusing <query>: N request(s) already queued` | The Tally queue is full. Raise `tally_queue_max_depth` in `connector.json`, or find out why reads are taking so long |
| Connector authenticates, then stops after a backend restart | Backend has no fixed `TALLYFLOW_JWT_SECRET`/`TALLYFLOW_SECRET_KEYS`, so stored connector secrets became undecryptable. Run `python run.py init` once and keep that `.env` |
| SmartScreen blocks the download | Expected until code-signed — More info → Run anyway |

Logs for any of the above: `%LOCALAPPDATA%\TallyFlow Connector\logs`
(also a Start Menu shortcut, **Connector Logs**).
