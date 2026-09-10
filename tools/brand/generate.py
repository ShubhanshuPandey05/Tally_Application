"""Write the TallyFlow icon into every place a platform looks for one.

    python tools/brand/generate.py

Needs `pip install -r tools/brand/requirements.txt` and a Chrome on the machine.
Deliberately not wired into `run.py check`: this runs when the logo changes,
which is roughly never, and it should not put a browser launch in the path of
every build.

Which colourway goes where is the decision worth remembering. The **app** wears
the black tile -- on a home screen full of colour a black square is the one that
reads as a piece of software rather than a sticker. The **website favicon**
wears the white one, because a browser tab strip is already white and a black
square there reads as a hole.
"""

from __future__ import annotations

import json
from pathlib import Path

import icon
import raster
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
MOBILE = ROOT / "apps/mobile"
WEBSITE = ROOT / "apps/website"
ANDROID = MOBILE / "android/app/src/main/res"
IOS = MOBILE / "ios/Runner/Assets.xcassets/AppIcon.appiconset"
#: The connector's window, which is a Windows target of the mobile package.
WINDOWS_ICON = MOBILE / "windows/runner/resources/app_icon.ico"

#: What Windows asks an .ico for. Explorer, the taskbar and Alt-Tab each pick a
#: different one, so a file carrying only a large image is resampled by the
#: shell at exactly the sizes where the mark has least room.
WINDOWS_SIZES = (16, 24, 32, 48, 64, 128, 256)

# Android density buckets, as multiples of the baseline. Legacy launcher icons
# are 48dp; the adaptive-icon layers are 108dp.
DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}


def _ios_jobs(square: str) -> list[tuple[str, Path, int]]:
    """Every size Xcode's own asset catalogue asks for, read from the catalogue."""
    contents = json.loads((IOS / "Contents.json").read_text(encoding="utf-8"))
    jobs = []
    for image in contents["images"]:
        if "filename" not in image:
            continue
        side = float(image["size"].split("x")[0]) * float(image["scale"].rstrip("x"))
        jobs.append((square, IOS / image["filename"], round(side)))
    return jobs


def windows_icon(app: str) -> None:
    """Pack the app tile into the .ico the connector's window is built with.

    Every size is drawn rather than downsampled from one large image. The mark
    falls back to a stacked monogram in a small square, and letting the shell
    resample a 256px wordmark down to 16px produces a grey smudge instead --
    which is the size the taskbar and Alt-Tab actually use.

    The **app** colourway, not the favicon's: this is a program on a taskbar
    beside other programs, the same place the phone app's black tile earns its
    keep, and a white square there reads as a missing image.
    """
    scratch = WINDOWS_ICON.parent / "_ico"
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        frames = [(app, scratch / f"{size}.png", size) for size in WINDOWS_SIZES]
        raster.render(frames)

        images = [Image.open(path).convert("RGBA") for _, path, _ in frames]
        try:
            # Largest first: Pillow writes the base image plus append_images,
            # and a reader that ignores the directory order takes the first.
            images[-1].save(
                WINDOWS_ICON,
                format="ICO",
                sizes=[(size, size) for size in WINDOWS_SIZES],
                append_images=images[:-1],
            )
        finally:
            for image in images:
                image.close()
        print(f"  {len(WINDOWS_SIZES):>4} sz  {WINDOWS_ICON.relative_to(ROOT)}")
    finally:
        for leftover in scratch.glob("*.png"):
            leftover.unlink()
        scratch.rmdir()


def _flatten(paths) -> None:
    """Drop the alpha channel.

    Apple rejects an app icon that has one, and these are fully opaque already
    -- the channel is there only because the renderer always writes RGBA. A
    build that fails at submission over this is a slow way to find out.
    """
    for path in paths:
        with Image.open(path) as image:
            image.convert("RGB").save(path)


def main() -> None:
    favicon = icon.build("favicon")
    app = icon.build("app")
    square = icon.build("app-square")
    maskable = icon.build("app-maskable")
    foreground = icon.build("adaptive-fg")

    # The website's favicon stays vector: it is served over HTTP to a browser
    # that renders SVG, so there is no reason to ship it a bitmap of a drawing.
    (WEBSITE / "public/favicon.svg").write_text(favicon, encoding="utf-8")

    jobs: list[tuple[str, Path, int]] = [
        # Safari ignores an SVG favicon, and this is also the tile somebody gets
        # if they add the site to an iOS home screen -- so it is the app's black.
        # Square, because iOS rounds the corners itself and fills transparency
        # with black anyway, which would leave a rounded tile inside a square.
        (square, WEBSITE / "public/apple-touch-icon.png", 180),
        (app, MOBILE / "web/favicon.png", 32),
        (app, MOBILE / "web/icons/Icon-192.png", 192),
        (app, MOBILE / "web/icons/Icon-512.png", 512),
        (maskable, MOBILE / "web/icons/Icon-maskable-192.png", 192),
        (maskable, MOBILE / "web/icons/Icon-maskable-512.png", 512),
    ]
    for bucket, factor in DENSITIES.items():
        jobs.append((app, ANDROID / f"mipmap-{bucket}/ic_launcher.png", round(48 * factor)))
        jobs.append(
            (foreground, ANDROID / f"mipmap-{bucket}/ic_launcher_foreground.png",
             round(108 * factor))
        )
    jobs += _ios_jobs(square)

    raster.render(jobs)
    _flatten(dest for _, dest, _ in _ios_jobs(square))
    _flatten([WEBSITE / "public/apple-touch-icon.png"])
    windows_icon(app)

    for _, dest, size in jobs:
        print(f"  {size:>4}px  {dest.relative_to(ROOT)}")
    print(f"  vector  {(WEBSITE / 'public/favicon.svg').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
