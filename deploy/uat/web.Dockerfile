# Caddy with the download site and the management portal baked in.
#
# Both are built here rather than on the server. `apps/*/dist` is gitignored --
# correctly, a build artefact does not belong in git -- so a plain `git pull`
# brings the *source* and none of the built files. Without these stages the UAT
# host would need Node installed and a manual `npm run build` remembered on
# every deploy, and the failure when someone forgets is a blank page rather than
# an error.
#
# Two stages rather than one, because the two apps have separate lockfiles and
# separate release cadences: changing the portal must not invalidate the cached
# `npm ci` for the marketing site, and vice versa.
#
# Downloads (the installer and the APK) are deliberately NOT baked in. They are
# large, they are built on Windows, and they change on a different cadence to the
# site -- so they stay a bind mount.

FROM node:20-alpine AS site

WORKDIR /src

# Dependencies first: this layer is cached unless the lockfile itself changes,
# which is what keeps a redeploy from re-downloading the world.
COPY apps/website/package.json apps/website/package-lock.json ./
RUN npm ci

COPY apps/website/ ./
RUN npm run build


FROM node:20-alpine AS portal

WORKDIR /src

COPY apps/portal/package.json apps/portal/package-lock.json ./
RUN npm ci

COPY apps/portal/ ./

# Where the portal is mounted, which differs between deployments:
#
#   /portal/   deploy/uat -- one hostname serves everything, and the Caddyfile
#              strips the prefix with `handle_path /portal*`
#   /          deploy/prod -- the portal has its own hostname, so it is served
#              from the root
#
# It is a build argument rather than a constant because vite bakes it into
# every asset URL in the bundle. Get it wrong and index.html asks for asset
# paths that the server answers with somebody else's index.html and a 200 --
# the portal renders as a blank page with nothing in any log to explain it.
#
# vite.config.js reads this, and main.jsx derives the router's basename from
# vite's own `import.meta.env.BASE_URL`, so the three cannot disagree.
ARG PORTAL_BASE=/
ENV PORTAL_BASE=${PORTAL_BASE}
RUN npm run build


FROM caddy:2-alpine

# Read-only in practice: nothing at runtime writes here, and either app is
# replaced by rebuilding the image, never edited in place.
COPY --from=site /src/dist /srv/site
COPY --from=portal /src/dist /srv/portal
