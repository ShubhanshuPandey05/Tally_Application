"""The connector's remote log buffer.

Every test here is about the buffer being unable to hurt the connector: logging
must not block, must not raise, and must not grow without limit when the backend
is unreachable -- which is the exact moment log lines keep being produced and
nothing drains them.
"""

from __future__ import annotations

import logging

import pytest

from tally_connector import remote_logs
from tally_connector.protocol import MAX_LOG_MESSAGE_CHARS


@pytest.fixture
def handler():
    """A handler attached to the root logger, removed however the test ends.

    The root level is lowered too, because ``logging`` filters at the logger
    before it consults any handler -- which is the same interaction
    ``remote_logs.effective_level`` exists to reconcile in the real connector.
    """
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.INFO)
    installed = remote_logs.install(level="INFO", capacity=5)
    try:
        yield installed
    finally:
        root.removeHandler(installed)
        root.setLevel(previous)


def test_remote_level_is_never_looser_than_the_file_level():
    """Otherwise the root logger filters it out and nothing ships, silently."""
    assert remote_logs.effective_level("WARNING", "INFO") == "WARNING"
    assert remote_logs.effective_level("INFO", "ERROR") == "ERROR"
    assert remote_logs.effective_level("INFO", "INFO") == "INFO"


def test_records_are_buffered_and_drained(handler):
    logging.getLogger("tally_connector.session").info("connected to backend")
    logging.getLogger("tally_connector.pipeline").error("tally refused")

    entries, dropped = handler.drain()
    assert dropped == 0
    assert [entry.level for entry in entries] == ["INFO", "ERROR"]
    assert "connected to backend" in entries[0].message
    assert entries[0].logger == "tally_connector.session"
    # Draining empties: a second drain must not re-send the same lines.
    assert handler.drain() == ([], 0)


def test_the_buffer_is_bounded_and_reports_what_it_lost(handler):
    """An outage must not become an out-of-memory kill on a shop's till."""
    for index in range(9):
        logging.getLogger("tally_connector.session").info("line %d", index)

    entries, dropped = handler.drain()
    # Capacity 5, nine lines: the oldest four go, and the count says so rather
    # than presenting the gap as a quiet period.
    assert len(entries) == 5
    assert dropped == 4
    assert "line 8" in entries[-1].message

    # The count is reset by the drain that reported it. A batch that never
    # reaches the backend loses its lines anyway; reporting the same drops
    # again next time would double-count them.
    assert handler.drain() == ([], 0)


def test_a_partial_drain_leaves_the_rest_in_order(handler):
    for index in range(4):
        logging.getLogger("tally_connector.session").info("line %d", index)

    first, _ = handler.drain(limit=2)
    second, _ = handler.drain(limit=2)
    assert [entry.message[-1] for entry in first] == ["0", "1"]
    assert [entry.message[-1] for entry in second] == ["2", "3"]


def test_the_shipper_never_ships_its_own_chatter(handler):
    """Shipping logs produces log lines; without this the loop cannot close."""
    logging.getLogger(remote_logs.__name__).error("could not ship log batch")
    logging.getLogger("websockets.client").warning("frame error")
    logging.getLogger("tally_connector.session").info("kept")

    entries, _ = handler.drain()
    assert [entry.logger for entry in entries] == ["tally_connector.session"]


def test_lines_below_the_level_are_not_buffered(handler):
    logging.getLogger("tally_connector.session").debug("noisy detail")
    assert handler.drain() == ([], 0)


def test_a_huge_line_is_truncated_rather_than_shipped(handler):
    logging.getLogger("tally_connector.session").info("x" * 50_000)
    entries, _ = handler.drain()
    assert len(entries[0].message) == MAX_LOG_MESSAGE_CHARS


def test_credentials_are_redacted_before_they_can_leave_the_machine(handler):
    """The same filter the file log uses -- see logging_setup.RedactingFilter."""
    logging.getLogger("tally_connector.session").info(
        'connector_secret="hunter2-and-then-some" signature=%s', "a" * 64
    )
    entries, _ = handler.drain()
    assert "hunter2" not in entries[0].message
    assert "redacted" in entries[0].message


def test_emit_never_raises_out_of_a_logging_call(handler, monkeypatch):
    """A handler that can fail turns a logged warning into a crash."""

    def explode(record):  # noqa: ANN001, ANN202
        raise RuntimeError("formatter is broken")

    monkeypatch.setattr(handler, "format", explode)
    monkeypatch.setattr(handler, "handleError", lambda record: None)

    # Would propagate out of the caller's `logger.info` without the guard.
    logging.getLogger("tally_connector.session").info("anything")


def test_the_timestamp_is_the_records_own_not_the_drains(handler):
    """Kept so a shop PC with a wrong system clock is visible, not confusing."""
    import time

    logging.getLogger("tally_connector.session").info("stamped")
    entries, _ = handler.drain()
    # Loosely, because `record.created` and `time.time()` round differently at
    # the microsecond -- the claim under test is "the record's own clock", not
    # sub-millisecond accuracy.
    assert abs(entries[0].logged_at.timestamp() - time.time()) < 5
