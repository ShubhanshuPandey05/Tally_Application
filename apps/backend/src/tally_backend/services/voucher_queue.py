"""Entries waiting for a PC that was not there.

The queue lives here and nowhere else. A copy on the shop's own machine —
which is what BizAnalyst ships, as a local SQLite outbox — cannot accept
anything while that machine is off, and the machine being off is the entire
reason the queue exists.

Three rules keep it from becoming a duplicate generator, and they are the whole
design:

1. **Only provably-unsent entries are queued.** The write path already decides
   this: a failure is ``retryable`` exactly when something could show nothing
   reached Tally. A timeout is not retryable, so it is never queued.
2. **A row is claimed before it is sent.** ``WAITING -> SENDING`` happens in its
   own committed transaction, so two drains — or two backend instances — cannot
   pick up the same entry and post the voucher twice.
3. **A claimed row that fails ambiguously is never re-queued.** If the drain
   cannot prove the entry did not land, it settles as ``FAILED`` with Tally's
   own words on it and a person decides.

And one rule that is about people rather than correctness: **nothing here is
invisible**. The count and the list are on the phone, because the real danger
of a queue is somebody recording a receipt, seeing it accepted, and walking
away from a PC that never comes back.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Callable
from datetime import timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tally_core.domain.writes import VoucherDraft, VoucherPosted
from tally_core.tally import CREATE_VOUCHER

from ..config import Settings
from ..db.models import (
    Company,
    PendingVoucher,
    PendingVoucherState,
    as_utc,
    utc_now,
)
from ..hub.hub import ConnectorHub

logger = logging.getLogger(__name__)

SessionFactory = Callable[[], Any]

#: How many entries one drain sends before yielding.
#:
#: Bounded because these go through the same single-worker pipeline as every
#: read, on a machine whose TallyPrime is somebody's till. A shop that queued
#: forty entries over a long weekend should not have its Tally locked up the
#: moment the PC boots -- the rest go on the next pass.
DRAIN_BATCH = 10


class _Outcome(StrEnum):
    """What one delivery attempt proved, from the drain loop's point of view."""

    SENT = "sent"
    #: Nothing reached Tally. The entry keeps its turn, and the pass stops --
    #: whatever stopped this one will stop the rest.
    STILL_UNREACHABLE = "still_unreachable"
    #: Settled one way or another. Move on to the next entry.
    FAILED = "failed"


class VoucherQueue:
    """Stores entries that could not reach Tally, and delivers them later."""

    def __init__(
        self,
        session_factory: SessionFactory,
        hub: ConnectorHub,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._hub = hub
        self._settings = settings
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        #: One drain per connector at a time. Two overlapping drains would both
        #: claim rows correctly, but they would also both queue work onto a
        #: Tally that serves one request at a time.
        self._draining: set[str] = set()

    # -- accepting -------------------------------------------------------

    async def enqueue(
        self,
        session: AsyncSession,
        *,
        company: Company,
        draft: VoucherDraft,
        user_id: str | None,
    ) -> PendingVoucher:
        """Hold an entry until the PC comes back.

        The caller decides *whether* to queue -- this method does not re-check
        that the failure was safe, because the evidence lives in the JobError
        and has already been read.
        """
        row = PendingVoucher(
            org_id=company.org_id,
            company_id=company.id,
            connector_id=company.connector_id,
            created_by=user_id,
            payload=draft.model_dump(mode="json"),
            kind=str(draft.kind),
            party_name=draft.party_name,
            state=PendingVoucherState.WAITING,
            expires_at=utc_now()
            + timedelta(days=self._settings.pending_voucher_expiry_days),
        )
        session.add(row)
        await session.flush()
        logger.info(
            "queued %s for company %s until %s", row.kind, company.id, row.expires_at
        )
        return row

    # -- delivering ------------------------------------------------------

    async def drain(self, connector_id: str, *, limit: int = DRAIN_BATCH) -> int:
        """Send what is waiting for one connector. Returns how many landed.

        Never raises. A drain runs on a reconnect and on a timer, and neither
        caller has anywhere sensible to put an exception.
        """
        if connector_id in self._draining:
            return 0
        self._draining.add(connector_id)
        try:
            return await self._drain(connector_id, limit)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a drain must not take down its caller
            logger.exception("draining connector %s failed", connector_id)
            return 0
        finally:
            self._draining.discard(connector_id)

    async def _drain(self, connector_id: str, limit: int) -> int:
        sent = 0
        for _ in range(limit):
            claimed = await self._claim_one(connector_id)
            if claimed is None:
                break

            row_id, company_id, payload = claimed
            posted, error = await self._deliver(company_id, payload)
            outcome = await self._settle(row_id, posted, error)

            if outcome is _Outcome.SENT:
                sent += 1
            elif outcome is _Outcome.STILL_UNREACHABLE:
                # Stop the whole pass, not just this entry. The PC is not there,
                # and it is not there for the next nine either -- walking the
                # rest of the queue would put this same row back in WAITING and
                # claim it again on the very next iteration, so one drain of a
                # single queued entry became ten pointless attempts.
                logger.info(
                    "stopping drain for %s: still unreachable", connector_id
                )
                break
        return sent

    async def _claim_one(
        self, connector_id: str
    ) -> tuple[str, str, dict[str, Any]] | None:
        """Take the oldest waiting entry, committing the claim before sending.

        The claim is a compare-and-swap -- ``UPDATE ... WHERE state = waiting``
        -- rather than a locking read. One statement, atomic on both Postgres
        and the SQLite the tests run on, and a loser simply sees no rows
        updated and moves to the next entry. ``SELECT ... FOR UPDATE`` would
        have been the obvious shape and is a no-op on SQLite, which is the
        worst kind of concurrency control: one that passes its tests.

        The claim is also its own committed transaction. If the process dies
        between here and the send, the row is left in ``SENDING`` and is never
        picked up again -- an entry that may have been half-sent is exactly the
        one a queue must not retry.
        """
        while True:
            async with self._session_factory() as session:
                row = (
                    await session.execute(
                        select(PendingVoucher)
                        .where(
                            PendingVoucher.connector_id == connector_id,
                            PendingVoucher.state == PendingVoucherState.WAITING,
                        )
                        # Oldest first, so entries reach Tally in the order
                        # somebody made them at the counter.
                        .order_by(PendingVoucher.created_at)
                        .limit(1)
                    )
                ).scalar_one_or_none()

                if row is None:
                    return None

                row_id = row.id
                company_id = row.company_id
                payload = dict(row.payload)
                # `as_utc`, not a bare comparison: SQLite has no timezone
                # type and hands back naive datetimes even from a
                # DateTime(timezone=True) column, so comparing directly
                # raises TypeError -- which this method's caller would
                # swallow, leaving every queued entry undeliverable and
                # nothing on screen to say why.
                expired = as_utc(row.expires_at) <= utc_now()

            if expired:
                await self._expire(row_id)
                # Expiring one does not end the drain -- the next may be live.
                continue

            async with self._session_factory() as session:
                won = await session.execute(
                    update(PendingVoucher)
                    .where(
                        PendingVoucher.id == row_id,
                        # The guard. Without it two drains both "claim" the row
                        # and the voucher is posted twice.
                        PendingVoucher.state == PendingVoucherState.WAITING,
                    )
                    .values(
                        state=PendingVoucherState.SENDING,
                        attempts=PendingVoucher.attempts + 1,
                        updated_at=utc_now(),
                    )
                )
                await session.commit()

            if won.rowcount == 1:
                return row_id, company_id, payload
            # Somebody else took it between the read and the update. Look again
            # rather than assuming there is nothing left.

    async def _expire(self, row_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(PendingVoucher)
                .where(
                    PendingVoucher.id == row_id,
                    PendingVoucher.state == PendingVoucherState.WAITING,
                )
                .values(
                    state=PendingVoucherState.FAILED,
                    last_error=(
                        "This entry waited too long to reach TallyPrime and "
                        "was not saved."
                    ),
                    settled_at=utc_now(),
                    updated_at=utc_now(),
                )
            )
            await session.commit()
        logger.info("expired queued entry %s", row_id)

    async def _deliver(
        self, company_id: str, payload: dict[str, Any]
    ) -> tuple[VoucherPosted | None, Any]:
        # Read what the send needs *inside* the session and carry plain values
        # out. Touching a detached ORM object after the session closes is the
        # kind of bug that only shows up once expire_on_commit changes.
        async with self._session_factory() as session:
            company = await session.get(Company, company_id)
            if company is None or not company.is_active:
                return None, _gone()
            connector_id = company.connector_id
            tally_name = company.tally_name

        result = await self._hub.write(
            connector_id=connector_id,
            mutation=CREATE_VOUCHER,
            params={"company": tally_name, "draft": payload},
            timeout_seconds=self._settings.default_write_timeout_seconds,
        )
        if not result.ok:
            return None, result.error
        return VoucherPosted.model_validate(result.data()), None

    async def _settle(
        self, row_id: str, posted: VoucherPosted | None, error: Any
    ) -> _Outcome:
        """Record the outcome, and decide whether this entry may wait again.

        The one subtle case: a failure that is ``retryable`` means nothing
        reached Tally, so the entry goes back to waiting and keeps its place.
        Anything else settles as ``FAILED`` -- including a timeout, because the
        voucher may be in the books already and sending it again is how one
        receipt becomes two.
        """
        async with self._session_factory() as session:
            row = await session.get(PendingVoucher, row_id)
            if row is None:
                return _Outcome.FAILED

            if posted is not None and posted.ok:
                row.state = PendingVoucherState.SENT
                row.tally_voucher_id = posted.voucher_id
                row.last_error = None
                row.settled_at = utc_now()
                outcome = _Outcome.SENT
            elif error is not None and getattr(error, "retryable", False):
                # Still offline, or Tally still closed. Nothing was sent, so it
                # keeps its turn rather than being counted as a failure.
                row.state = PendingVoucherState.WAITING
                row.last_error = _message(error)
                outcome = _Outcome.STILL_UNREACHABLE
            else:
                row.state = PendingVoucherState.FAILED
                row.last_error = (
                    _message(error)
                    if error is not None
                    else (posted.message if posted else None)
                ) or "TallyPrime did not save this entry."
                row.settled_at = utc_now()
                outcome = _Outcome.FAILED

            await session.commit()
            return outcome

    # -- the worker ------------------------------------------------------

    async def start(self) -> None:
        if not self._settings.pending_voucher_queue_enabled:
            logger.info("voucher queue worker disabled")
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        interval = self._settings.pending_voucher_drain_interval_seconds
        # Staggered, so several instances coming up together do not all sweep
        # in the same second.
        await asyncio.sleep(random.uniform(0, interval))

        while not self._stopping.is_set():
            try:
                await self.sweep()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - the worker must outlive any error
                logger.exception("voucher queue sweep failed")

            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stopping.wait(), timeout=interval)

    async def sweep(self) -> int:
        """One pass over every connector this instance holds a socket for.

        A reconnect drains immediately; this is the backstop for an entry
        queued while the PC was *already* connected -- Tally closed, say, and
        then reopened without the socket ever dropping.
        """
        delivered = 0
        for connector_id in self._connectors_to_try():
            delivered += await self.drain(connector_id)
        if delivered:
            logger.info("delivered %d queued entr(ies)", delivered)
        return delivered

    def _connectors_to_try(self) -> list[str]:
        # Only sockets this process holds. A queued entry for a connector
        # attached to a sibling instance is that instance's to deliver, and its
        # own sweep will find it -- writes are deliberately not routed across
        # instances.
        return [link.connector_id for link in self._hub.local_links()]


def _message(error: Any) -> str | None:
    text = getattr(error, "user_message", None) or getattr(error, "message", None)
    # The column is 500 characters; Tally's complaints are one sentence, but a
    # stack-shaped message from somewhere unexpected must not fail the update.
    return text[:500] if text else None


def _gone() -> Any:
    from tally_core.protocol import JobError

    return JobError(
        code="company_gone",
        message="the company this entry belongs to is no longer linked",
        user_message=(
            "The company this entry was for is no longer connected, so it was "
            "not saved."
        ),
        retryable=False,
    )
