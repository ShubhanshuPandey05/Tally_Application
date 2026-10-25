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
from tally_core.domain.masters import (
    CompanyProfile,
    Ledger,
    StockItem,
    VoucherTypeKind,
)
from tally_core.domain.money import Money
from tally_core.domain.transactions import OutstandingKind, Voucher

from ...core.errors import AppError, NotFound
from ...db.models import Company, CompanySyncState
from ...services import analytics as an
from ...services import bill_adjustments
from ...services import top_reports as top
from ...services.audit import record
from ...services.dashboard import voucher_kinds
from ...services.demo_books import financial_year_start
from ...services.reads import DataResult, FetchMode, NoDataYet, ReadService
from ...services.sync import HISTORY_VERSION, SyncCoordinator
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
    results = [bills_result, ledgers_result]
    # The group tree only nests sub-groups. A company whose tree has not been
    # read yet still gets its report, counted on direct membership, rather
    # than an error screen over figures we do have.
    groups = None
    try:
        groups_result = await reads.fetch(company, dataset="groups.list", mode=resolved)
    except NoDataYet:
        pass
    else:
        groups = an.parse_groups(groups_result.payload)
        results.append(groups_result)

    data = an.group_outstanding(
        an.parse_bills(bills_result.payload),
        an.parse_ledgers(ledgers_result.payload),
        group=party_group,
        kind=kind,
        as_of=today,
        groups=groups,
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

    return DataEnvelope(data=data, meta=_oldest_meta(results))


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


@router.get("/reports/stock/godowns", response_model=DataEnvelope)
async def godown_summary(
    company: CompanyDep,
    sync: SyncDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    mode: FetchModeParam = "auto",
) -> DataEnvelope:
    """Stock by the godown it sits in -- Tally's Godown Summary."""
    resolved = await _resolved(sync, company, mode)
    result = await reads.fetch(
        company, dataset="godown_stock.list", mode=resolved, heavy=True
    )
    # Only for the units, so never worth a second export: a missing snapshot
    # costs the report its "Mtr" and "Kg", not its figures.
    try:
        stock_result = await reads.fetch(
            company, dataset="stock_items.list", mode=FetchMode.CACHED, heavy=True
        )
        items = an.parse_stock(stock_result.payload)
    except AppError:
        items = []

    await record(
        session,
        action="report.godown_summary",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        request=request,
    )

    return DataEnvelope(
        data=an.godown_summary(an.parse_godown_stock(result.payload), items),
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

    # History read under the old rules holds no stock lines for slices older
    # than this, so an empty item list on an old voucher there has to be
    # reported as "not kept" rather than drawn as a voucher that sold nothing.
    # A store read under the current rules kept every line.
    sync_state = await session.get(CompanySyncState, company.id)
    inventory_kept = (
        sync_state is not None and sync_state.history_version >= HISTORY_VERSION
    ) or on >= date.today() - timedelta(days=settings.sync_inventory_days)

    return DataEnvelope(
        data=an.voucher_detail(
            found,
            inventory_kept=inventory_kept,
            party=await _party_master(reads, company, found.party_name),
            items=await _item_masters(reads, company, found),
            company=await _company_profile(reads, company),
        ),
        meta=result.meta(),
    )


@router.get("/reports/voucher/adjustments", response_model=DataEnvelope)
async def voucher_adjustments(
    company: CompanyDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    key: str,
    on: date,
) -> DataEnvelope:
    """The bills one receipt or payment settled, each beside its own figures.

    The "Adjustment Details" table of a printed receipt. Apart from the voucher
    detail because it reads up to two years of stored history to find each
    bill, which the detail screen -- opened far more often than it is shared --
    has no reason to pay for. The app asks only when it builds the print.
    """
    result = await reads.fetch_voucher(company, key=key, on=on)
    if result is None:
        result = await reads.fetch_vouchers(
            company, from_date=on, to_date=on, mode=FetchMode.CACHED
        )
    kinds = await voucher_kinds(reads, company, FetchMode.CACHED)
    found = an.find_voucher(an.classify(an.parse_vouchers(result.payload), kinds), key)
    if found is None:
        raise NotFound(
            f"voucher {key} not present on {on.isoformat()}",
            user_message=(
                "This voucher is no longer in your books for that date. It may "
                "have been changed or deleted in Tally since this list was read."
            ),
        )

    # Two financial years back, then one: a bill is nearly always raised within
    # a year of being paid, and a store that has not yet backfilled that far
    # still answers for this year rather than for nothing. Through the newest
    # voucher held, because a receipt fetched live may be newer than the last
    # sync, and stored_vouchers refuses a window that ends past it.
    history: list[Voucher] | None = None
    year_start = financial_year_start(found.date)
    for start in (year_start.replace(year=year_start.year - 1), year_start):
        payload = await reads.stored_vouchers(company, from_date=start)
        if payload is not None:
            history = an.classify(an.effective(an.parse_vouchers(payload)), kinds)
            break

    await record(
        session,
        action="report.voucher_adjustments",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={"on": on.isoformat()},
        request=request,
    )

    return DataEnvelope(
        data={"bills": bill_adjustments.adjustments(found, history)},
        meta=result.meta(),
    )


# The three lookups below exist for one reason: a voucher a customer shares has
# to carry the seller's address and GSTIN, which no voucher export holds, and a
# buyer and an HSN code for any voucher stored before its own copies of those
# were read. All read snapshots the backend already refreshes, so a shared
# invoice costs the shop's Tally nothing.
#
# All swallow their errors. The voucher is the answer to the request; a missing
# master read must cost the document a line of its header, not the whole screen.


async def _party_master(
    reads: ReadService, company: Company, name: str | None
) -> Ledger | None:
    if not name:
        return None
    try:
        result = await reads.fetch(company, dataset="ledgers.list", mode=FetchMode.CACHED)
    except AppError:
        return None
    wanted = name.strip().lower()
    return next(
        (led for led in an.parse_ledgers(result.payload) if led.name.strip().lower() == wanted),
        None,
    )


async def _company_profile(reads: ReadService, company: Company) -> CompanyProfile | None:
    """The company's own address and registrations, for the seller block."""
    try:
        result = await reads.fetch(company, dataset="company.profile", mode=FetchMode.CACHED)
        return CompanyProfile.model_validate(result.payload)
    except (AppError, ValueError):
        return None


async def _item_masters(
    reads: ReadService, company: Company, voucher: Voucher
) -> dict[str, StockItem]:
    """The stock masters this voucher's lines name, keyed by folded name."""
    wanted = {e.item_name.strip().lower() for e in voucher.inventory_entries if e.item_name}
    if not wanted:
        return {}
    try:
        result = await reads.fetch(
            company, dataset="stock_items.list", mode=FetchMode.CACHED, heavy=True
        )
    except AppError:
        return {}
    return {
        item.name.strip().lower(): item
        for item in an.parse_stock(result.payload)
        if item.name.strip().lower() in wanted
    }


async def _opening_balance(
    reads: ReadService,
    session: SessionDep,
    company: Company,
    ledger: Ledger,
    *,
    start: date,
    end: date,
    window: list[Voucher],
    kinds: dict[str, VoucherTypeKind],
) -> Money | None:
    """What one ledger stood at on the morning of ``start``, or ``None``.

    Tally will not say: it reports a closing balance as it stands now and takes
    no date to evaluate against. But it does not need to. Verified live
    2026-10-11 on "D.D Enterprises", ledger by ledger: the opening balance on
    the master plus every posted voucher came to Tally's own closing balance,
    to the paisa, for 27 ledgers of 28 -- the exception being "Profit & Loss
    A/c", refused below. So the balance on any morning is that closing balance
    with every later voucher taken back off: arithmetic on figures Tally itself
    reported, which an accountant can check against the Ledger Vouchers screen.

    What that run could not show: those books hold one financial year and no
    ledger in them carries an opening balance. The year-end rule for income
    accounts below is how Tally closes a year, not something seen there.

    Two kinds of account, worked two ways:

    * **Assets, liabilities and parties** carry their balance from year to
      year. Start from today's closing balance and take off everything posted
      since ``start``. Only recent vouchers are involved, which are the ones
      the sync reads first and keeps current.
    * **Income and expense accounts** start each financial year from nil --
      last year's sales are in last year's profit, not in this year's opening.
      So theirs is what was posted from 1 April up to ``start``, and working
      back from today's balance would be wrong for any window in a closed year.

    ``None`` whenever a voucher this depends on might be missing: no stored
    history for the range, or a group the masters read did not return. A
    statement without an opening balance says so; one with a wrong opening
    balance looks exactly like one with a right one.
    """
    # Tally's own running total of profit, with no vouchers posted to it.
    if ledger.name.strip().casefold() == "profit & loss a/c":
        return None

    try:
        group_result = await reads.fetch(company, dataset="groups.list", mode=FetchMode.CACHED)
    except AppError:
        return None
    revenue = an.is_revenue_group(an.parse_groups(group_result.payload), ledger.parent_group)
    if revenue is None:
        return None

    def movement(payload: Any) -> Money:
        stored = an.classify(an.effective(an.parse_vouchers(payload)), kinds)
        return an.ledger_movement(stored, ledger=ledger.name)

    if revenue:
        year_start = financial_year_start(start)
        before = Money.zero()
        if start > year_start:
            payload = await reads.stored_vouchers(
                company, from_date=year_start, to_date=start - timedelta(days=1)
            )
            if payload is None:
                return None
            before = movement(payload)
        # A master opening balance on an income account belongs to the year the
        # books began in and to no other.
        state = await session.get(CompanySyncState, company.id)
        books_from = state.books_from if state is not None else None
        if books_from is None or books_from >= year_start:
            before = before + ledger.opening_balance
        return before

    later = Money.zero()
    if end < date.today():
        payload = await reads.stored_vouchers(company, from_date=end + timedelta(days=1))
        if payload is None:
            return None
        later = movement(payload)
    in_window = an.ledger_movement(window, ledger=ledger.name)
    return ledger.closing_balance + in_window.negated() + later.negated()


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
    """Every voucher that posted to one ledger, from an opening balance.

    Opening balance, each voucher with the balance it left behind, closing
    balance -- Tally's Ledger Vouchers. Today's balance still rides along
    separately: a window that ended last month closes on last month's figure,
    and the two are different statements the app labels differently.
    """
    start, end = _detail_window(from_date, to_date)

    voucher_result = await reads.fetch_vouchers(
        company, from_date=start, to_date=end, mode=FetchMode.CACHED
    )
    kinds = await voucher_kinds(reads, company, FetchMode.CACHED)
    vouchers = an.classify(an.effective(an.parse_vouchers(voucher_result.payload)), kinds)

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

    opening = (
        await _opening_balance(
            reads, session, company, match, start=start, end=end, window=vouchers, kinds=kinds
        )
        if match is not None
        else None
    )
    statement = an.ledger_statement(
        vouchers, ledger=ledger, since=start, until=end, opening=opening
    )

    # None, never zero. "We could not read the balance" and "the balance is nil"
    # are different statements and the app renders them differently.
    statement["closing_balance"] = (
        an.money_out(match.closing_balance) if match is not None else None
    )
    statement["group"] = match.parent_group if match is not None else None
    # The two address blocks a printed ledger account carries: the company's
    # at the top, the account holder's under its name. Contact details only,
    # the same as on a shared voucher -- and null rather than empty when the
    # read is not there, so the print leaves the block off.
    statement["party_details"] = an.party_out(match) if match is not None else None
    profile = await _company_profile(reads, company)
    statement["company_details"] = an.company_out(profile) if profile is not None else None

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
    # Without this every shop that renamed "Sales" to "Tax Invoice" gets an
    # empty register rather than a wrong one, which is at least honest and
    # entirely useless.
    everything = an.classify(
        an.parse_vouchers(result.payload),
        await voucher_kinds(reads, company, FetchMode.CACHED),
    )
    vouchers = an.effective(everything)
    # Counted beside each month, and in no total. Optional entries are not
    # cancelled ones -- they are waiting for approval -- so they are left out.
    cancelled = [v for v in everything if v.is_cancelled]

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
        data=an.register(vouchers, kind, since=start, until=end, cancelled=cancelled),
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


# --------------------------------------------------------------------------
# Top Prime Reports
# --------------------------------------------------------------------------


@router.get("/reports/top/{key}", response_model=DataEnvelope)
async def top_report(
    company: CompanyDep,
    sync: SyncDep,
    principal: PrincipalDep,
    session: SessionDep,
    reads: ReadServiceDep,
    request: Request,
    key: str,
    from_date: date,
    to_date: date,
    sort: str | None = None,
    limit: Annotated[int, Query(ge=0, le=1000)] = 10,
    mode: FetchModeParam = "auto",
) -> DataEnvelope:
    """One ranked table from the Top Prime Reports set.

    Loads only what the named report reads (``top.NEEDS``): a top-customers
    request never pays for a stock export. ``limit=0`` means every row.
    """
    needs = top.NEEDS.get(key)
    if needs is None:
        raise NotFound(f"no top report {key!r}", user_message="That report does not exist.")

    resolved = await _resolved(sync, company, mode)
    start, end = _clamp_window(from_date, to_date)
    inputs = top.Inputs(since=start, until=end)
    results: list[DataResult] = []

    kinds: dict[str, VoucherTypeKind] | None = None
    if needs & {"window", "history"}:
        kinds = await voucher_kinds(reads, company, FetchMode.CACHED)

    if "window" in needs:
        result = await reads.fetch_vouchers(
            company, from_date=start, to_date=end, mode=resolved
        )
        results.append(result)
        inputs.window = an.classify(an.effective(an.parse_vouchers(result.payload)), kinds or {})

    if "history" in needs:
        history, history_result = await _top_history(
            reads, session, company, end, kinds or {}, inputs.notes
        )
        inputs.history = history
        if history_result is not None and "window" not in needs:
            results.append(history_result)
        if "window" in needs:
            # The window is the fresher read when the store lags behind today;
            # history supplies everything before it.
            inputs.history = [v for v in inputs.history if v.date < start] + inputs.window

    async def optional(dataset: str, *, heavy: bool = False) -> Any | None:
        try:
            result = await reads.fetch(company, dataset=dataset, mode=resolved, heavy=heavy)
        except AppError:
            # Optional: a missing master costs the report a filter or a column,
            # and the report says so, rather than costing the whole screen.
            return None
        results.append(result)
        return result.payload

    if "ledgers" in needs:
        payload = await optional("ledgers.list")
        inputs.ledgers = an.parse_ledgers(payload) if payload is not None else None
        groups = await optional("groups.list")
        inputs.groups = an.parse_groups(groups) if groups is not None else None
    if "stock" in needs:
        result = await reads.fetch(
            company, dataset="stock_items.list", mode=resolved, heavy=True
        )
        results.append(result)
        inputs.stock = an.parse_stock(result.payload)
    if "bills" in needs:
        result = await reads.fetch(
            company,
            dataset="outstanding.bills",
            params={"as_of": date.today().isoformat()},
            mode=resolved,
            heavy=True,
        )
        results.append(result)
        inputs.bills = an.parse_bills(result.payload)

    data = top.build(key, inputs, sort=sort, limit=limit)

    await record(
        session,
        action="report.top",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={"report": key, "from": start.isoformat(), "to": end.isoformat()},
        request=request,
    )
    if not results:
        # Nothing behind this report has been read yet -- no synced history and
        # no masters. "No data yet", never an empty table that reads as none.
        raise NoDataYet()
    return DataEnvelope(data=data, meta=_oldest_meta(results))


async def _top_history(
    reads: ReadService,
    session: SessionDep,
    company: Company,
    until: date,
    kinds: dict[str, VoucherTypeKind],
    notes: list[str],
) -> tuple[list[Voucher], DataResult | None]:
    """Every stored voucher up to ``until``, for "when did this last happen".

    From the history store rather than a window: the question reaches back to
    the start of the books, and asking Tally for years of vouchers to answer it
    is the export this product exists to avoid. What the store does not hold
    yet is said rather than implied -- a customer whose last sale predates the
    synced history would otherwise read as inactive for exactly that long.
    """
    result = await reads.stored_history(company, to_date=until)
    if result is None:
        notes.append(
            "Past sales have not been synced from Tally yet, so dates before this "
            "period are not known."
        )
        return [], None
    state = await session.get(CompanySyncState, company.id)
    if (
        state is not None
        and state.books_from is not None
        and state.backfilled_from is not None
        and state.backfilled_from > state.books_from
    ):
        notes.append(
            f"History is synced back to {state.backfilled_from:%d %b %Y}; anything "
            "older is not counted yet."
        )
    vouchers = an.classify(an.effective(an.parse_vouchers(result.payload)), kinds)
    return vouchers, result
