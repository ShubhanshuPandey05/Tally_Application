# TallyFlow marketing site

The public site: what the product is, how it works, and where customers get the
two things they need — the **Windows connector** and the **mobile app**.

React 18 + Vite, no UI framework and no runtime dependencies beyond React. The
visual language follows the app (`apps/mobile/lib/src/app/theme.dart`): indigo
`#3A5AF0` as the anchor, tabular figures on every amount, Indian digit grouping.

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
  App.jsx                 section order
  hooks.js                scroll reveal, count-up, pointer glow, INR formatting
  styles/base.css         design tokens, buttons, cards, reveal, ambience
  data/pricing.js         PLACEHOLDER plans + FAQ
  data/downloads.js       installer/app artefact names, versions, setup steps
  components/*.jsx|.css   one section per file, styles beside the component
```

## The two things you will want to change

**Pricing is fake.** `src/data/pricing.js` holds placeholder figures so the
section could be designed and reviewed — nothing there is commercially agreed.
When billing is real, replace that module (or fetch the same shape from the
backend); no component reads a price from anywhere else.

**Download URLs are placeholders.** `src/data/downloads.js` points at
`/downloads/…` with the filenames the build actually produces:

| Artefact | Produced by |
|---|---|
| `TallyFlowConnector-Setup-<version>.exe` | `python run.py connector` → `apps\connector\dist\installer\` |
| Android/iOS bundles | `python run.py release <api-url>` → `apps\mobile\build\` |

Publishing is copying those files to `/downloads` on the host or CDN and
updating the version, size and checksum constants in that file. The store
buttons are `href: '#'` until the listings exist.

## Notes

- Everything animates through CSS; JS only toggles classes and drives the
  count-up. `prefers-reduced-motion` disables the lot in `base.css`.
- Copy is deliberately specific — read-only enforcement, outbound-only
  connections, freshness stamps, per-branch connector status. Those are the
  claims that distinguish this from a remote-desktop app, and each one is true
  of the shipped code. Keep them accurate if the backend changes.
