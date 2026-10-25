"""The portal's sales report: who sold how many of which plan, and for how much.

Read from ``plan_sales`` -- one row per plan sold, with its price and partner
copied in at the moment of sale -- so the figures for a closed month never move
when a price changes or an account is transferred.

Scoped like every other portal route: a partner sees their own line and
nothing else, an owner sees every partner plus "Direct" for sales made on
accounts nobody's code brought in. It counts plans and rupees of *our*
revenue; it never reaches a customer's books.

Beside the sales in the window, each line carries the partner's book as it
stands today -- live plans, plans ending in the next 30 days, trials and
signups waiting -- because "what did you sell" is only half of what a
partner review asks; "what is about to lapse" is the other half.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from ...core.errors import InvalidInput
from ...db.models import (
    Organisation,
    OrgStatus,
    PlanSale,
    PlatformRole,
    PlatformUser,
    as_utc,
    utc_now,
)
from ...services.plans import CATALOGUE
from ...services.trial import is_on_trial
from ...services.usage import BUSINESS_TZ
from ..deps import PlatformPrincipalDep, SessionDep
from ..portal_schemas import (
    MonthSales,
    PartnerSalesRow,
    PlanResponse,
    SalesFigures,
    SalesReportResponse,
)

router = APIRouter(prefix="/portal", tags=["portal"])

#: A sale on the evening of 31 March in Mumbai is a March sale, though it is
#: already 1 April nowhere -- and still 31 March in UTC. Days are Indian days,
#: as on the usage view, and with the usage view's fixed +05:30 rather than
#: ``ZoneInfo("Asia/Kolkata")``: India has no daylight saving, and Windows and
#: slim images ship no tz database, so the named zone failed to import there.
_IST = BUSINESS_TZ

EXPIRING_WITHIN_DAYS = 30


def _financial_year_containing(day: date) -> tuple[date, date]:
    start_year = day.year if day.month >= 4 else day.year - 1
    return date(start_year, 4, 1), date(start_year + 1, 3, 31)


def _ist_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    start = datetime.combine(date_from, time.min, tzinfo=_IST).astimezone(UTC)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=_IST).astimezone(UTC)
    return start, end


def _add(figures: SalesFigures, sale: PlanSale) -> None:
    figures.count += 1
    if sale.kind == "renewal":
        figures.renewals += 1
    else:
        figures.new += 1
    figures.price_paise += sale.price_paise
    figures.gst_paise += sale.gst_paise
    figures.total_paise += sale.total_paise


def _empty_row(partner_id: str | None, name: str, code: str | None) -> PartnerSalesRow:
    return PartnerSalesRow(
        partner_id=partner_id,
        partner_name=name,
        partner_code=code,
        plans={plan.code.value: SalesFigures() for plan in CATALOGUE.values()},
        total=SalesFigures(),
    )


@router.get("/reports/sales", response_model=SalesReportResponse)
async def sales_report(
    principal: PlatformPrincipalDep,
    session: SessionDep,
    date_from: Annotated[date | None, Query(alias="from")] = None,
    date_to: Annotated[date | None, Query(alias="to")] = None,
) -> SalesReportResponse:
    """Plans sold between two dates (inclusive), by partner, by plan and by month.

    Defaults to the financial year we are in -- April to March, the year every
    customer of this product keeps their books in.
    """
    today = utc_now().astimezone(_IST).date()
    default_from, default_to = _financial_year_containing(today)
    date_from = date_from or default_from
    date_to = date_to or default_to
    if date_to < date_from:
        raise InvalidInput(
            "report window ends before it starts",
            user_message="The end date is before the start date.",
        )
    if (date_to - date_from).days > 3 * 366:
        raise InvalidInput(
            "report window too long",
            user_message="Choose a window of three years or less.",
        )
    start, end = _ist_bounds(date_from, date_to)
    scope = principal.partner_scope

    sales_query = select(PlanSale).where(
        PlanSale.created_at >= start, PlanSale.created_at < end
    )
    if scope is not None:
        sales_query = sales_query.where(PlanSale.partner_id == scope)
    sales = (await session.execute(sales_query.order_by(PlanSale.created_at))).scalars().all()

    # Every portal person who could own accounts, so a partner who sold nothing
    # this month still appears with a row of zeros -- "nothing" is a finding.
    people_query = select(PlatformUser).order_by(PlatformUser.created_at)
    if scope is not None:
        people_query = people_query.where(PlatformUser.id == scope)
    people = (await session.execute(people_query)).scalars().all()

    rows: dict[str | None, PartnerSalesRow] = {
        person.id: _empty_row(person.id, person.full_name or person.email, person.partner_code)
        for person in people
    }
    if scope is None:
        rows[None] = _empty_row(None, "Direct (no partner)", None)

    months: dict[str, MonthSales] = {}
    for sale in sales:
        row = rows.get(sale.partner_id)
        if row is None:
            # A partner removed since; their sales still happened.
            row = rows[sale.partner_id] = _empty_row(sale.partner_id, "Former partner", None)
        figures = row.plans.setdefault(sale.plan, SalesFigures())
        _add(figures, sale)
        _add(row.total, sale)

        month = as_utc(sale.created_at).astimezone(_IST).strftime("%Y-%m")
        bucket = months.setdefault(
            month,
            MonthSales(month=month, plans={code.value: 0 for code in CATALOGUE}),
        )
        bucket.plans[sale.plan] = bucket.plans.get(sale.plan, 0) + 1
        bucket.total_paise += sale.total_paise

    # Today's book, per partner.
    org_query = select(Organisation).where(Organisation.is_demo.is_(False))
    if scope is not None:
        org_query = org_query.where(Organisation.partner_id == scope)
    now = utc_now()
    soon = now + timedelta(days=EXPIRING_WITHIN_DAYS)
    book: dict[str | None, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for org in (await session.execute(org_query)).scalars().all():
        tally = book[org.partner_id]
        expires = as_utc(org.expires_at) if org.expires_at else None
        if org.status is OrgStatus.PENDING:
            tally["awaiting_approval"] += 1
        elif org.status is OrgStatus.ACTIVE and is_on_trial(org):
            if expires is None or expires > now:
                tally["on_trial"] += 1
        elif org.status is OrgStatus.ACTIVE and (expires is None or expires > now):
            tally["active_accounts"] += 1
            if expires is not None and expires <= soon:
                tally["expiring_soon"] += 1
    for partner_id, tally in book.items():
        row = rows.get(partner_id)
        if row is None:
            continue
        row.active_accounts = tally["active_accounts"]
        row.expiring_soon = tally["expiring_soon"]
        row.on_trial = tally["on_trial"]
        row.awaiting_approval = tally["awaiting_approval"]

    totals = _empty_row(None, "All partners", None)
    for row in rows.values():
        for code, figures in row.plans.items():
            target = totals.plans.setdefault(code, SalesFigures())
            for field in ("count", "new", "renewals", "price_paise", "gst_paise", "total_paise"):
                setattr(target, field, getattr(target, field) + getattr(figures, field))
        for field in ("count", "new", "renewals", "price_paise", "gst_paise", "total_paise"):
            setattr(totals.total, field, getattr(totals.total, field) + getattr(row.total, field))
        for field in ("active_accounts", "expiring_soon", "on_trial", "awaiting_approval"):
            setattr(totals, field, getattr(totals, field) + getattr(row, field))

    # A partner with nothing sold stays listed -- "nothing" is a finding. An
    # owner with nothing is just an operator, and a row of zeros per owner
    # buries the partners the report is about.
    owners = {person.id for person in people if person.role is PlatformRole.OWNER}
    visible = [
        row
        for row in rows.values()
        if row.partner_id not in owners
        or row.total.count
        or row.active_accounts
        or row.on_trial
        or row.awaiting_approval
    ]
    # Best sellers first.
    ordered = sorted(
        visible,
        key=lambda row: (-row.total.total_paise, -row.active_accounts, row.partner_name.lower()),
    )
    return SalesReportResponse(
        date_from=date_from,
        date_to=date_to,
        plans=[
            PlanResponse(
                code=plan.code,
                name=plan.name,
                price_paise=plan.price_paise,
                gst_paise=plan.gst_paise,
                total_paise=plan.total_paise,
                suggested_users=plan.suggested_users,
                suggested_companies=plan.suggested_companies,
            )
            for plan in CATALOGUE.values()
        ],
        partners=ordered,
        totals=totals,
        months=[months[key] for key in sorted(months)],
        expiring_within_days=EXPIRING_WITHIN_DAYS,
    )
