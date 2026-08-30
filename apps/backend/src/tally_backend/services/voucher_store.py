"""The accumulated voucher history, and the merge rules that keep it honest.

Snapshots answer "what did this window look like at 9:15?". History needs the
other shape -- one row per voucher, merged from many reads -- because the reads
arrive separately: a four-year backfill is delivered in date slices, and every
sync after it delivers only what changed. Both have to land in the same place,
and blob storage cannot express either without a read-modify-write of megabytes.

Two merge modes, and the distinction between them is load-bearing:

``authoritative``
    The caller read a *complete* date window from Tally. Whatever Tally returned
    is the truth for that window, so a stored voucher the read did not mention
    has been deleted in Tally and is removed here. This is the only mechanism
    that ever deletes, which is why a recent window is periodically re-read in
    full even when nothing appears to have changed.

``additive``
    The caller read a change delta (``AlterID > n``). Tally reports what was
    created or edited and says nothing at all about what was deleted, so an
    absent voucher means "unchanged", never "gone". Deleting on this signal
    would empty the store on the first quiet day.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import VoucherRecord, utc_now

logger = logging.getLogger(__name__)


def record_key(voucher: dict[str, Any]) -> str:
    """Stable identity for a voucher across edits.

    Tally's ``GUID`` is the real answer and live instances do send it. The
    fallbacks matter anyway: identity has to survive an edit, and a voucher's
    number, date and amount are all things an operator can change. Keying on
    those would leave the pre-edit copy behind as a second row, double-counting
    it in every total that follows -- so they are only ever a last resort, and
    ``MASTERID`` (stable within a company) is preferred over them.
    """
    guid = voucher.get("guid")
    if isinstance(guid, str) and guid.strip():
        return guid.strip()[:128]

    master_id = voucher.get("master_id")
    if master_id is not None:
        return f"m:{master_id}"

    blob = "|".join(
        str(voucher.get(field) or "")
        for field in ("voucher_type", "voucher_number", "date", "party_name")
    )
    return "h:" + hashlib.sha256(blob.encode()).hexdigest()[:40]


@dataclass
class IngestResult:
    inserted: int = 0
    updated: int = 0
    deleted: int = 0
    skipped: int = 0
    #: Highest AlterID seen in this batch, for advancing the sync cursor.
    max_alter_id: int | None = None

    @property
    def touched(self) -> int:
        return self.inserted + self.updated + self.deleted


class VoucherStore:
    """Reads and writes the per-company voucher history."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- writing ---------------------------------------------------------

    async def ingest(
        self,
        company_id: str,
        vouchers: list[dict[str, Any]],
        *,
        window: tuple[date, date] | None = None,
    ) -> IngestResult:
        """Merge a batch of vouchers.

        Pass ``window`` only when the batch is a complete read of that date
        range; see the module docstring for why that unlocks deletion.
        """
        result = IngestResult()
        incoming: dict[str, dict[str, Any]] = {}

        for voucher in vouchers:
            when = _voucher_date(voucher)
            if when is None:
                # A voucher with no parseable date cannot be placed on any
                # report and would poison the coverage window it lands in.
                result.skipped += 1
                continue
            key = record_key(voucher)
            alter = _alter_id(voucher)
            existing = incoming.get(key)
            # Tally can return the same voucher twice across overlapping reads;
            # keep the newest copy rather than letting insert order decide.
            if existing is None or _beats(alter, _alter_id(existing)):
                incoming[key] = voucher
            if alter is not None and (result.max_alter_id is None or alter > result.max_alter_id):
                result.max_alter_id = alter

        stored = await self._existing(company_id, keys=list(incoming), window=window)

        new_rows: list[dict[str, Any]] = []
        for key, voucher in incoming.items():
            row = stored.get(key)
            if row is None:
                new_rows.append(_row_values(company_id, key, voucher))
                result.inserted += 1
                continue
            if not _beats(_alter_id(voucher), row.alter_id):
                # Already have this exact revision. Rewriting it would churn
                # `updated_at` and make every sync look like it changed things.
                result.skipped += 1
                continue
            await self._session.execute(
                update(VoucherRecord)
                .where(VoucherRecord.id == row.id)
                .values(**_row_values(company_id, key, voucher, include_id=False))
            )
            result.updated += 1

        if new_rows:
            self._session.add_all([VoucherRecord(**values) for values in new_rows])

        if window is not None:
            result.deleted = await self._prune(company_id, window, keep=set(incoming))

        await self._session.flush()
        return result

    async def reconcile(
        self,
        company_id: str,
        vouchers: list[dict[str, Any]],
        *,
        window: tuple[date, date],
    ) -> int:
        """Delete stored vouchers a complete read of ``window`` did not return.

        The deletion half of :meth:`ingest`, on its own. It exists because the
        cheap way to ask "what still exists?" is an identity-only read -- no
        ledger lines, no inventory lines, 13x smaller -- and that payload must
        never reach :meth:`ingest`. Ingesting it would faithfully overwrite every
        stored voucher in the window with a copy that has no lines at all,
        destroying exactly the data the reconcile is meant to protect.

        So this writes nothing. It reads the returned identities, prunes what is
        missing, and leaves every surviving row untouched.
        """
        keep = {
            record_key(voucher)
            for voucher in vouchers
            if isinstance(voucher, dict) and _voucher_date(voucher) is not None
        }
        return await self._prune(company_id, window, keep=keep)

    async def _existing(
        self,
        company_id: str,
        *,
        keys: list[str],
        window: tuple[date, date] | None,
    ) -> dict[str, VoucherRecord]:
        """Rows this batch might collide with.

        Selected by key rather than only by window because an edit can move a
        voucher's *date*: the stored copy then sits outside the window being
        read, and a window-only lookup would insert a duplicate instead of
        updating it.
        """
        if not keys:
            return {}

        rows: dict[str, VoucherRecord] = {}
        # Chunked: SQLite caps a statement at 999 bound parameters and a
        # backfill slice routinely carries tens of thousands of vouchers.
        for batch in _batched(keys, 500):
            stmt = select(VoucherRecord).where(
                VoucherRecord.company_id == company_id,
                VoucherRecord.record_key.in_(batch),
            )
            for row in (await self._session.execute(stmt)).scalars():
                rows[row.record_key] = row
        return rows

    async def _prune(
        self, company_id: str, window: tuple[date, date], *, keep: set[str]
    ) -> int:
        """Drop stored vouchers the authoritative read did not return."""
        from_date, to_date = window
        stmt = select(VoucherRecord.id, VoucherRecord.record_key).where(
            VoucherRecord.company_id == company_id,
            VoucherRecord.voucher_date >= from_date,
            VoucherRecord.voucher_date <= to_date,
        )
        doomed = [
            row_id
            for row_id, key in (await self._session.execute(stmt)).all()
            if key not in keep
        ]
        if not doomed:
            return 0

        logger.info(
            "removing %d voucher(s) deleted in Tally between %s and %s for company %s",
            len(doomed),
            from_date,
            to_date,
            company_id,
        )
        for batch in _batched(doomed, 500):
            await self._session.execute(
                delete(VoucherRecord).where(VoucherRecord.id.in_(batch))
            )
        return len(doomed)

    async def clear(self, company_id: str) -> int:
        """Drop a company's history. Used when a backfill restarts from scratch."""
        result = await self._session.execute(
            delete(VoucherRecord).where(VoucherRecord.company_id == company_id)
        )
        await self._session.flush()
        return result.rowcount or 0

    # -- reading ---------------------------------------------------------

    async def read(
        self, company_id: str, *, from_date: date, to_date: date
    ) -> list[dict[str, Any]]:
        """Vouchers in a window, in the shape the connector would have sent.

        Same shape on purpose: analytics then parses store rows and live reads
        with one code path, so a report cannot mean different things depending
        on where its data came from.
        """
        stmt = (
            select(VoucherRecord.payload)
            .where(
                VoucherRecord.company_id == company_id,
                VoucherRecord.voucher_date >= from_date,
                VoucherRecord.voucher_date <= to_date,
            )
            .order_by(VoucherRecord.voucher_date, VoucherRecord.record_key)
        )
        return [payload for (payload,) in (await self._session.execute(stmt)).all()]

    async def find(self, company_id: str, *, key: str, on: date) -> dict[str, Any] | None:
        """One stored voucher, by the key a listed row carried.

        Separate from :meth:`read` because the coverage rule that guards a
        window does not apply to a single row. A report for a half-covered
        window must refuse rather than under-report -- a day book missing the
        half it does not have looks exactly like a quiet fortnight. One voucher
        has no such failure mode: either this key is stored or it is not, and
        answering from a partially backfilled company is strictly better than
        refusing a tap on a row that is visibly right there.

        ``on`` is the voucher's own date and only narrows the scan; the key is
        what identifies it. The store is indexed on (company, date), so this
        never walks a company's history.
        """
        stmt = select(VoucherRecord.payload).where(
            VoucherRecord.company_id == company_id,
            VoucherRecord.record_key == key,
            VoucherRecord.voucher_date == on,
        )
        return await self._session.scalar(stmt)

    async def count(self, company_id: str) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(VoucherRecord)
                .where(VoucherRecord.company_id == company_id)
            )
            or 0
        )

    async def max_alter_id(self, company_id: str) -> int | None:
        return await self._session.scalar(
            select(func.max(VoucherRecord.alter_id)).where(
                VoucherRecord.company_id == company_id
            )
        )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _row_values(
    company_id: str, key: str, voucher: dict[str, Any], *, include_id: bool = True
) -> dict[str, Any]:
    when = _voucher_date(voucher)
    assert when is not None  # callers filter undated vouchers first
    values: dict[str, Any] = {
        "voucher_date": when,
        "alter_id": _alter_id(voucher),
        "payload": voucher,
        "voucher_type": (voucher.get("voucher_type") or None),
        "is_effective": not (
            bool(voucher.get("is_cancelled")) or bool(voucher.get("is_optional"))
        ),
        "updated_at": utc_now(),
    }
    if include_id:
        values |= {"company_id": company_id, "record_key": key}
    return values


def _voucher_date(voucher: dict[str, Any]) -> date | None:
    raw = voucher.get("date")
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def _alter_id(voucher: dict[str, Any]) -> int | None:
    raw = voucher.get("alter_id")
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _beats(incoming: int | None, stored: int | None) -> bool:
    """Whether an incoming copy should replace the stored one.

    Unknown on either side means "cannot tell", and the incoming copy wins: it
    was read more recently, and a Tally that does not report change ids would
    otherwise freeze the store at whatever it held first.
    """
    if incoming is None or stored is None:
        return True
    return incoming >= stored


def _batched(items: list[Any], size: int) -> list[list[Any]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
