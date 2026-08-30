"""Master-data queries: companies, groups, ledgers, stock items.

Each query defines a custom TDL collection rather than exporting one of Tally's
built-in reports. Custom collections return only the requested fields, which
keeps payloads small over a customer's home broadband and keeps parsing stable
across TallyPrime releases.
"""

from __future__ import annotations

from collections.abc import Sequence
from xml.etree import ElementTree as ET

from pydantic import model_validator

from ...domain.masters import (
    Company,
    CompanyMarkers,
    Ledger,
    LedgerGroup,
    StockItem,
    VoucherType,
    VoucherTypeKind,
)
from ...domain.money import Money, Side
from ..codec import (
    find_text,
    first_text,
    parse_bool,
    parse_date,
    parse_float,
    parse_int,
    text_of,
)
from ..envelope import Collection, StaticVariables, build_export_envelope
from ..query import QueryParams, TallyQuery, register


def _as_magnitude(money: Money) -> Money:
    """Strip the accounting side from a value that has no direction (a rate)."""
    if money.side is Side.DEBIT:
        return money
    return Money(amount=money.amount, side=Side.DEBIT, currency=money.currency)


def _iter_objects(root: ET.Element, tag: str) -> list[ET.Element]:
    """Find collection members regardless of how deeply Tally nested them.

    Export responses normally wrap members in ``BODY/DATA/COLLECTION``, but
    ``TYPE=Report`` responses and some TallyPrime builds omit intermediate
    levels, so search the whole tree by tag instead of by fixed path.
    """
    return list(root.iter(tag))


# --------------------------------------------------------------------------
# Companies
# --------------------------------------------------------------------------


class CompanyListParams(QueryParams):
    """Company discovery is the one read not scoped to a company."""

    company: str = ""


@register
class CompanyListQuery(TallyQuery[CompanyListParams, list[Company]]):
    """Companies currently open in TallyPrime.

    This is the connector's pairing call: the phone can only pick from companies
    the operator has actually loaded, which is a deliberate security boundary --
    the connector cannot open a company on its own.
    """

    name = "companies.list"
    params_model = CompanyListParams

    def build(self, params: CompanyListParams) -> str:
        return build_export_envelope(
            request_type="Collection",
            request_id="TFCompanies",
            static_variables=StaticVariables(),
            collections=[
                Collection(
                    name="TFCompanies",
                    type="Company",
                    native_methods=[
                        "Name",
                        "StartingFrom",
                        "BooksFrom",
                        "CompanyNumber",
                        "StateName",
                        "GSTRegistrationNumber",
                        "BaseCurrencySymbol",
                    ],
                )
            ],
        )

    def parse(self, root: ET.Element, params: CompanyListParams) -> list[Company]:
        companies: list[Company] = []
        for el in _iter_objects(root, "COMPANY"):
            name = first_text(el, "NAME", "DSPDISPNAME") or el.get("NAME")
            if not name:
                continue
            companies.append(
                Company(
                    name=name,
                    guid=find_text(el, "GUID"),
                    financial_year_from=parse_date(find_text(el, "STARTINGFROM")),
                    books_from=parse_date(find_text(el, "BOOKSFROM")),
                    gstin=find_text(el, "GSTREGISTRATIONNUMBER"),
                    state=find_text(el, "STATENAME"),
                    base_currency=find_text(el, "BASECURRENCYSYMBOL") or "INR",
                )
            )
        return companies


# --------------------------------------------------------------------------
# Company change markers
# --------------------------------------------------------------------------


class CompanyMarkersParams(QueryParams):
    pass


@register
class CompanyMarkersQuery(TallyQuery[CompanyMarkersParams, CompanyMarkers]):
    """One company's change counters and book date range.

    The cheapest read in the product and the one the sync loop leans on hardest:
    it is what lets the backend ask "is there anything new?" without exporting a
    single voucher. A shop whose Tally has been idle since the last sweep costs
    one small request rather than a multi-minute day-book export -- which is the
    difference between a background refresh the owner never notices and one that
    freezes the till.

    Deliberately separate from ``companies.list``: that one is unscoped
    discovery used during pairing and must stay cheap and company-agnostic,
    while this is scoped to a single company and is called on every sweep.
    """

    name = "company.markers"
    params_model = CompanyMarkersParams

    def build(self, params: CompanyMarkersParams) -> str:
        return build_export_envelope(
            request_type="Collection",
            request_id="TFCompanyMarkers",
            static_variables=StaticVariables(company=params.company),
            collections=[
                Collection(
                    name="TFCompanyMarkers",
                    type="Company",
                    native_methods=[
                        "Name",
                        "StartingFrom",
                        "BooksFrom",
                        "EndingAt",
                        # The change counters. Older TallyPrime builds do not
                        # know these names and drop them silently, which is why
                        # `CompanyMarkers` treats a missing value as "unknown"
                        # rather than as zero.
                        "AltMstId",
                        "AltVchId",
                    ],
                )
            ],
        )

    def parse(self, root: ET.Element, params: CompanyMarkersParams) -> CompanyMarkers:
        for el in _iter_objects(root, "COMPANY"):
            name = first_text(el, "NAME", "DSPDISPNAME") or el.get("NAME")
            if not name:
                # The nameless placeholder every Tally collection opens with.
                continue
            # Only the company the request was scoped to. A Tally with several
            # companies loaded still answers with just this one, but matching
            # defensively costs nothing and stops a cursor from being advanced
            # using a neighbouring company's counter.
            if params.company and name.strip() != params.company.strip():
                continue
            return _markers_from(el, name)

        # No match: report the company by the name we asked for, with no
        # counters. Callers read that as "fall back to a date-window sync".
        return CompanyMarkers(name=params.company)


def _markers_from(el: ET.Element, name: str) -> CompanyMarkers:
    return CompanyMarkers(
        name=name,
        master_alter_id=parse_int(first_text(el, "ALTMSTID", "LASTALTERIDMASTER")),
        voucher_alter_id=parse_int(first_text(el, "ALTVCHID", "LASTALTERIDVOUCHER")),
        books_from=parse_date(find_text(el, "BOOKSFROM")),
        financial_year_from=parse_date(find_text(el, "STARTINGFROM")),
        ending_at=parse_date(find_text(el, "ENDINGAT")),
    )


# --------------------------------------------------------------------------
# Groups
# --------------------------------------------------------------------------


class GroupListParams(QueryParams):
    pass


@register
class GroupListQuery(TallyQuery[GroupListParams, list[LedgerGroup]]):
    """The chart-of-accounts group tree.

    Needed to classify ledgers (which are debtors, which are bank accounts)
    without hardcoding Indian-standard group names in the backend.
    """

    name = "groups.list"
    params_model = GroupListParams

    def build(self, params: GroupListParams) -> str:
        return build_export_envelope(
            request_type="Collection",
            request_id="TFGroups",
            static_variables=StaticVariables(company=params.company),
            collections=[
                Collection(
                    name="TFGroups",
                    type="Group",
                    native_methods=[
                        "Name",
                        "Parent",
                        "IsRevenue",
                        "IsDeemedPositive",
                        "PrimaryGroup",
                    ],
                )
            ],
        )

    def parse(self, root: ET.Element, params: GroupListParams) -> list[LedgerGroup]:
        groups: list[LedgerGroup] = []
        for el in _iter_objects(root, "GROUP"):
            name = first_text(el, "NAME", "DSPDISPNAME") or el.get("NAME")
            if not name:
                continue
            groups.append(
                LedgerGroup(
                    name=name,
                    parent=find_text(el, "PARENT"),
                    guid=find_text(el, "GUID"),
                    is_revenue=parse_bool(find_text(el, "ISREVENUE")),
                    is_deemed_positive=parse_bool(
                        find_text(el, "ISDEEMEDPOSITIVE"), default=True
                    ),
                    primary_group=find_text(el, "PRIMARYGROUP"),
                )
            )
        return groups


# --------------------------------------------------------------------------
# Ledgers
# --------------------------------------------------------------------------


#: Ceiling on how many names one targeted re-read may ask for. Above this the
#: OR chain stops being worth building: Tally walks the whole collection to
#: evaluate it either way, so past a few hundred names the filter costs more to
#: parse than the rows it saves. Callers check this and fall back to a full read.
MAX_NAME_FILTER = 200


def _name_filter(names: Sequence[str]) -> str:
    """Restrict a master collection to an explicit set of names.

    This is what makes an incremental master read possible at all. A master's
    ``AlterID`` does not move when a voucher moves its balance (CLAUDE.md, and
    ``tally-connector alterid-probe``), so the only sound way to refresh
    balances without re-reading every ledger is to name the ones the changed
    vouchers touched.

    Verified live 2026-08-29: ``$Name = "Cash" OR $Name = "CGST"`` returns
    exactly those two members, and the comparison is **case-insensitive** --
    ``"cash"`` matched ``Cash``. That matters because these names round-trip
    through JSON and a stored voucher line before coming back here, and a
    case-sensitive match would silently refresh nothing.
    """
    # Quotes stripped rather than escaped: TDL has no escape for a double quote
    # inside a quoted literal, and a ledger named with one is rarer than a
    # broken envelope. Matches the group filter's handling.
    return " OR ".join(f'$Name = "{name.replace(chr(34), "")}"' for name in names)


class LedgerListParams(QueryParams):
    group: str | None = None
    #: Only masters altered since this change id. Catches ledgers created,
    #: renamed or regrouped -- **not** ledgers whose balance moved, which does
    #: not touch AlterID at all. Pair it with :attr:`names`.
    alter_id_min: int | None = None
    #: Only these ledgers, by name. The balance half of an incremental refresh:
    #: the changed vouchers name the ledgers whose closing balance can have
    #: moved, and this reads back just those.
    names: list[str] | None = None

    @model_validator(mode="after")
    def _check_name_count(self) -> LedgerListParams:
        if self.names is not None and len(self.names) > MAX_NAME_FILTER:
            raise ValueError(
                f"names is capped at {MAX_NAME_FILTER}; ask for every ledger instead"
            )
        return self


@register
class LedgerListQuery(TallyQuery[LedgerListParams, list[Ledger]]):
    """All ledgers with opening and closing balances.

    Optionally restricted to one group -- the dashboard's cash and bank tiles
    ask for ``Cash-in-Hand`` and ``Bank Accounts`` rather than pulling every
    ledger in the company.
    """

    name = "ledgers.list"
    params_model = LedgerListParams
    heavy = True

    def build(self, params: LedgerListParams) -> str:
        filters = {}
        if params.alter_id_min is not None:
            filters["TFAlterIdFilter"] = f"$AlterID > {int(params.alter_id_min)}"
        if params.names:
            filters["TFNameFilter"] = _name_filter(params.names)
        if params.group:
            # $$IsSubGroupOf walks the whole group subtree, so asking for
            # "Bank Accounts" also returns ledgers under "Bank OD A/c".
            escaped = params.group.replace('"', "")
            filters["TFGroupFilter"] = f'$$IsSubGroupOf:$Parent:"{escaped}"'

        return build_export_envelope(
            request_type="Collection",
            request_id="TFLedgers",
            static_variables=StaticVariables(company=params.company),
            collections=[
                Collection(
                    name="TFLedgers",
                    type="Ledger",
                    native_methods=[
                        "Name",
                        "Parent",
                        # Cheap scalar, and the cursor an incremental master
                        # read filters on. Requested unconditionally so the
                        # value is there before anything depends on it.
                        "AlterID",
                        "OpeningBalance",
                        "ClosingBalance",
                        "IsBillWiseOn",
                        "CreditLimit",
                        "LedgerStateName",
                    ],
                    # Contact details are not storage fields on Ledger, so a
                    # NATIVEMETHOD request for them is silently ignored (Tally
                    # returns no error and no value). FETCH resolves them.
                    fetch=[
                        "CreditPeriod",
                        "PartyGSTIN",
                        "LedgerPhone",
                        "Email",
                        "LedgerMailingName",
                        "Address",
                    ],
                    filters=filters,
                )
            ],
        )

    def parse(self, root: ET.Element, params: LedgerListParams) -> list[Ledger]:
        ledgers: list[Ledger] = []
        for el in _iter_objects(root, "LEDGER"):
            name = first_text(el, "NAME", "DSPDISPNAME") or el.get("NAME")
            if not name:
                continue

            # Live Tally nests the address under LEDMAILINGDETAILS.LIST; older
            # docs show a flat ADDRESS.LIST. Both are checked.
            address = [
                t
                for t in (
                    text_of(line)
                    for line in (
                        el.findall("./LEDMAILINGDETAILS.LIST/ADDRESS.LIST/ADDRESS")
                        or el.findall("./ADDRESS.LIST/ADDRESS")
                    )
                )
                if t
            ]
            credit_period = find_text(el, "CREDITPERIOD")
            credit_limit = find_text(el, "CREDITLIMIT")

            ledgers.append(
                Ledger(
                    name=name,
                    parent_group=find_text(el, "PARENT"),
                    guid=find_text(el, "GUID"),
                    alter_id=parse_int(find_text(el, "ALTERID")),
                    opening_balance=Money.from_tally(find_text(el, "OPENINGBALANCE")),
                    closing_balance=Money.from_tally(find_text(el, "CLOSINGBALANCE")),
                    is_bill_wise=parse_bool(find_text(el, "ISBILLWISEON")),
                    credit_period_days=(
                        int(parse_float(credit_period)) if credit_period else None
                    ),
                    credit_limit=Money.from_tally(credit_limit) if credit_limit else None,
                    gstin=first_text(el, "PARTYGSTIN", "GSTREGISTRATIONNUMBER"),
                    phone=find_text(el, "LEDGERPHONE"),
                    email=find_text(el, "EMAIL"),
                    address=address,
                    state=first_text(el, "LEDGERSTATENAME", "LEDSTATENAME"),
                )
            )
        return ledgers


# --------------------------------------------------------------------------
# Voucher types
# --------------------------------------------------------------------------


class VoucherTypeListParams(QueryParams):
    pass


@register
class VoucherTypeListQuery(TallyQuery[VoucherTypeListParams, list[VoucherType]]):
    """Voucher types with their parent accounting class.

    Businesses rename voucher types freely -- "Tax Invoice", "Retail Bill",
    "GST Sales" are all Sales. Only the parent class is stable, and a voucher
    row does not always carry it, so the backend loads this map once per company
    and uses it to classify vouchers for analytics.
    """

    name = "voucher_types.list"
    params_model = VoucherTypeListParams

    def build(self, params: VoucherTypeListParams) -> str:
        return build_export_envelope(
            request_type="Collection",
            request_id="TFVoucherTypes",
            static_variables=StaticVariables(company=params.company),
            collections=[
                Collection(
                    name="TFVoucherTypes",
                    type="VoucherType",
                    native_methods=["Name", "Parent", "IsDeemedPositive"],
                )
            ],
        )

    def parse(self, root: ET.Element, params: VoucherTypeListParams) -> list[VoucherType]:
        types: list[VoucherType] = []
        for el in _iter_objects(root, "VOUCHERTYPE"):
            name = first_text(el, "NAME", "DSPDISPNAME") or el.get("NAME")
            if not name:
                continue
            parent = find_text(el, "PARENT")
            types.append(
                VoucherType(
                    name=name,
                    parent=parent,
                    # Fall back to the type's own name: for Tally's built-in
                    # types the name *is* the accounting class.
                    kind=VoucherTypeKind.from_parent(parent or name),
                    is_deemed_positive=parse_bool(
                        find_text(el, "ISDEEMEDPOSITIVE"), default=True
                    ),
                )
            )
        return types


# --------------------------------------------------------------------------
# Stock items
# --------------------------------------------------------------------------


class StockItemListParams(QueryParams):
    #: See :attr:`LedgerListParams.alter_id_min`.
    alter_id_min: int | None = None
    #: See :attr:`LedgerListParams.names`. Fed from the inventory lines of the
    #: vouchers a delta returned.
    names: list[str] | None = None

    @model_validator(mode="after")
    def _check_name_count(self) -> StockItemListParams:
        if self.names is not None and len(self.names) > MAX_NAME_FILTER:
            raise ValueError(
                f"names is capped at {MAX_NAME_FILTER}; ask for every item instead"
            )
        return self


@register
class StockItemListQuery(TallyQuery[StockItemListParams, list[StockItem]]):
    """Inventory with closing quantity, rate and value."""

    name = "stock_items.list"
    params_model = StockItemListParams
    heavy = True

    def build(self, params: StockItemListParams) -> str:
        filters = {}
        if params.alter_id_min is not None:
            filters["TFAlterIdFilter"] = f"$AlterID > {int(params.alter_id_min)}"
        if params.names:
            filters["TFNameFilter"] = _name_filter(params.names)

        return build_export_envelope(
            request_type="Collection",
            request_id="TFStockItems",
            static_variables=StaticVariables(company=params.company),
            collections=[
                Collection(
                    name="TFStockItems",
                    type="StockItem",
                    native_methods=[
                        "Name",
                        "Parent",
                        "AlterID",
                        "Category",
                        "BaseUnits",
                        "OpeningBalance",
                        "OpeningValue",
                        "ClosingBalance",
                        "ClosingValue",
                        "ClosingRate",
                        "ReorderLevel",
                    ],
                    # Tax attributes live on child GST detail objects rather
                    # than on the item, so NATIVEMETHOD does not reach them.
                    fetch=["HSNCode", "GSTRate", "GSTDetails"],
                    filters=filters,
                )
            ],
        )

    def parse(self, root: ET.Element, params: StockItemListParams) -> list[StockItem]:
        items: list[StockItem] = []
        for el in _iter_objects(root, "STOCKITEM"):
            name = first_text(el, "NAME", "DSPDISPNAME") or el.get("NAME")
            if not name:
                continue

            reorder = find_text(el, "REORDERLEVEL")
            gst_rate = find_text(el, "GSTRATE")
            closing_rate = find_text(el, "CLOSINGRATE")

            items.append(
                StockItem(
                    name=name,
                    parent_group=find_text(el, "PARENT"),
                    category=find_text(el, "CATEGORY"),
                    guid=find_text(el, "GUID"),
                    alter_id=parse_int(find_text(el, "ALTERID")),
                    base_unit=find_text(el, "BASEUNITS"),
                    opening_quantity=parse_float(find_text(el, "OPENINGBALANCE")),
                    opening_value=Money.from_tally(find_text(el, "OPENINGVALUE")),
                    closing_quantity=parse_float(find_text(el, "CLOSINGBALANCE")),
                    closing_value=Money.from_tally(find_text(el, "CLOSINGVALUE")),
                    # A rate is a unit price, not a directional amount, so it
                    # carries no Dr/Cr. Tally still signs it, which would render
                    # as "80.00 Cr" on a price tag; normalise to a magnitude.
                    closing_rate=_as_magnitude(Money.from_tally(closing_rate))
                    if closing_rate
                    else None,
                    reorder_level=parse_float(reorder) if reorder else None,
                    hsn_code=find_text(el, "HSNCODE"),
                    gst_rate=parse_float(gst_rate) if gst_rate else None,
                )
            )
        return items
