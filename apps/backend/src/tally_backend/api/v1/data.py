"""Dashboard and report endpoints.

Every response is a :class:`DataEnvelope` carrying ``meta`` alongside ``data``.
That is a hard rule in this product: a number without an "as of" is a number an
owner might act on believing it is current, and the whole design serves reads
from snapshots.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request
from tally_core.domain.masters import VoucherTypeKind
from tally_core.domain.transactions import OutstandingKind

from ...core.errors import AppError, NotFound
from ...db.models import Company
from ...services import analytics as an
from ...services.audit import record
from ...services.dashboard import voucher_kinds
from ...services.reads import DataResult, FetchMode
from ...services.sync import SyncCoordinator
from ..deps import (
    CompanyDep,
    DashboardServiceDep,
    PrincipalDep,
    ReadServiceDep,
    SessionDep,
    SettingsDep,
    SyncDep,
)
from ..schemas import DataEnvelope, FetchModeParam

router = APIRouter(prefix="/companies/{company_id}", tags=["data"])

#: Guard rail on report windows. A five-year day book is tens of megabytes of
#: XML and would block the shop's Tally for minutes; nobody reads that on a phone.
MAX_REPORT_DAYS = 400


def _mode(value: FetchModeParam) -> FetchMode:
    return FetchMode(value)


async def _resolved(sync: SyncCoordinator, company: Company, value: FetchModeParam) -> FetchMode:
    """Turn a hard refresh into a sync, then read what the sync published.

    A pull-to-refresh used to mean "ask Tally for every dataset again, in full"
    -- the most expensive request the app can make, wired to the button people
    press when a figure looks wrong. For a company with history it now runs the
    incremental delta instead: markers, the vouchers whose AlterID moved, and
    the balances those vouchers touched.

    Falls back to the old behaviour when there is no history to be incremental
    about, which is the first launch after linking a company.

    ``AUTO`` rather than ``CACHED`` on the way out, deliberately: a dataset the
    delta cannot refresh -- outstanding bills have no change id to sync against
    -- must still be allowed to fall through to Tally rather than be served
    indefinitely from a stale snapshot.
    """
    mode = _mode(value)
    if mode is not FetchMode.LIVE:
        return mode
    return FetchMode.AUTO if await sync.refresh(company.id) else FetchMode.LIVE


def _oldest_meta(results: list[DataResult]) -> dict[str, Any]:
    """Freshness of the oldest dataset behind a multi-dataset report.

    Quoting the fresher of the two would stamp "just now" over a report whose
    other half was read hours ago -- the same rule the dashboard header follows.
    """
    meta = max(results, key=lambda r: r.age_seconds).meta()
    meta["is_stale"] = any(r.is_stale for r in results)
    meta["connector_online"] = any(r.connector_online for r in results)
    return meta


def _clamp_window(from_date: date, to_date: date) -> tuple[date, date]:
    if from_date > to_date:
        from_date, to_date = to_date, from_date
    if (to_date - from_date).days > MAX_REPORT_DAYS:
        from_date = to_date - timedelta(days=MAX_REPORT_DAYS)
    return from_date, to_date


@router.get("/dashboard", response_model=dict)
async def dashboard(
    company: CompanyDep,
    sync: SyncDep,
    principal: PrincipalDep,
    session: SessionDep,
    service: DashboardServiceDep,
    request: Request,
    mode: FetchModeParam = "auto",
    as_of: date | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
) -> dict:
    """Everything the home screen needs, in one round trip.

    One endpoint rather than a tile-per-request: a phone on mobile data pays
    more for eight requests than for one slightly larger response, and eight
    separate calls would each race to refresh the same underlying datasets.

    ``from_date``/``to_date`` scope the voucher-derived sections to a period.
    Either bound alone is taken as the whole span up to the other. Omitting
    both is the ordinary dashboard, and is left untouched so it keeps hitting
    the snapshot the background refresher warms.

    ``as_of`` remains what counts as "today" -- it anchors month-to-date, the
    ageing on outstanding bills, and the trend. A period sets that anchor to
    its end date, so the two never disagree.
    """
    resolved = await _resolved(sync, company, mode)
    started = time.monotonic()

    period: tuple[date, date] | None = None
    if from_date is not None or to_date is not None:
        anchor = as_of or date.today()
        period = _clamp_window(from_date or anchor, to_date or anchor)

    # A period's end date is the only sensible "today" for the rest of the
    # dashboard: ageing a bill against the real today while reporting March's
    # sales beside it would put two dates in one glance.
    today = period[1] if period is not None else (as_of or date.today())
    payload = await service.build(company, today=today, period=period, mode=resolved)

    await record(
        session,
        action="dashboard.view",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={
            "mode": mode,
            "as_of": today.isoformat(),
            **(
                {"from": period[0].isoformat(), "to": period[1].isoformat()}
                if period is not None
                else {}
            ),
        },
        duration_ms=int((time.monotonic() - started) * 1000),
        request=request,
    )
    return payload


@router.get("/reports/daybook", response_model=DataEnvelope)
async def daybook(
    company: CompanyDep,
    sync: SyncDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    from_date: date,
    to_date: date,
    mode: FetchModeParam = "auto",
    kind: Annotated[VoucherTypeKind | None, Query()] = None,
) -> DataEnvelope:
    resolved = await _resolved(sync, company, mode)
    from_date, to_date = _clamp_window(from_date, to_date)
    # Store-backed when the history sync has covered this window, which is what
    # makes scrolling back through past months free. Before a backfill, and for
    # a deliberate live refresh, it falls through to the ordinary snapshot path.
    result = await reads.fetch_vouchers(
        company, from_date=from_date, to_date=to_date, mode=resolved
    )

    vouchers = an.effective(an.parse_vouchers(result.payload))
    # The voucher rows name their type but do not carry its accounting class, so
    # filtering by `kind` against the parser's name-based guess silently drops
    # every voucher whose type the shop renamed.
    vouchers = an.classify(vouchers, await voucher_kinds(reads, company, resolved))
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
    sync: SyncDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    kind: OutstandingKind = OutstandingKind.RECEIVABLE,
    mode: FetchModeParam = "auto",
    as_of: date | None = None,
) -> DataEnvelope:
    """Who owes me money -- with ageing, which is the part that matters."""
    resolved = await _resolved(sync, company, mode)
    today = as_of or date.today()
    result = await reads.fetch(
        company,
        dataset="outstanding.bills",
        params={"as_of": today.isoformat()},
        mode=resolved,
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
            "bills": [an.bill_line(bill, today) for bill in relevant],
        },
        meta=result.meta(),
    )


@router.get("/reports/outstanding/group", response_model=DataEnvelope)
async def outstanding_by_group(
    company: CompanyDep,
    sync: SyncDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    kind: OutstandingKind = OutstandingKind.RECEIVABLE,
    group: str | None = None,
    mode: FetchModeParam = "auto",
    as_of: date | None = None,
) -> DataEnvelope:
    """Outstanding for the parties under one ledger group.

    Tally's Group Outstanding, and a genuinely different question from
    ``/reports/outstanding``: that one classifies each *bill* by the side it
    closes on, this one classifies each *party* by the group its ledger sits
    under. A customer advance appears as a payable there and as an advance
    against a debtor here, and both are correct answers to different questions.

    ``group`` defaults to the stock Indian group for the kind. A company that
    renamed its groups passes its own name; nothing here assumes the default
    exists.
    """
    resolved = await _resolved(sync, company, mode)
    today = as_of or date.today()
    # Named apart from `resolved`, which is the fetch mode. Assigning the group
    # over it sent a ledger-group name where a FetchMode belongs: it matched no
    # branch in ReadService.fetch, so this report skipped the snapshot entirely
    # and re-exported bills from the shop's Tally on every open -- and had
    # nothing to show at all when that PC was off.
    party_group = group or an.DEFAULT_PARTY_GROUP[kind]

    bills_result = await reads.fetch(
        company,
        dataset="outstanding.bills",
        params={"as_of": today.isoformat()},
        mode=resolved,
        heavy=True,
    )
    # Group membership lives on the ledger master, not on the bill, so this
    # report needs both datasets. Sequential, not gathered: one AsyncSession.
    ledgers_result = await reads.fetch(
        company, dataset="ledgers.list", mode=resolved
    )

    data = an.group_outstanding(
        an.parse_bills(bills_result.payload),
        an.parse_ledgers(ledgers_result.payload),
        group=party_group,
        kind=kind,
        as_of=today,
    )
    data["as_of"] = today.isoformat()

    await record(
        session,
        action="report.outstanding_group",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={"kind": str(kind), "group": party_group},
        request=request,
    )

    return DataEnvelope(data=data, meta=_oldest_meta([bills_result, ledgers_result]))


@router.get("/reports/stock", response_model=DataEnvelope)
async def stock(
    company: CompanyDep,
    sync: SyncDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    mode: FetchModeParam = "auto",
    only: Annotated[str | None, Query(pattern="^(low|negative)$")] = None,
) -> DataEnvelope:
    resolved = await _resolved(sync, company, mode)
    result = await reads.fetch(
        company, dataset="stock_items.list", mode=resolved, heavy=True
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
    sync: SyncDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    mode: FetchModeParam = "auto",
    group: str | None = None,
) -> DataEnvelope:
    resolved = await _resolved(sync, company, mode)
    result = await reads.fetch(
        company,
        dataset="ledgers.list",
        params={"group": group} if group else None,
        mode=resolved,
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
    sync: SyncDep,
    reads: ReadServiceDep,
    days: int = 90,
    mode: FetchModeParam = "auto",
) -> DataEnvelope:
    """Stock on hand that has not sold in the window.

    Answers CLAUDE.md's "which products aren't selling?".
    """
    resolved = await _resolved(sync, company, mode)
    today = date.today()
    days = max(7, min(days, MAX_REPORT_DAYS))

    stock_result = await reads.fetch(
        company, dataset="stock_items.list", mode=resolved, heavy=True
    )
    voucher_result = await reads.fetch_vouchers(
        company,
        from_date=today - timedelta(days=days),
        to_date=today,
        mode=resolved,
    )

    items = an.parse_stock(stock_result.payload)
    vouchers = an.effective(an.parse_vouchers(voucher_result.payload))
    # This report keys entirely off `kind is SALES`. Misclassified sales make
    # every item look untouched, so the answer to "which products aren't
    # selling?" becomes the whole catalogue.
    vouchers = an.classify(vouchers, await voucher_kinds(reads, company, resolved))
    return DataEnvelope(
        data={
            "window_days": days,
            "items": an.slow_moving(items, vouchers, limit=50),
        },
        meta=stock_result.meta(),
    )



# --------------------------------------------------------------------------
# Drill-down
# --------------------------------------------------------------------------
#
# Four reads that answer "show me this one, in full". They share two rules.
#
# **Stored history only.** Every one of them passes ``FetchMode.CACHED``, never
# the caller's mode. These screens are reached by tapping a row, and a customer
# opening ten vouchers in a row must not queue ten exports against the PC that
# is also running their till. A window the sync has not covered says so and
# offers a refresh, which is a better outcome than a frozen Tally.
#
# **The same window clamp as every other report.** A drill-down is not a licence
# to ask for five years of vouchers.


def _detail_window(from_date: date | None, to_date: date | None) -> tuple[date, date]:
    """Default a drill-down window to the last quarter, then clamp it."""
    end = to_date or date.today()
    start = from_date or (end - timedelta(days=90))
    return _clamp_window(start, end)


@router.get("/reports/voucher", response_model=DataEnvelope)
async def voucher_detail(
    company: CompanyDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    settings: SettingsDep,
    request: Request,
    key: str,
    on: date,
) -> DataEnvelope:
    """One voucher, with every ledger line and stock line it posted.

    ``on`` narrows the search to a single day, which is what keeps this cheap:
    the store is indexed by (company, date), so finding one voucher never scans
    a company's history. It comes from the row the user tapped, so the app
    always has it.
    """
    # History first, by key -- which answers even while a backfill is still
    # running. Only if the store has never seen this voucher does it fall back
    # to whatever snapshot covers that day.
    result = await reads.fetch_voucher(company, key=key, on=on)
    if result is None:
        result = await reads.fetch_vouchers(
            company, from_date=on, to_date=on, mode=FetchMode.CACHED
        )

    vouchers = an.parse_vouchers(result.payload)
    vouchers = an.classify(vouchers, await voucher_kinds(reads, company, FetchMode.CACHED))
    found = an.find_voucher(vouchers, key)

    if found is None:
        # The day was readable but this voucher was not in it, which in practice
        # means it was changed or deleted in Tally since the row was drawn. Say
        # that rather than a bare "not found": the user is looking at the row.
        raise NotFound(
            f"voucher {key} not present on {on.isoformat()}",
            user_message=(
                "This voucher is no longer in your books for that date. It may "
                "have been changed or deleted in Tally since this list was read."
            ),
        )

    await record(
        session,
        action="report.voucher",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={"on": on.isoformat()},
        request=request,
    )

    # The sync stores no stock lines for slices older than this, so an empty
    # item list on an old voucher has to be reported as "not kept" rather than
    # drawn as a voucher that sold nothing.
    inventory_kept = on >= date.today() - timedelta(days=settings.sync_inventory_days)

    return DataEnvelope(
        data=an.voucher_detail(found, inventory_kept=inventory_kept),
        meta=result.meta(),
    )


@router.get("/reports/ledger-statement", response_model=DataEnvelope)
async def ledger_statement(
    company: CompanyDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    ledger: str,
    from_date: date | None = None,
    to_date: date | None = None,
) -> DataEnvelope:
    """Every voucher that touched one ledger, with a running total.

    The ledger's own closing balance rides along separately and is labelled
    "today" by the app. It is not the end of the running column and must never
    be presented as though it were: Tally evaluates a closing balance against
    the current date whatever window was asked for.
    """
    start, end = _detail_window(from_date, to_date)

    voucher_result = await reads.fetch_vouchers(
        company, from_date=start, to_date=end, mode=FetchMode.CACHED
    )
    vouchers = an.effective(an.parse_vouchers(voucher_result.payload))
    vouchers = an.classify(vouchers, await voucher_kinds(reads, company, FetchMode.CACHED))

    statement = an.ledger_statement(vouchers, ledger=ledger, since=start, until=end)

    # Today's balance is a second dataset with its own age, and it is the
    # optional half of this screen: the statement is the movement, and a missing
    # master read must degrade to "we cannot show today's balance" rather than
    # taking the vouchers down with it.
    ledger_result: DataResult | None = None
    match = None
    try:
        ledger_result = await reads.fetch(
            company, dataset="ledgers.list", mode=FetchMode.CACHED
        )
        match = next(
            (
                led
                for led in an.parse_ledgers(ledger_result.payload)
                if led.name.strip().lower() == ledger.strip().lower()
            ),
            None,
        )
    except AppError:
        pass

    # None, never zero. "We could not read the balance" and "the balance is nil"
    # are different statements and the app renders them differently.
    statement["closing_balance"] = (
        an.money_out(match.closing_balance) if match is not None else None
    )
    statement["group"] = match.parent_group if match is not None else None

    await record(
        session,
        action="report.ledger_statement",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={"ledger": ledger, "from": start.isoformat(), "to": end.isoformat()},
        request=request,
    )

    return DataEnvelope(
        data=statement,
        meta=_oldest_meta(
            [voucher_result, ledger_result] if ledger_result else [voucher_result]
        ),
    )


@router.get("/reports/register", response_model=DataEnvelope)
async def register(
    company: CompanyDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    from_date: date,
    to_date: date,
    kind: VoucherTypeKind = VoucherTypeKind.SALES,
) -> DataEnvelope:
    """A sales or purchase register: the vouchers, by month and by party."""
    start, end = _clamp_window(from_date, to_date)

    result = await reads.fetch_vouchers(
        company, from_date=start, to_date=end, mode=FetchMode.CACHED
    )
    vouchers = an.effective(an.parse_vouchers(result.payload))
    # Without this every shop that renamed "Sales" to "Tax Invoice" gets an
    # empty register rather than a wrong one, which is at least honest and
    # entirely useless.
    vouchers = an.classify(vouchers, await voucher_kinds(reads, company, FetchMode.CACHED))

    await record(
        session,
        action="report.register",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={"kind": str(kind), "from": start.isoformat(), "to": end.isoformat()},
        request=request,
    )

    return DataEnvelope(
        data=an.register(vouchers, kind, since=start, until=end),
        meta=result.meta(),
    )


@router.get("/reports/stock/movement", response_model=DataEnvelope)
async def stock_movement(
    company: CompanyDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    item: str,
    from_date: date | None = None,
    to_date: date | None = None,
) -> DataEnvelope:
    """What came in and what went out for one stock item."""
    start, end = _detail_window(from_date, to_date)

    result = await reads.fetch_vouchers(
        company, from_date=start, to_date=end, mode=FetchMode.CACHED
    )
    vouchers = an.effective(an.parse_vouchers(result.payload))
    # Direction is read off the voucher's class, so a misclassified voucher
    # would land in the wrong column rather than merely in the wrong total.
    vouchers = an.classify(vouchers, await voucher_kinds(reads, company, FetchMode.CACHED))

    await record(
        session,
        action="report.stock_movement",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={"item": item, "from": start.isoformat(), "to": end.isoformat()},
        request=request,
    )

    return DataEnvelope(
        data=an.item_movement(vouchers, item=item, since=start, until=end),
        meta=result.meta(),
    )


def _sum_values(vouchers: list) -> object:
    from tally_core.domain.money import Money

    total = Money.zero()
    for voucher in vouchers:
        total = total + an.voucher_value(voucher)
    return total
