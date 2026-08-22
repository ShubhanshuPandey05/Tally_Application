# The mark

`TallyFlow` set in Caveat, stacked — `Tally` with `Flow` beneath it — inside a
rounded tile. One drawing, generated into every size and format the platforms
ask for.

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
| Website | `public/favicon.svg`, `public/apple-touch-icon.png` | white tile, black mark (the touch icon is the app's black) |
| Android | `mipmap-*/ic_launcher.png`, `mipmap-*/ic_launcher_foreground.png` | black tile, white mark |
| iOS | `AppIcon.appiconset/*` | black, full-bleed square |
| Flutter web | `web/favicon.png`, `web/icons/*` | black tile, white mark |

The two colourways are not interchangeable. The **app** wears the black tile: on
a home screen full of colour, a black square reads as a piece of software rather
than a sticker. The **website favicon** wears the white one, because a browser
tab strip is already white and a black square there reads as a hole punched in
it.

## Two constraints, both deliberate

**The letterforms are baked as paths, not set in a font.** A favicon cannot
load a webfont, and the app is built to keep working when the shop's connection
is down — a logo that renders in a fallback face on exactly those days is a logo
that fails when it is noticed. `mark.py` shapes the two words through HarfBuzz
(Caveat is a connected script and leans on its kerning) and emits outlines.

**It is not TallyPrime's mark.** No script-over-swoosh lockup, no second word
tucked under a tail, and the site says plainly that TallyFlow is not affiliated
with Tally Solutions. If somebody asks for the logo to be brought closer to
Tally's, that is the thing they are asking for and the answer is no.

`Caveat-Bold.ttf` ships under the SIL Open Font License; the licence travels
with it in `apps/mobile/assets/fonts/Caveat-OFL.txt`.

## Files

- `mark.py` — the two words, shaped and converted to SVG path data
- `icon.py` — layout, the tile, and the five colourway/geometry variants
- `raster.py` — SVG to PNG at an exact size, via headless Chrome
- `generate.py` — writes every output into place
