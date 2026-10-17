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
    BankAccount,
    Company,
    CompanyMarkers,
    CompanyProfile,
    GodownStock,
    Ledger,
    LedgerGroup,
    StockItem,
    TallyLicence,
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
# Company profile
# --------------------------------------------------------------------------


class CompanyProfileParams(QueryParams):
    pass


@register
class CompanyProfileQuery(TallyQuery[CompanyProfileParams, CompanyProfile]):
    """The company's own name, address and registrations.

    This is the seller block of an invoice. A voucher somebody shares from the
    app used to carry the company's name and nothing else, because nothing read
    the rest -- so a tax invoice went out with no GSTIN and no state on it.

    **The GSTIN is not on the company.** Verified live 2026-10-11 on "D.D
    Enterprises": ``GSTRegistrationNumber`` on the ``Company`` object came back
    empty, and so did the "Default Tax Unit" every company is created with. The
    number was on a second ``TaxUnit``, "Gujarat Registration", which is where
    TallyPrime has kept GST registrations since it allowed more than one per
    company. So this reads the first tax unit that carries a number, and still
    asks the company for its own, which is where an older release keeps it.

    A company with several registrations gets the first. Printing the right one
    per voucher means reading the registration off the voucher itself, which
    this does not do.

    **Nor are the address and contact details, in this collection.** The
    lines on the Company Alteration screen are on the company object --
    verified live 2026-10-14 on "D.D Enterprises", whose object export carries
    ``309,BLUE EMIMINENCE SANGINI DANDI ROAD`` in ``ADDRESS.LIST`` -- but a
    ``Company`` collection returns nothing for ``Address``, ``MobileNumbers``,
    ``Email`` or ``Website``, fetched or named as native methods. They are
    reached instead the way the GSTIN is: the address and mobile lists as
    sub-collections of the loaded company, counted and read line by line, and
    the single fields as ``$Field:Company:##SVCurrentCompany``. Every one of
    those forms was run against that Tally first; none raised an error.
    """

    #: Address lines read. Tally's alteration screen holds fewer than this.
    ADDRESS_LINES = 8
    MOBILES = 4

    name = "company.profile"
    params_model = CompanyProfileParams

    def build(self, params: CompanyProfileParams) -> str:
        return build_export_envelope(
            request_type="Collection",
            request_id="TFCompanyProfile",
            static_variables=StaticVariables(company=params.company),
            collections=[
                Collection(
                    name="TFCompanyGstUnits",
                    type="TaxUnit",
                    native_methods=["Name", "GSTRegNumber"],
                    filters={"TFHasGstin": "NOT $$IsEmpty:$GSTRegNumber"},
                ),
                # Bank ledgers that carry an account number: the "Company's Bank
                # Details" block of an invoice. Verified live 2026-10-12 on
                # "D.D Enterprises", where the one such ledger held the account
                # number in ``BankDetails`` and the code in ``IFSCode``, and
                # Tally's own print used the ledger's name as the bank's.
                Collection(
                    name="TFCompanyBanks",
                    type="Ledger",
                    native_methods=["Name", "BankDetails", "IFSCode", "BranchName"],
                    filters={
                        "TFIsBank": (
                            "($$IsLedOfGrp:$Name:$$GroupBank OR $$IsLedOfGrp:$Name:$$GroupBankOD)"
                            " AND NOT $$IsEmpty:$BankDetails"
                        )
                    },
                ),
                Collection(
                    name="TFCompanyAddress",
                    type="Address : Company",
                    child_of="##SVCurrentCompany",
                ),
                Collection(
                    name="TFCompanyMobiles",
                    type="MobileNumbers : Company",
                    child_of="##SVCurrentCompany",
                ),
                Collection(
                    name="TFCompanyProfile",
                    type="Company",
                    native_methods=[
                        "Name",
                        "BasicCompanyFormalName",
                        "StateName",
                        "PinCode",
                        "CountryName",
                        "PhoneNumber",
                        "Email",
                        "IncomeTaxNumber",
                        "GSTRegistrationNumber",
                    ],
                    fetch=["Address"],
                    compute={
                        "TFGstin": "$$CollectionField:$GSTRegNumber:First:TFCompanyGstUnits",
                        "TFBankCount": "$$NumItems:TFCompanyBanks",
                        "TFBankName": "$$CollectionField:$Name:First:TFCompanyBanks",
                        "TFBankAccount": "$$CollectionField:$BankDetails:First:TFCompanyBanks",
                        "TFBankIfsc": "$$CollectionField:$IFSCode:First:TFCompanyBanks",
                        "TFBankBranch": "$$CollectionField:$BranchName:First:TFCompanyBanks",
                        **{
                            f"TFAddress{n}": f"$$CollectionField:$Address:{n}:TFCompanyAddress"
                            for n in range(1, self.ADDRESS_LINES + 1)
                        },
                        **{
                            f"TFMobile{n}": (
                                f"$$CollectionField:$MobileNumbers:{n}:TFCompanyMobiles"
                            )
                            for n in range(1, self.MOBILES + 1)
                        },
                        "TFPhone": "$PhoneNumber:Company:##SVCurrentCompany",
                        "TFIsdCode": "$CountryISDCode:Company:##SVCurrentCompany",
                        "TFEmail": "$Email:Company:##SVCurrentCompany",
                        "TFWebsite": "$Website:Company:##SVCurrentCompany",
                    },
                ),
            ],
        )

    def parse(self, root: ET.Element, params: CompanyProfileParams) -> CompanyProfile:
        for el in _iter_objects(root, "COMPANY"):
            name = first_text(el, "NAME", "DSPDISPNAME") or el.get("NAME")
            if not name:
                continue
            # Same defence as the markers read: a Tally with several companies
            # loaded must not put a neighbour's GSTIN on this one's invoices.
            if params.company and name.strip() != params.company.strip():
                continue

            address = [
                t
                for t in (
                    find_text(el, f"TFADDRESS{n}") for n in range(1, self.ADDRESS_LINES + 1)
                )
                if t
            ] or [t for t in (text_of(line) for line in el.findall(".//ADDRESS.LIST/ADDRESS")) if t]
            mobiles = [
                t for t in (find_text(el, f"TFMOBILE{n}") for n in range(1, self.MOBILES + 1)) if t
            ]
            isd = (find_text(el, "TFISDCODE") or "").strip()
            return CompanyProfile(
                name=name,
                mailing_name=find_text(el, "BASICCOMPANYFORMALNAME"),
                address=address,
                state=find_text(el, "STATENAME"),
                pincode=find_text(el, "PINCODE"),
                country=find_text(el, "COUNTRYNAME"),
                gstin=first_text(el, "TFGSTIN", "GSTREGISTRATIONNUMBER"),
                pan=find_text(el, "INCOMETAXNUMBER"),
                phone=first_text(el, "TFPHONE", "PHONENUMBER"),
                # As the alteration screen shows them: "+91 - 9998464854,
                # 9925364854".
                mobile=(f"{isd} - " if isd and mobiles else "") + ", ".join(mobiles) or None,
                email=first_text(el, "TFEMAIL", "EMAIL"),
                website=find_text(el, "TFWEBSITE"),
                bank=_company_bank(el),
            )

        # No match: the name we asked for and nothing else, which a document
        # prints exactly as it did before this read existed.
        return CompanyProfile(name=params.company)



def _company_bank(el: ET.Element) -> BankAccount | None:
    """The bank to print on invoices, when there is only one it could be.

    Which bank an invoice shows is chosen in Tally's print settings, and no
    export returns that choice. With one bank ledger carrying an account number
    there is nothing to choose; with two, printing the first would sooner or
    later tell a customer to pay into the wrong account, so the block is left
    off instead.
    """
    name = find_text(el, "TFBANKNAME")
    account = find_text(el, "TFBANKACCOUNT")
    if parse_int(find_text(el, "TFBANKCOUNT")) != 1 or not name or not account:
        return None
    return BankAccount(
        bank_name=name,
        account_number=account,
        ifsc=find_text(el, "TFBANKIFSC"),
        branch=find_text(el, "TFBANKBRANCH"),
    )


# --------------------------------------------------------------------------
# Licence and operator rights
# --------------------------------------------------------------------------


class TallyLicenceParams(QueryParams):
    pass


@register
class TallyLicenceQuery(TallyQuery[TallyLicenceParams, TallyLicence]):
    """Whether this TallyPrime will actually serve a complete set of books.

    Two conditions make every other read quietly wrong and neither announces
    itself: educational mode returns only a fraction of the vouchers, and a
    non-administrator Tally user cannot export some reports. Both are
    indistinguishable from "this shop simply has very little data", which is
    why a customer reports the product as broken and nothing in any log agrees.

    ``$$LicenseInfo:...`` is read through ``COMPUTE`` on a Company collection
    rather than through ``TYPE=Function``/``$$ProdInfo``. The Function form is
    what the closest shipping competitor uses; tried against a live TallyPrime
    on 2026-09-15 it answered ``<ERRORMSG>Function Execution Failed!</ERRORMSG>``
    for every parameter, while this form returned every value asked for.
    """

    name = "tally.licence"
    params_model = TallyLicenceParams

    def build(self, params: TallyLicenceParams) -> str:
        return build_export_envelope(
            request_type="Collection",
            request_id="TFLicence",
            static_variables=StaticVariables(company=params.company),
            collections=[
                Collection(
                    name="TFLicence",
                    type="Company",
                    compute={
                        "TFEduMode": "$$LicenseInfo:IsEducationalMode",
                        "TFLicMode": "$$LicenseInfo:IsLicensedMode",
                        "TFIsAdmin": "$$LicenseInfo:IsAdmin",
                        "TFSerial": "$$LicenseInfo:SerialNumber",
                        "TFGold": "$$LicenseInfo:IsGold",
                        "TFSilver": "$$LicenseInfo:IsSilver",
                    },
                )
            ],
        )

    def parse(self, root: ET.Element, params: TallyLicenceParams) -> TallyLicence:
        edition = None
        if _tally_flag(root, "TFGOLD"):
            edition = "Gold"
        elif _tally_flag(root, "TFSILVER"):
            edition = "Silver"

        serial = _first_tag_text(root, "TFSERIAL")
        # An unlicensed install reports 0, which is not a serial number and must
        # not be shown to anyone as one.
        if serial is not None and not serial.strip().strip("0"):
            serial = None

        return TallyLicence(
            is_educational=_tally_flag(root, "TFEDUMODE"),
            is_licensed=_tally_flag(root, "TFLICMODE"),
            is_admin=_tally_flag(root, "TFISADMIN"),
            serial_number=serial,
            edition=edition,
        )


def _first_tag_text(root: ET.Element, tag: str) -> str | None:
    for el in root.iter(tag):
        value = text_of(el)
        if value is not None:
            return value
    return None


def _tally_flag(root: ET.Element, tag: str) -> bool | None:
    """A computed Yes/No field, or ``None`` when this build did not report it.

    The three-way answer is the point. Most of these names are absent on older
    TallyPrime builds, and treating "did not say" as "No" would warn a customer
    about a perfectly healthy install.
    """
    raw = _first_tag_text(root, tag)
    return None if raw is None else parse_bool(raw)


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
                        # The party's mobile, which a shared invoice prints
                        # under the buyer. Unlike the phone it does answer as
                        # a native method: verified live 2026-10-14 on "D.D
                        # Enterprises", where it returned 2233556688 for
                        # DULHAN SAREE HOUSE.
                        "LedgerMobile",
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
                    mobile=find_text(el, "LEDGERMOBILE"),
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
                    # Tax attributes live on dated child lists, one entry per
                    # time the shop changed them, so NATIVEMETHOD does not
                    # reach them. ``HSNCode`` and ``GSTRate`` are still asked
                    # for on the item itself, where an older release keeps
                    # them.
                    #
                    # The HSN is in ``HSNDetails``, not ``GSTDetails``.
                    # Verified live 2026-10-12 on "D.D Enterprises": the
                    # ``HSNCODE`` inside ``GSTDETAILS.LIST`` was empty on all
                    # 144 items, and the code sat in ``HSNDETAILS.LIST``, which
                    # nothing asked for. The rate was being looked for on the
                    # item too, and it is three levels down. So every item came
                    # back with no HSN and no rate.
                    #
                    # Naming the leaves does not make this one smaller: Tally
                    # returns the whole of ``GSTDETAILS.LIST`` either way
                    # (406 KB for those 144 items). They are named so the
                    # request says what the parser reads.
                    fetch=[
                        "HSNCode",
                        "GSTRate",
                        "HSNDetails.ApplicableFrom",
                        "HSNDetails.HSNCode",
                        "GSTDetails.ApplicableFrom",
                        "GSTDetails.StatewiseDetails.RateDetails.GSTRateDutyHead",
                        "GSTDetails.StatewiseDetails.RateDetails.GSTRate",
                    ],
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
                    hsn_code=_item_hsn(el),
                    gst_rate=_item_gst_rate(el),
                )
            )
        return items


def _latest(el: ET.Element, path: str) -> list[ET.Element]:
    """A dated child list, newest first.

    Tally appends an entry each time the shop changes an item's tax details and
    stamps it ``APPLICABLEFROM``; the one in force is the latest. ``yyyymmdd``
    sorts as text, and an entry with no date sorts last.
    """
    return sorted(
        el.findall(path),
        key=lambda entry: find_text(entry, "APPLICABLEFROM") or "",
        reverse=True,
    )


def _item_hsn(el: ET.Element) -> str | None:
    for entry in _latest(el, "./HSNDETAILS.LIST"):
        code = find_text(entry, "HSNCODE")
        if code:
            return code
    return find_text(el, "HSNCODE")


def _item_gst_rate(el: ET.Element) -> float | None:
    """The full GST rate on a stock item, or ``None`` when it does not say.

    The integrated rate, for the same reason as on a voucher line: it is the
    whole rate whichever way a sale is taxed. A zero is read as "not set here"
    rather than as nil-rated -- an item taxed "as per stock group" carries the
    same row of zeroes, and a wrong 0% on a tax document is worse than a gap
    the invoice's own line fills in.
    """
    for entry in _latest(el, "./GSTDETAILS.LIST"):
        rates: dict[str, float] = {}
        for detail in entry.findall("./STATEWISEDETAILS.LIST/RATEDETAILS.LIST"):
            head = (find_text(detail, "GSTRATEDUTYHEAD") or "").strip().upper()
            rate = find_text(detail, "GSTRATE")
            if head and rate:
                rates[head] = parse_float(rate)
        rate = rates.get("IGST") or (rates.get("CGST", 0.0) + rates.get("SGST/UTGST", 0.0))
        if rate > 0:
            return rate
    flat = find_text(el, "GSTRATE")
    return parse_float(flat) if flat else None


# --------------------------------------------------------------------------
# Stock by godown
# --------------------------------------------------------------------------


class GodownStockParams(QueryParams):
    pass


@register
class GodownStockQuery(TallyQuery[GodownStockParams, list[GodownStock]]):
    """Closing stock, split by the godown it sits in.

    A stock item's own ``ClosingBalance`` is the company-wide figure and says
    nothing about place. Tally keeps the per-place position on its ``Batch``
    objects -- one per item, godown and batch, and an item with batches turned
    off still has one, named "Primary Batch" -- so this walks those and folds
    the batches of one item in one godown together.

    **A ``Batch`` collection is empty unless it names the stock item it
    belongs to.** Verified live 2026-10-11 on "D.D Enterprises": a bare
    ``<TYPE>Batch</TYPE>`` returned ``<COLLECTION></COLLECTION>`` with no error
    for a company holding stock in four godowns, and so did ``BELONGSTO`` and a
    ``CHILDOF`` of Primary. That is what this query sent until then, and it is
    why the Godown Summary had nothing to show. ``<CHILDOF>`` a named item
    returns that item's batches, and writing it as ``$Name`` and walking it from
    a stock-item collection returns every item's in one request -- 38 KB for
    144 items, where asking item by item would have been 144 exports against a
    shop's till.

    The same run reconciled: every item's godown quantities added up to its own
    closing balance, and the figures matched Tally's Godown Summary line for
    line. ``tally-connector livecheck`` repeats that comparison.

    Balances are always current, like the stock summary's -- there is no period
    to scope this to.
    """

    name = "godown_stock.list"
    params_model = GodownStockParams
    heavy = True

    def build(self, params: GodownStockParams) -> str:
        return build_export_envelope(
            request_type="Collection",
            request_id="TFGodownStock",
            static_variables=StaticVariables(company=params.company),
            collections=[
                Collection(name="TFGodownItems", type="StockItem", native_methods=["Name"]),
                Collection(
                    name="TFGodownBatches",
                    type="Batch",
                    # Evaluated once per stock item as the walk below reaches
                    # it. Without a parent this collection is always empty.
                    child_of="$Name",
                    # Leaf fields by name, as everywhere else: a batch object
                    # also carries its own inward and outward history.
                    native_methods=[
                        "Name",
                        "Parent",
                        "GodownName",
                        "ClosingBalance",
                        "ClosingValue",
                    ],
                ),
                Collection(
                    name="TFGodownStock",
                    source_collection="TFGodownItems",
                    walk=["TFGodownBatches"],
                    # A walked object exports only what is computed onto it, so
                    # these four are the whole of each row.
                    compute={
                        "TFItem": "$Parent",
                        "TFGodown": "$GodownName",
                        "TFQuantity": "$ClosingBalance",
                        "TFValue": "$ClosingValue",
                    },
                ),
            ],
        )

    def parse(self, root: ET.Element, params: GodownStockParams) -> list[GodownStock]:
        quantities: dict[tuple[str, str], float] = {}
        values: dict[tuple[str, str], Money] = {}

        for el in _iter_objects(root, "BATCH"):
            item = find_text(el, "TFITEM")
            godown = find_text(el, "TFGODOWN")
            if not item or not godown:
                continue
            key = (godown, item)
            quantities[key] = quantities.get(key, 0.0) + parse_float(
                find_text(el, "TFQUANTITY")
            )
            values[key] = values.get(key, Money.zero()) + Money.from_tally(
                find_text(el, "TFVALUE")
            )

        return [
            GodownStock(
                godown=godown,
                item_name=item,
                quantity=quantity,
                value=values[(godown, item)],
            )
            for (godown, item), quantity in quantities.items()
        ]
