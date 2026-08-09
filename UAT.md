# Deploying TallyFlow to UAT

A same-day runbook for putting the stack in front of pilot customers.

For the full production guide — registry, replicas, marketing site, store
builds, rollback — see **[DEPLOYMENT.md](DEPLOYMENT.md)**. This file is the
short path: one Linux host, one command, real TLS, one pilot shop.

**Every step below was executed against the real stack on 2026-08-09**, on
Docker 27.5.1. The commands and their outputs are what actually happened, not
what should happen.

---

## Read this first

UAT here means **real customers, real books, real credentials**. Three things
are true today and the pilot has to be designed around them:

1. **Only the currently-open financial year can be read.** TallyPrime exposes a
   custom voucher collection scoped to the company's open period, so a backfill
   cannot reach earlier years. Do not promise "4 years of history". The UAT env
   template sets `SYNC_MAX_HISTORY_YEARS=1` for this reason.
2. **TallyPrime can crash (`c0000005`) while serving exports.** That is a fault
   inside `tally.exe`, not in this code. It is mitigated — 7.6× smaller
   payloads, no retry of the request that killed it, a cooldown — but not
   eliminated. The affected machine is usually the shop's till.
3. **A revoked Tally PC leaves its companies behind.** They stay active and
   pointing at a connector that can never reconnect, so their reads fail
   permanently. Do not revoke a pilot customer's PC without also unlinking its
   companies.

Pick pilot shops you can phone. Not the busiest one.

---

## 0. What you need

| | |
|---|---|
| Host | Linux VM, 2 vCPU / 4 GB, Docker Engine + Compose v2 |
| Ports | 80 and 443 open to the internet |
| DNS | An A record for e.g. `uat-tallyflow.theshubhanshu.dev` pointing at the host |
| Repo | A checkout of this repository on the host |

The domain is not optional. Connectors dial `wss://`, phones use HTTPS, and
Caddy needs port 80 reachable to obtain the certificate. **Never point a
connector at a DHCP address** — that failure looks like a broken product and
costs an afternoon to diagnose.

---

## 1. Bring up the stack

```bash
git clone <repo> && cd "Tally Application/deploy/uat"
cp uat.env.example uat.env
```

Fill in the four required values in `uat.env`:

```bash
python3 -c "import secrets;print('POSTGRES_PASSWORD =',secrets.token_urlsafe(24))"
python3 -c "import secrets;print('JWT_SECRET        =',secrets.token_urlsafe(48))"
python3 -c "import secrets;print('SECRET_KEYS       =',secrets.token_urlsafe(32))"
```
- `UAT_DOMAIN` — your hostname
- `POSTGRES_PASSWORD` — and paste the same password into
  `TALLYFLOW_DATABASE_URL`
- `TALLYFLOW_JWT_SECRET`
- `TALLYFLOW_SECRET_KEYS`

> **Back up `TALLYFLOW_SECRET_KEYS` somewhere other than this host.** It
> encrypts every connector's pairing secret. Lose it and every pilot customer
> re-pairs their PC.

Then:

```bash
docker compose --env-file uat.env up -d --build
```

That builds both images, waits for Postgres to be healthy, runs the migrations
to completion, starts the API, and starts Caddy — in that order, enforced by
the compose file.

```
 Container tallyflow-uat-db-1        Healthy
 Container tallyflow-uat-migrate-1   Exited
 Container tallyflow-uat-api-1       Started
 Container tallyflow-uat-caddy-1     Started
```

### Why migrations are their own container

The runtime image packages `src/` only, so `alembic.ini` and `migrations/` are
not in it and `docker run <image> alembic upgrade head` cannot work. The
`migrator` build stage exists to carry them. This matters because **an
unmigrated Postgres does not fail at startup** — the API comes up clean and then
fails on the first query, which reads as "the app is broken" rather than
"nobody ran the migrations".

Confirm they ran:

```bash
docker compose --env-file uat.env logs migrate
# INFO  [alembic.runtime.migration] Running upgrade  -> 9b83127c60f8, initial schema
# INFO  [alembic.runtime.migration] Running upgrade 9b83127c60f8 -> c4f19a70d2e8, voucher store and chunked sync
```

---

## 2. Verify before letting anyone in

```bash
curl https://uat-tallyflow.theshubhanshu.dev/v1/health
# {"status":"ok"}

curl https://uat-tallyflow.theshubhanshu.dev/v1/ready
# {"status":"ok","database":"ok","instance_id":"...","environment":"prod"}

curl -o /dev/null -w '%{http_code}\n' https://uat-tallyflow.theshubhanshu.dev/docs
# 404   <- anything else means ENVIRONMENT is not "prod"
```

`environment: prod` is deliberate for UAT. It is what disables the API docs and
the debug error bodies; testing against production behaviour is the point.

**Check the WebSocket, not just HTTP.** A plain `GET /v1/connector` returns
`404` — that is correct and proves nothing, because the route only matches a
WebSocket scope. The whole product runs over that socket, so test the upgrade:

```bash
python3 - <<'PY'
import asyncio, json, websockets
async def main():
    async with websockets.connect("wss://uat-tallyflow.theshubhanshu.dev/v1/connector") as ws:
        await ws.send(json.dumps({"type":"hello","connector_id":"0"*32,
            "signature":"nope","nonce":"x","timestamp":0,"version":"0.1.0"}))
        print(await ws.recv())
asyncio.run(main())
PY
# {"type":"hello_ack","accepted":false,...}
```

`accepted:false` is the pass. It proves the upgrade survived the proxy and the
application answered — not just Caddy.

Finally, the environment itself:

```bash
cd ../.. && set -a && . deploy/uat/uat.env && set +a && python3 run.py preflight
# Environment looks deployable.
```

Two warnings are expected and correct for UAT: no `REDIS_URL` (single instance)
and no `CORS_ORIGINS` (native apps send no Origin).

---

## 3. Publish the site and the download artefacts

One hostname serves three things, so a pilot customer has one address to
remember:

| Path | Served by |
|---|---|
| `/v1/*` | the API and the connector WebSocket |
| `/downloads/*` | the connector installer and the Android APK |
| everything else | the download site (`apps/website`) |

### Build the artefacts

**The APK is the one that matters.** `run.py release` also produces an `.aab`,
but an app bundle **cannot be installed on a phone** — it is a Play Store upload
format. A pilot downloading from your own site needs the APK.

```powershell
# Windows, because PyInstaller and Inno Setup do not cross-compile
python run.py connector
# -> apps\connector\dist\installer\TallyFlowConnector-Setup-0.1.0.exe

python run.py release https://uat-tallyflow.theshubhanshu.dev
# -> apps\mobile\build\app\outputs\flutter-apk\app-release.apk
```

The API URL is compiled into the app. Get it wrong and the build points at
somebody's laptop, which you find out about from users.

> The APK is **debug-signed** (see DEPLOYMENT.md C3). That is fine for
> sideloading a pilot — it installs — but it cannot go to the Play Store, and
> Android will warn the user about an unknown source.

### Publish them

Copy both into `deploy/uat/downloads/`, named exactly as the site links them:

```bash
cp TallyFlowConnector-Setup-0.1.0.exe  deploy/uat/downloads/
cp app-release.apk                     deploy/uat/downloads/TallyFlow-0.1.0.apk
```

The names come from `apps/website/src/data/downloads.js`. If you bump a version
there, rename the files to match — the site is a static build and the links are
compiled in.

### The site builds itself

Nothing to do. `apps/website/dist` is gitignored — a build artefact does not
belong in git — so the Caddy image builds the site from source at
`docker compose ... --build` (see `web.Dockerfile`). The UAT host needs no Node
installed, and there is no "remember to run npm" step whose failure mode is a
blank page.

Rebuild the stack after changing anything under `apps/website/`, including the
version strings in `downloads.js`. Dropping a **new installer or APK** into
`downloads/` needs no rebuild at all — that directory is a bind mount.

### Verify

```bash
curl -o /dev/null -w '%{http_code}\n' https://uat-tallyflow.theshubhanshu.dev/
curl -o /dev/null -w '%{http_code}\n' https://uat-tallyflow.theshubhanshu.dev/downloads/TallyFlowConnector-Setup-0.1.0.exe
curl -o /dev/null -w '%{http_code}\n' https://uat-tallyflow.theshubhanshu.dev/downloads/TallyFlow-0.1.0.apk
curl -o /dev/null -w '%{http_code}\n' https://uat-tallyflow.theshubhanshu.dev/downloads/
# 200, 200, 200, 404  <- the last one is correct: no directory listing
```

### Before you show it to anyone

Two things on that page are not true yet:

- **The Google Play and App Store buttons are `href: '#'`** — they render as
  real buttons and do nothing. For a sideload pilot, either remove them in
  `Downloads.jsx` or tell testers to use "Or download the APK directly".
- **`pricing.js` contains invented figures.** They render as real prices to
  anyone who loads the page. Fix or remove the section before a customer sees it.

---

## 4. Onboard a pilot shop

**On your phone / the app**

1. Register the shop's owner account.
2. **Settings → Tally PCs → Add this computer.** Copy the Connector ID and
   Secret. The secret is shown once.

**On the shop PC**

3. Download the installer from `https://uat-tallyflow.theshubhanshu.dev` on the shop PC
   (built and published in section 3).

4. Run `TallyFlowConnector-Setup-<version>.exe` and enter the ID, the secret,
   and the server address:

   ```
   wss://uat-tallyflow.theshubhanshu.dev/v1/connector
   ```

   Or unattended:

   ```powershell
   TallyFlowConnector-Setup.exe /VERYSILENT /ID=<id> /SECRET=<secret> /SERVER=wss://uat-tallyflow.theshubhanshu.dev/v1/connector
   ```

5. Open TallyPrime **and load the company**. The connector refuses to read a
   company that is not open — Tally answers such a request with an empty result
   and no error, which would otherwise reach the owner as a shop with no sales.

6. Verify on the PC:

   ```powershell
   tally-connector status
   tally-connector diagnose     # lists the companies Tally has open
   ```

**Back in the app**

7. The PC shows online. **Add a company from this PC**, and the history sync
   starts on its own.

One connector per machine. Never install the same ID twice — two connectors
sharing an ID evict each other every few seconds and the link is down about
half the time.

---

## 5. Shipping a change to the UAT host

Three things never travel through git, so "pull and rebuild" is the whole story
only for code:

| | Travels via git? | |
|---|---|---|
| Backend, connector, website **source** | yes | rebuilt by compose |
| `uat.env` | **no** — gitignored | created once on the host |
| Installer `.exe` and `.apk` | **no** — gitignored, built on Windows | copied with `scp` |

### First deploy

```bash
# on the host
git clone <repo> && cd "Tally Application/deploy/uat"
cp uat.env.example uat.env && $EDITOR uat.env     # the four required values
docker compose --env-file uat.env up -d --build
```

```powershell
# on a Windows machine, then copy the results across
python run.py connector
python run.py release https://uat-tallyflow.theshubhanshu.dev
scp TallyFlowConnector-Setup-0.1.0.exe user@host:"Tally Application/deploy/uat/downloads/"
scp app-release.apk user@host:"Tally Application/deploy/uat/downloads/TallyFlow-0.1.0.apk"
```

### Every deploy after that

```bash
cd "Tally Application" && git pull
cd deploy/uat && docker compose --env-file uat.env up -d --build
```

That rebuilds the API and the site, re-runs any new migrations before the API
starts, and leaves the database and the published artefacts alone. `uat.env`
survives because it was never in git.

Only re-copy the `.exe` / `.apk` when you have actually rebuilt them — a new
backend commit does not change them.

### Check it landed

```bash
docker compose --env-file uat.env ps          # api healthy, migrate exited 0
docker compose --env-file uat.env logs migrate --tail 5
curl https://uat-tallyflow.theshubhanshu.dev/v1/ready
```

---

## 6. Day-to-day

```bash
cd deploy/uat

docker compose --env-file uat.env ps
docker compose --env-file uat.env logs -f api
docker compose --env-file uat.env exec db psql -U tallyflow -d tallyflow

# Ship a new commit
git pull
docker compose --env-file uat.env up -d --build   # migrations re-run automatically

# Stop, keeping data
docker compose --env-file uat.env down

# Stop and DESTROY the database
docker compose --env-file uat.env down -v
```

Give the API time to stop cleanly — `stop_grace_period` is 40s so in-flight
jobs finish and connectors notice the socket closing rather than hanging.

### Backups

The database holds every pilot customer's snapshots and sync state.

```bash
docker compose --env-file uat.env exec -T db \
  pg_dump -U tallyflow tallyflow | gzip > uat-$(date +%F).sql.gz
```

A dump without `TALLYFLOW_SECRET_KEYS` cannot decrypt connector secrets — which
is the point, and also why the key needs its own backup.

---

## 7. When a customer reports a problem

Ask two questions first: **is TallyPrime open, with the company loaded?** and
**what does `tally-connector status` say?** They resolve most reports.

| Symptom | Cause | Fix |
|---|---|---|
| "Tally PC offline", PC is on | `backend_url` unreachable; log shows `WinError 121` | `tally-connector configure --backend-url wss://...` |
| Link drops every ~20s, `replaced by a newer session` | Two connectors, same ID | Leave one running |
| Figures blank, no error | Company not open in Tally | Open it |
| "That company isn't open in TallyPrime" | Exactly that | Open it |
| Tally closed by itself | `c0000005` inside `tally.exe` | Reopen; collect `tallyerr.log` + `tallyN.dmp` |
| Owner lost the secret | — | App → the PC → **Re-pair this computer**, then `tally-connector configure --secret <key>` |

**Never tell a customer to add a second Tally PC.** Companies belong to one
connector, so re-linking the same books under a new PC creates a duplicate
company with its own half-finished history. Re-pair the existing one.

Connector logs: `%LOCALAPPDATA%\TallyFlow Connector\logs`.

---

## 8. What UAT will not tell you

Do not read a clean UAT as production readiness:

- **Single instance, single host.** No `REDIS_URL`, so a second replica cannot
  reach connectors held by the first. Scaling is untested.
- **Postgres in a container on the same host**, with no managed backups beyond
  the dump above.
- **No monitoring or alerting.** You find out a pilot is down when they call.
- **Android builds are debug-signed** (see DEPLOYMENT.md C3), so distribution is
  sideload or internal testing only.
- **`apps/connector.json` was committed with a live pairing secret** and remains
  in git history. It is now untracked and gitignored, and that connector's
  secret must be treated as burned — rotate or revoke it before the repo is
  shared. Never reuse it for a pilot.

---

## Appendix — what runs where

```
                       ┌── /v1/*        ──▶ api :8000 ──▶ db :5432
Phone   ──HTTPS──▶ Caddy ── /downloads/* ──▶ installer + APK   (1 worker)
                   :443 └── /*           ──▶ website (static)
Shop PC ──WSS──────┘
   └── connector ──▶ TallyPrime 127.0.0.1:9000   (never exposed)
```

| Service | Image | Notes |
|---|---|---|
| `db` | `postgres:16-alpine` | No published port; named volume `db-data` |
| `migrate` | built, `migrator` stage | Runs once, must exit 0 before `api` starts |
| `api` | built, `runtime` stage | No published port; one worker, on purpose |
| `caddy` | `caddy:2-alpine` | Owns 80/443, automatic TLS, 300s proxy timeouts |
| website | built into the caddy image | `web.Dockerfile`; no Node needed on the host |
| downloads | none — static files | `deploy/uat/downloads/`, no directory listing |

The API publishes no host port. It terminates no TLS and trusts
`X-Forwarded-For` unconditionally, so Caddy must remain the only way in.
