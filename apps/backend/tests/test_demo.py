"""The demo account.

What is being pinned here is not "the seeder ran". It is that a visitor who
signs into the demo gets the *product* -- the same endpoints, the same shapes,
the same numbers agreeing across screens -- and that they cannot change
anything, because thousands of other people are looking at the same books.
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
from tally_backend.db.models import Organisation
from tally_backend.main import create_app
from tally_backend.services.demo_books import (
    COMPANY_NAME,
    build_books,
    default_books_from,
    financial_year_start,
)

DEMO_EMAIL = "demo@tallyflow.in"
DEMO_PASSWORD = "explore-tallyflow"


@pytest.fixture
def demo_settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "demo_enabled": True,
            "demo_email": DEMO_EMAIL,
            "demo_password": DEMO_PASSWORD,
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


@pytest_asyncio.fixture
async def demo_client(demo_app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=demo_app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/v1/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}
        )
        assert response.status_code == 200, response.text
        client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        yield client


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
# Signing in and reading
# --------------------------------------------------------------------------


async def test_the_demo_signs_in_and_has_a_company(demo_client: AsyncClient):
    me = await demo_client.get("/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["subscription"]["is_demo"] is True
    # Reading is the whole point, so it must be allowed.
    assert me.json()["subscription"]["allows_data"] is True

    companies = await demo_client.get("/v1/companies")
    assert companies.status_code == 200
    assert [c["tally_name"] for c in companies.json()] == [COMPANY_NAME]
    # The financial-year picker is built from this, so it has to arrive.
    assert companies.json()[0]["books_from"] is not None


async def test_the_dashboard_has_figures_without_a_connector(
    demo_client: AsyncClient, fake_connector
):
    companies = (await demo_client.get("/v1/companies")).json()
    company_id = companies[0]["id"]

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
    company_id = (await demo_client.get("/v1/companies")).json()[0]["id"]
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
    company_id = (await demo_client.get("/v1/companies")).json()[0]["id"]
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
# What it refuses
# --------------------------------------------------------------------------


async def test_the_demo_cannot_be_taken_apart(demo_client: AsyncClient):
    """Anyone can sign in, so nobody may change anything."""
    companies = (await demo_client.get("/v1/companies")).json()
    company_id = companies[0]["id"]
    connector_id = companies[0]["connector_id"]

    removed = await demo_client.delete(f"/v1/companies/{company_id}")
    assert removed.status_code == 402, removed.text
    assert "demo" in removed.json()["error"]["message"].lower()

    revoked = await demo_client.delete(f"/v1/connectors/{connector_id}")
    assert revoked.status_code == 402

    invited = await demo_client.post(
        "/v1/team", json={"email": "someone@example.com", "role": "staff"}
    )
    assert invited.status_code == 402

    # Still there afterwards.
    assert len((await demo_client.get("/v1/companies")).json()) == 1


async def test_a_second_start_does_not_duplicate_the_account(demo_settings: Settings):
    """Startup runs the seeder every time; it must be idempotent."""
    from tally_backend.db.session import create_engine, create_session_factory
    from tally_backend.services.demo import ensure_demo_account

    application = create_app(demo_settings)
    async with LifespanRunner(application):
        hub = application.state.hub
        factory = application.state.session_factory
        await ensure_demo_account(factory, demo_settings, hub)
        await ensure_demo_account(factory, demo_settings, hub)

        async with factory() as session:
            orgs = (
                await session.execute(select(Organisation).where(Organisation.is_demo.is_(True)))
            ).scalars().all()
            assert len(orgs) == 1

    # Silence the unused-import warning while keeping the names available for
    # anyone extending this test.
    assert create_engine and create_session_factory


async def test_the_app_is_told_a_demo_exists_and_can_enter_it(demo_app):
    """The app must not carry the demo password; the server hands out the session."""
    async with AsyncClient(
        transport=ASGITransport(app=demo_app), base_url="http://test"
    ) as client:
        config = await client.get("/v1/public/config")
        assert config.status_code == 200
        assert config.json()["demo_available"] is True

        entered = await client.post("/v1/auth/demo")
        assert entered.status_code == 200, entered.text

        client.headers["Authorization"] = f"Bearer {entered.json()['access_token']}"
        me = await client.get("/v1/auth/me")
        assert me.json()["subscription"]["is_demo"] is True


async def test_a_server_without_a_demo_offers_no_door_into_one(client):
    """The ordinary fixture has no demo configured, which is most deployments."""
    assert (await client.get("/v1/public/config")).json()["demo_available"] is False
    assert (await client.post("/v1/auth/demo")).status_code == 404
