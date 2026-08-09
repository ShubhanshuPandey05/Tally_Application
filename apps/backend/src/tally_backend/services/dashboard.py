"""Composes the dashboard and the report endpoints from raw datasets.

Section-level degradation is the design point. A dashboard that returns 503
because one of four reads failed is a dashboard that is down; a dashboard that
shows cash, sales and stock while marking receivables unavailable is still
answering most of the owner's questions. Each section therefore carries its own
availability and freshness.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from tally_core.domain.masters import VoucherTypeKind
from tally_core.domain.transactions import OutstandingKind

from ..core.errors import AppError
from ..db.models import Company, as_utc
from . import analytics as an
from .reads import DataResult, FetchMode, ReadService

logger = logging.getLogger(__name__)

#: Datasets the dashboard is built from, with the query that produces each.
VOUCHERS = "vouchers.list"
LEDGERS = "ledgers.list"
BILLS = "outstanding.bills"
STOCK = "stock_items.list"
#: Cheap master read that tells us which voucher types are Sales, Purchase, etc.
VOUCHER_TYPES = "voucher_types.list"

#: Trend length on the dashboard chart.
TREND_DAYS = 30


@dataclass
class Section:
    """One dataset's result, successful or not."""

    name: str
    ok: bool
    data: Any = None
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {"ok": self.ok, "data": self.data, "meta": self.meta, "error": self.error}


def month_start(day: date) -> date:
    return day.replace(day=1)


def previous_month_bounds(day: date) -> tuple[date, date]:
    first = month_start(day)
    last_prev = first - timedelta(days=1)
    return month_start(last_prev), last_prev


def voucher_window(today: date) -> tuple[date, date]:
    """The voucher range the dashboard needs, in one place.

    The background refresher warms snapshots using this same function. They must
    agree exactly: the params are part of a snapshot's identity, so warming a
    different window writes rows the dashboard will never read -- a bug whose
    only symptom is "the refresher appears to do nothing".
    """
    prev_start, _ = previous_month_bounds(today)
    return min(prev_start, today - timedelta(days=TREND_DAYS)), today


def voucher_params(today: date) -> dict[str, Any]:
    start, end = voucher_window(today)
    return {
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "include_inventory": True,
    }


async def voucher_kinds(
    reads: ReadService, company: Company, mode: FetchMode
) -> dict[str, VoucherTypeKind]:
    """The company's voucher-type name -> accounting class map.

    Every screen that groups vouchers by kind needs this, because the voucher
    rows themselves only carry a name a shop is free to change. Returns an empty
    map if the read fails, which leaves classification exactly as it was rather
    than emptying the sales figures -- see :func:`analytics.classify`.
    """
    try:
        result = await reads.fetch(company, dataset=VOUCHER_TYPES, mode=mode)
    except AppError as exc:
        logger.info("voucher types unavailable, keeping name-based kinds: %s", exc.message)
        return {}
    return an.voucher_kind_map(an.parse_voucher_types(result.payload))


class DashboardService:
    def __init__(self, reads: ReadService) -> None:
        self._reads = reads

    async def _load(
        self,
        company: Company,
        dataset: str,
        *,
        params: dict[str, Any] | None = None,
        mode: FetchMode,
        heavy: bool = False,
        errors: dict[str, str] | None = None,
    ) -> DataResult | None:
        """Fetch one dataset, converting an expected failure into ``None``.

        Reads are sequential rather than gathered: a SQLAlchemy ``AsyncSession``
        must not be used concurrently, and the connector only has a couple of
        job slots anyway. On the normal path every one of these is a snapshot
        hit -- four indexed primary-key lookups, not four trips to Tally.

        The failure's own wording is kept in ``errors`` rather than discarded.
        Some causes are things only the shop can fix -- the company not being
        open in TallyPrime is the one that prompted this -- and replacing that
        with "Could not read vouchers from Tally" tells them to check the
        network when they should be clicking Open Company.
        """
        try:
            return await self._reads.fetch(
                company, dataset=dataset, params=params, mode=mode, heavy=heavy
            )
        except AppError as exc:
            logger.info("dashboard section %s unavailable: %s", dataset, exc.message)
            if errors is not None:
                errors[dataset] = exc.user_message
            return None

    async def build(
        self, company: Company, *, today: date, mode: FetchMode = FetchMode.AUTO
    ) -> dict[str, Any]:
        #: Dataset -> why it could not be read, when it could not be.
        errors: dict[str, str] = {}

        # One voucher read covers today, this month, last month and the trend.
        # A round trip to a customer's desktop is the expensive part, so the
        # window is widened rather than split into several reads.
        vouchers_result = await self._load(
            company,
            VOUCHERS,
            params=voucher_params(today),
            mode=mode,
            heavy=True,
            errors=errors,
        )
        ledgers_result = await self._load(company, LEDGERS, mode=mode, errors=errors)
        bills_result = await self._load(
            company,
            BILLS,
            params={"as_of": today.isoformat()},
            mode=mode,
            heavy=True,
            errors=errors,
        )
        stock_result = await self._load(company, STOCK, mode=mode, heavy=True, errors=errors)

        def why(dataset: str, fallback: str) -> str:
            return errors.get(dataset, fallback)

        sections: dict[str, Section] = {}
        vouchers = (
            an.effective(an.parse_vouchers(vouchers_result.payload))
            if vouchers_result
            else []
        )
        # Without this every renamed sales voucher type -- "Tax Invoice", "GST
        # Sales" -- lands in OTHER, and the sales tile reads zero on a shop that
        # sold all day.
        if vouchers:
            vouchers = an.classify(vouchers, await voucher_kinds(self._reads, company, mode))

        no_vouchers = why(VOUCHERS, "Could not read vouchers from Tally.")
        no_bills = why(BILLS, "Could not read outstanding bills from Tally.")

        sections["sales"] = self._trade_section(
            "sales", VoucherTypeKind.SALES, vouchers, vouchers_result, today, no_vouchers
        )
        sections["purchases"] = self._trade_section(
            "purchases", VoucherTypeKind.PURCHASE, vouchers, vouchers_result, today, no_vouchers
        )
        sections["cash_and_bank"] = self._funds_section(
            ledgers_result, why(LEDGERS, "Could not read ledgers from Tally.")
        )
        sections["receivables"] = self._outstanding_section(
            "receivables", OutstandingKind.RECEIVABLE, bills_result, today, no_bills
        )
        sections["payables"] = self._outstanding_section(
            "payables", OutstandingKind.PAYABLE, bills_result, today, no_bills
        )
        sections["inventory"] = self._inventory_section(
            stock_result, why(STOCK, "Could not read stock from Tally.")
        )
        sections["activity"] = self._activity_section(vouchers, vouchers_result, no_vouchers)

        return {
            "company": {
                "id": company.id,
                "name": company.label,
                "currency": company.base_currency,
            },
            "as_of": today.isoformat(),
            "sections": {name: section.to_json() for name, section in sections.items()},
            "freshness": self._freshness(
                [vouchers_result, ledgers_result, bills_result, stock_result]
            ),
        }

    # -- sections --------------------------------------------------------

    def _trade_section(
        self,
        name: str,
        kind: VoucherTypeKind,
        vouchers: list[Any],
        result: DataResult | None,
        today: date,
        error: str,
    ) -> Section:
        if result is None:
            return Section(name, ok=False, error=error)

        prev_start, prev_end = previous_month_bounds(today)
        this_month = an.total_for(vouchers, kind, since=month_start(today), until=today)
        last_month = an.total_for(vouchers, kind, since=prev_start, until=prev_end)

        party_kind = (
            VoucherTypeKind.SALES if kind is VoucherTypeKind.SALES else VoucherTypeKind.PURCHASE
        )
        return Section(
            name,
            ok=True,
            data={
                "today": an.money_out(an.total_for(vouchers, kind, on=today)),
                "yesterday": an.money_out(
                    an.total_for(vouchers, kind, on=today - timedelta(days=1))
                ),
                "this_month": an.money_out(this_month),
                "last_month": an.money_out(last_month),
                "change_pct": _change_pct(this_month, last_month),
                "trend": an.daily_series(
                    vouchers, kind, since=today - timedelta(days=TREND_DAYS - 1), until=today
                ),
                "top_parties": an.top_parties(vouchers, party_kind),
                "top_products": an.top_products(vouchers, kind),
            },
            meta=result.meta(),
        )

    def _funds_section(self, result: DataResult | None, error: str) -> Section:
        if result is None:
            return Section("cash_and_bank", ok=False, error=error)

        ledgers = an.parse_ledgers(result.payload)
        cash = an.group_balance(ledgers, an.CASH_GROUPS)
        bank = an.group_balance(ledgers, an.BANK_GROUPS)
        return Section(
            "cash_and_bank",
            ok=True,
            data={
                "cash": an.money_out(cash),
                "bank": an.money_out(bank),
                "total": an.money_out(cash + bank),
                "cash_accounts": an.balance_lines(ledgers, an.CASH_GROUPS),
                "bank_accounts": an.balance_lines(ledgers, an.BANK_GROUPS),
            },
            meta=result.meta(),
        )

    def _outstanding_section(
        self,
        name: str,
        kind: OutstandingKind,
        result: DataResult | None,
        today: date,
        error: str,
    ) -> Section:
        if result is None:
            return Section(name, ok=False, error=error)

        bills = an.parse_bills(result.payload)
        summary = an.outstanding_summary(bills, kind, as_of=today)
        summary["top_parties"] = an.top_outstanding_parties(bills, kind, as_of=today)
        return Section(name, ok=True, data=summary, meta=result.meta())

    def _inventory_section(self, result: DataResult | None, error: str) -> Section:
        if result is None:
            return Section("inventory", ok=False, error=error)
        return Section(
            "inventory",
            ok=True,
            data=an.inventory_summary(an.parse_stock(result.payload)),
            meta=result.meta(),
        )

    def _activity_section(
        self, vouchers: list[Any], result: DataResult | None, error: str
    ) -> Section:
        if result is None:
            return Section("activity", ok=False, error=error)
        return Section(
            "activity",
            ok=True,
            data={
                "recent": an.recent_transactions(vouchers),
                "voucher_count": len(vouchers),
            },
            meta=result.meta(),
        )

    def _freshness(self, results: list[DataResult | None]) -> dict[str, Any]:
        """Worst-case freshness across sections.

        The header banner should reflect the *oldest* dataset on screen, not the
        newest -- otherwise it says "updated just now" over month-old numbers.
        """
        present = [r for r in results if r is not None]
        if not present:
            return {"available": False, "connector_online": False}

        oldest = max(present, key=lambda r: r.age_seconds)
        return {
            "available": True,
            # Aware, for the same reason as DataResult.meta: a naive
            # timestamp is read by the app as local time.
            "refreshed_at": as_utc(oldest.refreshed_at).isoformat(),
            "age_seconds": round(oldest.age_seconds, 1),
            "is_stale": any(r.is_stale for r in present),
            "connector_online": any(r.connector_online for r in present),
            "sections_unavailable": len(results) - len(present),
        }


def _change_pct(current: Any, previous: Any) -> float | None:
    """Percent change, or ``None`` when there is no baseline.

    Returning 0 or 100 for "no sales last month" would be a lie either way, and
    the app needs to render that case as "--" rather than as a real movement.
    """
    if previous.is_zero:
        return None
    delta = (current.amount - previous.amount) / previous.amount * 100
    return round(float(delta), 1)
