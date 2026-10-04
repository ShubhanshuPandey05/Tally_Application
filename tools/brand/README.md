# The mark

One circle cut in two, the halves slipped against each other, inside a rounded
tile: double entry drawn as a shape. One drawing, generated into every size and
format the platforms ask for.

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
| Website | `public/favicon.svg`, `public/apple-touch-icon.png` | white tile, ink and blue halves (the touch icon is the app's black) |
| Android | `mipmap-*/ic_launcher.png`, `mipmap-*/ic_launcher_foreground.png` | black tile, white and blue halves |
| iOS | `AppIcon.appiconset/*` | black, full-bleed square |
| Flutter web | `web/favicon.png`, `web/icons/*` | black tile, white and blue halves |

The two colourways are not interchangeable. The **app** wears the black tile: on
a home screen full of colour, a black square reads as a piece of software rather
than a sticker. The **website favicon** wears the white one, because a browser
tab strip is already white and a black square there reads as a hole punched in
it.

## Two constraints, both deliberate

**There is no lettering in it, and so no font.** A favicon cannot load one, and
the app is built to keep working when the shop's connection is down. The name
was once set inside the tile; thirteen letters across 32px gave each one barely
more than a pixel. Two arcs read at 16px.

**It is not TallyPrime's mark.** The name shares a word with theirs, and a
symbol with no "Prime" in it cannot be taken for their lockup. The site says
plainly that Prime Analyst is not affiliated with Tally Solutions. If somebody
asks for the logo to be brought closer to Tally's, that is the thing they are
asking for and the answer is no.

**The app draws the same numbers.** `BrandMark` in
`apps/mobile/lib/src/core/widgets/primitives.dart` paints the two halves from
the constants in `icon.py`. Change both or neither.

## Files

- `icon.py` — layout, the tile, and the five colourway/geometry variants
- `raster.py` — SVG to PNG at an exact size, via headless Chrome
- `generate.py` — writes every output into place
