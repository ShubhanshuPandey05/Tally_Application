"""Seal the rows written before sealing existed.

Every row written since this release is sealed on the way in. Rows written
before it are still plaintext, and this walks them in small batches and seals
them in place, then stops. It is safe to interrupt at any point and safe to run
twice: each update only touches a row that is *still* plaintext, so a row a
sync rewrote in the meantime -- sealed, with newer content -- is left alone.

Slow on purpose. It runs beside customers' dashboards on the same database, so
it takes small batches with a pause between them; finishing in an hour instead
of ten minutes costs nobody anything.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Text, or_, select, type_coerce, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..core.sealing import PREFIX
from ..db.models import PendingVoucher, Snapshot, VoucherRecord
from . import voucher_queue, voucher_store
from .data_keys import company_cipher
from .reads import _PARAMS, _PAYLOAD

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Column:
    name: str
    purpose: str
    #: Legacy value is JSON text; otherwise a bare string (a party name).
    json: bool = True
    #: Whether the column takes SQL NULL. A legacy JSON ``null`` in a NOT NULL
    #: column is sealed as ``null`` rather than written as NULL.
    nullable: bool = False


#: Each table, and the sealed columns in it with what each is bound to.
_TABLES: list[tuple[Any, list[_Column]]] = [
    (Snapshot, [_Column("payload", _PAYLOAD), _Column("params", _PARAMS, nullable=True)]),
    (VoucherRecord, [_Column("payload", voucher_store.PURPOSE)]),
    (
        PendingVoucher,
        [
            _Column("payload", voucher_queue.PAYLOAD),
            _Column("party_name", voucher_queue.PARTY, json=False, nullable=True),
        ],
    ),
]


@dataclass
class ResealReport:
    sealed: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.sealed.values())


def _plaintext(column: Any) -> Any:
    return (column.is_not(None)) & (column.not_like(f"{PREFIX}%"))


async def count_plaintext(session: AsyncSession) -> dict[str, int]:
    """Rows still unsealed, per table -- what ``datakeys status`` reports."""
    from sqlalchemy import func

    counts: dict[str, int] = {}
    for model, columns in _TABLES:
        condition = or_(*(_plaintext(getattr(model, c.name)) for c in columns))
        counts[model.__tablename__] = int(
            await session.scalar(select(func.count()).select_from(model).where(condition)) or 0
        )
    return counts


async def reseal_all(
    factory: async_sessionmaker[AsyncSession],
    *,
    batch_size: int = 200,
    pause_seconds: float = 0.2,
) -> ResealReport:
    report = ResealReport()
    for model, columns in _TABLES:
        sealed = 0
        while True:
            done = await _reseal_batch(factory, model, columns, batch_size)
            sealed += done
            if done < batch_size:
                break
            await asyncio.sleep(pause_seconds)
        report.sealed[model.__tablename__] = sealed
        if sealed:
            logger.info("sealed %d legacy %s rows", sealed, model.__tablename__)
    return report


async def _reseal_batch(
    factory: async_sessionmaker[AsyncSession], model: Any, columns: list[_Column], limit: int
) -> int:
    condition = or_(*(_plaintext(getattr(model, c.name)) for c in columns))
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    model.id,
                    model.company_id,
                    # Raw text, past the column type: a legacy JSON `null` and
                    # an SQL NULL decode alike, and only one of them matches.
                    *(type_coerce(getattr(model, c.name), Text).label(c.name) for c in columns),
                )
                .where(condition)
                .limit(limit)
            )
        ).all()
        if not rows:
            return 0
        for row in rows:
            cipher = await company_cipher(session, row.company_id, create=True)
            values = {}
            guards = []
            for column in columns:
                raw = getattr(row, column.name)
                if raw is None or raw.startswith(PREFIX):
                    continue
                value = json.loads(raw) if column.json else raw
                # A JSON `null` becomes an SQL NULL where the column allows
                # one, and a sealed `null` where it does not.
                values[column.name] = cipher.seal(
                    value, purpose=column.purpose, nullable=column.nullable
                )
                # Still plaintext at the moment of writing, or leave it: a
                # concurrent sync may have written newer, sealed content.
                guards.append(getattr(model, column.name).not_like(f"{PREFIX}%"))
            if values:
                await session.execute(
                    update(model).where(model.id == row.id, *guards).values(**values)
                )
        await session.commit()
        return len(rows)


async def reseal_in_background(
    factory: async_sessionmaker[AsyncSession], *, batch_size: int, pause_seconds: float
) -> None:
    """The startup task. Never raises: a failure here costs only the conversion."""
    try:
        report = await reseal_all(factory, batch_size=batch_size, pause_seconds=pause_seconds)
        if report.total:
            logger.info("resealing finished: %s", report.sealed)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - the next start picks up where this stopped
        logger.exception("resealing legacy rows stopped; it resumes on the next start")
