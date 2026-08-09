"""Decoding of TallyPrime XML responses.

Tally does not emit well-formed XML. In practice a response can contain:

* raw C0 control bytes and ``&#4;`` numeric entities that no conformant XML
  parser will accept;
* bare ``&`` inside data ("Ram & Sons") that was never escaped on the way out;
* no encoding declaration, with the payload actually in cp1252/latin-1;
* ``<LINEERROR>`` elements carrying a TDL error instead of an HTTP error status.

Every one of those is a routine occurrence, not an edge case, so parsing runs
through a sanitising pass before it reaches ElementTree.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from xml.etree import ElementTree as ET

from .errors import TallyParseError, TallyResponseError

#: C0 controls that are illegal in XML 1.0 even as numeric character references.
#: Tab (09), LF (0A) and CR (0D) are legal and must survive.
_ILLEGAL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_ILLEGAL_ENTITIES = re.compile(
    r"&#(?:x0*(?:[0-8bcefBCEF]|1[0-9a-fA-F])|0*(?:[0-8]|1[124-9]|2[0-9]|3[01]));"
)
#: A ``&`` that does not begin a valid entity reference.
_BARE_AMPERSAND = re.compile(r"&(?!(?:[a-zA-Z][a-zA-Z0-9]{1,7}|#[0-9]{1,7}|#x[0-9a-fA-F]{1,6});)")

_ENCODINGS = ("utf-8", "cp1252", "latin-1")


def decode_bytes(raw: bytes) -> str:
    """Decode a Tally response body, tolerating its inconsistent encoding."""
    for encoding in _ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def sanitize_xml(raw: str | bytes) -> str:
    """Make a Tally payload parseable without changing its data."""
    text = decode_bytes(raw) if isinstance(raw, bytes) else raw
    text = text.lstrip("﻿").strip()
    text = _ILLEGAL_ENTITIES.sub("", text)
    text = _ILLEGAL_CHARS.sub("", text)
    text = _BARE_AMPERSAND.sub("&amp;", text)
    return text


def parse_xml(raw: str | bytes) -> ET.Element:
    """Sanitise and parse a Tally response into an element tree."""
    text = sanitize_xml(raw)
    if not text:
        raise TallyParseError("Tally returned an empty response body")

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        preview = text[:400]
        raise TallyParseError(
            f"Malformed XML from Tally: {exc}. First 400 chars: {preview!r}"
        ) from exc

    _raise_for_line_errors(root)
    return root


def _raise_for_line_errors(root: ET.Element) -> None:
    """Tally reports TDL failures in-band, with HTTP 200."""
    errors = [
        (el.text or "").strip()
        for el in root.iter()
        if el.tag.upper() == "LINEERROR" and (el.text or "").strip()
    ]
    if errors:
        raise TallyResponseError("; ".join(dict.fromkeys(errors)))


def text_of(element: ET.Element | None) -> str | None:
    """Full text of an element including tail-less children, or ``None`` if blank.

    Tally wraps long strings in nested ``<LINE>`` fragments, so ``element.text``
    alone silently truncates narrations and addresses.
    """
    if element is None:
        return None
    value = "".join(element.itertext()).strip()
    return value or None


def find_text(parent: ET.Element, path: str) -> str | None:
    return text_of(parent.find(path))


def first_text(parent: ET.Element, *paths: str) -> str | None:
    """First non-empty value among several candidate paths.

    Field names drift between Tally releases and between master types
    (``<NAME>`` vs ``<LEDGERNAME>`` vs ``<DSPDISPNAME>``); listing the aliases
    at the call site keeps mappers readable.
    """
    for path in paths:
        value = find_text(parent, path)
        if value is not None:
            return value
    return None


def all_texts(parent: ET.Element, path: str) -> list[str]:
    return [t for t in (text_of(el) for el in parent.findall(path)) if t is not None]


def parse_bool(value: str | None, *, default: bool = False) -> bool:
    """Tally booleans are ``Yes``/``No``, occasionally ``1``/``0``."""
    if value is None:
        return default
    return value.strip().lower() in {"yes", "true", "1"}


def parse_float(value: str | None, *, default: float = 0.0) -> float:
    """Parse a Tally quantity.

    Quantities arrive as ``"12 Nos"``, ``"-3.5 Kg"``, ``"1,200 Pcs"`` or bare
    numbers; the unit is carried separately in the domain model.
    """
    if value is None:
        return default
    match = re.search(r"-?[\d,]*\.?\d+", value.replace(" ", ""))
    if not match:
        return default
    try:
        return float(match.group().replace(",", ""))
    except ValueError:
        return default


def parse_int(value: str | None) -> int | None:
    """Parse a Tally integer identifier (AlterID, MasterID).

    Returns ``None`` rather than ``0`` for a missing value, because the two mean
    very different things to an incremental sync: "Tally did not tell us" must
    fall back to a date-based read, whereas ``0`` would read as "nothing has
    ever been altered" and skip the fetch entirely.
    """
    if value is None:
        return None
    match = re.search(r"-?\d+", value.replace(",", ""))
    if not match:
        return None
    try:
        return int(match.group())
    except ValueError:
        return None


#: Formats Tally emits for dates, in the order they are worth trying.
#: Which one appears depends on the object's field and on the operator's
#: configured date format, so all of them must be handled.
_DATE_FORMATS = ("%Y%m%d", "%d-%b-%Y", "%d-%b-%y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%Y")


def parse_date(value: str | None) -> date | None:
    """Parse a Tally date, returning ``None`` for blank or unrecognised values."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def element_to_dict(element: ET.Element) -> Any:
    """Generic element -> dict/list/str conversion.

    Used for diagnostics and for the connector's ``raw`` passthrough mode; the
    typed mappers do not go through this.
    """
    children = list(element)
    if not children:
        return text_of(element)

    result: dict[str, Any] = {}
    for child in children:
        value = element_to_dict(child)
        key = child.tag
        if key in result:
            existing = result[key]
            if isinstance(existing, list):
                existing.append(value)
            else:
                result[key] = [existing, value]
        else:
            result[key] = value
    return result
