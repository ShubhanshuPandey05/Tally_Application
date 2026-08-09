# Caddy with the download site baked in.
#
# The site is built here rather than on the server. `apps/website/dist` is
# gitignored -- correctly, a build artefact does not belong in git -- so a plain
# `git pull` brings the *source* of the site and none of the built files. Without
# this stage the UAT host would need Node installed and a manual `npm run build`
# remembered on every deploy, and the failure when someone forgets is a blank
# page rather than an error.
#
# Downloads (the installer and the APK) are deliberately NOT baked in. They are
# large, they are built on Windows, and they change on a different cadence to the
# site -- so they stay a bind mount.

FROM node:20-alpine AS build

WORKDIR /src

# Dependencies first: this layer is cached unless the lockfile itself changes,
# which is what keeps a redeploy from re-downloading the world.
COPY apps/website/package.json apps/website/package-lock.json ./
RUN npm ci

COPY apps/website/ ./
RUN npm run build


FROM caddy:2-alpine

# Read-only in practice: nothing at runtime writes here, and the site is
# replaced by rebuilding the image, never edited in place.
COPY --from=build /src/dist /srv/site
