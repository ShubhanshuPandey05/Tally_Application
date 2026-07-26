"""Test fixtures.

The whole stack is exercised through the real ASGI app against a real (SQLite)
database, with only the connector faked. That boundary is deliberate: the
connector is the one thing a test genuinely cannot have, and mocking anything
closer to the app -- the session, the hub, the read service -- would stop the
tests from proving that authorisation and freshness actually work end to end.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from tally_core.protocol import JobResult

from tally_backend.config import Settings
from tally_backend.db.models import Company, Connector, ConnectorStatus
from tally_backend.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        environment="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        jwt_secret="test-secret-that-is-long-enough-to-be-valid",
        secret_keys=["unit-test-encryption-key"],
        # Off by default: a background task that fires mid-test makes failures
        # depend on wall-clock timing. Tests that want it drive `sweep()` directly.
        refresh_worker_enabled=False,
        rate_limit_per_minute=10_000,
        auth_rate_limit_per_minute=10_000,
        snapshot_stale_after_seconds=900,
        min_refresh_interval_seconds=0,
    )


class FakeConnector:
    """Stands in for a paired connector.

    Answers queries from a canned response table and records what it was asked,
    so tests can assert on *how many* times Tally was hit -- which is the whole
    point of the snapshot design and is invisible from the response body alone.
    """

    def __init__(self) -> None:
        self.responses: dict[str, Any] = {}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.online = True
        self.fail_with: str | None = None

    def set(self, query: str, payload: Any) -> None:
        self.responses[query] = payload

    async def run(
        self,
        *,
        connector_id: str,
        query: str,
        params: dict[str, Any],
        timeout_seconds: float | None = None,
        cache_ttl_seconds: float = 0.0,
        coalesce: bool = True,
    ) -> JobResult:
        self.calls.append((query, params))

        if not self.online:
            return JobResult.failure(
                "job",
                _error("connector_offline", "Your Tally PC is offline."),
            )
        if self.fail_with:
            return JobResult.failure("job", _error(self.fail_with, "Something failed."))
        if query not in self.responses:
            return JobResult.failure(
                "job", _error("unknown_query", f"no canned response for {query}")
            )

        return JobResult.success("job", self.responses[query], duration_ms=5)

    async def is_online(self, connector_id: str) -> bool:
        """Mirrors the hub's reachability check.

        Faked alongside ``run`` because callers legitimately consult both -- the
        background refresher skips unreachable connectors *before* dispatching,
        so a fake that only answers ``run`` makes every sweep look like a no-op.
        """
        return self.online

    def call_count(self, query: str) -> int:
        return sum(1 for name, _ in self.calls if name == query)


def _error(code: str, message: str):
    from tally_core.protocol import JobError

    return JobError(code=code, message=message, user_message=message, retryable=True)


@pytest_asyncio.fixture
async def fake_connector() -> FakeConnector:
    return FakeConnector()


@pytest_asyncio.fixture
async def app(settings: Settings, fake_connector: FakeConnector):
    application = create_app(settings)

    async with LifespanRunner(application):
        # Swap only the outward-facing methods, so coalescing, routing and the
        # hub's own bookkeeping stay under test.
        application.state.hub.run = fake_connector.run  # type: ignore[method-assign]
        application.state.hub.is_online = fake_connector.is_online  # type: ignore[method-assign]
        yield application


class LifespanRunner:
    """Runs the app's lifespan without spinning up a server."""

    def __init__(self, app) -> None:  # noqa: ANN001
        self._app = app
        self._cm = None

    async def __aenter__(self):
        from contextlib import AsyncExitStack

        self._stack = AsyncExitStack()
        router = self._app.router
        self._cm = router.lifespan_context(self._app)
        await self._stack.enter_async_context(self._cm)
        return self._app

    async def __aexit__(self, *exc_info) -> None:
        await self._stack.aclose()


@pytest_asyncio.fixture
async def client(app) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        yield http_client


@pytest_asyncio.fixture
async def registered(client: AsyncClient) -> dict[str, Any]:
    """A registered owner with an auth header ready to use."""
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": "owner@bhatiastores.in",
            "password": "a-sufficiently-long-password",
            "full_name": "Shop Owner",
            "org_name": "Bhatia Supermarket",
        },
    )
    assert response.status_code == 201, response.text
    tokens = response.json()
    return {
        "tokens": tokens,
        "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
    }


@pytest_asyncio.fixture
async def linked_company(app, registered, client: AsyncClient) -> dict[str, Any]:
    """An org with a paired connector and one linked company."""
    headers = registered["headers"]

    created = await client.post(
        "/v1/connectors", json={"name": "Shop PC"}, headers=headers
    )
    assert created.status_code == 201, created.text
    connector_id = created.json()["connector_id"]

    # Linked directly rather than through discovery: discovery needs a live
    # socket, and this fixture is about the data path.
    async with app.state.session_factory() as session:
        connector = await session.get(Connector, connector_id)
        connector.status = ConnectorStatus.ACTIVE
        company = Company(
            org_id=connector.org_id,
            connector_id=connector_id,
            tally_name="Bhtia Supermarket",
        )
        session.add(company)
        await session.commit()
        company_id = company.id

    return {"headers": headers, "connector_id": connector_id, "company_id": company_id}


# --------------------------------------------------------------------------
# Sample data shaped like real connector responses
# --------------------------------------------------------------------------


def money(amount: str, side: str = "debit") -> dict[str, Any]:
    return {"amount": amount, "side": side, "currency": "INR"}


def sample_ledgers() -> list[dict[str, Any]]:
    """Balances on their real sides, matching the live-verified convention."""
    return [
        {
            "name": "Cash",
            "parent_group": "Cash-in-Hand",
            "closing_balance": money("344220.00", "debit"),
            "opening_balance": money("0.00", "debit"),
        },
        {
            "name": "HDFC Bank",
            "parent_group": "Bank Accounts",
            "closing_balance": money("125000.00", "debit"),
            "opening_balance": money("0.00", "debit"),
        },
        {
            "name": "SARA DISITIBUTOR",
            "parent_group": "Sundry Creditors",
            "closing_balance": money("212600.00", "credit"),
            "opening_balance": money("0.00", "debit"),
        },
    ]


def sample_vouchers(today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    return [
        {
            "voucher_number": "1",
            "voucher_type": "Sales",
            "kind": "sales",
            "date": today.isoformat(),
            "party_name": "Reliance Retail",
            "amount": money("11800.00", "credit"),
            "ledger_entries": [
                {"ledger_name": "Reliance Retail", "amount": money("11800.00", "debit")},
                {"ledger_name": "Sales", "amount": money("10000.00", "credit")},
                {"ledger_name": "Output GST", "amount": money("1800.00", "credit")},
            ],
            "inventory_entries": [
                {
                    "item_name": "Rice",
                    "quantity": 100.0,
                    "unit": "KG",
                    "amount": money("10000.00", "credit"),
                }
            ],
        },
        {
            "voucher_number": "2",
            "voucher_type": "Sales",
            "kind": "sales",
            "date": yesterday.isoformat(),
            "party_name": "Local Kirana",
            "amount": money("5000.00", "credit"),
            "ledger_entries": [
                {"ledger_name": "Local Kirana", "amount": money("5000.00", "debit")},
                {"ledger_name": "Sales", "amount": money("5000.00", "credit")},
            ],
            "inventory_entries": [],
        },
        {
            "voucher_number": "3",
            "voucher_type": "Purchase",
            "kind": "purchase",
            "date": today.isoformat(),
            "party_name": "SARA DISITIBUTOR",
            "amount": money("212600.00", "debit"),
            "ledger_entries": [
                {"ledger_name": "Purchases", "amount": money("212600.00", "debit")},
                {"ledger_name": "SARA DISITIBUTOR", "amount": money("212600.00", "credit")},
            ],
            "inventory_entries": [],
        },
        {
            # Cancelled: must never reach a total.
            "voucher_number": "4",
            "voucher_type": "Sales",
            "kind": "sales",
            "date": today.isoformat(),
            "party_name": "Ghost Buyer",
            "is_cancelled": True,
            "amount": money("999999.00", "credit"),
            "ledger_entries": [
                {"ledger_name": "Ghost Buyer", "amount": money("999999.00", "debit")},
                {"ledger_name": "Sales", "amount": money("999999.00", "credit")},
            ],
            "inventory_entries": [],
        },
    ]


def sample_bills(today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    return [
        {
            "party_name": "SARA DISITIBUTOR",
            "bill_name": "2",
            "kind": "payable",
            "bill_date": (today - timedelta(days=113)).isoformat(),
            "due_date": (today - timedelta(days=113)).isoformat(),
            "pending_amount": money("212600.00", "credit"),
            "opening_amount": money("212600.00", "credit"),
            "credit_period_days": 0,
            "is_advance": False,
        },
        {
            "party_name": "Reliance Retail",
            "bill_name": "INV-9",
            "kind": "receivable",
            "bill_date": (today - timedelta(days=10)).isoformat(),
            "due_date": (today + timedelta(days=20)).isoformat(),
            "pending_amount": money("11800.00", "debit"),
            "opening_amount": money("11800.00", "debit"),
            "credit_period_days": 30,
            "is_advance": False,
        },
    ]


def sample_voucher_types() -> list[dict[str, Any]]:
    """A company's voucher-type masters, including a renamed sales type.

    "Tax Invoice" is what a real shop's sales voucher type is usually called.
    Its *parent* is what makes it a sale; the name alone says nothing.
    """
    return [
        {"name": "Tax Invoice", "parent": "Sales", "kind": "sales"},
        {"name": "Sales", "parent": "Sales", "kind": "sales"},
        {"name": "Purchase", "parent": "Purchase", "kind": "purchase"},
        {"name": "Receipt", "parent": "Receipt", "kind": "receipt"},
        {"name": "Payment", "parent": "Payment", "kind": "payment"},
    ]


def sample_stock() -> list[dict[str, Any]]:
    return [
        {
            "name": "Rice",
            "parent_group": "Grains",
            "base_unit": "KG",
            "closing_quantity": 500.0,
            "closing_value": money("40000.00", "debit"),
            "closing_rate": money("80.00", "debit"),
            "reorder_level": 100.0,
        },
        {
            "name": "Sugar",
            "parent_group": "Grocery",
            "base_unit": "KG",
            "closing_quantity": 5.0,
            "closing_value": money("250.00", "debit"),
            "reorder_level": 50.0,
        },
        {
            "name": "Oil",
            "parent_group": "Grocery",
            "base_unit": "LTR",
            "closing_quantity": -3.0,
            "closing_value": money("450.00", "credit"),
            "reorder_level": 10.0,
        },
    ]


def load_all(fake: FakeConnector, today: date | None = None) -> None:
    fake.set("ledgers.list", sample_ledgers())
    fake.set("voucher_types.list", sample_voucher_types())
    fake.set("vouchers.list", sample_vouchers(today))
    fake.set("outstanding.bills", sample_bills(today))
    fake.set("stock_items.list", sample_stock())
    fake.set("companies.list", [{"name": "Bhtia Supermarket", "guid": "abc"}])


def pretty(data: Any) -> str:
    return json.dumps(data, indent=2, default=str)


class Samples:
    """Sample connector payloads, exposed as a fixture.

    A fixture rather than importable helpers on purpose: two test suites in this
    repo each have a ``conftest``, and a bare ``from conftest import ...`` picks
    whichever landed on ``sys.path`` first.
    """

    ledgers = staticmethod(sample_ledgers)
    vouchers = staticmethod(sample_vouchers)
    voucher_types = staticmethod(sample_voucher_types)
    bills = staticmethod(sample_bills)
    stock = staticmethod(sample_stock)

    @staticmethod
    def load(fake: FakeConnector, today: date | None = None) -> None:
        load_all(fake, today)


@pytest.fixture
def samples() -> Samples:
    return Samples()


@pytest.fixture
def loaded(fake_connector: FakeConnector) -> FakeConnector:
    """A connector primed with a full set of realistic responses."""
    load_all(fake_connector)
    return fake_connector
