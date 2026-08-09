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
| DNS | An A record for e.g. `uat.tallyflow.app` pointing at the host |
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
curl https://uat.example.com/v1/health
# {"status":"ok"}

curl https://uat.example.com/v1/ready
# {"status":"ok","database":"ok","instance_id":"...","environment":"prod"}

curl -o /dev/null -w '%{http_code}\n' https://uat.example.com/docs
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
    async with websockets.connect("wss://uat.example.com/v1/connector") as ws:
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

## 3. Onboard a pilot shop

**On your phone / the app**

1. Register the shop's owner account.
2. **Settings → Tally PCs → Add this computer.** Copy the Connector ID and
   Secret. The secret is shown once.

**On the shop PC**

3. Build the installer (Windows only, see [INSTALL.md](apps/connector/INSTALL.md)):

   ```powershell
   python run.py connector
   ```

4. Run `TallyFlowConnector-Setup-<version>.exe` and enter the ID, the secret,
   and the server address:

   ```
   wss://uat.example.com/v1/connector
   ```

   Or unattended:

   ```powershell
   TallyFlowConnector-Setup.exe /VERYSILENT /ID=<id> /SECRET=<secret> /SERVER=wss://uat.example.com/v1/connector
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

## 4. Day-to-day

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

## 5. When a customer reports a problem

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

## 6. What UAT will not tell you

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
Phone ──HTTPS──▶ Caddy :443 ──▶ api :8000 ──▶ db :5432
                   │                (uvicorn, 1 worker)
Shop PC ──WSS──────┘
   └── connector ──▶ TallyPrime 127.0.0.1:9000   (never exposed)
```

| Service | Image | Notes |
|---|---|---|
| `db` | `postgres:16-alpine` | No published port; named volume `db-data` |
| `migrate` | built, `migrator` stage | Runs once, must exit 0 before `api` starts |
| `api` | built, `runtime` stage | No published port; one worker, on purpose |
| `caddy` | `caddy:2-alpine` | Owns 80/443, automatic TLS, 300s proxy timeouts |

The API publishes no host port. It terminates no TLS and trusts
`X-Forwarded-For` unconditionally, so Caddy must remain the only way in.
