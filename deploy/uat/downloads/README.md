# Published artefacts

Caddy serves this directory at `https://<UAT_DOMAIN>/downloads/`.

| File | Written by |
|---|---|
| `TallyFlowConnector-Setup-<version>.exe` | `python run.py connector` (Windows) |
| `TallyFlow-<version>.apk`                | `python run.py release https://<UAT_DOMAIN>` |
| `manifest.json`                          | `python run.py publish` (both commands call it) |

Both build commands stage their artefact here and regenerate the manifest, so
the normal path needs no copying by hand.

## manifest.json is what makes an update live

Publishing a release *is* writing this file; until it names the new version,
nothing updates.

Nothing polls it any more. The **API reads it** (compose mounts this directory
into the `api` container read-only) and answers the version question on traffic
that already exists:

| Client | How it learns | Delay |
|---|---|---|
| Phone | `X-Latest-App-Version` / `X-Min-App-Version` on every API response; `426` when below the floor | one request |
| Connector | `latest_connector_version` on every WebSocket frame, plus an explicit `update` command | one heartbeat |

The connector keeps a six-hourly manifest poll as a fallback, for the case that
matters most: a connector that cannot reach the backend at all is exactly the one
that may need a new build, and it will never be told.

So a release reaches the fleet in seconds. The flip side is that a mistake does
too — see the kill switches in `uat.env.example`.

It is generated, never edited. The `sha256` in it is measured from the exact
bytes being served, and both clients refuse to install anything that does not
match — so a hand-typed hash produces an update the whole fleet rejects. What a
human does control lives in `deploy/release-policy.json`: whether a release is
mandatory, the version below which clients stop working, and the release note.
Change that, then re-run `python run.py publish`.

**Deploy the manifest together with the artefacts it names.** A manifest that
arrives first advertises a download that 404s; one that arrives late means
nobody updates. `git pull` on the UAT host does both at once, which is the
reason these files are committed.

The names must also match `apps/website/src/data/downloads.js`, and the site
must be rebuilt after changing the versions there.

These binaries **are committed**, so `git pull` on the UAT host publishes them
along with the code. That keeps the deploy to one command, at roughly 60 MB per
release in git history that git cannot later reclaim. Worth revisiting if the
repo gets heavy -- GitHub Releases or an object store are the usual answer.
