# The connector's window — design record and handoff

**Status:** built. The toolkit is **Flutter**, the window is
`tally-connector-window.exe`, and the IPC stayed as loopback HTTP. Nothing in
this file is open any more; it is kept as the record of what was decided and
why, because every one of these choices has a failure attached to it that is
invisible from the code alone.

**Why this exists.** The connector used to show its status and pairing code by
serving a page on `127.0.0.1:9787` and opening the default browser at it. That
worked and was fully tested, but it presented as a browser tab rather than as
part of the product, so it was replaced with a **native Windows application**.
This file records what was preserved, what was thrown away, and what the choice
cost.

---

## 1. The shape: two processes, one installer

There are **two running things**, and that is not new — the installer has always
produced two executables:

| Executable | What it is | Lifetime |
|---|---|---|
| `tally-connector-service.exe` | The connector. Windowless. Registered as a Scheduled Task at logon (`TallyFlow Connector`). Holds the WebSocket to the backend and is the only thing that ever talks to TallyPrime. | Always, from logon |
| `tally-connector.exe` | The console CLI — `status`, `diagnose`, `pair`, `install`, `ui`, … | On demand |

The native window becomes a **third executable in the same install**. The
customer still downloads one file and runs one installer.

**The service must never depend on the window.** It starts at logon, before
anybody opens anything, and must keep serving Tally whether or not a window is
open. So the window is a *client* of the service — never its host, never its
parent process. Anything that makes the connector stop working when the window
is closed is wrong by construction.

```
TallyPrime  ◀──localhost:9000──  tally-connector-service.exe  ──WSS──▶  Backend
                                            ▲
                                            │  local IPC (see §5)
                                            │
                                     the native window
```

---

## 2. What the window is for

It answers the questions somebody standing at the shop's PC actually has, and
it is the screen that pairs the machine in the first place.

**It shows:**

- the pairing code, as a QR, when this machine is not paired
- whether the connector is connected to TallyFlow, and for how long
- whether TallyPrime is answering, and on which port
- which companies are open in Tally right now
- which companies this PC feeds, and when each last synced
- who in the business can see them, and whether each person actually has access
- the Tally port, the server address, and where the logs are

**It can do exactly four things:**

| Action | Effect |
|---|---|
| `restart` | Tear the session down and rebuild it from freshly loaded settings |
| `refresh` | Ask the backend to re-send the roster |
| `port` | Save a new Tally port, then restart |
| `new-code` | Abandon the current pairing claim and draw a fresh one |

### What it must never be able to do

**No stop. No disconnect. No unlink. No unpair. No remove.**

This is a product rule, not an oversight. Those decisions belong to whoever
holds the account on their phone, not to whoever happens to be standing at the
till. "Re-pair this computer" in the mobile app is what cuts a PC off; the PC
then falls back to showing a code by itself.

A "Stop" button on this window would let anyone with physical access to a shop
counter silently take that shop's dashboard offline, with nothing in the app to
say why. Do not add one, however natural it looks beside "Restart".

**No figures.** No balances, no sales, nothing from the books. The account data
the window shows arrives as a `roster` frame — names, roles, sync times only.
A shop PC that could be asked for a balance over a local socket would be a
second read path into the books, outside every check in `deps.get_company`. See
`apps/backend/src/tally_backend/services/roster.py`, which is where that line is
held.

---

## 3. Where the code lives

```
apps/connector/src/tally_connector/
  ui/
    state.py    the state snapshot and the action bridge — unchanged
    server.py   reduced to the window's API: /api/state, /api/qr, four actions
    qr.py       the module grid; the SVG writer went with the page
    page.py     deleted — it was the HTML
  runner.py     the supervisor: pair → serve → restart → pair
  pairing.py    the claim/collect client
  session.py    the WebSocket session

apps/mobile/                      the phone app's package, with a second target
  lib/connector_window.dart       the window's entrypoint
  lib/src/connector/
    state.dart                    the state contract, mirrored from state.py
    client.dart                   the loopback HTTP client
    code_image.dart               the pairing code, painted
    window.dart                   the window itself
  windows/                        the Windows runner, scaffolded for this
  test/connector_window_test.dart both panels, laid out
```

**The window is a second `--target` of the phone app, not a package of its
own.** The visual language is one file — `lib/src/app/theme.dart` — that
thirty-odd screens already import, and a shop owner sees the window and the app
in the same afternoon; splitting the theme out to keep them in step would have
been a sweep across every one of those imports to gain what `--target` gives for
nothing. The cost is real and worth knowing: the Windows build compiles every
plugin in `pubspec.yaml` that has a Windows implementation, so the output
carries about 5 MB of DLLs the window never calls, and a new mobile dependency
can break the connector's build. See §6.

**`ui/state.py` is the important one and is toolkit-agnostic.** `UiBridge` is
already the seam:

- it holds a `UiState` behind a lock and hands out an immutable snapshot
- it satisfies the `SessionObserver` protocol in `session.py`, which is how the
  session reports connection and Tally state without importing anything about a
  user interface
- `invoke(name, payload)` posts a registered coroutine onto the connector's
  event loop from another thread and waits with a timeout

A native window plugs into exactly the same object. Nothing below `state.py`
knows a user interface exists, and `ui_enabled: false` must keep working —
every button has a CLI equivalent.

### The state contract

`UiBridge.snapshot()` returns this, and it is what the window renders:

```json
{
  "version": "0.2.7",
  "uptime_seconds": 0,
  "paired": false,
  "connector_id": "",
  "connector_name": "",
  "pairing":  { "waiting": false, "payload": "", "seconds_left": 0, "detail": "" },
  "backend":  { "url": "", "connected": false, "detail": "",
                "session_id": "", "connected_seconds": null },
  "tally":    { "host": "", "port": 0, "online": null, "companies_open": [] },
  "account":  { "organisation": "", "status": "",
                "companies": [], "users": [], "as_of": null },
  "log_dir": ""
}
```

Two fields carry meaning that is easy to flatten and must not be:

- **`tally.online` is `null` before the first probe**, not `false`. "We have not
  looked yet" and "TallyPrime is not answering" are different things to tell
  somebody, and rendering the second one wrongly sends them to restart software
  that is working perfectly.
- **`pairing.seconds_left` is computed from an absolute expiry** on every read,
  so it counts down. A window that caches it will promise fifteen minutes for
  fifteen minutes.

`account.companies[]` entries are `{id, name, tally_name, is_active,
last_synced_at}`; `account.users[]` are `{name, email, role, has_access}`.
`last_synced_at` may be `null` — render "not synced yet", never "synced never".

---

## 4. The pairing screen

This is the part with the most product value, and the reason the window exists
at all rather than a CLI.

An unpaired connector registers a claim with the backend, gets back a `code`
and a `token`, and draws the code as a QR. The owner scans it in the phone app;
the credentials then travel from the backend to this machine over TLS and are
collected once, using the token — the half that was never on screen.

`PendingClaim.payload()` in `pairing.py` produces the string to encode:

```json
{"v":1,"c":"<22-char code>","h":"api-tallyflow.jsrprimesolution.com"}
```

Whatever draws the QR must:

- **force a full QR, never a Micro QR.** Left to choose, encoders drop to Micro
  QR for short payloads, and many phone cameras do not decode it. The failure is
  a code that looks perfect and simply does not scan. `qr.py` passes
  `micro=False` for exactly this reason.
- **draw black on an explicitly white ground**, never inherited colour and never
  a transparent quiet zone. A code that took the window's theme colours is
  invisible on a machine whose owner runs a dark desktop.
- **keep the 4-module quiet zone.** Without it many scanners will not read at
  all.
- **redraw only when the payload changes.** It is being scanned; blanking it on
  a timer fights the camera.

`qr.py` returns an SVG today. A native toolkit will likely want the module
matrix instead — `segno.make(payload, error="m", micro=False).matrix` is a list
of rows of 0/1 and is the thing to draw from.

---

## 5. The seam: how the window talks to the service

**Decided: loopback HTTP, kept.** `ThreadingHTTPServer` bound to
`127.0.0.1:9787`, reduced to three routes — `GET /api/state`, `GET /api/qr`,
and `POST /api/{restart,refresh,port,new-code}`. The window is handed the port
on its command line by `tally-connector ui`, so a machine that moved it keeps a
working shortcut.

A **named pipe** (`\\.\pipe\TallyFlowConnector`) was the alternative and is the
more natural IPC for two processes on one Windows machine: it removes the
listening port entirely and can be ACL'd to the installing user. It was not
taken because Dart has no pipe support in its standard library and would need an
FFI shim — code that must be right on the first machine it ships to, in order to
draw a status screen, against a socket both sides already speak. The three
defences below are what carry the property the pipe would have given for free,
and they are cheap enough that keeping them is not a compromise.

The defences exist for real reasons, and each is cheap enough that losing one
silently is the only real risk:

- bound to loopback and **not configurable** — the product opens no inbound port
  on a customer's machine, ever
- the `Host` header is checked, because a hostname that resolves to 127.0.0.1
  is how DNS rebinding turns a loopback server into a remote one
- every API call must carry `X-TallyFlow-Local`, which a cross-origin page
  cannot set without a CORS preflight this server refuses
- `allow_reuse_address = False`, because on Windows `SO_REUSEADDR` lets a second
  process take a port that is already being listened on

The request/response shape is what `state.py` already defined: one "give me the
state" call and four named actions. `/api/qr` was added — the connector encodes
the symbol with `segno` and sends the module grid as rows of `0` and `1`, and
the window paints it. The encoding stayed on this side deliberately: the two
decisions that make a code scannable off a monitor (§4) are load-bearing and
were paid for once already, and keeping them beside the encoder means a second
front end cannot quietly get them wrong.

---

## 6. The toolkit: Flutter

Current connector download: **38.1 MB** (v0.2.7). That is the budget to argue
against. These machines pull it over ADSL.

| | Look | Download | Language | Blocker |
|---|---|---|---|---|
| **Flutter desktop** | Identical to the phone app — same theme, tile palette, Caveat mark | +18–25 MB | Flutter, already in the repo | Windows SDK missing on the build machine ⚠️ |
| **C# / WPF (.NET 8)** | Most Windows-native; best tray and toast story | +20–40 MB self-contained | **A third language to maintain** | .NET SDK on the build machine |
| **pywebview** | Real window, own taskbar icon, no browser chrome — HTML inside | +1 MB | Python, same exe | WebView2 runtime (on ~all Win10/11) |

Ruled out, with reasons:

- **tkinter** — free, already half-wired (it is in `connector.spec`'s exclude
  list). Looks like Windows 98. For a product positioned as "a modern SaaS
  dashboard, not Remote Desktop for Tally", it degrades the product more than
  the browser tab it would replace.
- **Qt / PySide6** — genuinely native widgets, but +60–100 MB on a 38 MB
  download. That is the constraint the whole codebase is built around. (PyQt6
  additionally has GPL/commercial licensing to resolve; PySide6 is LGPL.)

**Chosen: Flutter.** It is the only option where the window shares code with the
phone app, so it looks like TallyFlow rather than like a utility bolted on —
`app/theme.dart`, the tile palette, the stacked Caveat mark. The team already
writes Flutter daily, so it adds no language to maintain forever. React Native
was raised and turned down: it has the same Windows SDK requirement, shares no
code with the phone app, is the same size, and would put Node and Metro into a
build that is otherwise pure Python and PyInstaller.

The window opens **light**, not the app's dim default: it is read at a counter
under shop lighting rather than on a phone in the evening, and the pairing code
it exists to show has to sit on white in any case.

### What the build machine needs

Two things, both one-time and neither reaching a customer.

**A Windows SDK.** `flutter doctor` must show a clean Visual Studio line. On
this machine it landed at `D:\Windows Kits\10` (10.0.26100).

**A Visual Studio instance that has ATL.** This one costs an afternoon if it is
not written down. CMake picks a VS instance by itself, and on a machine with
more than one it can pick a Build Tools install without ATL — at which point
`flutter_secure_storage_windows`, a dependency of the *phone app* that the
window never calls, fails on `atlstr.h` with nothing to suggest that the fix is
"use the other install". `run.py build_window` now asks `vswhere` for an
instance that has both the C++ tools and `Microsoft.VisualStudio.Component.VC.ATL`
and hands it to CMake through `VS170COMNTOOLS`, which CMake consults before
choosing for itself. `CMAKE_GENERATOR_INSTANCE` does **not** work here: CMake
ignores that environment variable when the generator is given with `-G`, which
Flutter always does.

### What it costs

The window's build output is about **32 MB**, of which 18 MB is
`flutter_windows.dll` and roughly 5 MB is plugin DLLs the window never calls —
`pdfium.dll` and friends, pulled in because the Windows build compiles every
plugin in the phone app's `pubspec.yaml` that has a Windows implementation.
They cannot simply be deleted from the output: the plugin registrant compiled
into the executable loads them at startup, so a missing one is a window that
fails to start with nothing written anywhere to say why. That is the price of
sharing the package with the phone app, and the day it stops being worth paying,
the fix is a package of its own plus a shared theme — a sweep across thirty-odd
imports.

---

## 7. Non-negotiables carried from CLAUDE.md

- **Read-only.** Nothing here may grow a write path to Tally.
- **No inbound port on a customer's machine.** Loopback or named pipe only.
- **The service is independent of the window.** Closing the window must not stop
  the connector.
- **`ui_enabled: false` must keep working.** Every button has a CLI equivalent;
  a machine locked down by an IT department must be able to refuse the extra
  surface.
- **Fail soft.** A window that cannot reach the service says so; it never takes
  the service down with it, and the service never waits on it.
- **Comments explain *why*, not *what*.** Match the density of reasoning in the
  existing files.

---

## 8. Done, and how it was checked

1. ✅ A native window, launched from the Start menu and from `tally-connector
   ui`, showing everything in §2 and offering exactly those four actions.
2. ✅ The pairing QR scannable by the phone app from a normal viewing distance.
3. ✅ The connector service unaffected when the window is closed, killed, or
   never opened — the window only ever reads and posts over loopback.
4. ✅ `ui_enabled: false` still starts the connector cleanly with no window;
   `tally-connector ui` says so and exits.
5. ✅ One installer, one download, one Start-menu group.
6. ✅ `python run.py check` green.
7. ✅ `page.py` deleted; `server.py` reduced to the window's API.

### How to verify end to end

`scratchpad/e2e_pair.py` runs the real backend and the real connector in one
process and drives the whole loop — unpaired → code shown → app scans → paired →
online → roster → re-pair → code again → rescanned → same connector row. It
asserts against the local API, which did not change shape, so it remains the
fastest proof the wiring is right.

For the window on its own, `scratchpad/fake_connector.py` stands up the real
`LocalUiServer` against a made-up `UiBridge` and switches from "waiting to be
scanned" to "paired and working" after a few seconds, so both screens can be
looked at in one run without a backend.

---

## 9. History worth not rediscovering

Three bugs were found and fixed while building the current version. All three
are properties of the *service*, not the page, so they survive the rewrite — do
not undo them:

1. **`repair_requested_at` on the connector row.** Re-pairing replaces the
   secret, so the PC's next handshake is a failed signature — indistinguishable
   from a genuine authentication failure. Only a deliberate admin action sets
   this column, and only it makes the connector show a pairing code instead of
   exiting. Without it a re-paired PC sits dead. With it answered on *any*
   failure, one bad deploy puts the whole fleet on pairing screens.
2. **`allow_reuse_address = False`.** On Windows, `SO_REUSEADDR` lets a second
   process take a port that is already being listened on. Still live: the IPC
   stayed a socket.
3. **`micro=False` on the QR.** See §4.

Three more were paid for while building the window:

4. **A `Row` with `CrossAxisAlignment.stretch` inside a scroll view asks its
   children to be infinitely tall.** In a debug build that is a loud assertion;
   in the release build a customer runs it is a screen with every line of text
   drawn on top of every other, and there is nothing in any log to explain it.
   It got past `flutter analyze` and past a release build without a word, and
   was caught by looking at a screenshot. `test/connector_window_test.dart`
   pumps both panels in debug precisely so that class of mistake is a test
   failure again — check that it still fails if you remove the
   `IntrinsicHeight` around those two cards.
5. **The QR's modules are snapped to whole *device* pixels, not logical ones.**
   Rounding in logical pixels looks right until the first machine at 125%
   scaling, which is the Windows default on a laptop; then every second module
   edge falls mid-pixel and the antialiasing between neighbours is what decides
   whether the code reads from a step back.
6. **CMake's VS instance selection.** See §6 — ATL, `vswhere`,
   `VS170COMNTOOLS`, and why `CMAKE_GENERATOR_INSTANCE` does not work.
