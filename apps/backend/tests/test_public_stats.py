"""The one route that answers a stranger.

Two things are being defended here. The first is that the counts are *right*:
a pending signup is not a customer, and a revoked connector is not a PC. The
second, and the reason this endpoint exists at all rather than a hard-coded
number on a web page, is that a failure must not be indistinguishable from a
zero — the figure appears on a public page beside a claim about the product.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from tally_backend.config import Settings
from tally_backend.db.models import Connector, ConnectorStatus, utc_now
from tally_backend.main import create_app

pytestmark = pytest.mark.asyncio


async def test_stats_need_no_token(client: AsyncClient) -> None:
    response = await client.get("/v1/public/stats")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"businesses", "tally_pcs", "companies", "connected_now", "as_of"}


async def test_empty_deployment_reports_zero_rather_than_nothing(client: AsyncClient) -> None:
    """Zero is a real answer and has to be served as one.

    The site prints these figures, and it distinguishes "0" from "unavailable".
    If an empty database produced anything other than a plain zero, the row
    would silently vanish on the day the product had its first honest number to
    show.
    """
    body = (await client.get("/v1/public/stats")).json()

    assert body["businesses"] == 0
    assert body["tally_pcs"] == 0
    assert body["companies"] == 0
    assert body["connected_now"] == 0


async def test_a_signup_nobody_approved_is_not_a_business(
    client: AsyncClient, signed_up: dict
) -> None:
    """An unapproved account is a form submission, not a customer.

    Counting it would let anyone who can fill in the sign-up form move the
    number on the public page.
    """
    body = (await client.get("/v1/public/stats")).json()

    assert body["businesses"] == 0


async def test_approved_business_with_a_pc_and_a_company_is_counted(
    client: AsyncClient, linked_company: dict
) -> None:
    body = (await client.get("/v1/public/stats")).json()

    assert body["businesses"] == 1
    assert body["tally_pcs"] == 1
    assert body["companies"] == 1
    # Paired, but never heard from: connected is about now, not about ever.
    assert body["connected_now"] == 0


async def test_connected_now_follows_last_seen(
    app, client: AsyncClient, linked_company: dict
) -> None:
    connector_id = linked_company["connector_id"]

    async with app.state.session_factory() as session:
        connector = await session.get(Connector, connector_id)
        connector.last_seen_at = utc_now()
        await session.commit()

    # The service caches, so a second call inside the TTL would answer with the
    # figures from before the update. Clearing it is the honest way to test the
    # query rather than the cache; the cache has its own test below.
    app.state.public_stats._cached = None
    assert (await client.get("/v1/public/stats")).json()["connected_now"] == 1

    async with app.state.session_factory() as session:
        connector = await session.get(Connector, connector_id)
        connector.last_seen_at = utc_now() - timedelta(hours=2)
        await session.commit()

    app.state.public_stats._cached = None
    assert (await client.get("/v1/public/stats")).json()["connected_now"] == 0


async def test_revoked_pc_stops_counting(app, client: AsyncClient, linked_company: dict) -> None:
    async with app.state.session_factory() as session:
        connector = await session.get(Connector, linked_company["connector_id"])
        connector.status = ConnectorStatus.REVOKED
        await session.commit()

    app.state.public_stats._cached = None
    assert (await client.get("/v1/public/stats")).json()["tally_pcs"] == 0


async def test_counts_are_reused_within_the_ttl(
    app, client: AsyncClient, linked_company: dict
) -> None:
    """Every visitor to the home page asks for this.

    Without the cache a crawler turns a static page into four aggregate queries
    per hit, against the same database a customer's dashboard reads from.
    """
    first = (await client.get("/v1/public/stats")).json()

    async with app.state.session_factory() as session:
        connector = await session.get(Connector, linked_company["connector_id"])
        connector.status = ConnectorStatus.REVOKED
        await session.commit()

    second = (await client.get("/v1/public/stats")).json()
    assert second["tally_pcs"] == first["tally_pcs"] == 1


async def test_a_failure_is_a_503_and_never_a_zero(app, client: AsyncClient) -> None:
    """The distinction the whole endpoint exists to preserve.

    If the counts cannot be computed the answer must not be a page saying nobody
    uses the product.
    """

    class Broken:
        async def get(self, session):  # noqa: ANN001, ANN202 - stands in for the service
            raise RuntimeError("database is having a day")

    app.state.public_stats = Broken()

    response = await client.get("/v1/public/stats")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "stats_unavailable"
    assert "businesses" not in response.json()


async def test_the_switch_turns_the_endpoint_off(settings: Settings, fake_connector) -> None:
    """These counts are a commercial fact about the business.

    Publishing them is a decision, so it has to be reversible without a code
    change.
    """
    app = create_app(settings.model_copy(update={"public_stats_enabled": False}))

    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (await client.get("/v1/public/stats")).status_code == 404
