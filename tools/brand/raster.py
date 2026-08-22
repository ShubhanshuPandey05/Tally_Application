"""Rasterise an SVG at an exact pixel size, using the Chrome that is already here.

Every alternative wants a native rendering library compiled for this machine.
The browser is a correct SVG renderer that is already installed on any machine
that can look at the website, and this only has to run when the logo changes.
"""

from __future__ import annotations

import asyncio
import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websockets

CHROME_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
PORT = 9334
PROFILE = Path(__file__).with_name(".chrome-profile")

PAGE = """<!doctype html><meta charset=utf-8>
<style>html,body{{margin:0;padding:0;background:transparent}}
img{{display:block;width:{n}px;height:{n}px}}</style>
<img src="data:image/svg+xml;base64,{b64}">"""


def _chrome() -> str:
    for path in CHROME_CANDIDATES:
        if Path(path).exists():
            return path
    raise SystemExit("Chrome not found; edit CHROME_CANDIDATES")


def _target() -> str:
    """Start a headless Chrome if none is listening, and return its page socket."""
    for attempt in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=1) as r:
                pages = [t for t in json.load(r) if t["type"] == "page"]
            if pages:
                return pages[0]["webSocketDebuggerUrl"]
        except Exception:
            if attempt == 0:
                PROFILE.mkdir(exist_ok=True)
                subprocess.Popen(
                    [
                        _chrome(), "--headless=new", "--disable-gpu", "--hide-scrollbars",
                        f"--remote-debugging-port={PORT}", f"--user-data-dir={PROFILE}",
                        "--no-first-run", "--no-default-browser-check", "about:blank",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            time.sleep(0.5)
    raise SystemExit("Chrome did not start a debuggable page")


async def _run(jobs: list[tuple[str, Path, int]]) -> None:
    async with websockets.connect(_target(), max_size=64 * 1024 * 1024) as ws:
        n = 0

        async def send(method: str, **params):
            nonlocal n
            n += 1
            await ws.send(json.dumps({"id": n, "method": method, "params": params}))
            while True:
                message = json.loads(await ws.recv())
                if message.get("id") == n:
                    return message.get("result", {})

        await send("Page.enable")
        # Without this the compositor paints an opaque white page behind the
        # capture and every transparent icon comes out a solid white square --
        # which on Android is an adaptive foreground that hides the mark
        # completely, and looks fine in a file listing while doing it.
        await send(
            "Emulation.setDefaultBackgroundColorOverride",
            color={"r": 0, "g": 0, "b": 0, "a": 0},
        )
        for source, dest, size in jobs:
            b64 = base64.b64encode(source.encode()).decode()
            html = base64.b64encode(PAGE.format(n=size, b64=b64).encode()).decode()
            await send(
                "Emulation.setDeviceMetricsOverride",
                width=size, height=size, deviceScaleFactor=1, mobile=False,
            )
            await send("Page.navigate", url="data:text/html;base64," + html)
            await asyncio.sleep(0.3)
            shot = await send(
                "Page.captureScreenshot", format="png",
                captureBeyondViewport=False, fromSurface=True,
            )
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(base64.b64decode(shot["data"]))


def render(jobs: list[tuple[str, Path, int]]) -> None:
    """Write each (svg source, destination, pixel size) to disk."""
    asyncio.run(_run(jobs))
