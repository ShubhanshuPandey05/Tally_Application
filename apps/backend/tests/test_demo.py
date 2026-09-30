"""The demo company.

What is being pinned here is not "the seeder ran". It is that a new account
waiting for approval is lent the *product* -- the same endpoints, the same
shapes, the same numbers agreeing across screens -- that it cannot change
anything, because every other new account is looking at the same books, and
that the loan ends the moment the account has books of its own.

The lending rule is the one exception to tenant isolation in the whole backend,
so most of what follows is about what it must *not* allow.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from backend_support import LifespanRunner
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tally_backend.config import Settings
from tally_backend.core.security import hash_password
from tally_backend.db.models import (
    Company,
    Connector,
    ConnectorStatus,
    Membership,
    Organisation,
    OrgStatus,
    RefreshToken,
    Role,
    User,
    utc_now,
)
from tally_backend.main import create_app
from tally_backend.services.demo_books import (
    COMPANY_NAME,
    build_books,
    default_books_from,
    financial_year_start,
)


@pytest.fixture
def demo_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "demo_enabled": True,
            # One year rather than two. The books are a pure function of the
            # window, so a shorter one exercises identical code and keeps the
            # suite quick.
            "demo_years": 1,
        }
    )


@pytest_asyncio.fixture
async def demo_app(demo_settings: Settings, fake_connector):
    application = create_app(demo_settings)
    async with LifespanRunner(application):
        application.state.hub.run = fake_connector.run  # type: ignore[method-assign]
        application.state.hub.is_online = fake_connector.is_online  # type: ignore[method-assign]
        yield application


async def _sign_up(client: AsyncClient, email: str) -> dict[str, str]:
    """A brand new signup: pending, entitled to nothing -- the demo's audience."""
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "a-sufficiently-long-password",
            "full_name": "New Owner",
            "org_name": f"Business of {email}",
        },
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest_asyncio.fixture
async def demo_client(demo_app) -> AsyncClient:
    """Signed in as a new, unapproved business -- nobody's demo login."""
    async with AsyncClient(
        transport=ASGITransport(app=demo_app), base_url="http://test"
    ) as client:
        client.headers.update(await _sign_up(client, "new@shop.in"))
        yield client


async def _demo_id(client: AsyncClient) -> str:
    companies = (await client.get("/v1/companies")).json()
    assert len(companies) == 1 and companies[0]["is_demo"] is True, companies
    return companies[0]["id"]


async def approve(app, org_id: str) -> None:
    """Stand in for a portal approval. Local rather than imported from
    ``conftest``: see ``backend_support`` for why this suite never does that."""
    async with app.state.session_factory() as session:
        org = await session.get(Organisation, org_id)
        org.status = OrgStatus.ACTIVE
        org.max_users = 5
        org.max_companies = 5
        org.approved_at = utc_now()
        await session.commit()


async def _link_own_company(app, client: AsyncClient) -> str:
    """Approve the caller's business and give it a real company."""
    org_id = (await client.get("/v1/auth/me")).json()["org_id"]
    await approve(app, org_id)
    async with app.state.session_factory() as session:
        connector = Connector(
            org_id=org_id,
            name="Shop PC",
            secret_encrypted="unused",
            status=ConnectorStatus.ACTIVE,
        )
        session.add(connector)
        await session.flush()
        company = Company(org_id=org_id, connector_id=connector.id, tally_name="Own Books")
        session.add(company)
        await session.commit()
        return company.id


# --------------------------------------------------------------------------
# The books themselves
# --------------------------------------------------------------------------


def test_the_books_are_the_same_every_time_they_are_built():
    """A demo whose history rewrites itself is the one thing an accountant sees."""
    upto = date(2026, 9, 2)
    first = build_books(books_from=date(2026, 4, 1), upto=upto)
    second = build_books(books_from=date(2026, 4, 1), upto=upto)

    assert first.vouchers == second.vouchers
    assert first.ledgers == second.ledgers


def test_extending_the_books_leaves_yesterday_alone():
    """Tomorrow's rebuild must not restate today's figures."""
    books_from = date(2026, 4, 1)
    upto = date(2026, 8, 20)
    today = build_books(books_from=books_from, upto=upto)
    tomorrow = build_books(books_from=books_from, upto=upto + timedelta(days=1))

    assert tomorrow.vouchers[: len(today.vouchers)] == today.vouchers


def test_every_voucher_balances():
    """Invented or not, these are books: debits equal credits on every voucher."""
    books = build_books(books_from=date(2026, 4, 1), upto=date(2026, 6, 30))

    for voucher in books.vouchers:
        debit = sum(
            Decimal(e["amount"]["amount"])
            for e in voucher["ledger_entries"]
            if e["amount"]["side"] == "debit"
        )
        credit = sum(
            Decimal(e["amount"]["amount"])
            for e in voucher["ledger_entries"]
            if e["amount"]["side"] == "credit"
        )
        assert debit == credit, voucher["voucher_number"]


def test_what_customers_owe_matches_their_ledger_balances():
    """The dashboard reads receivables from bills and cash from ledgers.

    They are computed by different code from different datasets, so a demo that
    invented them separately would contradict itself the moment somebody tapped
    a customer's name -- which is exactly when they were starting to trust it.
    """
    books = build_books(books_from=date(2026, 4, 1), upto=date(2026, 8, 31))

    by_party: dict[str, Decimal] = {}
    for bill in books.bills:
        if bill["kind"] != "receivable":
            continue
        by_party[bill["party_name"]] = by_party.get(
            bill["party_name"], Decimal(0)
        ) + Decimal(bill["pending_amount"]["amount"])

    balances = {
        row["name"]: Decimal(row["closing_balance"]["amount"])
        for row in books.ledgers
        if row["parent_group"] == "Sundry Debtors"
        and row["closing_balance"]["side"] == "debit"
    }

    for party, owed in by_party.items():
        assert balances.get(party) == owed, party


def test_the_books_run_to_today_so_the_dashboard_is_not_a_museum():
    upto = date(2026, 9, 2)
    books = build_books(books_from=default_books_from(upto, years=1), upto=upto)

    assert books.vouchers[-1]["date"] == upto.isoformat()
    assert books.books_from == date(2025, 4, 1)
    assert books.markers["financial_year_from"] == financial_year_start(upto).isoformat()


# --------------------------------------------------------------------------
# Being lent it
# --------------------------------------------------------------------------


async def test_a_new_signup_is_lent_the_demo_while_it_waits(demo_client: AsyncClient):
    """The whole point: a pending account explores instead of staring at nothing."""
    me = (await demo_client.get("/v1/auth/me")).json()
    assert me["subscription"]["status"] == "pending"

    companies = (await demo_client.get("/v1/companies")).json()
    assert [c["tally_name"] for c in companies] == [COMPANY_NAME]
    assert companies[0]["is_demo"] is True
    # The financial-year picker is built from this, so it has to arrive.
    assert companies[0]["books_from"] is not None

    detail = await demo_client.get(f"/v1/companies/{companies[0]['id']}")
    assert detail.status_code == 200
    assert detail.json()["is_demo"] is True


async def test_the_dashboard_has_figures_without_a_connector(
    demo_client: AsyncClient, fake_connector
):
    company_id = await _demo_id(demo_client)

    response = await demo_client.get(f"/v1/companies/{company_id}/dashboard")
    assert response.status_code == 200, response.text
    body = response.json()

    sections = body["sections"]
    assert sections["sales"]["ok"] is True
    assert sections["receivables"]["ok"] is True
    assert sections["cash_and_bank"]["ok"] is True
    assert sections["inventory"]["ok"] is True

    # The point of the whole design: nothing was asked of any connector.
    assert fake_connector.calls == []
    # And the freshness banner stays quiet -- there is no PC to be stale with.
    assert body["freshness"]["is_stale"] is False


async def test_reports_are_populated(demo_client: AsyncClient):
    company_id = await _demo_id(demo_client)
    today = date.today()

    ledgers = await demo_client.get(f"/v1/companies/{company_id}/reports/ledgers")
    assert ledgers.status_code == 200
    assert len(ledgers.json()["data"]["ledgers"]) > 20

    stock = await demo_client.get(f"/v1/companies/{company_id}/reports/stock")
    assert stock.status_code == 200
    assert len(stock.json()["data"]["items"]) > 5

    outstanding = await demo_client.get(
        f"/v1/companies/{company_id}/reports/outstanding", params={"kind": "receivable"}
    )
    assert outstanding.status_code == 200
    assert outstanding.json()["data"]["bills"]

    daybook = await demo_client.get(
        f"/v1/companies/{company_id}/reports/daybook",
        params={
            "from_date": (today - timedelta(days=30)).isoformat(),
            "to_date": today.isoformat(),
        },
    )
    assert daybook.status_code == 200
    assert daybook.json()["data"]["vouchers"]


async def test_a_year_ago_still_answers_from_the_store(demo_client: AsyncClient):
    """History is what makes the year filter worth having."""
    company_id = await _demo_id(demo_client)
    year_start = financial_year_start(date.today())

    response = await demo_client.get(
        f"/v1/companies/{company_id}/reports/register",
        params={
            "kind": "sales",
            "from_date": year_start.isoformat(),
            "to_date": date.today().isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["months"]


# --------------------------------------------------------------------------
# What a borrower may not do
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("DELETE", ""),
        ("POST", "/sync"),
        ("DELETE", "/sync"),
        ("POST", "/vouchers"),
        ("POST", "/ledgers"),
        ("POST", "/stock-items"),
        ("DELETE", "/vouchers/pending/anything"),
    ],
)
async def test_nothing_in_the_demo_can_be_changed(
    demo_client: AsyncClient, fake_connector, method: str, path: str
):
    """Every new account shares these books, so no one of them may alter them.

    One case per write route that exists today. They are refused in
    ``deps.get_company`` rather than route by route, so a write route added
    tomorrow is refused too -- and this list is where to add it.
    """
    company_id = await _demo_id(demo_client)

    response = await demo_client.request(
        method, f"/v1/companies/{company_id}{path}", json={}
    )

    assert response.status_code == 403, response.text
    assert "demo" in response.json()["error"]["message"].lower()
    assert fake_connector.calls == []
    # Still there afterwards, for this account and for everyone else.
    assert await _demo_id(demo_client) == company_id


async def test_the_loan_ends_when_the_account_has_books_of_its_own(
    demo_app, demo_client: AsyncClient
):
    demo_id = await _demo_id(demo_client)

    own_id = await _link_own_company(demo_app, demo_client)

    companies = (await demo_client.get("/v1/companies")).json()
    assert [c["id"] for c in companies] == [own_id]
    assert companies[0]["is_demo"] is False
    # And not merely hidden from the list: the loan is over.
    gone = await demo_client.get(f"/v1/companies/{demo_id}/dashboard")
    assert gone.status_code == 404


async def test_a_paired_pc_without_a_company_keeps_the_demo(
    demo_app, demo_client: AsyncClient
):
    """Pairing and linking are separate steps; the gap between them must not be empty."""
    org_id = (await demo_client.get("/v1/auth/me")).json()["org_id"]
    await approve(demo_app, org_id)
    paired = await demo_client.post("/v1/connectors", json={"name": "Shop PC"})
    assert paired.status_code == 201, paired.text

    assert (await demo_client.get("/v1/companies")).json()[0]["is_demo"] is True


async def test_borrowing_the_demo_opens_nobody_elses_books(demo_app):
    """The exception to tenant isolation is exactly one company wide."""
    async with AsyncClient(
        transport=ASGITransport(app=demo_app), base_url="http://test"
    ) as client:
        owner = await _sign_up(client, "owner@real.in")
        client.headers.update(owner)
        real_id = await _link_own_company(demo_app, client)

        newcomer = await _sign_up(client, "newcomer@shop.in")
        client.headers.update(newcomer)
        # Borrowing the demo does not make someone else's company readable.
        refused = await client.get(f"/v1/companies/{real_id}/dashboard")
        assert refused.status_code == 404

        # Nor does an account with its own books get to read the demo.
        lent_id = await _demo_id(client)
        client.headers.update(owner)
        assert (await client.get(f"/v1/companies/{lent_id}/dashboard")).status_code == 404


async def test_a_lapsed_account_is_not_lent_anything(demo_app, demo_client: AsyncClient):
    """402 means "your subscription is not live" -- the demo is no way round it."""
    demo_id = await _demo_id(demo_client)
    org_id = (await demo_client.get("/v1/auth/me")).json()["org_id"]
    async with demo_app.state.session_factory() as session:
        org = await session.get(Organisation, org_id)
        org.status = OrgStatus.SUSPENDED
        await session.commit()

    assert (await demo_client.get(f"/v1/companies/{demo_id}/dashboard")).status_code == 402


# --------------------------------------------------------------------------
# Provisioning, and the login it replaced
# --------------------------------------------------------------------------


async def test_a_second_start_does_not_duplicate_the_company(demo_settings: Settings):
    """Startup runs the seeder every time; it must be idempotent."""
    from tally_backend.services.demo import ensure_demo_company

    application = create_app(demo_settings)
    async with LifespanRunner(application):
        hub = application.state.hub
        factory = application.state.session_factory
        await ensure_demo_company(factory, demo_settings, hub)
        await ensure_demo_company(factory, demo_settings, hub)

        async with factory() as session:
            orgs = (
                await session.execute(select(Organisation).where(Organisation.is_demo.is_(True)))
            ).scalars().all()
            assert len(orgs) == 1
            companies = (
                await session.execute(select(Company).where(Company.org_id == orgs[0].id))
            ).scalars().all()
            assert len(companies) == 1


async def test_the_retired_shared_login_is_shut(demo_app, demo_settings: Settings):
    """Its password was published; a deployment that had it must not keep it open."""
    from tally_backend.services.demo import ensure_demo_company

    email, password = "demo@tallyflow.in", "a-published-password"
    async with demo_app.state.session_factory() as session:
        org = await session.scalar(select(Organisation).where(Organisation.is_demo.is_(True)))
        user = User(email=email, password_hash=hash_password(password))
        session.add(user)
        await session.flush()
        session.add(Membership(org_id=org.id, user_id=user.id, role=Role.ADMIN))
        session.add(
            RefreshToken(
                user_id=user.id,
                token_hash="a" * 64,
                family_id="family",
                expires_at=utc_now() + timedelta(days=30),
            )
        )
        await session.commit()

    await ensure_demo_company(
        demo_app.state.session_factory, demo_settings, demo_app.state.hub
    )

    async with demo_app.state.session_factory() as session:
        user = await session.scalar(select(User).where(User.email == email))
        assert user.is_active is False
        token = await session.scalar(select(RefreshToken).where(RefreshToken.user_id == user.id))
        assert token.revoked_at is not None

    async with AsyncClient(
        transport=ASGITransport(app=demo_app), base_url="http://test"
    ) as client:
        login = await client.post("/v1/auth/login", json={"email": email, "password": password})
        assert login.status_code in (401, 403)
        assert (await client.post("/v1/auth/demo")).status_code == 404


async def test_older_apps_are_told_there_is_no_demo_login(demo_app):
    """Installed builds from before 0.9 draw "Explore the demo" when this is true."""
    async with AsyncClient(
        transport=ASGITransport(app=demo_app), base_url="http://test"
    ) as client:
        assert (await client.get("/v1/public/config")).json()["demo_available"] is False


async def test_a_server_without_a_demo_lends_nothing(client, signed_up):
    """The ordinary fixture has the demo turned off, which is most deployments."""
    companies = await client.get("/v1/companies", headers=signed_up["headers"])
    assert companies.json() == []
