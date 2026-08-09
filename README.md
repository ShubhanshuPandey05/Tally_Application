# TallyFlow

AI-powered mobile dashboard for TallyPrime. **Read-only** — see `CLAUDE.md` for
the product vision and the rules this codebase is held to.

## Layout

```
packages/
  tally_core/      Shared layer               (domain models, XML codec, query registry, wire protocol)
apps/
  connector/       Windows connector          (the only thing that talks to Tally)
  backend/         FastAPI API server         (auth, connector hub, dashboards, reports)
  mobile/          Flutter app                (dashboard, reports, pairing wizard)
  website/         React marketing site       (product story, downloads, pricing)
```

`tally_core.protocol` is the backend/connector wire contract. It lives in the
shared package so that neither side imports the other -- a server deployment
must not pull in a Windows desktop application.

Data flows one way in, one way out:

```
Flutter  ->  Backend API  -- WebSocket -->  Connector  -->  TallyPrime (localhost:9000)
```

The connector dials **out** to the backend. Tally is never exposed to the
internet and no inbound port is ever opened on the customer's machine.

## Stack

Python 3.11+ (backend and connector), Flutter 3.24 (mobile). Chosen 2026-07-22.

## Getting started

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e packages\tally_core[dev] -e apps\connector[dev] -e apps\backend[dev]
```

`run.py` wraps the day-to-day commands. It is standard library only and finds
the virtualenv itself, so the same file works on a laptop and on a deploy box:

```powershell
python run.py            # list commands
python run.py init       # generate apps\backend\.env with real secrets
python run.py dev        # backend on 127.0.0.1:8000, auto-reload
python run.py app        # Flutter app pointed at it
python run.py check      # pytest + ruff + flutter analyze + flutter test
```

Deployment side:

```powershell
python run.py preflight  # refuse-to-deploy checks against the real environment
python run.py migrate    # alembic upgrade head
python run.py image      # docker build (context is the repo root)
python run.py connector  # the Windows connector installer (Windows only)
python run.py release https://api.tallyflow.in   # app bundle + web, https only
```

`init` generates the secrets rather than leaving them blank because the blank
case fails quietly: with no fixed `TALLYFLOW_JWT_SECRET` the backend invents one
per process, so every restart signs you out *and* makes already-paired connector
secrets undecryptable — which presents as "the connector stopped authenticating".

The underlying commands are all plain, if you would rather run them directly:

```powershell
.\.venv\Scripts\python.exe -m pytest packages\tally_core apps\connector apps\backend -q
.\.venv\Scripts\python.exe -m ruff check packages apps
```

## Connector

```powershell
# Is TallyPrime reachable, and which companies are open?
.\.venv\Scripts\python.exe -m tally_connector.main diagnose

# What reads does this build support?
.\.venv\Scripts\python.exe -m tally_connector.main capabilities

# Save credentials issued by the app's pairing wizard, then serve.
.\.venv\Scripts\python.exe -m tally_connector.main pair --id <ID> --secret <SECRET>
.\.venv\Scripts\python.exe -m tally_connector.main run
```

`diagnose` works before pairing and while the backend is down — it is the first
thing to run whenever a customer reports missing data. `livecheck` goes further:
it runs every query against the live company, checks the accounting adds up, and
prints how long each read took against the budget the backend allows one read.

### One request at a time

Tally's HTTP gateway serves a single caller, and a second POST arriving mid-export
does not queue — it can wedge the gateway until Tally is restarted. Everything the
connector sends to Tally therefore goes through `pipeline.py`: one bounded FIFO
queue, one worker, no exceptions.

The queue exists for what happens to work *behind* a slow read, which is where a
missing sales figure actually comes from:

- **A ticket's deadline is checked immediately before dispatch.** The dashboard
  fires five reads; the day-book export takes minutes; the phone gives up. Those
  four expired reads are dropped in the queue instead of being handed to Tally
  one after another, long after anyone stopped waiting.
- **A caller that walked away spends nothing.** A cancelled waiter resolves its
  ticket, and the worker skips it.
- **A failure buys a pause.** After a timeout or a refused connection the
  pipeline holds off for `tally_cooldown_seconds` before touching Tally again,
  and a heavy read that timed out is not retried. Hammering a wedged gateway is
  how one slow report becomes a connector that never recovers.
- **A full queue is refused immediately**, with a retryable error, so the caller
  falls back to its snapshot instead of waiting out a timeout.

Liveness rides on the same seam. The heartbeat's `is_alive()` used to take the
client lock and so queued behind whatever was running — and because the ping was
answered inline in the socket read loop, a slow `vouchers.list` stopped the
connector reading *any* frame. The backend saw no heartbeat for 90 seconds,
dropped a healthy connection, and the voucher result was discarded on arrival;
every sweep hit the same wall, so the dashboard's sales section never filled in.
Pings are now answered on their own task, from what the pipeline has just
observed, and only probe Tally when the pipeline is idle.

`max_concurrent_jobs` now bounds only how many jobs are *accepted* at once. What
reaches Tally is always one.

### The Windows installer

What a customer actually gets. `python run.py connector` builds it:

```
apps\connector\dist\installer\TallyFlowConnector-Setup-<version>.exe
```

It asks for the Connector ID and Secret from the app's pairing screen, writes
`connector.json`, registers a startup task, and starts the connector. Silent
deployment across several shop PCs — each machine still needs its own pairing,
because one connector row per PC is what lets the app say *which* shop is
offline:

```powershell
TallyFlowConnector-Setup-0.1.0.exe /VERYSILENT /ID=<id> /SECRET=<secret>
```

Three decisions worth knowing about:

**A logon Scheduled Task, not a Windows Service.** TallyPrime is a desktop
application — it only exists while someone is logged in, so a service starting
at boot would spend every night reconnecting to a Tally that is not there, and
would need an admin install to gain nothing. A logon task also needs no
elevation, which is why the whole install is per-user with no UAC prompt.
`install.py` overrides four Task Scheduler defaults that would each break the
connector days later: the 72-hour execution limit, both battery settings, and
stop-on-idle.

**Two executables.** `tally-connector.exe` is the console CLI a person runs;
`tally-connector-service.exe` is windowless, because a black console window
parked on a shop counter's desktop gets closed by somebody. Console subsystem
is a link-time property, so they cannot be one file. The windowless build
passes its own `fallback_log_dir` rather than sniffing `sys.stdout`, since a
GUI-subsystem exe started from an open terminal inherits that terminal's
handles — the process that most needs a log file is the one that looks least
like it.

**The secret never touches a command line.** Any process on the machine can
read another's arguments, so the installer writes the pairing details to a
file in `{tmp}` and passes a path.

Uninstall removes the task, the process, `connector.json` (it holds the pairing
secret) and the logs.

### Enabling Tally's HTTP gateway

The connector needs TallyPrime listening on `localhost:9000`:

1. Open TallyPrime.
2. `F1` → Settings → Connectivity → Client/Server configuration.
3. Set *TallyPrime acts as* to **Both**, port **9000**.
4. Keep a company open. Close any modal dialog — an open dialog blocks requests.

## Backend

```powershell
copy apps\backend\.env.example apps\backend\.env   # then fill in the secrets
.\.venv\Scripts\python.exe -m tally_backend.main   # http://127.0.0.1:8000/docs
```

Schema changes go through Alembic:

```powershell
cd apps\backend
..\..\.venv\Scripts\python.exe -m alembic upgrade head
..\..\.venv\Scripts\python.exe -m alembic check       # fails if models drifted
```

### Reads are served from snapshots, not proxied to Tally

The single most important decision in the backend. A naive design forwards every
dashboard open straight through to TallyPrime, and that cannot work: Tally serves
one request at a time, takes seconds per export, sits on a desktop behind home
broadband, and is switched off at night. Four staff refreshing a dashboard would
queue four multi-second exports against the PC the till runs on.

So every dataset is stored as a `Snapshot` and refreshed out of band. What falls
out of that:

- The dashboard opens in milliseconds and still works when Tally is closed.
- Load on a customer's PC scales with the number of **companies**, not users.
  Ten staff cost the same as one — which is what makes a thousand users viable.
- Pull-to-refresh forces a live read, throttled per company so that holding the
  gesture cannot become a one-tap denial of service against the shop's own Tally.

The price is that data can be old, so **every response carries its own
freshness** (`refreshed_at`, `age_seconds`, `is_stale`, `connector_online`) and
the app is expected to show it. A number without an "as of" is a number an owner
might act on believing it is current.

`connector_online` is always *observed*, never assumed — including on the paths
that return a snapshot without contacting anyone. That distinction was a real
bug: a throttled refresh reported the connector as online without checking, which
would have told an owner their figures were live while their PC was off.

### History is read in slices, then only what changed

Snapshots make *today* cheap. They do nothing for the first read of a company
that has four years of books — and that read is what kills a shop's Tally. A
single `vouchers.list` over four years does not come back slowly; it exhausts the
gateway, and TallyPrime is left alive but answering nothing until somebody
dismisses a dialog on the PC. It is not a timeout to tune, it is a request that
must never be sent.

`services/sync.py` and `services/voucher_store.py` handle it in two phases.

**Backfill, newest slice first.** The books are cut into date windows
(`TALLYFLOW_SYNC_CHUNK_MONTHS`, default 6), read one at a time with a pause
between them, and merged into `voucher_records` — one row per voucher, keyed on
Tally's GUID so an edit replaces rather than duplicates. Newest first is a
product decision: after the first slice the dashboard already answers "what did I
sell this week?", and the older years fill in behind it while the app is in use.
Each slice is its own job with its own deadline, so a slow one is retried or
abandoned alone, and a run that stops half way keeps what it read and resumes at
the slice it reached.

**After that, only the changes.** Tally stamps every voucher with an `AlterID`
that increases on each edit, and a company reports the highest one it has issued
(`company.markers`, the cheapest read in the product). Comparing that one number
against the last sync answers "is there anything new?" without exporting
anything; when the answer is yes, `$AlterID > n` returns just those vouchers. A
shop that sold twenty things today transfers twenty vouchers, not four years of
them — and a quiet company costs a few hundred bytes per sweep instead of a
full re-export of the dashboard's window every fifteen minutes.

Two gaps in that story, both handled rather than ignored:

- **Deletion.** A delta says what was created or edited and nothing about what
  was removed. So a recent window (`TALLYFLOW_SYNC_RECONCILE_DAYS`) is
  periodically re-read *in full* and treated as authoritative — the only path
  that ever deletes a stored voucher. Without it, a voucher deleted in Tally
  would stay on the dashboard forever.
- **Tally builds that do not report change ids.** The markers query comes back
  empty rather than failing (unknown native methods are dropped silently), and
  the sync degrades to re-reading a bounded recent window. Slower, still correct,
  and never widened back into a full-history export.

The cursor is read *before* the backfill, not after: a voucher edited during a
multi-minute read has a higher `AlterID` and is picked up by the next delta.
Advancing it afterwards would step past that edit and lose it permanently. For
the same reason a delta advances to the marker it read, not to the highest id
that came back.

The app is told all of this. `GET /v1/companies/{id}/sync` reports slices done
out of slices planned, the months currently being read, transactions ingested and
a projected ETA — enough for a determinate progress bar, because a spinner held
for four minutes is indistinguishable from a hang. `eta_seconds` is `null` until
the first slice finishes; the app renders that as "estimating", never as zero.

`tally-connector livecheck` verifies the whole mechanism against a real Tally,
including the failure that is otherwise silent: if `$AlterID` is accepted but
ignored, every "fetch only what changed" read quietly returns the entire history
instead, and nothing else in the system would notice.

### Scaling past one instance

A connector holds one WebSocket to one backend instance, but a phone's request
lands wherever the load balancer sends it. Without routing, "show me today's
sales" fails whenever those two disagree — with N instances, (N-1)/N of the time.

`hub/bus.py` solves this with a directory of `connector -> instance` plus a
pub/sub inbox per instance:

- **No Redis configured** → `LocalBus`, single process. Correct, and genuinely
  enough for a few hundred connectors on one box.
- **`TALLYFLOW_REDIS_URL` set** → `RedisBus`. Any instance can route a job to the
  one holding the socket. No application code changes.

Directory entries carry a TTL and are refreshed while the socket lives, so an
instance killed uncleanly stops attracting traffic on its own instead of
blackholing requests until someone notices.

Run **one worker per container**. Forking workers would give each fork a
different set of sockets with no way to route between them; scale by adding
containers.

### Guarding the customer's PC

Several mechanisms, all pointed at the same constraint — Tally is single-threaded
and belongs to someone running a shop:

| Mechanism | Where | Effect |
|---|---|---|
| Snapshot reads | `services/reads.py` | Users do not multiply Tally load |
| Chunked backfill | `services/sync.py` | History is never one enormous export |
| Change-id deltas | `services/sync.py` | A quiet company costs almost nothing |
| Request coalescing | `hub/hub.py` | Identical concurrent reads share one round trip |
| Per-connector job cap | `hub/link.py` | Never more than 2 exports in flight |
| Refresh throttle | `services/reads.py` | Forced refreshes have a floor |
| Shrinking deadlines | `hub/link.py` | A queued job does not get a fresh full timeout |
| Batched warming | `services/refresher.py` | A restart cannot stampede the fleet |

### Security

- Passwords: Argon2id. Refresh tokens: opaque, stored hashed, **single-use**.
  Replaying a spent token revokes the entire token family, on the assumption
  that replay means the token leaked.
- Connector pairing secrets are **encrypted at rest, not hashed**. The connector
  authenticates by HMAC-ing with the secret, and verifying an HMAC requires the
  key that produced it — a one-way hash cannot do that job. The encryption key
  lives in the environment, so a database dump alone yields no usable credential.
  See `core/crypto.py`.
- Tenant isolation is enforced in one place (`api/deps.py:get_company`) and fails
  closed: another organisation's company returns **404, not 403**, so the API
  cannot be used to probe for valid ids.
- Roles are re-read from the database on every request rather than trusted from
  the access token, so revoking an accountant takes effect immediately instead of
  after the token's 15-minute life.
- Reads are audited, not just writes. In a read-only accounting product the
  sensitive act *is* the read.

## Mobile app

```powershell
cd apps\mobile
flutter run --dart-define=TALLYFLOW_API_URL=http://10.0.2.2:8000   # Android emulator
flutter run -d chrome --dart-define=TALLYFLOW_API_URL=http://127.0.0.1:8000
flutter test
flutter analyze
```

`10.0.2.2` is the host machine as seen from the Android emulator; `localhost`
there is the emulator itself. That single fact accounts for most "the app can't
reach my backend" reports. The base URL is a `--dart-define` so a release build
cannot ship pointing at a developer's laptop.

Feature-first under `lib/src/features/<feature>/{data,domain,application,presentation}`,
with cross-feature pieces in `lib/src/core`. Riverpod for state, `go_router` for
navigation, Dio for HTTP.

### The wire contract is generated, not hand-written

`apps/backend/tests/test_wire_contract.py` drives the real ASGI app and writes
every response the app decodes into `apps/mobile/test/fixtures/*.json`. The
Flutter tests parse those files.

This exists because both halves were independently well tested and would both
have stayed green through a field rename — the app's tests would keep passing
against the app's own idea of the shape, and the only symptom would be a blank
tile on a shopkeeper's phone. After an intentional API change:

```powershell
$env:UPDATE_WIRE_FIXTURES=1
.\.venv\Scripts\python.exe -m pytest apps\backend\tests\test_wire_contract.py
```

`test/live_backend_test.dart` goes further and runs the app's real repositories
against a running server. It skips itself unless given a URL:

```powershell
.\.venv\Scripts\python.exe -m uvicorn tally_backend.main:create_app --factory --port 8099
cd apps\mobile
flutter test test/live_backend_test.dart --dart-define=TALLYFLOW_LIVE_URL=http://127.0.0.1:8099
```

That run is what caught a boot-time crash: `TALLYFLOW_SECRET_KEYS=key1,key2`, the
form `.env.example` documents, was JSON-decoded by pydantic-settings before any
validator ran. Every unit test built `Settings(...)` in Python and never touched
the environment path, so 237 passing tests said nothing about whether the server
could start as documented. `apps/backend/tests/test_config.py` now covers it.

### Freshness is a UI obligation, not a footnote

The backend serves reads from snapshots, so every response carries `refreshed_at`
/ `is_stale` / `connector_online`, and every repository here returns `Fresh<T>` —
data and freshness together. A screen physically cannot render figures without
having the "as of" in hand. `FreshnessBanner` distinguishes two states with two
different fixes:

- **stale but online** — "these figures are not current", pull to refresh.
- **offline** — "your Tally PC is offline", showing the last figures we read.

Telling a shopkeeper to check their internet when the real problem is that
TallyPrime is closed wastes their afternoon, so `Connector.health` keeps
`online` and `tallyOnline` separate all the way to the screen.

### What the app refuses to render

Zeroes when nothing could be read. "You sold nothing today" and "we could not
reach your Tally" are completely different claims, and a grid of ₹0.00 tiles
asserts the first. No data means an empty state; a failed section means that
section says why while the rest of the dashboard still works.

Likewise `change_pct: null` renders as `--`, never as 0% or 100% — percent
change against a zero baseline is an unanswerable question, not a movement.

### Money and the interceptor

Amounts are parsed into `Decimal`, never `double`; the API sends them as strings
for the same reason. Formatting is Indian-grouped (`₹1,23,45,678.90`) with
lakh/crore compaction on tiles, because that is the shape the users read.

`AuthInterceptor` serialises token refreshes through a single in-flight future.
That is a correctness requirement, not an optimisation: the backend rotates
refresh tokens and revokes the whole family on replay, so two parallel refreshes
would present the same token twice and sign the user out of every device for the
crime of opening two screens at once.

## Marketing site

```powershell
cd apps\website
npm install
npm run dev     # http://localhost:5173
npm run build   # -> apps\website\dist
```

React + Vite. It is where customers get the connector installer and the app, so
the download links in `src/data/downloads.js` must match what `run.py connector`
and `run.py release` actually produce. Pricing in `src/data/pricing.js` is
**placeholder data** pending real plans. See `apps/website/README.md`.

## Architecture notes

### The query registry is the backend/connector contract

The backend never sends XML. It sends `{"query": "ledgers.list", "params": {…}}`;
the connector resolves the name in `tally_core.tally.query`, builds the envelope,
calls Tally, and returns typed domain objects. Consequences worth knowing:

- The connector holds no product logic, so a dashboard change ships without
  touching the customer's machine.
- A broken TDL query is fixed by shipping a connector update, not an app update.
- Connectors advertise their query manifest at handshake, so the backend can
  detect version skew instead of failing mysteriously.
- Write support (Phase 5) is a `TallyMutation` class alongside `TallyQuery`,
  using the same registry. No redesign.

### Read-only is enforced, not just intended

`tally_core.tally.envelope` can only emit `TALLYREQUEST=Export`. A test asserts
that every registered query produces an envelope containing no `Import` and no
`ISMODIFY="Yes"`, so a write path cannot be added by accident.

### Tally's XML is not well-formed

Real responses contain raw C0 control bytes, unescaped `&` in customer names,
and no encoding declaration despite being cp1252. Everything is sanitised in
`tally_core.tally.codec` before parsing. Errors arrive in-band as `<LINEERROR>`
with HTTP 200, so status codes alone are not a success signal.

This is not theoretical: the captured day-book response contains literal `&#4;`
character references, which make `xml.etree` reject the entire document.

Other live-verified quirks:

- Master names live in the `NAME` **attribute**, not a child element.
- Every collection starts with a nameless placeholder element; mappers skip it.
- Invoices return their lines under **both** `ALLLEDGERENTRIES` and
  `LEDGERENTRIES`. Read the first wrapper that yields entries — reading both
  double-counts every invoice.
- `PERSISTEDVIEW` is a UI view name ("Accounting Voucher View"), *not* an
  accounting class. Classify on `PARENT`, falling back to the type name.
- Unknown `NATIVEMETHOD` names are silently ignored — no error, just a missing
  field. Fields not backed by storage (contact details, HSN) need `FETCH`.

### A bad request can wedge Tally

Requesting a non-existent collection type (`Ledger Bills`, `BillAllocations`)
made TallyPrime stop answering HTTP entirely while its process stayed alive —
almost certainly an error dialog opened in its UI, and an open dialog blocks all
request handling until a human dismisses it. The connector survives this
correctly (timeouts are retryable and it serves stale cache), but it is a strong
argument for never sending speculative TDL to a customer's machine.

### Money is never a float, and Tally's signs are inverted

`Money` stores an unsigned `Decimal` plus an explicit `Side` (debit/credit).

**In Tally's XML a negative number is a DEBIT and a positive number is a
CREDIT** — the opposite of the usual debit-positive convention. Verified against
a live instance across a full chart of accounts: Cash `-344220` (an asset, Dr),
Capital `+700000` (Cr), Sundry Creditors `+212600` (Cr), Sales `+114000` (Cr),
Purchases `-197000` (Dr). Get this backwards and every asset renders as an
overdraft.

On voucher lines `ISDEEMEDPOSITIVE` is authoritative and overrides the sign;
disagreeing with it yields registers that do not balance.

A rate (`"80.00/KG"`) is a unit price with no Dr/Cr, so the stock mapper
normalises it to a magnitude.

### Tally is single-threaded

Its gateway serves one request at a time and blocks its own UI while exporting.
So: `TallyClient` serialises all calls behind a lock, the executor collapses
duplicate concurrent jobs into one round trip, and results are cached. Three
people opening the app at once must not freeze the shop's Tally.

### Offline is a first-class path

When Tally is unreachable, the executor serves the last good response from cache
and flags it `from_cache`. The app is expected to show a "last updated" stamp.
Stale data is only served for *retryable* errors — a malformed request will fail
identically next time, and hiding that behind old data would mask a real bug.

## Status

| Component | State |
|---|---|
| `tally_core` — domain models, codec, 7 queries, transport | Done, 108 tests |
| `connector` — protocol, cache, executor, session, CLI | Done, 90 tests |
| Windows exes via PyInstaller | Both build and run (18 MB each) |
| Windows installer | Install / upgrade-over-running / uninstall verified end to end |
| Verified against a live TallyPrime | **All 7 queries, 20/20 checks, 2026-07-23** |
| `backend` — auth, hub, snapshots, dashboard, reports | Done, 125 tests |
| Backend ↔ connector, real sockets end to end | Verified, 7 integration tests |
| `mobile` — auth, pairing, dashboard, 6 reports | Done, 63 tests |
| Backend ↔ app wire shapes | Pinned by generated fixtures, 12 tests |
| App against a running backend | Verified, 7 live tests |
| Android debug APK, web release bundle | Both build |

323 Python tests plus 63 Dart tests. Run them together — `pytest packages`
on its own misses the `asyncio_mode = "auto"` setting, which lives in
`apps/backend/pyproject.toml`, and ten `tally_core` async tests error out.

Build the executables and the installer (needs Inno Setup 6 —
`winget install JRSoftware.InnoSetup`):

```powershell
python run.py connector
.\apps\connector\dist\tally-connector.exe diagnose
```

Both are unsigned. SmartScreen will warn on first download until the installer
is code-signed; sign it before sending a link to a customer.

Queries implemented: `companies.list`, `groups.list`, `ledgers.list`,
`voucher_types.list`, `stock_items.list`, `vouchers.list`, `outstanding.bills`.

### Live verification

```powershell
.\.venv\Scripts\python.exe -m tally_connector.main livecheck --company "<name>"
```

Runs every query against the real TallyPrime and asserts the results make
accounting sense: balances fall on their natural side, every voucher's debits
equal its credits, and stock quantity × rate reconciles with value. Run it after
any mapper change — recorded fixtures cannot catch a wrong assumption about the
wire format.

Fixtures in `tests/fixtures/live/` are captured verbatim from a real instance
(company "Bhtia Supermarket", 2026-07-23) and are the ground truth the mappers
are held to.

Bugs the first live run found, none of which fixture tests could have caught:

1. **The debit/credit convention was inverted** — every asset landed on the
   credit side, so a healthy bank balance rendered as an overdraft.
2. **Invoice lines were double-counted** — Tally returns them under two
   wrappers, so every purchase was valued at twice its real amount.
3. **Voucher classification was universally wrong** — `PERSISTEDVIEW` holds a UI
   view name, so every voucher fell through to `OTHER` and sales/purchase
   analytics would have been empty.
4. **Stock rates parsed to zero** — the `"80.00/KG"` unit suffix broke parsing.
5. **Outstanding bills silently returned nothing** — see below.

### Bill-wise outstanding: three traps in one query

Worth reading before touching `outstanding.bills`, because each failure is
silent — an empty report, not an error:

- **The member tag is `<BILL>`, singular.** Searching for `<BILLS>` returns
  zero bills on a company that has them.
- **Both `SVFROMDATE` and `SVTODATE` are required.** With only the to-date,
  Tally answers with an empty collection.
- **The due date is in a `JD` attribute** on `<BILLCREDITPERIOD>`, whose text is
  empty. `JD` counts days from 1899-12-31. Read the text alone and every bill
  has no due date, which makes the entire ageing report read "not due".

A `$$IsNonZero:$ClosingBalance` filter on this collection also suppresses all
output, so settled bills are dropped in the mapper instead.

### Receivable-vs-payable and Sundry-Debtors-vs-Creditors are different axes

Two reports read `outstanding.bills`, and mixing them up produces confident
wrong numbers:

- `/reports/outstanding` classifies each **bill** by the side it closes on. That
  is the honest answer to "who owes me money": a customer's advance sits in a
  debtor's ledger but is genuinely a payable, and counting it as a receivable
  would tell an owner they are owed money they have already been paid.
- `/reports/outstanding/group` classifies each **party** by the group its ledger
  sits under — Tally's Group Outstanding. A party belongs to Sundry Debtors
  however their individual bills point.

So the same advance appears as a payable in the first report and as an *advance
against a debtor* in the second, and both are correct. The group report
therefore publishes `total` (billed, on the group's expected side), `advances`
(everything pointing the other way) and `net`, rather than one figure that
silently picks a convention.

Group membership is the ledger's **direct** parent group. A party filed under a
home-made sub-group is counted as ungrouped rather than quietly claimed for its
ancestor, and `ungrouped_party_count` is returned so the app can say the report
is incomplete — a total that is quietly too small is worse than one that admits
what it could not see.
