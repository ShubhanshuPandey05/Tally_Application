# Deploying TallyFlow

Step-by-step for the things that live on servers: the **backend API**
(`apps/backend`), the **marketing website** (`apps/website`) and the
**management portal** (`apps/portal`).

The other two artefacts — the Windows connector and the mobile app — are not
"deployed" so much as *published*: the connector installer is built on a Windows
machine and copied to the website's `/downloads`, and the app goes to the stores.
Part C covers publishing them, because the website links to them and the
connector has to be told the backend's address at build time.

Deploy in this order. Each part depends on the one before it:

| Order | What | Why it must come first |
|---|---|---|
| 1 | Backend API | Everything else needs its public URL |
| 2 | Connector installer | Its backend URL is compiled in |
| 3 | Mobile app | Its API URL is compiled in |
| 4 | Marketing website | It links to the artefacts from 2 and 3 |
| 5 | Management portal | It calls the API from 1, and needs its origin |

---

## 0. Before you start

### Decide the two hostnames

Nothing further works until these are settled, because both get baked into
compiled artefacts that customers install:

| Hostname | Serves | Example |
|---|---|---|
| API | Backend, HTTPS + WebSocket | `api.tallyflow.app` |
| Website | Static site + `/downloads` | `tallyflow.app` |

The repository currently contains **two different placeholder domains** —
`api.tallyflow.app` in `apps/connector/installer/tallyflow-connector.iss:27` and
`api.tallyflow.in` in the `run.py` docstring. Pick one and make it consistent
before building anything a customer will install; a connector pointed at a
hostname you don't own is an installer you have to re-issue.

### What you need

- A Linux host or container platform for the backend (2 vCPU / 2 GB is ample to
  start), or an account on Fly.io / Render / Railway / any Kubernetes cluster.
- A managed **PostgreSQL 14+** instance. Not SQLite — `run.py preflight` refuses
  to pass with a SQLite URL, on purpose.
- Any static host for the website (Netlify, Vercel, Cloudflare Pages, S3+CDN, or
  plain nginx).
- TLS certificates for both hostnames (Let's Encrypt is fine).
- A Windows 10/11 machine with Python 3.11+, Inno Setup 6 and TallyPrime for the
  connector build.
- Optional but recommended: a **code-signing certificate**. Without one,
  SmartScreen warns every customer who downloads the connector.

### Read this once

Two facts shape most of what follows:

1. **A connector holds a long-lived WebSocket to one specific backend process.**
   That is why there is one uvicorn worker per container, why more capacity means
   more containers rather than more workers, and why a second replica requires
   Redis.
2. **Losing `TALLYFLOW_SECRET_KEYS` means every customer must re-pair their
   connector.** It encrypts pairing secrets at rest. Back it up somewhere other
   than the database — that separation is the entire point.

---

# Part A — Backend API

## A1. Provision the database

Create a Postgres database and a role for the app. Note the connection string;
the backend needs the **asyncpg** driver in the URL or preflight fails:

```
postgresql+asyncpg://tallyflow:<password>@db-host:5432/tallyflow
```

Require TLS to the database if your provider offers it. Turn on automated
backups now, not later — this holds every customer's pairing state.

## A2. Generate the secrets

Two secrets, generated once, then stored in your platform's secret manager
(never in the image, never in git):

```bash
python -c "import secrets; print('TALLYFLOW_JWT_SECRET=' + secrets.token_urlsafe(48))"
python -c "import secrets; print('TALLYFLOW_SECRET_KEYS=' + secrets.token_urlsafe(48))"
```

Both matter, and they fail differently:

- **`TALLYFLOW_JWT_SECRET`** — if unset, the backend invents one per process. The
  symptom is not an error; it is users being signed out on every restart, and
  every replica rejecting tokens issued by every other replica.
- **`TALLYFLOW_SECRET_KEYS`** — if unset, connector secrets fall back to being
  encrypted with the JWT secret, so rotating the JWT secret silently orphans the
  entire connector fleet. Set it explicitly. Store a copy offline.

## A3. Write the production environment

The full annotated list is `apps/backend/.env.example`. Minimum viable production
set:

```bash
TALLYFLOW_ENVIRONMENT=prod
TALLYFLOW_DEBUG=false
TALLYFLOW_JWT_SECRET=<from A2>
TALLYFLOW_SECRET_KEYS=<from A2>
TALLYFLOW_DATABASE_URL=postgresql+asyncpg://tallyflow:<password>@db-host:5432/tallyflow
TALLYFLOW_CORS_ORIGINS=https://tallyflow.app
TALLYFLOW_REDIS_URL=            # leave empty for a single instance; see A9
```

Notes on the ones people get wrong:

- `TALLYFLOW_ENVIRONMENT=prod` also disables `/docs` and `/openapi.json`. If you
  can still load `/docs` in production, this variable did not take effect.
- `TALLYFLOW_CORS_ORIGINS` is a **comma-separated** list, not JSON. It only
  matters if a browser calls the API — the Flutter web build or a future web
  dashboard. The mobile app does not need it.
- `TALLYFLOW_SECRET_KEYS` is also comma-separated, oldest keys retained for
  decryption. See A11 for rotation.

In a container platform these are environment variables in the service config.
Do not ship a `.env` file inside the image.

## A4. Preflight

`run.py preflight` reads the **real environment**, not a file, because that is
what a container gets. Run it in a shell holding the production variables:

```bash
export TALLYFLOW_ENVIRONMENT=prod
export TALLYFLOW_JWT_SECRET=... TALLYFLOW_SECRET_KEYS=...
export TALLYFLOW_DATABASE_URL=postgresql+asyncpg://...
python run.py preflight
```

It exits non-zero on anything that would be silently expensive: a short or
missing JWT secret, an unset `SECRET_KEYS`, a SQLite or wrong-driver database
URL, debug left on. Warnings (no Redis, no CORS origins) are fine to ignore for a
single-instance launch.

Wire this into CI as a deploy gate if you have one.

## A5. Run the migrations

**This is the step most likely to trip you up.** The runtime image contains the
application wheel only — `alembic.ini` and `migrations/` are *not* packaged into
it (`pyproject.toml` packages `src/` alone). You cannot run
`docker run <image> alembic upgrade head`.

Auto-create-on-startup only happens for SQLite. On Postgres, an unmigrated
database means the app starts and then fails on the first query.

Run migrations from a checkout of the repo, against the production database:

```bash
git clone <repo> && cd "Tally Application"
python -m venv .venv
.venv/bin/python -m pip install -e packages/tally_core -e apps/backend
export TALLYFLOW_DATABASE_URL=postgresql+asyncpg://tallyflow:...@db-host:5432/tallyflow
python run.py migrate
```

On Windows:

```powershell
$env:TALLYFLOW_DATABASE_URL = "postgresql+asyncpg://tallyflow:...@db-host:5432/tallyflow"
python run.py migrate
```

Verify the schema matches the models — this fails if someone changed a model
without generating a migration:

```powershell
cd apps\backend
..\..\.venv\Scripts\python.exe -m alembic check
```

If you would rather migrate from inside your platform (a Kubernetes Job, a
release command), build a second image that also copies `apps/backend/alembic.ini`
and `apps/backend/migrations/` into the runtime stage, and run
`alembic upgrade head` from `/app`.

Order matters: **migrate before starting the new image**, and keep migrations
backward-compatible with the currently running version so a rollback does not
strand you.

## A6. Build the image

The build context is the **repository root**, not `apps/backend` — the Dockerfile
also copies the shared `packages/tally_core`. `run.py image` handles that:

```powershell
python run.py image tallyflow-backend:0.1.0
```

Equivalent to:

```bash
docker build -f apps/backend/Dockerfile -t tallyflow-backend:0.1.0 .
```

Tag with a real version, never deploy `:latest` — you cannot roll back to a tag
that keeps moving. Push to your registry:

```bash
docker tag tallyflow-backend:0.1.0 registry.example.com/tallyflow-backend:0.1.0
docker push registry.example.com/tallyflow-backend:0.1.0
```

## A7. Run the container

The image's `CMD` already runs the correct command: one uvicorn worker,
`0.0.0.0:8000`, proxy headers on, 30-second graceful shutdown. Do not override it
with `--workers 4`.

```bash
docker run -d --name tallyflow-api \
  --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  --env-file /etc/tallyflow/backend.env \
  registry.example.com/tallyflow-backend:0.1.0
```

Bind to `127.0.0.1` on the host and let the reverse proxy own port 443. The
container must never be directly reachable from the internet: it terminates no
TLS and trusts `X-Forwarded-For` unconditionally.

A minimal `docker-compose.yml` if you prefer:

```yaml
services:
  api:
    image: registry.example.com/tallyflow-backend:0.1.0
    restart: unless-stopped
    env_file: /etc/tallyflow/backend.env
    ports: ["127.0.0.1:8000:8000"]
```

Graceful shutdown matters here: 30 seconds gives in-flight jobs time to finish
and connectors time to notice the socket closing and re-dial elsewhere. Give the
container at least that long to stop (`docker stop -t 40`).

## A8. Reverse proxy and TLS

Two requirements beyond a normal proxy config: **WebSocket upgrade** on
`/v1/connector`, and **long read timeouts** so idle connector sockets are not
culled between heartbeats.

nginx:

```nginx
server {
    listen 443 ssl http2;
    server_name api.tallyflow.app;

    ssl_certificate     /etc/letsencrypt/live/api.tallyflow.app/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/api.tallyflow.app/privkey.pem;

    # The connector's long-lived WebSocket. Must come before the / block.
    location /v1/connector {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade    $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host       $host;
        proxy_set_header X-Real-IP  $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Heartbeat is 30s; anything under ~2x that kills healthy connectors.
        proxy_read_timeout  3600s;
        proxy_send_timeout  3600s;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host       $host;
        proxy_set_header X-Real-IP  $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout  120s;
    }
}

server {
    listen 80;
    server_name api.tallyflow.app;
    return 301 https://$host$request_uri;
}
```

**Security note on forwarded headers.** The container runs with
`--forwarded-allow-ips *` and the rate limiter keys anonymous requests off the
first entry in `X-Forwarded-For`. That is correct *only* behind a proxy that
overwrites the header. If the container is ever reachable directly, a client can
send its own `X-Forwarded-For` and rotate past the auth rate limit — which is the
bucket protecting password guessing. Keep A7's `127.0.0.1` binding.

If you use a cloud load balancer instead, the equivalents are: enable WebSocket
support, set the idle timeout to 3600s, and ensure it strips inbound
`X-Forwarded-For`.

## A9. Health checks and smoke tests

Two endpoints, and they are deliberately different:

| Endpoint | Use for | Checks |
|---|---|---|
| `/v1/health` | **Liveness** | Nothing. Always cheap. |
| `/v1/ready` | **Readiness** / load balancer | Database connectivity |
| `/v1/fleet` | Monitoring | Connectors on *this* instance only |

Point liveness at `/health` and readiness at `/ready`. Do not point liveness at
`/ready`: a database blip would then restart every replica at once and turn a
partial outage into a total one.

Smoke test after deploying:

```bash
curl -sf https://api.tallyflow.app/v1/health
# {"status":"ok"}

curl -sf https://api.tallyflow.app/v1/ready
# {"status":"ok","database":"ok","instance_id":"...","environment":"prod"}

# Must 404 in production -- if this returns docs, ENVIRONMENT is not "prod".
curl -s -o /dev/null -w '%{http_code}\n' https://api.tallyflow.app/docs

# WebSocket endpoint reachable (426/400 is correct for a plain GET; 502 is not).
curl -s -o /dev/null -w '%{http_code}\n' https://api.tallyflow.app/v1/connector
```

Then create the first real account and confirm the round trip:

```bash
curl -sX POST https://api.tallyflow.app/v1/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"owner@example.com","password":"<12+ chars>","org_name":"Acme Traders","full_name":"Owner"}'
```

`org_name` is required and creates the organisation; `password` has a 10-character
minimum. A 201 with an access token means auth, the database and the migrations
are all working.

Registration is open — anyone who finds the API can create an organisation. If
that is not what you want at launch, put the endpoint behind an invite check or
block `/v1/auth/register` at the proxy until you are ready.

## A10. Scaling past one instance

One instance is genuinely fine for a few hundred connectors. When you add a
second, you **must** add Redis, or roughly (N-1)/N of all reads fail: the phone's
request lands on whichever instance the load balancer picked, and the connector's
socket is held by a different one.

```bash
TALLYFLOW_REDIS_URL=redis://redis-host:6379/0
TALLYFLOW_INSTANCE_ID=<unique per process>   # Kubernetes: the pod name
```

`TALLYFLOW_INSTANCE_ID` defaults to a random hex string, which is correct but
makes logs harder to follow; set it to the pod or container name. It must be
unique — two processes sharing an id will route jobs to each other's sockets.

Still one uvicorn worker per container. Scale by adding containers.

Kubernetes sketch:

```yaml
env:
  - name: TALLYFLOW_INSTANCE_ID
    valueFrom: { fieldRef: { fieldPath: metadata.name } }
livenessProbe:
  httpGet: { path: /v1/health, port: 8000 }
  periodSeconds: 30
readinessProbe:
  httpGet: { path: /v1/ready, port: 8000 }
  periodSeconds: 10
terminationGracePeriodSeconds: 45
```

## A11. Rotating secrets

**JWT secret.** Replace `TALLYFLOW_JWT_SECRET` and restart. Everyone is signed
out; no data is lost. Safe to do whenever you suspect exposure — *provided*
`TALLYFLOW_SECRET_KEYS` is set independently. If it is not, this also destroys
every connector pairing.

**Encryption keys.** Prepend the new key; keep the old one for decryption:

```bash
TALLYFLOW_SECRET_KEYS=<new key>,<old key>
```

The first key encrypts, all keys are tried on decrypt. Leave the old key in place
until every connector has reconnected and had its secret re-encrypted, then drop
it in a later deploy.

## A12. Rolling back

```bash
docker stop -t 40 tallyflow-api && docker rm tallyflow-api
docker run -d --name tallyflow-api ... registry.example.com/tallyflow-backend:<previous tag>
```

Database migrations do not roll back automatically. Because of that, keep every
migration backward-compatible with the previous release (add columns, don't drop
them; drop in a later release once nothing reads them).

If you must reverse a migration, `run.py migrate` cannot do it — it only runs
`alembic upgrade`. Downgrade explicitly, and read the migration's `downgrade()`
first, since one that drops a column loses its data:

```powershell
cd apps\backend
..\..\.venv\Scripts\python.exe -m alembic downgrade <previous revision>
```

---

# Part B — Marketing website

The site is a React 18 + Vite static build. No server, no runtime API calls, no
environment variables — everything it shows is compiled in. That means
**publishing new artefacts requires rebuilding the site**, which is the workflow
in B1–B3.

## B1. Make the content true before building

The site reads its release facts from `/downloads/manifest.json` at runtime —
the file `run.py publish` generates by measuring the bytes actually being
served — so version, size and SHA-256 correct themselves the moment you
publish, with no rebuild. What you still have to check is everything that
**cannot** come from a manifest:

**`apps/website/src/data/downloads.js`** holds the fallback shown before that
fetch resolves and on a host with no manifest. It describes the last published
build. If it names a version that is no longer in `/downloads`, its download
link 404s for the first second of every visit — update it when you publish.

**`apps/website/src/data/site.js`** holds the contact address and the report
list. The report list is a claim about the app: it must match the app's report
index, or the site is promising screens that are not there.

**`components/Security.jsx`** states only what the code does. If a mechanism
listed there changes, the sentence goes in the same commit.

**The hero prints live counts** from `GET /v1/public/stats` -- active
businesses, paired Tally PCs, companies being read, and how many of those PCs
are connected right now. It is the only route in the product that reads customer
data without a token, and it returns four integers and a timestamp: no names, no
identifiers. Two consequences for a deploy:

- The site and the API must share a hostname, or the request is cross-origin and
  the row silently does not appear. The Caddyfile already arranges this.
- `TALLYFLOW_PUBLIC_STATS_ENABLED=false` turns it off. Publishing a customer
  count is a commercial decision, and reversing it must not need a rebuild.

There is no pricing module any more. There is no published price list and no
self-service billing — accounts are activated by hand in the portal — and the
site says exactly that. Do not reintroduce figures nobody has agreed to.

Also check the support URL in the connector installer
(`AppSupportURL=https://tallyflow.app/support`) resolves to a real page on the
site — `/docs` is the obvious target.

## B2. Build

```powershell
cd apps\website
npm ci               # ci, not install -- respects the lockfile
npm run build        # -> apps\website\dist
npm run preview      # serve the build locally and click through it once
```

`npm run preview` before deploying is worth the thirty seconds: the build is
static, so anything broken at build time is broken for everyone.

## B3. Publish the download artefacts

The site expects the files at `/downloads/…` on the same origin:

```
dist/
  index.html
  assets/…
  downloads/
    TallyFlowConnector-Setup-0.1.0.exe
    TallyFlow-0.1.0.apk
```

Copy the signed installer (Part C) and, if you distribute one, the APK into
`apps/website/public/downloads/` before running `npm run build` — Vite copies
`public/` into `dist/` verbatim. Alternatively upload them straight to the host's
`/downloads` path and point `downloads.js` at your CDN's absolute URLs instead.

Generate the checksum you publish from the file you actually upload:

```powershell
Get-FileHash .\TallyFlowConnector-Setup-0.1.0.exe -Algorithm SHA256
(Get-Item .\TallyFlowConnector-Setup-0.1.0.exe).Length / 1MB
```

A published checksum that doesn't match the file is worse than none — it teaches
customers to ignore it.

## B4. Deploy the static build

**Managed host** (Netlify / Vercel / Cloudflare Pages):

| Setting | Value |
|---|---|
| Base directory | `apps/website` |
| Build command | `npm ci && npm run build` |
| Publish directory | `apps/website/dist` |
| Node version | 20 |

The site is a single page with in-page anchors, so no SPA rewrite rule is
required. Add one (`/* → /index.html`, 200) only if you later add client routing
— and make sure it excludes `/downloads/*`, or a mistyped installer URL will
serve HTML with a `.exe` name.

**Self-hosted nginx**, if the website shares the box with the API:

```nginx
server {
    listen 443 ssl http2;
    server_name tallyflow.app;

    ssl_certificate     /etc/letsencrypt/live/tallyflow.app/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/tallyflow.app/privkey.pem;

    root /var/www/tallyflow;
    index index.html;

    # Hashed filenames -- safe to cache hard.
    location /assets/ {
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # Installers are large and versioned in the filename.
    location /downloads/ {
        expires 7d;
        add_header Cache-Control "public";
    }

    # index.html must never be cached, or a deploy does not reach anyone.
    location = /index.html {
        add_header Cache-Control "no-cache, must-revalidate";
    }

    location / { try_files $uri $uri/ /index.html; }
}
```

Deploying is `rsync -a --delete apps/website/dist/ server:/var/www/tallyflow/` —
but note `--delete` removes `/downloads` too if you keep artefacts outside the
build. Either put artefacts in `public/downloads/` (so they are part of every
build) or exclude the path: `--exclude downloads/`.

## B5. Verify

```bash
curl -sI https://tallyflow.app | head -1
curl -sI https://tallyflow.app/downloads/TallyFlowConnector-Setup-0.1.0.exe | head -1
```

Then open the site and check, in order: the download button hits a real file of
the size stated on the page; the checksum on the page matches the file; pricing
is the agreed pricing; and every claim in the copy is still true of the shipped
backend (read-only enforcement, outbound-only connections, freshness stamps).
That last one is a real maintenance obligation — the copy is specific on purpose.

---

# Part B2 — Management portal

Also a React 18 + Vite static build, and deployed the same way — but with one
constraint that is not negotiable and is easy to get wrong.

## The portal must be same-origin with the API

The portal holds platform-wide authority: its token can approve accounts, set
subscription limits, and read the server log. Serve it from the **same hostname
as `/v1`**, so that token is never a cross-origin credential and no CORS rule is
what stands between it and another origin.

In the UAT stack that is already true — Caddy serves `/portal/*` from disk and
proxies `/v1/*` to the API on one hostname (`deploy/uat/Caddyfile`), and the
build happens inside `deploy/uat/web.Dockerfile`, so a `docker compose build`
is the whole deploy. Nothing below is needed for UAT.

If you host the portal separately, three values have to agree or the page loads
once and 404s on the first navigation:

| Where | Value |
|---|---|
| `apps/portal/vite.config.js` | `base: '/portal/'` |
| `apps/portal/src/main.jsx` | `basename="/portal"` |
| Your web server | serves those files at `/portal/`, with an SPA fallback |

## Build and serve

```powershell
cd apps\portal
npm ci
npm run build        # -> apps\portal\dist
```

nginx, alongside the API on the same server block:

```nginx
location /portal/ {
    alias /var/www/tallyflow-portal/;
    # A client-side route must survive a refresh. The support view is the
    # screen most likely to be bookmarked, and without this /portal/logs/...
    # 404s on reload.
    try_files $uri $uri/ /portal/index.html;
}

# Hashed filenames -- safe to cache hard. index.html never is: a browser
# holding yesterday's copy asks for asset names that no longer exist, and the
# portal renders as a blank page with nothing in any log to explain it.
location /portal/assets/ {
    alias /var/www/tallyflow-portal/assets/;
    expires 1y;
    add_header Cache-Control "public, immutable";
}
```

Keep it off search engines and, if you have a fixed office IP, behind an
allow-list — it lists customer businesses by name. The page already sends
`noindex`; an `allow`/`deny` block is the stronger half.

## Verify

Sign in as the bootstrap owner (`TALLYFLOW_PORTAL_BOOTSTRAP_EMAIL`), which will
force a password change on first use, then check three things: the Accounts list
loads, **Server logs** shows live lines from the API you just deployed, and a
hard refresh on a deep link like `/portal/logs/connector` still renders. That
last one is the SPA fallback, and it is the failure that only shows up later.

---

# Part C — Publishing the connector and the app

## C1. Point the connector at your API — before building

`apps/connector/installer/tallyflow-connector.iss:27`:

```
#define DefaultBackend   "wss://api.tallyflow.app/v1/connector"
```

Change this to your real API hostname. It is a plain `#define`, so an `ISCC /D`
override on the command line will not win — edit the file (or wrap it in
`#ifndef`/`#endif` the way `AppVersion` is, if you want to set it per build).

Note the shape of the URL: `wss://` scheme, `/v1/connector` path. The connector
validates that it is `ws://` or `wss://` and will refuse to start otherwise. Use
`wss://` for anything a customer touches.

## C2. Build and sign the installer

On Windows, with Inno Setup 6 installed (`winget install JRSoftware.InnoSetup`):

```powershell
python run.py connector
# -> apps\connector\dist\installer\TallyFlowConnector-Setup-<version>.exe
```

The version comes from `tally_connector.__version__`, so bump that — not the
filename — for a new release.

**Sign it.** The build output is unsigned, and SmartScreen will warn every
customer on first download until it is signed and has built reputation:

```powershell
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
  /f cert.pfx /p <password> `
  apps\connector\dist\installer\TallyFlowConnector-Setup-0.1.0.exe
```

Test the signed installer on a clean Windows VM that has TallyPrime, using both
paths: the wizard, and the silent rollout used for multi-branch customers —
`TallyFlowConnector-Setup.exe /VERYSILENT /ID=<id> /SECRET=<secret>`. The install
is per-user (no UAC), so run it as the account TallyPrime runs under.

## C3. Build the mobile app

The API URL is compiled in and has no default — `run.py release` requires it and
rejects anything that is not `https://`:

```powershell
python run.py release https://api.tallyflow.app
# -> apps\mobile\build\app\outputs\bundle\release\   (Android App Bundle)
# -> apps\mobile\build\web\                          (Flutter web)
```

### App signing

Gradle reads the release key from `apps/mobile/android/key.properties`, which is
gitignored. **Without it, `run.py release` refuses to build** — because Gradle
falls back to the debug key so a fresh checkout can `flutter run --release`, and
that fallback is exactly how a debug-signed APK reaches customers unnoticed: it
builds cleanly, installs cleanly, and looks identical to a real one.

It is not merely a Play Console rejection. The debug keystore's password is
publicly documented (`android`), so **anyone can sign a package that Android will
install over TallyFlow as an update.** For an app that reads a business's books,
that is a supply-chain hole, not a signing detail.

Generate the upload keystore once:

```powershell
keytool -genkey -v -keystore C:\keys\tallyflow-release.jks `
        -keyalg RSA -keysize 2048 -validity 10000 -alias tallyflow
```

Then copy `key.properties.example` to `key.properties` and fill it in.

**Back the keystore and both passwords up somewhere you will still have in five
years, outside this repo** (a `git clean -xfd` deletes untracked files). Android
identifies an app by its signing key: lose it and no existing install can ever be
updated in place again — every user must uninstall and reinstall, losing their
session. Play App Signing is the only recovery path, and it must be opted into
before the first upload.

> **The 0.1.0 APK already distributed is debug-signed** (verified: its
> certificate is `CN=Android Debug`). Moving to a real key changes the signature,
> so **existing 0.1.0 installs cannot update in place** — those users must
> uninstall and reinstall once. Take that break now, while the pilot is small.

iOS is built and signed through Xcode with your distribution profile.

If you host the Flutter web build on the website domain, add that origin to
`TALLYFLOW_CORS_ORIGINS` on the backend (A3) and redeploy the API — otherwise the
browser blocks every request and the app appears to hang at login.

## C4. Update the website and rebuild

Back to B1: set `CONNECTOR_VERSION`, `APP_VERSION`, the sizes and the checksums
in `downloads.js`, drop the signed artefacts into `public/downloads/`, then
`npm run build` and redeploy. The website is the only place customers get these
files, so the loop closes here.

---

# Pre-launch checklist

Backend:

- [ ] `python run.py preflight` passes against the production environment
- [ ] `alembic upgrade head` applied; `alembic check` clean
- [ ] `https://api.../v1/health` and `/v1/ready` both 200 over TLS
- [ ] `https://api.../docs` returns 404
- [ ] WebSocket upgrade works through the proxy on `/v1/connector`
- [ ] Proxy idle timeout ≥ 3600s; container not reachable except via the proxy
- [ ] `TALLYFLOW_SECRET_KEYS` backed up **outside** the database
- [ ] Database backups on and restore tested once
- [ ] Liveness → `/health`, readiness → `/ready` (not the other way round)
- [ ] Decided whether `/v1/auth/register` stays open

Connector:

- [ ] `DefaultBackend` in the `.iss` is your real `wss://` API URL
- [ ] Installer code-signed and timestamped
- [ ] Installed end-to-end on a clean VM with TallyPrime; pairs and reports online
- [ ] Silent-install flags verified

App:

- [ ] Built with the production `https://` API URL
- [ ] Release signing config added to `android/app/build.gradle` (currently debug)
- [ ] Android bundle signed with the release key; iOS profile valid
- [ ] Flutter web origin (if used) present in `TALLYFLOW_CORS_ORIGINS`

Website:

- [ ] `site.js` report list still matches the app; `Security.jsx` claims still true
- [ ] `/v1/public/stats` answers from the site's own hostname, and you are content
      for those four counts to be public
- [ ] `downloads.js` versions, sizes and checksums match the uploaded files
- [ ] `/downloads/…` URLs return the files, not HTML
- [ ] `index.html` served with no-cache; hashed assets cached long
- [ ] Support URL in the installer resolves to a real page

---

# Known issues to resolve before going public

**`apps/connector.json` is committed to the repository and contains a live
connector id and pairing secret.** `.gitignore` excludes `connector.local.json`
but not `connector.json`, so the credential is in git history. Before the repo is
shared or made public: delete that connector through the API
(`DELETE /v1/connectors/{connector_id}`), add `apps/connector.json` to
`.gitignore`, and treat the secret as burned. Pairing secrets are the credential
a connector authenticates with — a leaked one is a machine that can impersonate a
customer's connector.

**Placeholder pricing is live in the site source.** See B1. It renders as real
figures to anyone who loads the page.

**Android release builds are debug-signed.** See C3. The store upload will fail
until a release signing config exists.

---

# Routine operations

| Task | Command |
|---|---|
| Deploy a new backend version | `run.py image <tag>` → push → migrate → restart |
| Apply migrations | `run.py migrate` with prod `TALLYFLOW_DATABASE_URL` |
| Check environment before deploying | `run.py preflight` |
| See connectors on an instance | `GET /v1/fleet` (per-instance, not fleet-wide) |
| Release a connector build | bump `__version__` → `run.py connector` → sign → B3 |
| Release the app | `run.py release https://api...` → sign → stores |
| Rebuild the site | `npm ci && npm run build` in `apps/website` |

Watch, at minimum: `/v1/ready` per instance, connector count from `/v1/fleet`
across instances, Postgres connection pool saturation, and the auth rate-limit
rejection rate. In a read-only accounting product the sensitive act is the read,
and reads are already audited server-side — make sure those logs are retained
somewhere you can query.
