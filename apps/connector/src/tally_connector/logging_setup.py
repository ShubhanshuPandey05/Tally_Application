"""Logging configuration for the connector.

The connector runs unattended on someone else's PC, so logs are the only
diagnostic channel. They rotate (a wedged connector must not fill a shop's C:
drive) and they must never contain accounting data or the pairing secret.
"""

from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)-28s %(message)s"
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3

#: Anything that looks like a pairing secret or bearer token gets masked before
#: it can reach a support bundle.
_SECRET_PATTERNS = [
    re.compile(r"(connector_secret[\"']?\s*[:=]\s*[\"']?)([^\s\"',}]+)", re.IGNORECASE),
    re.compile(r"(signature[\"']?\s*[:=]\s*[\"']?)([0-9a-f]{32,})", re.IGNORECASE),
    re.compile(r"(Bearer\s+)(\S+)", re.IGNORECASE),
]


class RedactingFilter(logging.Filter):
    """Masks credentials in formatted log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub(r"\1***redacted***", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(level: str = "INFO", log_dir: Path | None = None) -> None:
    """Install console logging, plus rotating file logging when a dir is given."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    for existing in list(root.handlers):
        root.removeHandler(existing)

    formatter = logging.Formatter(LOG_FORMAT)
    redactor = RedactingFilter()

    # A windowed PyInstaller build has no console, and sets both streams to
    # None. StreamHandler would happily accept that and then swallow an
    # AttributeError on every single record.
    stream = sys.stdout or sys.stderr
    if stream is not None:
        console = logging.StreamHandler(stream)
        console.setFormatter(formatter)
        console.addFilter(redactor)
        root.addHandler(console)

    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_dir / "connector.log",
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.addFilter(redactor)
        root.addHandler(file_handler)

    # websockets logs every frame at DEBUG, which would include report payloads.
    logging.getLogger("websockets").setLevel(logging.WARNING)
