"""Codec tests built around the ways real Tally responses are malformed."""

from __future__ import annotations

from datetime import date

import pytest

from tally_core.tally.codec import (
    parse_date,
    parse_float,
    parse_xml,
    sanitize_xml,
    text_of,
)
from tally_core.tally.errors import TallyParseError, TallyResponseError


def test_strips_raw_control_bytes():
    """Tally emits C0 bytes mid-string; ElementTree refuses the whole document."""
    dirty = "<ENVELOPE><NAME>Ram\x04 Traders\x1f</NAME></ENVELOPE>"
    root = parse_xml(dirty)
    assert text_of(root.find("NAME")) == "Ram Traders"


def test_strips_illegal_numeric_entities():
    dirty = "<ENVELOPE><NAME>Ram&#4;Traders</NAME></ENVELOPE>"
    root = parse_xml(dirty)
    assert text_of(root.find("NAME")) == "RamTraders"


def test_escapes_bare_ampersand_but_keeps_real_entities():
    dirty = "<ENVELOPE><NAME>Ram & Sons &amp; Co &#8377; A&amp;B</NAME></ENVELOPE>"
    root = parse_xml(dirty)
    assert text_of(root.find("NAME")) == "Ram & Sons & Co ₹ A&B"


def test_preserves_legal_whitespace():
    assert "\n" in sanitize_xml("<A>line1\nline2</A>")
    assert "\t" in sanitize_xml("<A>a\tb</A>")


def test_decodes_cp1252_payload():
    """Tally often sends cp1252 with no encoding declaration."""
    raw = "<ENVELOPE><NAME>Café Traders</NAME></ENVELOPE>".encode("cp1252")
    root = parse_xml(raw)
    assert text_of(root.find("NAME")) == "Café Traders"


def test_text_of_joins_nested_line_fragments():
    """Long narrations are split across <LINE> children; naive .text truncates."""
    root = parse_xml("<A><NARRATION>Stock for <LINE>festive</LINE> season</NARRATION></A>")
    assert text_of(root.find("NARRATION")) == "Stock for festive season"


def test_line_error_raises(fixture_xml):
    with pytest.raises(TallyResponseError, match="Nonexistent Traders"):
        parse_xml(fixture_xml("line_error"))


def test_empty_body_raises():
    with pytest.raises(TallyParseError, match="empty response"):
        parse_xml("   ")


def test_unrecoverable_xml_raises_with_preview():
    with pytest.raises(TallyParseError, match="Malformed XML"):
        parse_xml("<ENVELOPE><UNCLOSED>")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20250715", date(2025, 7, 15)),
        ("15-Jul-2025", date(2025, 7, 15)),
        ("2025-07-15", date(2025, 7, 15)),
        ("15/07/2025", date(2025, 7, 15)),
        ("", None),
        (None, None),
        ("garbage", None),
    ],
)
def test_parse_date_handles_every_format_tally_uses(raw, expected):
    assert parse_date(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("12 Nos", 12.0),
        ("-3.5 Kg", -3.5),
        ("1,250 Nos", 1250.0),
        ("45", 45.0),
        ("", 0.0),
        (None, 0.0),
    ],
)
def test_parse_float_strips_units(raw, expected):
    assert parse_float(raw) == expected
