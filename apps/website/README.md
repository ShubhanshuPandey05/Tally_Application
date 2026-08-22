# TallyFlow marketing site

The public site: what the product is, how it works, where to get the two things
a customer installs, and — at `/docs` — how to set them up.

React 18 + Vite, no UI framework and no runtime dependency beyond React itself.

```powershell
cd apps\website
npm install
npm run dev      # http://localhost:5173
npm run build    # -> dist/
npm run preview  # serve the build
```

## Layout

```
src/
  App.jsx                 chrome + which page
  router.jsx              two routes, thirty lines, no dependency
  hooks.js                scroll state, release manifest, live stats, formatting
  styles/base.css         tokens, buttons, panels, section headings
  data/site.js            contact address, the report list
  data/downloads.js       fallback release facts + the three setup steps
  pages/Home.jsx          the pitch
  pages/Docs.jsx          the setup guide
  components/*.jsx|.css   one section per file, styles beside the component
```

`/docs` is a client-side route. Caddy serves `index.html` for any unmatched
path, so a refresh or a shared link resolves; a static host without that
fallback would 404 it.

## Everything on the page has to be true

This site was rewritten once because it was not. It claimed seventeen reports
(there are ten), eighteen dashboard widgets, a dashboard that opens in 0.4
seconds, store listings that did not exist, and three price tiers nobody had
agreed to. Marketing copy that outruns the build is not optimism; it is the
first support ticket.

Two rules keep it honest:

**Release facts come from the manifest, not from a person.** `useManifest()`
fetches `/downloads/manifest.json` — the file `python run.py publish` generates
by measuring the bytes actually being served — and takes version, size and
SHA-256 from it. `PUBLISHED` in `data/downloads.js` is only the fallback for
before that resolves and for a dev server with no manifest, and it describes the
**last published build**, which is not necessarily the version in the source
tree. If a fetch fails the page still offers the download; it just stops
claiming a version.

**The hero's figures come from the database.** `useStats()` fetches
`/v1/public/stats` — four aggregate counts, no names, no identifiers. `null` and
`0` are kept apart deliberately: zero businesses is a fact worth printing, and
"we could not ask" is not, so a failed or disabled endpoint drops the row rather
than falling back to zeros. In production Caddy serves the site and the API from
one hostname so the request is same-origin; `vite.config.js` proxies `/v1` in
development so that stays true there. Turn the endpoint off with
`TALLYFLOW_PUBLIC_STATS_ENABLED=false` and the row disappears with no rebuild.

**Product claims match the shipped app.** The report list in `data/site.js` is
the app's report index. The security section states only what the code does. If
one of those stops being true, it comes off this page in the same change — a
claim that has quietly drifted is worse than no claim.

## Design

White page, soft grey for anything that *contains* something rather than being
content, one near-black card per screen, and a single blue for action. Inter
throughout; IBM Plex Mono only for filenames, checksums and commands, where
monospace is information rather than style. The same tokens exist in the Flutter
app (`apps/mobile/lib/src/app/theme.dart`) — change one and change the other,
because a customer sees both in the same afternoon.

**The logo** is the name set in Caveat over a straight rule, on a tile — white
on black, or black on white via `<Logo light />`. Two constraints produced it
and both are worth keeping:

- The mark before it (bars in a squircle) was the shape half the analytics
  industry already uses, and a logo that makes a reader think of another product
  is doing the opposite of its job.
- It is deliberately **not** a version of TallyPrime's own logo. Theirs is a
  script "Tally" over a swoosh over a plain second word; rebuilding that lockup
  with "Flow" in place of "Prime" would produce a mark readers would reasonably
  take for an official Tally Solutions product, which is the exact claim the
  footer on this site disclaims. Hence a straight rule rather than a swoosh, one
  handwriting face throughout rather than a script word above a typeset one, and
  letterforms that look nothing like theirs.

There is a second, **stacked** form — `Tally` with `Flow` beneath it — for
square icons, where the name across a 32px tile gives each letter about two
pixels. It is not a component: an icon cannot load a webfont, so it lives as
outlines in `tools/brand` and is generated into `public/favicon.svg`,
`public/apple-touch-icon.png` and the app's launcher icons. **Both files in
`public/` are generated — edit `tools/brand` and re-run it, never the output.**

The favicon wears the white tile and the app wears the black one, and they are
not interchangeable: a browser tab strip is already white, so a black square
there reads as a hole punched in it, while on a home screen full of colour the
black square is the one that reads as a piece of software.

The same face is bundled with the Flutter app (`assets/fonts/Caveat-Bold.ttf`,
SIL OFL) rather than fetched at runtime, so the mark does not fall back to a
system script on exactly the days the shop's connection is down. No gradients,
glows, orbs, emoji or scroll animation: an accounting tool is bought on trust,
and the visual language of a template landing page works against that.

## Not on the page

- **Pricing.** There is no published price list and no self-service billing —
  accounts are activated by hand in the management portal. The site says that
  instead of quoting figures.
- **Store buttons.** The app is not on Google Play and there is no iOS build.
  The APK is downloaded directly; the iPhone button is a disabled "coming
  later".
