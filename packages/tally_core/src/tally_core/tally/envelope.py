"""Builders for TallyPrime XML request envelopes.

Reference: https://help.tallysolutions.com/tally-prime-integration-using-json-1/

Every request Tally accepts has the same skeleton::

    <ENVELOPE>
      <HEADER>
        <VERSION>1</VERSION>
        <TALLYREQUEST>Export</TALLYREQUEST>
        <TYPE>Collection|Report|Function|Data</TYPE>
        <ID>...</ID>
      </HEADER>
      <BODY>
        <DESC>
          <STATICVARIABLES>...</STATICVARIABLES>
          <TDL><TDLMESSAGE>...</TDLMESSAGE></TDL>
        </DESC>
      </BODY>
    </ENVELOPE>

Reads emit ``TALLYREQUEST=Export``; writes emit ``Import``, and the two have
separate builders on purpose. :func:`build_export_envelope` cannot produce an
``Import`` header whatever it is passed, so no read path can mutate a book of
accounts by way of a mistyped argument -- reaching Tally's writer means calling
:func:`build_import_envelope` by name.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal
from xml.sax.saxutils import escape

RequestType = Literal["Collection", "Report", "Function", "Data"]

XML_EXPORT_FORMAT = "$$SysName:XML"


def tally_date(value: date) -> str:
    """Tally's wire format for dates in static variables: ``YYYYMMDD``."""
    return value.strftime("%Y%m%d")


def xml_escape(value: str) -> str:
    """Escape a value for use as XML text.

    Company names routinely contain ``&`` ("Ram & Sons"), which produces an
    envelope Tally rejects outright if passed through raw.
    """
    return escape(str(value), {'"': "&quot;", "'": "&apos;"})


def tag(name: str, value: object) -> str:
    return f"<{name}>{xml_escape(str(value))}</{name}>"


@dataclass(frozen=True)
class StaticVariables:
    """The ``<STATICVARIABLES>`` block: what Tally scopes the export to.

    ``company`` is the display name of a company currently *loaded* in Tally.
    Leaving it ``None`` means "whatever company is currently active", which is
    fine for discovery calls but never for reports -- always pin the company.
    """

    company: str | None = None
    from_date: date | None = None
    to_date: date | None = None
    export_format: str = XML_EXPORT_FORMAT
    explode_flag: bool | None = None
    extra: Mapping[str, str] = field(default_factory=dict)

    def to_xml(self) -> str:
        parts = [tag("SVEXPORTFORMAT", self.export_format)]
        if self.company is not None:
            parts.append(tag("SVCURRENTCOMPANY", self.company))
        if self.from_date is not None:
            parts.append(tag("SVFROMDATE", tally_date(self.from_date)))
        if self.to_date is not None:
            parts.append(tag("SVTODATE", tally_date(self.to_date)))
        if self.explode_flag is not None:
            parts.append(tag("EXPLODEFLAG", "Yes" if self.explode_flag else "No"))
        parts.extend(tag(key, value) for key, value in self.extra.items())
        return f"<STATICVARIABLES>{''.join(parts)}</STATICVARIABLES>"


@dataclass(frozen=True)
class Collection:
    """A custom TDL collection definition.

    Custom collections are strongly preferred over Tally's stock reports: they
    return exactly the fields asked for, which keeps payloads small over a
    home-broadband connector link and keeps parsing stable when Tally changes a
    report layout between releases.
    """

    name: str
    type: str
    native_methods: Sequence[str] = ()
    fetch: Sequence[str] = ()
    filters: Mapping[str, str] = field(default_factory=dict)
    compute: Mapping[str, str] = field(default_factory=dict)
    is_modify: bool = False

    def to_xml(self) -> str:
        parts: list[str] = [tag("TYPE", self.type)]
        parts.extend(tag("NATIVEMETHOD", m) for m in self.native_methods)
        parts.extend(tag("FETCH", f) for f in self.fetch)
        parts.extend(tag("COMPUTE", f"{k} : {v}") for k, v in self.compute.items())

        if self.filters:
            parts.extend(tag("FILTER", name) for name in self.filters)

        attrs = f'NAME="{xml_escape(self.name)}" ISMODIFY="{"Yes" if self.is_modify else "No"}"'
        collection = f"<COLLECTION {attrs}>{''.join(parts)}</COLLECTION>"

        # Filter expressions live in <SYSTEM TYPE="Formulae"> siblings, not
        # inside the collection body.
        systems = "".join(
            f'<SYSTEM TYPE="Formulae" NAME="{xml_escape(name)}">{xml_escape(expr)}</SYSTEM>'
            for name, expr in self.filters.items()
        )
        return collection + systems


def build_export_envelope(
    *,
    request_type: RequestType,
    request_id: str,
    static_variables: StaticVariables | None = None,
    collections: Sequence[Collection] = (),
    raw_tdl: str | None = None,
) -> str:
    """Assemble a complete ``Export`` envelope.

    ``request_id`` is the collection name for ``Collection`` requests, or the
    report name (e.g. ``"Day Book"``) for ``Report`` requests.
    """
    sv = (static_variables or StaticVariables()).to_xml()

    tdl_body = "".join(c.to_xml() for c in collections)
    if raw_tdl:
        tdl_body += raw_tdl
    tdl = f"<TDL><TDLMESSAGE>{tdl_body}</TDLMESSAGE></TDL>" if tdl_body else ""

    return (
        "<ENVELOPE>"
        "<HEADER>"
        "<VERSION>1</VERSION>"
        "<TALLYREQUEST>Export</TALLYREQUEST>"
        f"{tag('TYPE', request_type)}"
        f"{tag('ID', request_id)}"
        "</HEADER>"
        "<BODY><DESC>"
        f"{sv}{tdl}"
        "</DESC></BODY>"
        "</ENVELOPE>"
    )


def build_import_envelope(*, request_id: str, company: str, messages: Sequence[str]) -> str:
    """Assemble a complete ``Import`` envelope -- the only way to write to Tally.

    Deliberately not a flag on :func:`build_export_envelope`. A boolean that
    turns a read into a write is one wrong default away from a mutation, and the
    whole point of the read-only rule is that such a mistake should not be
    expressible.

    ``company`` is required and never optional, unlike on an export. Tally
    imports into whichever company is *active* when none is named, so an
    unpinned import posts a customer's voucher into whichever company the person
    at the till last clicked.

    Verified live 2026-09-17: the reply is an ``<IMPORTRESULT>`` carrying
    ``CREATED``/``ERRORS``/``EXCEPTIONS`` counts and ``LASTVCHID``, even for an
    empty payload -- so a caller can always tell what actually happened rather
    than inferring it from the absence of an error.
    """
    if not company:
        raise ValueError("an import must name its company")

    sv = f"<STATICVARIABLES>{tag('SVCURRENTCOMPANY', company)}</STATICVARIABLES>"
    # The UDF namespace declaration is required even when no user-defined field
    # is used: Tally rejects the payload outright without it.
    body = "".join(
        f'<TALLYMESSAGE xmlns:UDF="TallyUDF">{message}</TALLYMESSAGE>' for message in messages
    )

    return (
        "<ENVELOPE>"
        "<HEADER>"
        "<VERSION>1</VERSION>"
        "<TALLYREQUEST>Import</TALLYREQUEST>"
        "<TYPE>Data</TYPE>"
        f"{tag('ID', request_id)}"
        "</HEADER>"
        "<BODY>"
        f"<DESC>{sv}</DESC>"
        f"<DATA>{body}</DATA>"
        "</BODY>"
        "</ENVELOPE>"
    )
