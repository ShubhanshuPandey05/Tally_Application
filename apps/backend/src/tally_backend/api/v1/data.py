"""Dashboard and report endpoints.

Every response is a :class:`DataEnvelope` carrying ``meta`` alongside ``data``.
That is a hard rule in this product: a number without an "as of" is a number an
owner might act on believing it is current, and the whole design serves reads
from snapshots.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Request
from tally_core.domain.masters import VoucherTypeKind
from tally_core.domain.transactions import OutstandingKind

from ...services import analytics as an
from ...services.audit import record
from ...services.dashboard import voucher_kinds
from ...services.reads import FetchMode
from ..deps import (
    CompanyDep,
    DashboardServiceDep,
    PrincipalDep,
    ReadServiceDep,
    SessionDep,
)
from ..schemas import DataEnvelope, FetchModeParam

router = APIRouter(prefix="/companies/{company_id}", tags=["data"])

#: Guard rail on report windows. A five-year day book is tens of megabytes of
#: XML and would block the shop's Tally for minutes; nobody reads that on a phone.
MAX_REPORT_DAYS = 400


def _mode(value: FetchModeParam) -> FetchMode:
    return FetchMode(value)


def _clamp_window(from_date: date, to_date: date) -> tuple[date, date]:
    if from_date > to_date:
        from_date, to_date = to_date, from_date
    if (to_date - from_date).days > MAX_REPORT_DAYS:
        from_date = to_date - timedelta(days=MAX_REPORT_DAYS)
    return from_date, to_date


@router.get("/dashboard", response_model=dict)
async def dashboard(
    company: CompanyDep,
    principal: PrincipalDep,
    session: SessionDep,
    service: DashboardServiceDep,
    request: Request,
    mode: FetchModeParam = "auto",
    as_of: date | None = None,
) -> dict:
    """Everything the home screen needs, in one round trip.

    One endpoint rather than a tile-per-request: a phone on mobile data pays
    more for eight requests than for one slightly larger response, and eight
    separate calls would each race to refresh the same underlying datasets.
    """
    started = time.monotonic()
    today = as_of or date.today()
    payload = await service.build(company, today=today, mode=_mode(mode))

    await record(
        session,
        action="dashboard.view",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={"mode": mode, "as_of": today.isoformat()},
        duration_ms=int((time.monotonic() - started) * 1000),
        request=request,
    )
    return payload


@router.get("/reports/daybook", response_model=DataEnvelope)
async def daybook(
    company: CompanyDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    from_date: date,
    to_date: date,
    mode: FetchModeParam = "auto",
    kind: Annotated[VoucherTypeKind | None, Query()] = None,
) -> DataEnvelope:
    from_date, to_date = _clamp_window(from_date, to_date)
    result = await reads.fetch(
        company,
        dataset="vouchers.list",
        params={
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "include_inventory": True,
        },
        mode=_mode(mode),
        heavy=True,
    )

    vouchers = an.effective(an.parse_vouchers(result.payload))
    # The voucher rows name their type but do not carry its accounting class, so
    # filtering by `kind` against the parser's name-based guess silently drops
    # every voucher whose type the shop renamed.
    vouchers = an.classify(vouchers, await voucher_kinds(reads, company, _mode(mode)))
    if kind is not None:
        vouchers = [v for v in vouchers if v.kind is kind]

    total = an.money_out(
        an.magnitude(_sum_values(vouchers)),
    )
    await record(
        session,
        action="report.daybook",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={"from": from_date.isoformat(), "to": to_date.isoformat(), "kind": kind},
        request=request,
    )

    return DataEnvelope(
        data={
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "voucher_count": len(vouchers),
            "total": total,
            "vouchers": an.recent_transactions(vouchers, limit=500),
        },
        meta=result.meta(),
    )


@router.get("/reports/outstanding", response_model=DataEnvelope)
async def outstanding(
    company: CompanyDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    kind: OutstandingKind = OutstandingKind.RECEIVABLE,
    mode: FetchModeParam = "auto",
    as_of: date | None = None,
) -> DataEnvelope:
    """Who owes me money -- with ageing, which is the part that matters."""
    today = as_of or date.today()
    result = await reads.fetch(
        company,
        dataset="outstanding.bills",
        params={"as_of": today.isoformat()},
        mode=_mode(mode),
        heavy=True,
    )

    bills = an.parse_bills(result.payload)
    relevant = [b for b in bills if b.kind is kind]
    relevant.sort(key=lambda b: b.days_overdue(today), reverse=True)

    summary = an.outstanding_summary(bills, kind, as_of=today)
    await record(
        session,
        action="report.outstanding",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={"kind": str(kind)},
        request=request,
    )

    return DataEnvelope(
        data={
            "as_of": today.isoformat(),
            "kind": str(kind),
            "summary": summary,
            "bills": [
                {
                    "party": bill.party_name,
                    "bill_name": bill.bill_name,
                    "bill_date": bill.bill_date.isoformat() if bill.bill_date else None,
                    "due_date": bill.due_date.isoformat() if bill.due_date else None,
                    "amount": an.money_out(an.magnitude(bill.pending_amount)),
                    "days_overdue": bill.days_overdue(today),
                    "ageing_bucket": bill.ageing_bucket(today),
                    "is_advance": bill.is_advance,
                }
                for bill in relevant
            ],
        },
        meta=result.meta(),
    )


@router.get("/reports/stock", response_model=DataEnvelope)
async def stock(
    company: CompanyDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    mode: FetchModeParam = "auto",
    only: Annotated[str | None, Query(pattern="^(low|negative)$")] = None,
) -> DataEnvelope:
    result = await reads.fetch(
        company, dataset="stock_items.list", mode=_mode(mode), heavy=True
    )
    items = an.parse_stock(result.payload)

    if only == "low":
        items = [i for i in items if i.is_below_reorder]
    elif only == "negative":
        items = [i for i in items if i.is_negative_stock]

    items.sort(key=lambda i: i.closing_value.amount, reverse=True)
    await record(
        session,
        action="report.stock",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        request=request,
    )

    return DataEnvelope(
        data={
            "summary": an.inventory_summary(an.parse_stock(result.payload)),
            "items": [
                {
                    "name": item.name,
                    "group": item.parent_group,
                    "quantity": item.closing_quantity,
                    "unit": item.base_unit,
                    "rate": an.money_out(item.closing_rate) if item.closing_rate else None,
                    "value": an.money_out(an.magnitude(item.closing_value)),
                    "reorder_level": item.reorder_level,
                    "is_negative": item.is_negative_stock,
                    "is_below_reorder": item.is_below_reorder,
                }
                for item in items
            ],
        },
        meta=result.meta(),
    )


@router.get("/reports/ledgers", response_model=DataEnvelope)
async def ledgers(
    company: CompanyDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    mode: FetchModeParam = "auto",
    group: str | None = None,
) -> DataEnvelope:
    result = await reads.fetch(
        company,
        dataset="ledgers.list",
        params={"group": group} if group else None,
        mode=_mode(mode),
    )
    rows = an.parse_ledgers(result.payload)
    rows.sort(key=lambda led: led.closing_balance.amount, reverse=True)

    await record(
        session,
        action="report.ledgers",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={"group": group},
        request=request,
    )

    return DataEnvelope(
        data={
            "ledgers": [
                {
                    "name": led.name,
                    "group": led.parent_group,
                    "opening": an.money_out(led.opening_balance),
                    "closing": an.money_out(led.closing_balance),
                    "gstin": led.gstin,
                    "phone": led.phone,
                }
                for led in rows
            ]
        },
        meta=result.meta(),
    )


@router.get("/insights/slow-moving", response_model=DataEnvelope)
async def slow_moving(
    company: CompanyDep,
    reads: ReadServiceDep,
    days: int = 90,
    mode: FetchModeParam = "auto",
) -> DataEnvelope:
    """Stock on hand that has not sold in the window.

    Answers CLAUDE.md's "which products aren't selling?".
    """
    today = date.today()
    days = max(7, min(days, MAX_REPORT_DAYS))

    stock_result = await reads.fetch(
        company, dataset="stock_items.list", mode=_mode(mode), heavy=True
    )
    voucher_result = await reads.fetch(
        company,
        dataset="vouchers.list",
        params={
            "from_date": (today - timedelta(days=days)).isoformat(),
            "to_date": today.isoformat(),
            "include_inventory": True,
        },
        mode=_mode(mode),
        heavy=True,
    )

    items = an.parse_stock(stock_result.payload)
    vouchers = an.effective(an.parse_vouchers(voucher_result.payload))
    # This report keys entirely off `kind is SALES`. Misclassified sales make
    # every item look untouched, so the answer to "which products aren't
    # selling?" becomes the whole catalogue.
    vouchers = an.classify(vouchers, await voucher_kinds(reads, company, _mode(mode)))
    return DataEnvelope(
        data={
            "window_days": days,
            "items": an.slow_moving(items, vouchers, limit=50),
        },
        meta=stock_result.meta(),
    )


def _sum_values(vouchers: list) -> object:
    from tally_core.domain.money import Money

    total = Money.zero()
    for voucher in vouchers:
        total = total + an.voucher_value(voucher)
    return total
