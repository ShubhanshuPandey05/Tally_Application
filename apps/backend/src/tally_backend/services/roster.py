"""What a connector is told about the account it serves.

The connector has a small local page -- 127.0.0.1, opened from the Start menu on
the shop's own PC -- answering the two questions somebody standing at that
machine actually has: *which of my companies is this PC feeding*, and *who can
see them*. Neither is answerable locally. The connector knows which companies
are open in Tally and nothing whatever about the account, because everything
about the account lives here.

So the backend pushes a roster down the socket that already exists.

The line this module holds is that a roster is **not a data feed**. Names,
roles, and when each company last synced -- no figures, no balances, nothing
from anybody's books. A connector that could be asked for a balance over its
loopback socket would be a second read path into the books, sitting outside
every check in ``deps.get_company``, reachable by anything running on that PC.
There is no such path, and this module is where it would have started.

Scoped to one connector, always. A shop with a counter PC and a back-office PC
sees each machine's own companies on that machine, which is also what stops one
customer's roster ever being built for another's connector: the connector id is
the query, not a parameter of it.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tally_core.protocol import Roster, RosterCompany, RosterUser

from ..db.models import (
    Company,
    CompanyAccess,
    CompanySyncState,
    Connector,
    Membership,
    Organisation,
    Role,
    User,
)

logger = logging.getLogger(__name__)


async def build_roster(session: AsyncSession, connector_id: str) -> Roster | None:
    """The roster frame for one connector, or ``None`` if it no longer exists.

    ``None`` rather than an empty roster: a connector whose row was deleted
    while its socket was open must not be told it serves an account with no
    companies and no people, which reads on the local page as "your books
    disappeared" rather than as "this PC was removed".
    """
    connector = await session.get(Connector, connector_id)
    if connector is None:
        return None

    # Fetched rather than reached through ``connector.organisation``: lazy
    # relationship loading on an async session raises on first access, and it
    # would do so here on a path whose failure mode is a socket that drops
    # immediately after every handshake.
    organisation = await session.get(Organisation, connector.org_id)

    companies = (
        (
            await session.execute(
                select(Company)
                .where(Company.connector_id == connector_id)
                .order_by(Company.tally_name)
            )
        )
        .scalars()
        .all()
    )

    # One query for every company's sync clock rather than one per company: a
    # roster is rebuilt on every reconnect across the whole fleet, and a loop of
    # gets here would be the reason a backend restart is slow.
    sync_times = dict(
        (
            await session.execute(
                select(CompanySyncState.company_id, CompanySyncState.updated_at).where(
                    CompanySyncState.company_id.in_([c.id for c in companies])
                )
            )
        ).all()
        if companies
        else []
    )

    company_ids = {company.id for company in companies}

    members = (
        await session.execute(
            select(User, Membership.role)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == connector.org_id)
            .order_by(User.email)
        )
    ).all()

    # Which staff members can see anything on this PC. Admins are not in here
    # and do not need to be -- their access comes from their role, and
    # materialising it as rows is exactly the mistake `visible_company_ids`
    # avoids.
    granted: set[str] = set()
    if company_ids:
        granted = set(
            (
                await session.execute(
                    select(CompanyAccess.user_id).where(
                        CompanyAccess.company_id.in_(company_ids)
                    )
                )
            )
            .scalars()
            .all()
        )

    return Roster(
        organisation=organisation.name if organisation is not None else "",
        org_status=organisation.status.value if organisation is not None else "",
        connector_name=connector.name,
        companies=[
            RosterCompany(
                id=company.id,
                name=company.label,
                tally_name=company.tally_name,
                is_active=company.is_active,
                last_synced_at=sync_times.get(company.id),
            )
            for company in companies
        ],
        users=[
            RosterUser(
                name=user.full_name or "",
                email=user.email,
                role=role.value,
                has_access=role.allows(Role.ADMIN) or user.id in granted,
            )
            for user, role in members
            if user.is_active
        ],
    )
