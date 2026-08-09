# Published artefacts

Caddy serves this directory at `https://<UAT_DOMAIN>/downloads/`.

Drop exactly the two files the website links to:

| File | Built by |
|---|---|
| `TallyFlowConnector-Setup-<version>.exe` | `python run.py connector` (Windows) |
| `TallyFlow-<version>.apk`                | `python run.py release https://<UAT_DOMAIN>` |

The names must match `apps/website/src/data/downloads.js`, and the site must be
rebuilt after changing the versions there.

These binaries **are committed**, so `git pull` on the UAT host publishes them
along with the code. That keeps the deploy to one command, at roughly 60 MB per
release in git history that git cannot later reclaim. Worth revisiting if the
repo gets heavy -- GitHub Releases or an object store are the usual answer.
