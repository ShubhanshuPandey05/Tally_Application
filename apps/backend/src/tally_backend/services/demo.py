"""The demo company: provisioning it, lending it out, and keeping it current.

:mod:`tally_backend.services.demo_books` invents a business. This module gives
it somewhere to live: an organisation nobody signs into, a connector that was
never installed, a company, and the stored datasets every read path already
knows how to serve.

**It is lent, not logged into.** Every account that has no books of its own yet
sees the demo company in its list -- a new signup waiting for approval, an
approved one that has paired a PC but not yet linked a company -- and stops
seeing it the moment its first real company is linked. There is one copy, held
by the demo organisation: two years of generated vouchers per signup would grow
the database with every download rather than with every customer.

There used to be a shared demo *login* instead, with a published password.
Everyone who used it was the same user, and a pending signup still opened on an
empty screen. :func:`ensure_demo_company` now disables that user on every start
and revokes its sessions, so the retired door stays shut on deployments that
had it.

The shape of the thing is the point. A demo could have been a pile of canned
API responses behind a flag, and that is what makes a demo that drifts away
from the product: the fake endpoints keep answering after the real ones have
changed shape, and nobody notices until a prospect does. Here there is no demo
read path at all. The rows go into ``snapshots`` and ``voucher_records`` exactly
as a connector's would, and every screen, report and drill-down is the same code
serving the same tables. If a dashboard tile breaks, it breaks in the demo too.

Two things are deliberately different, both in :class:`ReadService`:

* a demo read never falls through to a connector -- there is not one, and the
  attempt would cost a request timeout before failing;
* it is never reported stale. Freshness is a promise about a shop's PC, and
  this account has no PC to be out of date with. The app is told ``is_demo`` so
  it can say what the data is instead of implying it came from somewhere.

A third difference lives in ``deps.get_company``, which is where the lending
happens: a borrowed demo company answers reads only. Every write route resolves
its company through that one dependency, so none of them has to remember.

:func:`ensure_demo_company` is idempotent and cheap when there is nothing to do,
so it runs at startup and again each day: the books have to keep ending *today*
or "today's sales" is permanently zero and the dashboard is a museum piece.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings
from ..core.crypto import SecretBox
from ..db.models import (
    Company,
    CompanySyncState,
    Connector,
    ConnectorStatus,
    Membership,
    Organisation,
    OrgStatus,
    RefreshToken,
    User,
    utc_now,
)
from ..hub import ConnectorHub
from .demo_books import (
    COMPANY_NAME,
    DemoBooks,
    build_books,
    default_books_from,
    financial_year_start,
)
from .reads import ReadService
from .voucher_store import VoucherStore

logger = logging.getLogger(__name__)

DEMO_ORG_NAME = "TallyFlow Demo"
DEMO_CONNECTOR_NAME = "Demo Tally PC"


async def find_demo_company(session: AsyncSession) -> Company | None:
    """The demo company, if this deployment has one."""
    return await session.scalar(
        select(Company)
        .join(Organisation, Organisation.id == Company.org_id)
        .where(Organisation.is_demo.is_(True), Company.is_active.is_(True))
        .limit(1)
    )


async def is_demo_company(session: AsyncSession, company: Company) -> bool:
    org = await session.get(Organisation, company.org_id)
    return bool(org is not None and org.is_demo)


async def has_own_books(session: AsyncSession, org_id: str) -> bool:
    """Whether this organisation has linked a real company yet.

    Asked of the *organisation*, not of the person. A staff member who has not
    been granted any of their business's companies is waiting on their admin,
    not on a Tally PC, and handing them invented books instead would make the
    missing grant look like a working account.
    """
    found = await session.scalar(
        select(Company.id)
        .where(Company.org_id == org_id, Company.is_active.is_(True))
        .limit(1)
    )
    return found is not None


async def demo_company_for(
    session: AsyncSession, settings: Settings, org_id: str
) -> Company | None:
    """The demo company, when this organisation should be shown it.

    None once the organisation has a company of its own -- so the demo
    disappears on the same request that lists the first real one, rather than
    at pairing, which would leave a paired-but-unlinked account looking at
    nothing at all. None for the demo organisation itself, which already owns
    the company and must not be shown it twice.
    """
    if not settings.demo_enabled:
        return None
    demo = await find_demo_company(session)
    if demo is None or demo.org_id == org_id:
        return None
    if await has_own_books(session, org_id):
        return None
    return demo


async def ensure_demo_company(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    hub: ConnectorHub,
    *,
    today: date | None = None,
) -> None:
    """Create the demo company if it is missing, and bring its books to today.

    Silent when turned off. Not every deployment should lend a demo -- a
    developer's laptop, a test run -- and one that appears because a default
    was generous is a company every signup is shown.
    """
    if not settings.demo_enabled:
        return

    today = today or date.today()
    async with session_factory() as session:
        org = await _ensure_org(session)
        await _retire_shared_login(session, org)
        company = await _ensure_company(session, settings, org)
        await _extend_books(session, settings, hub, company, today=today)
        await session.commit()


# --------------------------------------------------------------------------
# The organisation that holds it
# --------------------------------------------------------------------------


async def _ensure_org(session: AsyncSession) -> Organisation:
    org = await session.scalar(select(Organisation).where(Organisation.is_demo.is_(True)))
    if org is None:
        org = Organisation(
            name=DEMO_ORG_NAME,
            status=OrgStatus.ACTIVE,
            # It holds the demo company and nothing else, and nobody signs into
            # it. Every *change* to that company is refused on the strength of
            # `is_demo` -- see `deps.get_company` -- so these numbers only
            # describe what the organisation already holds.
            max_users=0,
            max_companies=1,
            is_demo=True,
            approved_at=utc_now(),
        )
        session.add(org)
        await session.flush()
        logger.info("created the demo organisation %s", org.id)

    return org


async def _retire_shared_login(session: AsyncSession, org: Organisation) -> None:
    """Shut the old shared demo login, on deployments that had one.

    Its password was published, so leaving the user active would leave a door
    open that the app no longer shows. Disabled rather than deleted: the audit
    trail names this user, and "who did that?" must still have an answer.
    Sessions are revoked too, because disabling the user stops the next sign-in
    but not a refresh token already sitting on somebody's phone.
    """
    users = (
        await session.execute(
            select(User)
            .join(Membership, Membership.user_id == User.id)
            .where(Membership.org_id == org.id, User.is_active.is_(True))
        )
    ).scalars().all()
    now = utc_now()
    for user in users:
        user.is_active = False
        tokens = (
            await session.execute(
                select(RefreshToken).where(
                    RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None)
                )
            )
        ).scalars().all()
        for token in tokens:
            token.revoked_at = now
        logger.info("disabled the retired shared demo login %s", user.email)


async def _ensure_company(
    session: AsyncSession, settings: Settings, org: Organisation
) -> Company:
    connector = await session.scalar(
        select(Connector).where(Connector.org_id == org.id).limit(1)
    )
    if connector is None:
        connector = Connector(
            org_id=org.id,
            name=DEMO_CONNECTOR_NAME,
            # A secret that is never used: nothing will ever dial in with it,
            # and the column is not nullable. Generated rather than fixed so a
            # copied deployment does not share a working credential with any
            # other, however inert it is here.
            secret_encrypted=SecretBox(settings.encryption_keys).encrypt(
                f"demo-{org.id}-never-used"
            ),
            status=ConnectorStatus.ACTIVE,
            hostname="demo-pc",
            os="Windows 11",
            # Presented as recently seen so the connectors screen reads like a
            # working install rather than a broken one. Nothing routes to it:
            # `ReadService` short-circuits every demo read before the hub.
            last_seen_at=utc_now(),
            last_tally_online=True,
        )
        session.add(connector)
        await session.flush()

    company = await session.scalar(
        select(Company).where(Company.connector_id == connector.id).limit(1)
    )
    if company is None:
        company = Company(
            org_id=org.id,
            connector_id=connector.id,
            tally_name=COMPANY_NAME,
            base_currency="INR",
        )
        session.add(company)
        await session.flush()
        logger.info("created the demo company %s", company.id)

    return company


# --------------------------------------------------------------------------
# The books
# --------------------------------------------------------------------------


async def _extend_books(
    session: AsyncSession,
    settings: Settings,
    hub: ConnectorHub,
    company: Company,
    *,
    today: date,
) -> None:
    """Post whatever days are missing, and republish the derived datasets.

    Only the *new* vouchers are ingested. The generator is deterministic, so
    rebuilding the whole history costs under a second -- but writing five
    thousand unchanged rows back to the database every morning is a cost with
    nothing on the other side of it.

    The masters are rewritten every time, because they are not history: a
    ledger balance, a stock level and an unpaid bill are all statements about
    *now*, and every one of them moved when yesterday's trade was posted.
    """
    state = await session.get(CompanySyncState, company.id)
    if state is None:
        state = CompanySyncState(company_id=company.id)
        session.add(state)

    books_from = state.backfilled_from or default_books_from(today, years=settings.demo_years)
    if state.backfilled_to is not None and state.backfilled_to >= today:
        return

    books = build_books(books_from=books_from, upto=today)
    first_new = (
        state.backfilled_to + timedelta(days=1) if state.backfilled_to else books.books_from
    )
    new_vouchers = books.vouchers_between(first_new, today)

    if new_vouchers:
        await VoucherStore(session).ingest(
            company.id, new_vouchers, window=(first_new, today)
        )

    # The year Tally would report as open, so the app's financial-year picker
    # lands on the right one by the same rule it uses for a real company.
    company.financial_year_from = datetime.combine(
        financial_year_start(today), time.min, tzinfo=UTC
    )
    state.books_from = books.books_from
    state.backfilled_from = books.books_from
    state.backfilled_to = today
    state.voucher_alter_id = books.markers.get("voucher_alter_id")
    state.master_alter_id = books.markers.get("master_alter_id")
    state.supports_incremental = True
    state.last_delta_at = utc_now()
    state.last_reconcile_at = utc_now()

    await _publish(session, settings, hub, company, books, today=today)
    logger.info(
        "demo books now cover %s to %s (%d new voucher(s))",
        books.books_from,
        today,
        len(new_vouchers),
    )


async def _publish(
    session: AsyncSession,
    settings: Settings,
    hub: ConnectorHub,
    company: Company,
    books: DemoBooks,
    *,
    today: date,
) -> None:
    """Write the master datasets as snapshots, under the keys reads look for.

    Through :meth:`ReadService.put_snapshot` rather than by inserting rows, so
    the params key, row count and freshness stamp are derived by the same code
    that derives them for a real read. A snapshot written a second way is a
    snapshot the read path can fail to find.
    """
    reads = ReadService(session, hub, settings)

    await reads.put_snapshot(company, dataset="groups.list", payload=books.groups)
    await reads.put_snapshot(company, dataset="ledgers.list", payload=books.ledgers)
    await reads.put_snapshot(company, dataset="stock_items.list", payload=books.stock_items)
    await reads.put_snapshot(company, dataset="voucher_types.list", payload=books.voucher_types)
    await reads.put_snapshot(company, dataset="company.markers", payload=books.markers)
    # Ageing is computed against a date, so the bills carry the day they were
    # aged as of -- the same params the dashboard and the outstanding report
    # ask with. Yesterday's key is left behind rather than deleted: it is what
    # answers a read that arrives between midnight and the next refresh.
    await reads.put_snapshot(
        company,
        dataset="outstanding.bills",
        params={"as_of": today.isoformat()},
        payload=books.bills,
    )
