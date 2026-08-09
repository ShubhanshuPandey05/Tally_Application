# TallyFlow Connector

The small Windows program that lets the TallyFlow app read TallyPrime.

It runs on the shop PC where Tally is installed and dials **out** to the
backend over a WebSocket. Tally is never exposed to the internet, and no
inbound port is opened on the shop's network.

```
Phone  ──HTTPS──▶  Backend  ──WSS──▶  Connector  ──▶  TallyPrime (127.0.0.1:9000)
```

**It only ever reads.** There is no code path in this package that creates,
edits or deletes anything in Tally — the query registry contains read requests
only, and a test asserts that.

> Building the installer? See **[INSTALL.md](INSTALL.md)**. This file is about
> running and supporting the connector once it is built.

---

## Install

Run `TallyFlowConnector-Setup-<version>.exe` on the shop PC and enter the
Connector ID and Secret from the app (**Settings → Tally PCs → Add this
computer**).

It installs per-user, so there is no UAC prompt, and registers a Scheduled Task
that starts the connector at logon.

For an unattended rollout across many machines:

```powershell
TallyFlowConnector-Setup.exe /VERYSILENT /ID=<id> /SECRET=<secret> /SERVER=wss://api.tallyflow.app/v1/connector
```

Each PC needs its **own** pairing. One connector row per machine is what lets
the app say *which* shop went offline.

---

## Commands

`tally-connector.exe` lives in `%LOCALAPPDATA%\Programs\TallyFlow Connector`.

| Command | What it does |
|---|---|
| `run` | Connect and serve requests in the foreground |
| `status` | Is it paired, registered, and running? |
| `diagnose` | Check TallyPrime on this PC and say what is wrong, in plain words |
| `livecheck` | Run every query against live Tally and verify the accounting adds up |
| `pair --id --secret` | Save credentials |
| `configure` | Change the server address or the credentials — see below |
| `install` | Pair and register the logon task (what the installer runs) |
| `uninstall [--purge]` | Remove from startup; `--purge` also unpairs |
| `capabilities` | Print the queries this build supports |

`status` and `diagnose` are the two worth knowing — they answer most support
calls without anyone reading a log.

---

## Changing the server address or the secret

Both change in real life: a backend moves, or the app reissues a secret because
the old one was lost. Neither needs a reinstall.

```powershell
tally-connector configure --backend-url wss://api.tallyflow.app/v1/connector
tally-connector configure --from-file C:\path\to\pairing.txt
```

Nothing is written until the result validates, so a mistyped URL is an error
message rather than a connector that starts and never dials home. If the logon
task is running it is restarted automatically, because a running connector holds
the old settings in memory.

**Lost the secret?** Do *not* add a second Tally PC. In the app, open the PC and
choose **Re-pair this computer** — it issues a fresh secret for the same
connector, keeping its companies and their synced history. Then:

```powershell
tally-connector configure --secret <new secret>
```

Pairing a *new* connector instead links the same books a second time, and the
company appears twice in the app with two separate histories.

Prefer `--from-file` over `--secret` where you can: on Windows any process can
read another process's command line.

---

## Uninstall

Use **Add or remove programs**, or the Start-menu shortcut. That removes the
executables, the startup task, the saved credential and the logs.

From a terminal, or for an install that did not come from the installer:

```powershell
tally-connector uninstall --purge
```

Without `--purge` it only removes the startup task and leaves the pairing in
place — that is the path an *upgrade* takes, so that a version bump is not a
re-pair for every shop.

---

## Where things live

| | |
|---|---|
| Executables | `%LOCALAPPDATA%\Programs\TallyFlow Connector` |
| Config + secret | `connector.json`, next to the executables |
| Logs | `%LOCALAPPDATA%\TallyFlow Connector\logs` |
| Startup task | Task Scheduler → `TallyFlow Connector` |

`connector.json` holds a credential that can read the company's books. Treat it
like a password: it is not needed for support, and a log file is.

---

## Troubleshooting

Start with `tally-connector status`, then `tally-connector diagnose`.

**"Your Tally PC is offline" in the app, but the PC is on.**
Check the log for repeated `connecting to ws://…` followed by
`session ended (OSError: [WinError 121] …)`. That is the connector failing to
reach the backend — almost always a `backend_url` pointing at an address that
no longer exists. Fix it with `configure --backend-url`. Never point a shop at a
DHCP address; use a hostname.

**Sessions keep dropping with `replaced by a newer session`.**
Two connectors are running with the same Connector ID and evicting each other,
so the link is down about half the time. Check for a leftover
`tally-connector-service.exe` alongside a manually started `run`. One machine,
one connector.

**"That company isn't open in TallyPrime."**
Exactly what it says — open the company in Tally. The connector refuses to read
a company that is not open, because Tally answers such a request with an *empty*
result and no error, which would otherwise reach the owner as a shop with no
sales. A voucher read in that state can also crash Tally outright.

**Blank figures with no error at all.**
Almost always the case above. Confirm with `diagnose`, which lists the companies
Tally currently has open.

**TallyPrime closed by itself.**
Look in `tallyerr.log` next to `tally.exe` for
`Software Exception c0000005 (Memory Access Violation)`. That is a fault inside
Tally, not in the connector — the connector detects it, stops retrying the
request that triggered it, and waits for Tally to be reopened. Send that file
and the matching `tallyN.dmp` with any report.

**Tally is open but nothing responds.**
A modal dialog in Tally blocks its whole HTTP gateway. Dismiss any open dialog.
Also confirm **F1 → Settings → Connectivity → Client/Server configuration** has
*TallyPrime acts as* set to **Both**, and the port matches (default `9000`).

---

## Development

```powershell
pip install -e apps/connector[dev]
python -m tally_connector.main diagnose
python -m tally_connector.main livecheck --company "Your Company"
pytest apps/connector
```

`livecheck` is the one that matters: it runs every query against a real
TallyPrime and asserts the results make accounting sense. Unit tests against
recorded fixtures cannot catch a wrong assumption about Tally's wire format —
the first live run of that check found four real bugs.
