"""Provisioning the demo account, and keeping its books up to today.

:mod:`tally_backend.services.demo_books` invents a business. This module gives
it somewhere to live: an organisation, a user to sign in as, a connector that
was never installed, a company, and the stored datasets every read path already
knows how to serve.

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

:func:`ensure_demo_account` is idempotent and cheap when there is nothing to do,
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
from ..core.security import hash_password, verify_password
from ..db.models import (
    Company,
    CompanySyncState,
    Connector,
    ConnectorStatus,
    Membership,
    Organisation,
    OrgStatus,
    Role,
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


async def ensure_demo_account(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    hub: ConnectorHub,
    *,
    today: date | None = None,
) -> None:
    """Create the demo account if it is missing, and bring its books to today.

    Silent when unconfigured. Most deployments should not have a demo -- a
    developer's laptop, a test run, a second instance -- and one that appears
    because a default was generous is an account anybody can sign into.
    """
    email = settings.demo_email.strip().lower()
    if not settings.demo_enabled or not email or not settings.demo_password:
        return

    today = today or date.today()
    async with session_factory() as session:
        org = await _ensure_org(session, settings, email)
        company = await _ensure_company(session, settings, org)
        await _extend_books(session, settings, hub, company, today=today)
        await session.commit()


# --------------------------------------------------------------------------
# The account
# --------------------------------------------------------------------------


async def _ensure_org(session: AsyncSession, settings: Settings, email: str) -> Organisation:
    org = await session.scalar(select(Organisation).where(Organisation.is_demo.is_(True)))
    if org is None:
        org = Organisation(
            name=DEMO_ORG_NAME,
            status=OrgStatus.ACTIVE,
            # Live enough to read its own books. Every *change* is refused on
            # the strength of `is_demo` rather than on these numbers -- see
            # `services.entitlements.Entitlement.allows_changes` -- so the
            # limits here only describe what the account already holds.
            max_users=1,
            max_companies=1,
            is_demo=True,
            approved_at=utc_now(),
        )
        session.add(org)
        await session.flush()
        logger.info("created the demo organisation %s", org.id)

    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        user = User(
            email=email,
            password_hash=hash_password(settings.demo_password),
            full_name="Demo User",
            # Never true here, whatever the value is worth elsewhere: the demo
            # password is published, everyone shares the account, and a forced
            # password change would lock the next visitor out of it.
            must_change_password=False,
        )
        session.add(user)
        await session.flush()
        session.add(Membership(org_id=org.id, user_id=user.id, role=Role.ADMIN))
        logger.info("created the demo user %s", email)
    elif not verify_password(settings.demo_password, user.password_hash):
        # Rotating the published password has to actually take effect, which is
        # the opposite of the portal owner's rule: that account belongs to a
        # person who may have changed their own password, this one belongs to
        # the deployment manifest and to nobody.
        user.password_hash = hash_password(settings.demo_password)
        logger.info("reset the demo password from the environment")

    return org


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
