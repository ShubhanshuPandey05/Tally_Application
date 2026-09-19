"""Creating a party or a stock item, and knowing when one is missing.

The most common way a voucher fails is that something it names is not in the
books yet: *"Ledger 'Ram Traders' does not exist!"*. That sentence is useful to
a person and useless to a program, so :func:`missing_master` turns it into
something the app can act on — a kind and a name — and the app offers to create
exactly that.

**Nothing here creates a master on its own.** Every call is something a person
confirmed. A ledger created automatically on each unrecognised name turns a
chart of accounts into "Ram Traders", "Ram traders" and "Ram Trader", and no
report afterwards can say which of the three a customer's outstanding belongs
to. The confirmation is the feature, not an obstacle in front of it.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from tally_core.domain.writes import LedgerDraft, MasterCreated, StockItemDraft
from tally_core.tally import CREATE_LEDGER, CREATE_STOCK_ITEM

from ..config import Settings
from ..core.errors import WriteRefused
from ..db.models import Company, Organisation
from ..hub.hub import ConnectorHub
from .audit import record

logger = logging.getLogger(__name__)

#: Tally's own phrasing, verified live on 2026-09-17 against a company that had
#: neither: ``Ledger 'ZZ' does not exist!`` and
#: ``Stock Group 'ZZ' does not exist!``. The quotes are ASCII apostrophes in the
#: decoded XML, having arrived as ``&apos;``.
_MISSING = re.compile(
    r"(?P<what>Ledger|Stock Item|Group|Stock Group|Unit)\s+"
    r"'(?P<name>[^']+)'\s+does not exist",
    re.IGNORECASE,
)

#: What the app may offer to create. ``Group`` and ``Unit`` are deliberately
#: absent: a stock group or a unit of measure is a decision about how a
#: business classifies things, and inventing one from a counter is how a
#: chart of accounts grows a shape nobody chose.
CREATABLE = {"ledger", "stock item"}


def missing_master(message: str | None) -> tuple[str, str] | None:
    """Pull ``("ledger", "Ram Traders")`` out of a Tally complaint.

    Returns ``None`` for anything it does not recognise, which is the safe
    direction: an unparsed error is shown to the person as Tally worded it,
    and the offer to create simply does not appear.
    """
    if not message:
        return None
    found = _MISSING.search(message)
    if found is None:
        return None
    return found.group("what").lower(), found.group("name").strip()


class MasterService:
    """Creates the one or two master records a counter genuinely needs."""

    def __init__(self, session: AsyncSession, hub: ConnectorHub, settings: Settings) -> None:
        self._session = session
        self._hub = hub
        self._settings = settings

    async def create_ledger(
        self, *, company: Company, draft: LedgerDraft, user_id: str, request: Any = None
    ) -> MasterCreated:
        return await self._create(
            company=company,
            mutation=CREATE_LEDGER,
            draft=draft,
            name=draft.name,
            what="ledger",
            user_id=user_id,
            request=request,
        )

    async def create_stock_item(
        self,
        *,
        company: Company,
        draft: StockItemDraft,
        user_id: str,
        request: Any = None,
    ) -> MasterCreated:
        return await self._create(
            company=company,
            mutation=CREATE_STOCK_ITEM,
            draft=draft,
            name=draft.name,
            what="stock item",
            user_id=user_id,
            request=request,
        )

    async def _create(
        self,
        *,
        company: Company,
        mutation: str,
        draft: Any,
        name: str,
        what: str,
        user_id: str,
        request: Any,
    ) -> MasterCreated:
        started = time.monotonic()

        org = await self._session.get(Organisation, company.org_id)
        if org is not None and org.is_demo:
            raise WriteRefused(
                "refusing a master write to the demo organisation",
                user_message=(
                    "This is the demo account, so nothing can be added. "
                    "Connect your own TallyPrime to create them."
                ),
            )

        # Never queued, unlike a voucher. A master exists to let an entry be
        # made *now*; holding it for later would leave somebody looking at a
        # form they still cannot complete, and by the time the PC came back the
        # entry it was for is long gone.
        result = await self._hub.write(
            connector_id=company.connector_id,
            mutation=mutation,
            params={"company": company.tally_name, "draft": draft.model_dump(mode="json")},
            timeout_seconds=self._settings.default_write_timeout_seconds,
        )

        duration_ms = int((time.monotonic() - started) * 1000)

        if not result.ok:
            error = result.error
            await record(
                self._session,
                action=f"master.create.{what.replace(' ', '_')}",
                org_id=company.org_id,
                user_id=user_id,
                company_id=company.id,
                outcome="error",
                duration_ms=duration_ms,
                detail={"name": name, "code": error.code if error else "unknown"},
                request=request,
            )
            return MasterCreated(
                errors=1,
                name=name,
                message=error.user_message if error else f"That {what} was not created.",
            )

        created = MasterCreated.model_validate(result.data())
        await record(
            self._session,
            action=f"master.create.{what.replace(' ', '_')}",
            org_id=company.org_id,
            user_id=user_id,
            company_id=company.id,
            outcome="ok" if created.ok else "error",
            duration_ms=duration_ms,
            # The name is the point of the record: a chart of accounts that
            # grew a duplicate needs to say who added it and when.
            detail={"name": name},
            request=request,
        )
        return created
