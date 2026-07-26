"""Master-data queries: companies, groups, ledgers, stock items.

Each query defines a custom TDL collection rather than exporting one of Tally's
built-in reports. Custom collections return only the requested fields, which
keeps payloads small over a customer's home broadband and keeps parsing stable
across TallyPrime releases.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

from ...domain.masters import (
    Company,
    Ledger,
    LedgerGroup,
    StockItem,
    VoucherType,
    VoucherTypeKind,
)
from ...domain.money import Money, Side
from ..codec import find_text, first_text, parse_bool, parse_date, parse_float, text_of
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


class LedgerListParams(QueryParams):
    group: str | None = None


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
    pass


@register
class StockItemListQuery(TallyQuery[StockItemListParams, list[StockItem]]):
    """Inventory with closing quantity, rate and value."""

    name = "stock_items.list"
    params_model = StockItemListParams
    heavy = True

    def build(self, params: StockItemListParams) -> str:
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
