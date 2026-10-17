# The mark

The name on a rounded tile: heavy **Prime** over light **Analyst** in Sora,
both stretched to one width, with the app's blue bar beneath. One drawing,
generated into every size and format the platforms ask for.

```powershell
./.venv/Scripts/python.exe -m pip install -r tools/brand/requirements.txt
./.venv/Scripts/python.exe tools/brand/generate.py
```

Chrome must be installed; `raster.py` drives a headless one to turn the SVG into
PNGs. Nothing here is part of `run.py check` — the logo changes roughly never,
and a browser launch does not belong in the path of every build.

## What comes out

| Where | Files | Colourway |
|---|---|---|
| Website, portal | `public/favicon.svg` | white tile, ink letters, blue bar |
| Website | `public/apple-touch-icon.png` | the app's black, square |
| Android | `mipmap-*/ic_launcher.png`, `mipmap-*/ic_launcher_foreground.png` | black tile, white letters, light blue bar |
| iOS | `AppIcon.appiconset/*` | black, full-bleed square |
| Flutter web | `web/favicon.png`, `web/icons/*` | black tile |
| Connector window | `windows/runner/resources/app_icon.ico` | black tile |
| App (`BrandMark`) | `lib/src/core/widgets/brand_outlines.dart` | outlines, painted in the app |

The two colourways are not interchangeable. The **app** wears the black tile: on
a home screen full of colour, a black square reads as a piece of software rather
than a sticker. The **website favicon** wears the white one, because a browser
tab strip is already white and a black square there reads as a hole punched in
it.

## Constraints

**Outlines, never live text.** A favicon cannot load a font, and the app is
built to keep working when the shop's connection is down. `wordmark.py` reads
`fonts/Sora[wght].ttf` (SIL Open Font License, `fonts/OFL.txt`) at build time,
instances it at weights 800 and 300, and places each line the way SVG's
`textLength` with `spacingAndGlyphs` would. Nothing downstream needs the font.

**The icons a launcher crops are fitted, not shrunk by a guess.** The Android
adaptive foreground and the maskable web icon scale the name until the corners
of its block sit inside the circle that survives every mask.

**It is not TallyPrime's mark.** The name shares a word with theirs, and the
site says plainly that Prime Analyst is not affiliated with Tally Solutions. If
somebody asks for the logo to be brought closer to Tally's, that is the thing
they are asking for and the answer is no.

**The app draws the same shapes.** `BrandMark` in
`apps/mobile/lib/src/core/widgets/primitives.dart` paints the generated
`brand_outlines.dart`. Regenerate; never edit that file by hand.

## Files

- `wordmark.py` — the name as outlines, and the bar
- `icon.py` — the tile and the five colourway/geometry variants
- `raster.py` — SVG to PNG at an exact size, via headless Chrome
- `generate.py` — writes every output into place
