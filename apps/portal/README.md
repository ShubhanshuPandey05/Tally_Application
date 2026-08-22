# TallyFlow Management Portal

The internal console: who is waiting on a decision, what each account is
entitled to, and — when a customer rings up — what their Tally PC has actually
been saying.

React 18 + Vite. No state library, no UI kit, no CSS framework. It is used at a
desk by a handful of people, and the whole thing is small enough that adding
those would be more machinery than the screens it renders.

## Running it

```powershell
python run.py dev       # backend on 127.0.0.1:8000
python run.py portal    # this app on localhost:5174/portal
```

Two processes, because the backend does not serve this app. Vite proxies `/v1`
to the backend so the browser sees **one origin** in development, exactly as it
does in production — the portal token carries platform-wide authority, and the
only CORS behaviour worth exercising is the one that ships.

Sign in with `TALLYFLOW_PORTAL_BOOTSTRAP_EMAIL` from `apps/backend/.env`
(`run.py init` generates the password and prints it). The first sign-in forces a
password change, because that value lives in a deployment manifest.

## How it is served

Caddy, from disk, on the same hostname as the API — built into the image by
`deploy/uat/web.Dockerfile` and routed by `handle_path /portal*` in the
Caddyfile.

Three values have to agree, and the failure when they do not is a page that
renders once and 404s on the first navigation:

| Where | Value |
|---|---|
| `vite.config.js` | `base: '/portal/'` |
| `src/main.jsx` | `basename="/portal"` |
| `deploy/uat/Caddyfile` | `handle_path /portal*` → `/srv/portal` |

## Layout

```
src/
  api.js            fetch wrapper, token store, SSE reader
  format.js         dates, statuses, levels
  App.jsx           session gates and routing
  layout/Shell      sidebar, top bar, theme
  components/
    ui.jsx          toasts, pills, modal, useLoad
    LogView.jsx     the log reader, shared by both log screens
  pages/
    Overview        counters and the pending queue
    Accounts        the list, and AccountDrawer for one account
    Partners        portal access (owner only)
    BackendLogs     the API's own log (owner only)
    ConnectorLogs   what a customer's Tally PC reported
    Activity        the audit trail
    Settings        your own account
```

## Two things worth knowing before changing it

**The portal never shows a financial figure.** It counts how much of the product
an account uses — people, companies, Tally PCs. What is in anybody's books is
not a subscription question, and no endpoint it calls reaches a customer's data.
Adding one would be a change in what this tool *is*.

**Colour encodes attention.** Amber appears in exactly one circumstance —
something waiting on a human — and nowhere else. The support view extends the
same rule to `ERROR` in red. If every state gets a colour, none of them is a
signal, and the queue nobody can afford to miss stops being visible from across
the room.
