"""Creating a voucher in a customer's books.

The only service in this backend that changes anything in TallyPrime. Every
other service reads.

Three things it will not do, and each is here rather than in the route so that
a second caller added later inherits them:

* **It will not write to the demo account.** There is no PC behind those books
  and the rows are regenerated daily, so an entry would vanish and look like a
  bug in the product a prospect is evaluating.
* **It will never re-send on its own.** Not on a timeout, not on a disconnect,
  not on a busy connector. Once the frame is on the socket this side cannot
  tell a write that never arrived from one that posted and lost its reply, and
  re-sending turns an unknown into a duplicate. What it *does* pass back is
  ``can_retry`` -- whether something further down could prove nothing was
  written, so the app knows when offering the person a "Try again" is safe.
* **It will not invent a success.** The result is whatever Tally said. A reply
  that parsed but created nothing is a failure with a reason attached, not an
  optimistic "saved".
"""

from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from tally_core.domain.writes import VoucherDraft, VoucherPosted
from tally_core.tally import CREATE_VOUCHER

from ..config import Settings
from ..core.errors import WriteRefused
from ..db.models import Company, Organisation
from ..hub.hub import ConnectorHub
from .audit import record
from .masters import CREATABLE, missing_master
from .voucher_queue import VoucherQueue

logger = logging.getLogger(__name__)

class VoucherWriteService:
    """Sends one voucher to the connector that owns a company's books."""

    def __init__(
        self,
        session: AsyncSession,
        hub: ConnectorHub,
        settings: Settings,
        queue: VoucherQueue | None = None,
    ) -> None:
        self._session = session
        self._hub = hub
        self._settings = settings
        #: Optional so a test can drive the live path in isolation. In the app
        #: it is always present.
        self._queue = queue

    async def create(
        self,
        *,
        company: Company,
        draft: VoucherDraft,
        user_id: str,
        request: Any = None,
    ) -> VoucherPosted:
        """Create one voucher, returning what Tally reported.

        Raises :class:`WriteRefused` only for the cases where nothing was sent.
        Anything that actually reached a connector comes back as a
        :class:`VoucherPosted` whose ``ok`` says how it went, because the
        caller needs the counts and the message either way.
        """
        started = time.monotonic()

        org = await self._session.get(Organisation, company.org_id)
        if org is not None and org.is_demo:
            raise WriteRefused(
                "refusing a write to the demo organisation",
                user_message=(
                    "This is the demo account, so entries cannot be saved. "
                    "Connect your own TallyPrime to create them."
                ),
            )

        # The draft names the company by our id; Tally knows it by its own name,
        # and only the connector can resolve that. Sending `tally_name` is what
        # keeps `SVCURRENTCOMPANY` pinned to the right books -- an unpinned
        # import posts into whichever company is open on that PC.
        params: dict[str, Any] = {
            "company": company.tally_name,
            "draft": draft.model_dump(mode="json"),
        }

        result = await self._hub.write(
            connector_id=company.connector_id,
            mutation=CREATE_VOUCHER,
            params=params,
            timeout_seconds=self._settings.default_write_timeout_seconds,
        )

        duration_ms = int((time.monotonic() - started) * 1000)

        if not result.ok:
            error = result.error
            await self._audit(
                company=company,
                user_id=user_id,
                draft=draft,
                outcome="error",
                duration_ms=duration_ms,
                detail={"code": error.code if error else "unknown"},
                request=request,
            )
            safe = bool(error and error.retryable)

            # The queue accepts exactly the failures that can *prove* nothing
            # reached Tally -- the PC offline, TallyPrime closed, the company
            # not open. A timeout is not among them: the voucher may already be
            # in the books, and holding it for a second attempt is how one
            # receipt becomes two.
            if safe and self._queue is not None:
                queued = await self._queue.enqueue(
                    self._session, company=company, draft=draft, user_id=user_id
                )
                return VoucherPosted(
                    created=0,
                    message=(
                        "Your Tally PC isn't available, so this is saved here and "
                        "will go to TallyPrime as soon as it is back."
                    ),
                    optional=draft.optional,
                    queued=True,
                    pending_id=queued.id,
                    # Nothing for the person to do: it is already held. Offering
                    # a retry as well would be an invitation to create a second
                    # copy of an entry that is sitting in the queue.
                    can_retry=False,
                )

            # "Ledger 'Ram Traders' does not exist!" is the most common way
            # an entry fails, and it is fixable in one tap if the app is told
            # *what* is missing rather than handed a sentence to parse.
            missing = missing_master(error.message if error else None)
            kind, name = missing if missing else (None, None)
            if kind is not None and kind not in CREATABLE:
                # A stock group or a unit is a decision about how a business
                # classifies things. Report it; never offer to invent it.
                kind = None
                name = None

            # Surfaced rather than raised: the phone must be able to tell
            # "nothing was saved, try again" from "this may have been saved,
            # go and look", and both arrive here as a failed result.
            return VoucherPosted(
                created=0,
                errors=1,
                message=error.user_message if error else "The entry was not saved.",
                optional=draft.optional,
                missing_kind=kind,
                missing_name=name,
                # Carried through from whoever decided it -- the hub for a PC
                # it could not reach, the connector for a Tally it could not
                # reach. Never widened here: this layer has no evidence of its
                # own about whether anything was written.
                can_retry=safe,
            )

        posted = VoucherPosted.model_validate(result.data())

        await self._audit(
            company=company,
            user_id=user_id,
            draft=draft,
            outcome="ok" if posted.ok else "error",
            duration_ms=duration_ms,
            # The voucher id, never the amount. This trail is kept for two days
            # and read during support calls; what somebody's receipt was worth
            # is not a diagnostic.
            detail={"voucher_id": posted.voucher_id, "optional": posted.optional},
            request=request,
        )
        return posted

    async def _audit(
        self,
        *,
        company: Company,
        user_id: str,
        draft: VoucherDraft,
        outcome: str,
        duration_ms: int,
        detail: dict[str, Any],
        request: Any,
    ) -> None:
        """Record who asked for what.

        A write is the one thing in this product that changes a customer's
        books, so it is audited whatever the outcome -- including the refusals.
        A trail that only holds the successes cannot answer "who tried?".
        """
        await record(
            self._session,
            action="voucher.create",
            org_id=company.org_id,
            user_id=user_id,
            company_id=company.id,
            outcome=outcome,
            duration_ms=duration_ms,
            detail={"kind": str(draft.kind), **detail},
            request=request,
        )
