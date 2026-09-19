"""Creating a party or a stock item in TallyPrime.

The most common way a voucher fails is that something it names does not exist:
*"Ledger 'Ram Traders' does not exist!"*. Somebody at a counter with a new
customer in front of them cannot fix that in TallyPrime without leaving the
till, so the app offers to create it.

**It is offered, never automatic.** A ledger created silently on every unknown
name turns a chart of accounts into a list of near-duplicates — "Ram Traders",
"Ram traders", "Ram Trader" — and nothing after the fact can tell which of them
a customer's outstanding belongs to. The confirmation is the feature.

Masters go through the same ``Import`` envelope as a voucher but under
``REPORTNAME`` ``All Masters`` rather than ``Vouchers``, which is the older of
the two header forms Tally accepts::

    <ENVELOPE>
      <HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>
      <BODY>
        <IMPORTDATA>
          <REQUESTDESC>
            <REPORTNAME>All Masters</REPORTNAME>
            <STATICVARIABLES><SVCURRENTCOMPANY>...</SVCURRENTCOMPANY></STATICVARIABLES>
          </REQUESTDESC>
          <REQUESTDATA>
            <TALLYMESSAGE xmlns:UDF="TallyUDF">
              <LEDGER NAME="Ram Traders"><PARENT>Sundry Debtors</PARENT>...</LEDGER>
            </TALLYMESSAGE>
          </REQUESTDATA>
        </IMPORTDATA>
      </BODY>
    </ENVELOPE>

Neither mutation sends a figure. A ledger gets a name and a group; a stock item
gets a name, a group and possibly a unit. Opening balances and opening
quantities are money and stock, and inventing either from a phone would move a
trial balance on the strength of a typo.
"""

from __future__ import annotations

from typing import ClassVar
from xml.etree import ElementTree as ET

from ...domain.writes import LedgerDraft, MasterCreated, StockItemDraft
from ..codec import first_text
from ..envelope import tag, xml_escape
from ..errors import TallyResponseError
from ..mutation import MutationParams, TallyMutation, register_mutation

CREATE_LEDGER = "master.ledger"
CREATE_STOCK_ITEM = "master.stock_item"


def _masters_envelope(*, company: str, body: str) -> str:
    """The ``All Masters`` import envelope.

    A separate builder from the voucher one rather than a parameter on it. The
    two differ in header form *and* in body nesting, and a single function with
    a mode flag would be one wrong argument away from posting a ledger where a
    voucher was meant.
    """
    if not company:
        raise ValueError("an import must name its company")

    return (
        "<ENVELOPE>"
        "<HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>"
        "<BODY><IMPORTDATA>"
        "<REQUESTDESC>"
        "<REPORTNAME>All Masters</REPORTNAME>"
        f"<STATICVARIABLES>{tag('SVCURRENTCOMPANY', company)}</STATICVARIABLES>"
        "</REQUESTDESC>"
        "<REQUESTDATA>"
        f'<TALLYMESSAGE xmlns:UDF="TallyUDF">{body}</TALLYMESSAGE>'
        "</REQUESTDATA>"
        "</IMPORTDATA></BODY>"
        "</ENVELOPE>"
    )


def _language_name(name: str) -> str:
    """Tally's name list, which it expects on every master it creates.

    Omitting it produces a master that exists but displays blank in some
    screens, which is worse than one that failed to be created at all.
    """
    return (
        "<LANGUAGENAME.LIST>"
        f'<NAME.LIST TYPE="String">{tag("NAME", name)}</NAME.LIST>'
        "<LANGUAGEID>1033</LANGUAGEID>"
        "</LANGUAGENAME.LIST>"
    )


def _parse(root: ET.Element, name: str, what: str) -> MasterCreated:
    # Two wrappers, and which one arrives depends on the header form. The
    # voucher import (``TALLYREQUEST=Import``) answers with <IMPORTRESULT>; the
    # masters import (``TALLYREQUEST=Import Data``) answers with <RESPONSE>
    # carrying the identical counts.
    #
    # Verified live on 2026-09-17: looking only for <IMPORTRESULT> here made a
    # ledger that Tally had genuinely created report as a failure, which is the
    # worst way to be wrong -- the person is told to try again and gets a
    # duplicate for their trouble.
    result = root.find(".//IMPORTRESULT")
    if result is None:
        # `.//RESPONSE` would not find it: <RESPONSE> is the *document root* of
        # a masters reply, and a descendant search never matches the element it
        # starts from. Checking the root itself is the whole fix.
        result = root if root.tag.upper() == "RESPONSE" else root.find(".//RESPONSE")
    if result is None:
        raise TallyResponseError(
            f"{what} import reply carried neither <IMPORTRESULT> nor <RESPONSE>"
        )

    def count(field: str) -> int:
        raw = first_text(result, field)
        try:
            return int((raw or "0").strip() or 0)
        except ValueError:
            return 0

    created = count("CREATED")
    altered = count("ALTERED")
    errors = count("ERRORS")
    exceptions = count("EXCEPTIONS")

    # A named fault arrives as an in-band <LINEERROR> and `parse_xml` has
    # already raised on it. What reaches here is the quiet failure.
    message: str | None = None
    if errors or exceptions:
        message = f"TallyPrime refused to create that {what}."
    elif created + altered == 0:
        message = f"TallyPrime accepted the request but created no {what}."

    return MasterCreated(
        created=created,
        altered=altered,
        errors=errors,
        exceptions=exceptions,
        name=name,
        message=message,
    )


class CreateLedgerParams(MutationParams):
    draft: LedgerDraft


@register_mutation
class CreateLedgerMutation(TallyMutation[CreateLedgerParams, MasterCreated]):
    """Create one party ledger."""

    name: ClassVar[str] = CREATE_LEDGER
    version: ClassVar[int] = 1
    params_model: ClassVar[type[CreateLedgerParams]] = CreateLedgerParams

    def build(self, params: CreateLedgerParams) -> str:
        draft = params.draft
        parts = [
            _language_name(draft.name),
            tag("PARENT", draft.parent),
            tag("ISBILLWISEON", "Yes" if draft.bill_wise else "No"),
            # Said explicitly so the ledger behaves like one a person made in
            # Tally. Left out, some builds default these off and the party
            # cannot be selected on an invoice.
            tag("AFFECTSSTOCK", "No"),
            tag("ISCOSTCENTRESON", "No"),
            tag("OPENINGBALANCE", "0"),
        ]
        if draft.gstin:
            parts.append(tag("PARTYGSTIN", draft.gstin))

        body = f'<LEDGER NAME="{xml_escape(draft.name)}" ACTION="Create">{"".join(parts)}</LEDGER>'
        return _masters_envelope(company=params.company, body=body)

    def parse(self, root: ET.Element, params: CreateLedgerParams) -> MasterCreated:
        return _parse(root, params.draft.name, "ledger")


class CreateStockItemParams(MutationParams):
    draft: StockItemDraft


@register_mutation
class CreateStockItemMutation(TallyMutation[CreateStockItemParams, MasterCreated]):
    """Create one stock item."""

    name: ClassVar[str] = CREATE_STOCK_ITEM
    version: ClassVar[int] = 1
    params_model: ClassVar[type[CreateStockItemParams]] = CreateStockItemParams

    def build(self, params: CreateStockItemParams) -> str:
        draft = params.draft
        parts = [
            _language_name(draft.name),
            tag("PARENT", draft.parent),
        ]
        if draft.unit:
            # Both, because Tally reads one or the other depending on how old
            # the company's data is, and a mismatch leaves an item whose
            # quantities cannot be entered.
            parts.append(tag("BASEUNITS", draft.unit))
            parts.append(tag("ADDITIONALUNITS", draft.unit))

        body = (
            f'<STOCKITEM NAME="{xml_escape(draft.name)}" ACTION="Create">'
            f'{"".join(parts)}</STOCKITEM>'
        )
        return _masters_envelope(company=params.company, body=body)

    def parse(self, root: ET.Element, params: CreateStockItemParams) -> MasterCreated:
        return _parse(root, params.draft.name, "stock item")
