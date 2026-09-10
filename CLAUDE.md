# CLAUDE.md

**TallyFlow** — an AI-powered, mobile-first dashboard for TallyPrime.

Think *"Google Analytics for Tally"*, not *"Remote Desktop for Tally"*. The user
should never feel like they are using Tally; they should experience a modern SaaS
dashboard.

> ## The one rule
>
> **The MVP is READ ONLY.** No create, update, or delete APIs reach Tally.
> Users view reports, dashboards, stock, receivables and analytics. They cannot
> modify anything in their books.
>
> The *architecture* must nonetheless be ready for writes, AI and automation
> without redesign. Read the [Future-proofing](#future-proofing) rules before
> adding a module.

---

## 1. Orientation

```
packages/
  tally_core/    Shared layer      domain models, XML codec, query registry, wire protocol
apps/
  connector/     Windows service   the only thing that ever talks to Tally,
                                   plus its own native window on the shop's PC
  backend/       FastAPI server    auth, connector hub, dashboards, reports, sync
  mobile/        Flutter app       dashboard, reports, QR pairing
  website/       React site        product story, downloads, pricing
  portal/        React SPA         management portal, served by Caddy at `/portal`
deploy/uat/      Compose stack     Postgres + API + Caddy (TLS, /downloads)
tools/brand/     Icon generator    favicon and launcher icons, from one drawing
run.py           Task runner       standard library only; finds the venv itself
```

Data flows one way in, one way out:

```
Flutter ──HTTPS──▶ Backend API ──WebSocket──▶ Connector ──▶ TallyPrime (localhost:9000)
```

**The connector dials out.** Tally is never exposed to the internet and no
inbound port is ever opened on the customer's machine. That single decision is
why the product is secure, cloud-independent and multi-tenant by default.

`tally_core.protocol` is the backend↔connector wire contract. It lives in the
shared package so neither side imports the other — a server deployment must not
pull in a Windows desktop application.

**Stack:** Python 3.11+ (backend, connector), Flutter 3.24 (mobile), React
(website, portal). Decided 2026-07-22. Do not propose alternatives.

Further reading: `README.md` (setup), `DEPLOYMENT.md`, `UAT.md`,
`apps/connector/INSTALL.md`.

---

## 2. Commands

```powershell
python run.py check      # ← the gate: pytest + ruff + flutter analyze + flutter test
python run.py dev        # backend on 127.0.0.1:8000, auto-reload
python run.py app        # Flutter app pointed at the local backend
python run.py portal     # management portal on localhost:5174, proxying /v1
python run.py init       # generate apps\backend\.env with real secrets
python run.py migrate    # alembic upgrade head
```

Release side:

```powershell
python run.py preflight                          # refuse-to-deploy checks
python run.py connector                          # Windows installer (Windows only)
python run.py release https://api.tallyflow.in   # app bundles + web (https only)
python run.py publish                            # regenerate the update manifest
```

`connector` and `release` stage their artefact and call `publish` automatically.
Run `publish` alone after editing `deploy/release-policy.json`.

`check` also builds `apps/website` and `apps/portal` when their `node_modules`
exist, and names the ones it skipped when they do not. Neither has a test suite;
the build is what catches the failure that matters there — a bad import ships as
a blank page with nothing in any log to explain it.

**`python run.py check` must pass before anything ships.** Python is not on
`PATH` in this environment — use `./.venv/Scripts/python.exe`.

---

## 3. Non-negotiables

**Read-only.** No mutation path to Tally. A read-only product grows an accidental
write path by misparsing a request, so unknown message types are refused or
ignored, never guessed at.

**Never expose Tally.** The connector is the only component that speaks to it,
over `localhost`. No port forwarding, ever.

**Never leak Tally models into the UI.** Always
`Tally XML → Mapper → Domain model → API DTO → Flutter`. A response shape that
reaches a widget unmapped is a redesign waiting to happen.

**Money is a value object, never a float.** `Money` holds an unsigned `Decimal`
plus an explicit `Side` (Dr/Cr). Binary floating point cannot represent `0.1`,
and this app shows people their bank balance. Use `.signed` for arithmetic and
charting, `.side` to render Dr/Cr.

**Fail soft, never crash.** Tally offline → show last synced data, say so,
reconnect automatically. A malformed amount in one row of a 5,000-row daybook
must not fail the report.

**Never render zeroes for missing data.** "No data yet" and "₹0 of sales" are
different statements, and confusing them in an accounting product is not a
cosmetic bug.

**Security.** HTTPS everywhere. JWT access tokens (short — a stolen one cannot be
revoked, only outlived) plus refresh tokens stored hashed and single-use.
Connector handshake is HMAC over `connector_id|nonce|issued_at`, so the pairing
secret never crosses the wire. Connector secrets are encrypted at rest, not
hashed — verifying an HMAC needs the key back. Audit every request.

**Code standards.** Clean architecture, feature-first, repository pattern,
dependency injection, domain-driven naming. No god classes.

**Comments explain *why*, not *what*.** This codebase's comments carry the
reason a line exists and the failure it prevents — often with the live-Tally
evidence behind it. Match that. A comment restating the code is noise; a comment
recording a two-day debugging session is the most valuable thing in the file.

---

## 4. TallyPrime: facts learned the hard way

Every item here was verified against a live TallyPrime and cost real debugging
time. Violating one produces silently wrong figures, not an error.

### Sign convention — the easiest thing to get backwards

**In Tally's XML a negative number is a DEBIT; a positive number is a CREDIT.**
That is the opposite of the usual debit-positive convention.

Verified 2026-07-23 across a full chart of accounts: Cash-in-Hand `-344220` (an
asset, so Dr), Capital `+700000` (Cr), Sales `+114000` (Cr), Purchases `-197000`
(Dr) — cross-checked against `ISDEEMEDPOSITIVE` on voucher lines. An explicit
`Dr`/`Cr` suffix always overrides the sign. See `domain/money.py`.

### Date windows do not work the way the envelope suggests

**`SVFROMDATE`/`SVTODATE` do not scope a custom `<COLLECTION><TYPE>Voucher</TYPE>`.**
They set the period other parts of the response are computed against, but
collection *membership* ignores them and yields the company's current period
whatever you ask for.

Verified live 2026-08-09: asking for 1–9 Aug, all of April, and a two-year span
each returned the identical 21 vouchers. Filter on `$Date` explicitly
(`_date_window_filter` in `queries/transactions.py`). Before this, the chunking
that exists to keep exports small was doing nothing at all.

Use `yyyymmdd` date literals — the month-name form depends on Tally's interface
language, and `dd-mm-yyyy` is indistinguishable from `mm-dd-yyyy` to a reader.

### A master's AlterID does not move when a voucher moves its balance

**`AlterID` on a ledger or stock item tracks edits to the *master record* —
renamed, regrouped, opening balance changed. It does not move when a voucher
changes that master's closing balance.** The company-wide `AltMstId` watermark
does not move either.

Verified live 2026-08-29 on "D.D Enterprises" with `tally-connector
alterid-probe`: one receipt voucher took `AltVchId` 261 → 262 and moved three
ledger balances (Cash `0.00 Dr` → `50,000.00 Cr`, Profit & Loss, Transportation
Charges). Every one of those three kept its original `AlterID`, and `AltMstId`
stood at 994 before and after.

The consequence is the whole shape of incremental master sync. `$AlterID >
cursor` on `Ledger` or `StockItem` is correct for **which masters exist** and
useless for **what they are worth** — a delta-only master read serves a stale
balance with no error to point at. Balances have to be re-read for the masters
the changed *vouchers* name, which the voucher delta already returns.

### Balances are always current

Closing balance and stock value ignore any date range. **The dashboard's period
view cannot scope cash or inventory** — a period selection must say so rather
than imply the figure was scoped. Prior financial years are largely unreachable.

### A closed company fails silently

A company that is not open in Tally returns **empty collections with no error**,
and voucher reads against one crash Tally outright. Every company-scoped read
goes through the loaded-company guard (`connector/loaded.py`); do not add a
scoped read that bypasses it.

### Name leaf fields, never wrapper collections

A bare `<FETCH>AllLedgerEntries</FETCH>` costs **7.6× more** than naming the leaf
fields for identical data (measured: 6.83 MB → 893 KB for six months). This is
the single biggest lever on export size, and export size is what decides whether
a shop's Tally survives a backfill.

### `PERSISTEDVIEW` is not an accounting class

On a live TallyPrime it holds a UI view name ("Accounting Voucher View"), so
every voucher classified as `OTHER`. Use `PARENT`, falling back to the type name.

### Tally crashes on its own exports

`c0000005` (Memory Access Violation) happens while Tally *builds* a collection,
so the size of one slice decides whether it survives. The evidence is in
`tallyerr.log` and the crash dumps, **not** in our logs. `sync_chunk_months` is
the lever; prefer naming leaf fields first.

### Operational gotchas

- Group and ledger names arrive on the `NAME` **attribute**, not as element text.
- Invoice lines are easy to double-count — check before summing.
- **An open dialog box in Tally blocks all requests.** First thing to ask a user.
- Tally serves **one request at a time** and blocks its own UI while exporting.
  That UI is the shop's till. Concurrency per connector is capped at 2, and more
  does not make anything faster.

---

## 5. Architecture as built

### Reads are snapshot-first

The phone talks to stored snapshots, not to Tally. A request that falls through
to live Tally is a bug worth a log warning — `RequestContextMiddleware` flags
anything over 2 s. A background refresher re-reads stale companies; pull-to-refresh
forces one, floored so mashing it cannot hammer a shop's Tally.

### The connector hub

One `ConnectorLink` per live socket owns request/response correlation, capped
concurrency and heartbeat liveness. Two invariants matter more than the rest:

1. **Every pending future is always resolved.** A future abandoned when a socket
   dies leaks a request handler until timeout — at fleet scale, that is how a
   backend runs out of workers during an internet outage.
2. **Concurrency per connector is capped.** See above; it protects the till.

Routing works across backend instances when `TALLYFLOW_REDIS_URL` is set;
without it the backend still works, single-process. **One uvicorn worker only** —
connectors hold long-lived sockets to a specific process, and forks would each
hold a different set with no way to route between them.

### History sync

Chunked backfill (newest first) plus AlterID deltas. An AlterID delta reports
what changed but never what was *deleted*, so a recent window is periodically
re-read in full and reconciled. Slices older than `sync_inventory_days` omit
inventory lines — line items are what make a voucher export large, and no report
reads the lines on a three-year-old invoice.

### One interface, three skins and two shapes

The app ships **Light, Dim and Dark**, chosen by the customer in Profile and
stored on the device rather than on the account — the same owner may want light
on the counter tablet and dark on the phone at night, and syncing the choice
would make one of those wrong every time. **Dim is the default**, not the system
setting: a theme that flips with the system clock changes the look of somebody's
books halfway through the day for no reason they asked for.

Colour lives almost entirely inside small rounded icon tiles. That is what keeps
a screen with six categories on it legible, and it is why the tile palette is a
fixed set rather than something a new feature adds to.

The same source produces a web build, so the shell has two shapes: a floating
pill over the content on a phone, and a rail down the side once there is width
beside the content for one. Screens ask `HomeShell.contentInset(context)` for
their bottom padding and wrap their body in `ContentPane`, which caps a column of
rows at a width where the eye can still track a name on the left to an amount on
the right.

**The mark** is `TallyFlow` in Caveat over a straight rule — one line where
there is room for one, and stacked (`Tally` over `Flow`) in a square, which is
every icon. The stacked form is baked to outlines by `tools/brand` rather than
set in a font: a favicon cannot load one, and an app built to keep working
through an outage must not render its own logo in a fallback face on exactly
those days. The favicon wears the white tile and the app wears the black one,
and swapping them is a mistake in both directions — a black square in a browser
tab strip reads as a hole, and a white one on a home screen reads as a missing
image.

It is deliberately not TallyPrime's mark. No script-word-over-swoosh-over-plain-
second-word lockup: rebuilding that with "Flow" for "Prime" produces something a
customer would take for an official Tally Solutions product, which is the exact
claim the website's footer disclaims. If somebody asks for the logo to be moved
closer to Tally's, that is what they are asking for and the answer is no.

### Pairing is a camera, not a keyboard

The first thing every customer does used to be the worst thing: read a connector
id and a 43-character secret off a phone, walk across a shop, and type both into
a Windows machine. That step is gone.

The **connector shows a code and the phone reads it.** The connector invents a
`code` and a `token`, registers them with the backend and draws the code as a
QR; an admin scans it, which is when a `Connector` row and its secret first
exist; the connector then collects that secret with the token — the half that
was never on screen — over TLS, exactly once. Both halves are stored only as
SHA-256 and the waiting secret is encrypted, so `connector_claims` completes no
pairing if it leaks, and photographing the screen is not enough to receive
somebody's credential.

**The connector still dials out for every step of it.** A flow where the phone
connected to the PC over the shop's wifi would have been less code and would
have put an inbound socket on a network we do not control — and it would have
failed on mobile data, on guest wifi, and behind AP isolation. Routing through
the backend is both the safer shape and the one that works.

The typed flow is kept, second, for a machine with no camera to hand or no
working window. It is the one that shows a secret to a human, which is why it is
second rather than why it is gone.

### The connector's local window

A real window — the pairing code, whether the backend and TallyPrime are
answering, which companies this PC feeds, who in the business can see them, the
Tally port, Reconnect and Refresh. It was a page in a browser tab until it
became `tally-connector-window.exe`, a **third executable in the same
installer**: a Flutter Windows build, produced from the phone app's package
under `apps/mobile` as a second `--target`, so the window and the app share one
theme rather than drifting into two products. The full record is
`apps/connector/NATIVE-UI.md`.

**The window is a client, never a host.** The connector starts at logon, before
anybody opens anything, and keeps serving Tally whether the window is closed,
killed or never installed. Anything that makes the connector stop working when
the window closes is wrong by construction.

**Loopback only, and not configurable.** Same rule as everywhere else: no
inbound port on a customer's machine. It stayed HTTP rather than becoming a
named pipe because Dart has no pipe in its standard library and would need an
FFI shim to draw a status screen. So the two defences against a browser tab on
the same machine driving this socket both stay: the `Host` header is checked
(DNS rebinding), and every call must carry a custom header, which a
cross-origin page cannot set without a preflight this server refuses. Address
reuse is off, because on Windows `SO_REUSEADDR` lets a second process take a
port that is already being listened on.

**The pairing code is encoded by the connector, painted by the window.** `segno`
builds the module grid — a full QR, never a Micro QR, with its four-module quiet
zone included in the grid — and the window draws it black on white, snapped to
whole *device* pixels. Every one of those is a code that scans or does not, and
none of them is visible from the machine drawing it.

**It cannot disconnect this computer.** No unlink, no remove, no unpair — those
decisions belong to whoever holds the account on their phone, not to whoever is
standing at the till. Reconnect is a *session* restart, not a process one: a
process restart cannot report its own result and cannot relaunch a scheduled
task it is running under, while rebuilding the session from freshly loaded
settings gives a new socket, a re-read `connector.json` and a new Tally client
on whatever port was just saved.

What it shows about the account is pushed down as a **roster** — names, roles
and sync times, never a figure. A shop PC that could be asked for a balance over
its own loopback socket would be a read path into the books outside every check
in `deps.get_company`.

### Re-pairing cuts the PC off first

"Re-pair this computer" in the app replaces the credential *immediately*, closes
the live socket, and keeps the row — companies, history and sync cursors all
stay attached, which is the whole reason it is not "add the PC again". The
machine then sees `reason_code: revoked` on its next handshake, stops offering a
credential that cannot work, and shows a fresh code to scan.

`revoked` is the only rejection that does that. A signature failure, or a
backend that cannot decrypt its own secrets, leaves the stored pairing alone:
a connector that unpairs itself on any refusal is one bad deploy away from a
fleet that has to be re-paired by hand, machine by machine.

### Freshness reaches the UI

Every read carries its own freshness metadata; the app wraps reads in `Fresh<T>`
and shows last-known figures with a banner rather than an error page when the
connector is unreachable.

### The year is chosen once, at the top

Every figure in this product belongs to an **Indian financial year, 1 April to
31 March**, and not to a calendar year. That choice is made once — a muted line
under the company name on the home screen, listing the years the company's own
books cover (`BOOKSFROM`, read from Tally and carried on `CompanyResponse`) and
defaulting to the year we are in.

Every date filter underneath it is **confined to that year**. A window that
would reach across 31 March is clamped, not honoured: a total spanning two sets
of books is a wrong number rather than an untidy one, and nothing on screen
would say so. So the presets differ by year — the open one offers today, this
week, this month, this quarter and April-to-today; a closed one offers its four
quarters and the whole year, because "today" means nothing in a year that
ended. The custom-range calendar is bounded at both ends for the same reason,
and a closed year is labelled as such wherever it is shown.

The rule lives in `FinancialYear.confine` and is applied twice — in the picker
and again when a period is applied — because a preset computed a moment before
midnight on 31 March is not the window it was when it is used.

### The demo account

One organisation flagged `is_demo`: an invented electricals distributor with
two financial years of books, no connector, and a published one-tap sign-in
(`POST /v1/auth/demo`, offered only where `/v1/public/config` says so). It
exists so somebody can see the product before they own TallyPrime.

It is deliberately an **ordinary account**. The rows are real — organisation,
user, company, snapshots, voucher records — and every screen reads them through
the same code that reads a customer's books, so the demo cannot drift away from
the product the way a set of canned responses would. `services/demo_books.py`
posts double-entry vouchers and *folds* them into ledger balances, stock levels
and outstanding bills, because those figures appear on different screens
computed by different code: invent them separately and the demo contradicts
itself the moment somebody taps through. The generator is seeded per day, so
the books are a pure function of the window they cover and yesterday never
restates itself.

Two rules hang off the flag. `Entitlement.allows_changes` is false, so nobody
who signs in can unlink the company, revoke the connector, invite a colleague
or change the shared password — enforced at the same choke points as everything
else, plus `require_mutable` for the *removals* that a lapsed customer is still
entitled to make on their own account. And `ReadService` never routes a demo
read to the hub and never reports it stale: there is no PC to be out of date
with, and the app is told `is_demo` so it says what the data is instead.

### Signing up provisions nothing

A customer installs the app and the connector, registers, and signs in — to an
`Organisation` whose `status` is `pending` and whose `max_users` and
`max_companies` are **zero**. They can add no Tally PC, no company and no
colleague until somebody in the management portal approves the account and says
what it covers. The organisation row *is* the onboarding request; there is no
second table, because two ids for one business is a reconciliation step that only
ever goes wrong.

Zero is the only safe default. Anything generous would make approval an optional
formality that nobody notices being skipped.

Two permissions come out of the subscription, and conflating them is the mistake
to avoid:

| | `allows_changes` | `allows_data` |
|---|---|---|
| pending | ✗ | ✓ — nothing to read yet, and an error screen on first launch is worse than a wait |
| active | ✓ | ✓ |
| suspended · rejected · expired | ✗ | ✗ |

Both are enforced at choke points that every route already passes through:
`deps.get_company` for reads, and inside `check_company_limit` /
`check_user_limit` for growth — so the count and the status can never be checked
separately. Refusals are **402**, distinct from 403, because "your subscription
is not live" and "you are not an admin" need completely different screens.
Expiry is computed from `expires_at`, never swept by a job that could stop
running.

### Portal authority is a second axis

`PlatformUser` is a separate table from `User`, and `PlatformRole`
(`partner` < `owner`) is a separate ladder from `Role` (`staff` < `admin`). A
partner outranks a shop's admin over that shop's subscription and outranks nobody
over its ledgers, which no single ordered enum can express.

Portal tokens carry `typ: portal`, so a customer's access token is refused by
`decode_token` before any handler runs — there is no ordering of checks that
could let a tenant escalate. A partner sees only accounts whose `partner_id` is
theirs, enforced in one helper and answering 404, never 403.

**The portal never shows a financial figure.** It counts how much of the product
an account uses. What is in anybody's books is not a subscription question, and
no route in `api/v1/portal.py` reaches a customer's data.

Portal accounts are never self-service. The first one is seeded from
`TALLYFLOW_PORTAL_BOOTSTRAP_*` at startup — once, only when no portal account
exists at all, and flagged `must_change_password` because that value lives in a
deployment manifest.

### The support view

The portal also answers "why did it stop working for this customer?", which
before it did meant SSH — so the person who noticed could not be the person who
looked. Two log surfaces, and the difference between them is what each can
promise.

**The backend's own log** is captured twice. An in-memory ring
(`services/logs.py`) holds every level for a live tail over SSE — this process
only, gone on restart, which is honest because a live tail of a process that no
longer exists is not a thing. `server_logs` takes WARNING and above and survives
a redeploy, because an incident is nearly always reported after the container
that produced it is gone. Owner-only: a stack trace from one tenant's request
routinely names another's connector, so there is no partner-shaped slice of it.

**Connectors push their log** over the socket they already hold open — a new
`log_batch` client frame, batched because these machines sit on asymmetric
broadband where upload is the bottleneck for report exports. Old connectors
never send it and old backends ignore it, which is the forward-compatibility
rule working in both directions.

Three properties matter more than the feature:

1. **It cannot hurt what it observes.** `RemoteLogHandler.emit` appends to a
   bounded deque and does nothing else — no I/O, no awaits, no exceptions.
   `ConnectorLink._handle_logs` swallows everything, because this is a
   diagnostic side-channel on the socket a customer's reports come back on.
2. **A gap is reported, never implied.** Both buffers are lossy under pressure,
   and both count what they dropped: the connector's count rides in the next
   batch and lands on the row. A missing stretch that reads as a quiet period is
   how a support session goes down the wrong path.
3. **The buffers drop opposite ends.** The connector drops its *oldest* — it is
   preserving what is happening now. The backend drops the *newest* — a full
   buffer there means one connector is flooding, and letting it evict everyone
   else's lines turns one broken machine into a fleet-wide blind spot.

Connector lines carry two timestamps. `created_at` is our receipt time and the
only safe sort key; `logged_at` is the shop PC's own clock, kept because "their
machine thinks it is six hours ago" is a real finding and invisible if quietly
overwritten. Neither table has a foreign key — the lines explaining why a
connector was removed must not go with it.

**Diagnostics are kept for two days.** `LogWriter` prunes four tables on a time
window, not two: the kept server log, the fleet's connector logs, the audit
trail and `job_stats`. All four grow with *traffic* rather than with customers —
the audit trail fastest of all, since it is a row per request and in a read-only
product every screen a customer opens is a request — and a diagnostic
side-channel that outgrows the books it sits beside has stopped being
diagnostic. Retention is not the only lever: the portal can clear any of them on
demand, per connector, per account or entirely. Clearing the audit trail is
owner-only and is itself audited, so the gap it leaves has a name and a time on
it.

---

## 6. Releases and updates

Publishing a release *is* writing `manifest.json` into both stacks'
`downloads/` directories -- `deploy/uat` and, mirrored from it,
`deploy/prod`, which is the one customers actually reach. It is
**generated, never edited** — `run.py publish` measures version, SHA-256 and size
from the bytes actually being served. A hand-typed checksum ships an update every
client refuses. The human half lives in `deploy/release-policy.json`
(`mandatory`, `min_supported_version`, release notes).

The backend reads that manifest and answers the version question **inline**, on
traffic that already exists — nothing polls:

| Client | How it learns | Delay |
|---|---|---|
| Phone | `X-Latest-App-Version` / `X-Min-App-Version` on every response; `426` below the floor | one request |
| Connector | `latest_connector_version` on every WebSocket frame, plus an `update` command | one heartbeat |

The connector keeps a 6-hourly manifest poll as a fallback for the case that
matters most: a connector that cannot reach the backend at all is exactly the one
that may need a new build, and it will never be told.

**Everything fails open.** Missing, unreadable or malformed manifest all yield
"no opinion", never "you are out of date". A config mistake must not be able to
brick a fleet. Kill switches: `TALLYFLOW_ENFORCE_MIN_APP_VERSION=false` restores
service without waiting for a corrected manifest to reach every phone;
`TALLYFLOW_PUSH_CONNECTOR_UPDATES=false` falls back to the poll.

Connector installs are genuinely silent (Windows service, `/VERYSILENT`) but wait
for Tally to go idle first — that wait is not negotiable. **Android cannot
silently install an APK**; the download starts automatically, the system
installer's confirmation cannot be suppressed.

Version strings must match across `tally_connector/__init__.py`,
`apps/mobile/pubspec.yaml` and `apps/website/src/data/downloads.js`, and the site
must be rebuilt after changing them.

---

## 7. MVP scope

**In:** authentication · company connection · dashboard · reports · analytics ·
charts · inventory · outstanding · ledger summary · sales summary · purchase
summary.

**Out:** any create, update or delete API.

**Dashboard widgets.** Today's and monthly sales/purchase, cash and bank balance,
receivables and payables, inventory value, top customers/products, sales and
purchase trend, expense breakdown, profit overview, recent transactions, GST
summary, company health.

**Reports.** Day book, cash/bank book, ledger, outstanding, stock summary, sales
and purchase register, P&L, balance sheet, trial balance, top customers/suppliers/
products, inactive products, negative stock, negative ledgers.

**Explicit non-goals.** This is not accounting software, an ERP replacement, a
remote desktop, or a Tally clone. It is a business-intelligence and mobile
companion for TallyPrime.

---

## 8. Future-proofing

Every module must be able to grow from READ to WRITE without redesign:
`DashboardService` today; `VoucherService`, `LedgerService`, `StockService`
tomorrow. Adding write support means adding new `type` values to the wire
protocol — existing readers ignore unknown types by design, so old connectors
degrade rather than crash.

Planned, not built: voucher/ledger/stock creation, an LLM tool-selection layer
(`User → LLM → tool → Backend → Connector → Tally`), notifications, workflow
automation, voice commands, a web dashboard, public APIs, and a marketplace.

**Roadmap:** ① read-only dashboards → ② advanced analytics → ③ notifications →
④ AI chat → ⑤ write APIs → ⑥ automation → ⑦ voice → ⑧ marketplace.

Before implementing anything, answer: *can this scale to 100,000 companies, and
can it support AI, write APIs, plugins and automation later?* If no, redesign
first.

---

## 9. Success criteria

A business owner opens their phone and, within **10 seconds**, knows:

- How much did I sell today?
- Who owes me money?
- What is my cash position?
- Which products are running out?
- What is today's profit?
- What happened since yesterday?

…without opening TallyPrime.

---

## 10. References

Prefer official examples over inference, always.

- [TallyPrime API Explorer](https://tallysolutions.com/tallyprime-api-explorer/) — the primary reference
- [Tally JSON integration](https://help.tallysolutions.com/tally-prime-integration-using-json-1/) — export, import, headers, request types, collections, static variables
- [Tally developer docs](https://help.tallysolutions.com/)
- [SetuBi](https://www.setubi.in/) — study how accountants consume information. **Do not clone.** Build something significantly better.
