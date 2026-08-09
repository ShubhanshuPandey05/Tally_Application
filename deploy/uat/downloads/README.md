# Published artefacts

Caddy serves this directory at `https://<UAT_DOMAIN>/downloads/`.

Drop exactly the two files the website links to:

| File | Built by |
|---|---|
| `TallyFlowConnector-Setup-<version>.exe` | `python run.py connector` (Windows) |
| `TallyFlow-<version>.apk`                | `python run.py release https://<UAT_DOMAIN>` |

The names must match `apps/website/src/data/downloads.js`, and the site must be
rebuilt after changing the versions there.

Binaries are gitignored: a repository is not an artefact store, and an installer
carrying a pairing wizard is not something to hand out from a git clone.
