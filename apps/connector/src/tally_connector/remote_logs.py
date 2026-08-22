"""Ships the connector's log to the backend so support can read it remotely.

The connector runs unattended on a shop's PC. When something goes wrong the
person who can fix it is not in the building, and the file in
``%PROGRAMDATA%\\TallyFlow\\logs`` might as well not exist -- asking a shop owner
to find it, zip it and email it is a support call that ends in "never mind".
So the log comes to us.

Three properties matter, and they are all about *not* making a bad situation
worse:

**Logging must never block the connector.** :meth:`RemoteLogHandler.emit` does
nothing but append to a bounded deque. It never touches the socket, never
awaits, and never raises -- a handler that can fail is a handler that turns a
logged warning into a crash.

**The buffer is bounded and lossy at the tail.** When the backend is
unreachable, log lines keep being produced and nothing drains them. A queue that
grew without limit would turn an internet outage into an out-of-memory kill on a
customer's till. So the oldest lines are dropped and *counted*: the count rides
along in the next batch, because a support view that cannot tell "quiet period"
from "we lost this part" sends somebody down the wrong path.

**It must not feed itself.** Shipping logs produces log lines of its own, which
would be shipped, which would produce more. Records from this module are dropped
at the handler, so the loop cannot close.

Everything is redacted by the same filter the file log uses -- see
:mod:`.logging_setup`. Accounting data never appears in a log line in the first
place, and the pairing secret is masked before it can reach a handler at all.
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import UTC, datetime

from .protocol import MAX_LOG_ENTRIES_PER_BATCH, MAX_LOG_MESSAGE_CHARS, LogEntry

#: Loggers whose records are never shipped. ``websockets`` is already capped at
#: WARNING, but a TLS or framing error there fires per frame and would arrive as
#: a self-sustaining flood on exactly the connection we are trying to use.
_NEVER_SHIP = (__name__, "websockets")


class RemoteLogHandler(logging.Handler):
    """Buffers formatted log records for the session to ship.

    Deliberately not a :class:`logging.handlers.QueueHandler`: that pairs with a
    listener thread, and this connector already has an event loop whose session
    task is the only thing that may touch the socket. A plain deque is the
    smaller mechanism -- ``append`` and ``popleft`` on a bounded deque are atomic
    under the GIL, so the logging side needs no lock and the draining side needs
    no thread.
    """

    def __init__(self, *, capacity: int = 2000, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._buffer: deque[LogEntry] = deque(maxlen=capacity)
        self._dropped = 0

    @property
    def dropped(self) -> int:
        """Lines discarded because the buffer was full and nothing drained it."""
        return self._dropped

    def emit(self, record: logging.LogRecord) -> None:
        # No I/O, no awaits, no exceptions. Anything raised here would surface
        # at the call site of an ordinary logger.info(), which is the last place
        # a caller is prepared to handle an error.
        try:
            if record.name.startswith(_NEVER_SHIP):
                return
            if len(self._buffer) == self._buffer.maxlen:
                # deque discards the oldest itself; count it so the batch can
                # say so rather than presenting a gap as silence.
                self._dropped += 1
            self._buffer.append(
                LogEntry(
                    logged_at=datetime.fromtimestamp(record.created, tz=UTC),
                    level=record.levelname,
                    logger=record.name,
                    message=self.format(record)[:MAX_LOG_MESSAGE_CHARS],
                )
            )
        except Exception:  # noqa: BLE001 - see above; logging cannot be allowed to fail
            self.handleError(record)

    def drain(self, limit: int = MAX_LOG_ENTRIES_PER_BATCH) -> tuple[list[LogEntry], int]:
        """Take up to ``limit`` buffered lines plus the drop count since the last drain.

        The drop count is reset here rather than on send. A batch that never
        reaches the backend loses its lines anyway, and reporting the same
        drops again on the next batch would double-count them.
        """
        taken: list[LogEntry] = []
        while self._buffer and len(taken) < limit:
            taken.append(self._buffer.popleft())
        dropped, self._dropped = self._dropped, 0
        return taken, dropped

    def __len__(self) -> int:
        return len(self._buffer)


def effective_level(root_level: str, wanted: str) -> str:
    """The strictest of the two, because the root logger filters first.

    ``logging`` checks the *logger's* level before it consults any handler, so a
    handler asking for INFO under a root set to WARNING receives nothing at all.
    That failure is silent and reads as "remote logging is broken" rather than
    as a configuration conflict, so the two settings are reconciled here: remote
    logging may be quieter than the file log, never noisier.
    """
    order = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
    root = root_level.upper()
    try:
        return wanted.upper() if order.index(wanted.upper()) >= order.index(root) else root
    except ValueError:
        return root


def install(*, level: str = "INFO", capacity: int = 2000) -> RemoteLogHandler:
    """Attach a :class:`RemoteLogHandler` to the root logger and return it.

    Called after :func:`.logging_setup.setup_logging`, which owns the formatter
    and the redaction filter. Reusing both rather than declaring its own is the
    point: the line support reads is character-for-character the line in the
    file on the customer's PC, so a support conversation and a log file cannot
    disagree about what happened.
    """
    from .logging_setup import LOG_FORMAT, RedactingFilter

    handler = RemoteLogHandler(
        capacity=capacity, level=getattr(logging, level.upper(), logging.INFO)
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(RedactingFilter())
    logging.getLogger().addHandler(handler)
    return handler
