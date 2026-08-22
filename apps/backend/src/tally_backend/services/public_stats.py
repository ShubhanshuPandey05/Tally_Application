"""Aggregate counts for the public marketing site.

The only unauthenticated read of customer data in the product, so the shape of
what it may return is fixed here rather than left to a handler: four integers
and a timestamp. No names, no identifiers, nothing per-organisation. Anything
that could single out one business does not belong in this module.

Two properties matter more than the numbers themselves:

1. **Zero is a real answer, and "we do not know" is a different one.** The
   marketing site prints these figures beside a claim, so a count that failed to
   compute must never arrive looking like a count of nothing. A failure raises,
   and the site drops the row entirely rather than announcing zero businesses.
2. **It cannot cost the phones anything.** These are aggregate scans of tables
   the app reads from, requested by every visitor and every crawler. The result
   is reused for a minute, so traffic to the site cannot turn into load on the
   database behind a customer's dashboard.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Company, Connector, ConnectorStatus, Organisation, OrgStatus

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PublicStats:
    """What the site is allowed to know."""

    #: Businesses whose account has actually been switched on. Pending signups
    #: are deliberately excluded: an account nobody has approved is a form
    #: submission, and counting it would let the headline figure be inflated by
    #: anyone who can fill one in.
    businesses: int
    #: Connectors paired and not revoked -- one per Windows PC running Tally.
    tally_pcs: int
    #: Companies being read across all of them.
    companies: int
    #: How many of those PCs have been heard from recently. Derived from
    #: ``last_seen_at`` rather than from the hub's live sockets, because the hub
    #: only knows about connectors attached to *this* process and the answer
    #: would quietly shrink the moment a second replica existed.
    connected_now: int
    #: When these were computed. The site does not print it; it is here so a
    #: stale cache is visible in the response rather than inferred.
    as_of: datetime

    def as_dict(self) -> dict:
        return {
            "businesses": self.businesses,
            "tally_pcs": self.tally_pcs,
            "companies": self.companies,
            "connected_now": self.connected_now,
            "as_of": self.as_of.isoformat(),
        }


class PublicStatsService:
    """Computes the counts, and reuses them for a while.

    The cache is process-local and deliberately unsophisticated. With several
    replicas each keeps its own copy and they may disagree for up to one TTL,
    which is the correct trade: these are figures on a marketing page, and
    coordinating them through Redis would be spending a dependency on nothing.
    """

    def __init__(self, *, ttl_seconds: int, online_window_seconds: int) -> None:
        self._ttl = max(0, ttl_seconds)
        self._online_window = timedelta(seconds=max(0, online_window_seconds))
        self._cached: PublicStats | None = None
        self._cached_at: float = 0.0

    async def get(self, session: AsyncSession) -> PublicStats:
        """Return the counts, computing them only when the cache has expired.

        Raises whatever the database raised. A caller that swallowed the error
        and substituted zeros would be publishing a false claim, which is the
        one outcome this endpoint must not have.
        """
        now = time.monotonic()
        cached = self._cached
        if cached is not None and now - self._cached_at < self._ttl:
            return cached

        stats = await self._compute(session)
        self._cached = stats
        self._cached_at = now
        return stats

    async def _compute(self, session: AsyncSession) -> PublicStats:
        seen_since = datetime.now(UTC) - self._online_window

        businesses = await session.scalar(
            select(func.count())
            .select_from(Organisation)
            .where(Organisation.status == OrgStatus.ACTIVE)
        )
        tally_pcs = await session.scalar(
            select(func.count())
            .select_from(Connector)
            .where(Connector.status != ConnectorStatus.REVOKED)
        )
        companies = await session.scalar(
            select(func.count()).select_from(Company).where(Company.is_active.is_(True))
        )
        connected_now = await session.scalar(
            select(func.count())
            .select_from(Connector)
            .where(
                Connector.status != ConnectorStatus.REVOKED,
                Connector.last_seen_at.is_not(None),
                Connector.last_seen_at >= seen_since,
            )
        )

        return PublicStats(
            businesses=int(businesses or 0),
            tally_pcs=int(tally_pcs or 0),
            companies=int(companies or 0),
            connected_now=int(connected_now or 0),
            as_of=datetime.now(UTC),
        )
