"""What a shop PC is told about the account it serves.

The connector's local page answers two questions -- which companies does this
machine feed, and who can see them -- and neither is answerable on the machine.
So the backend pushes a roster down the socket the connector already holds open.

The line these tests hold is that a roster is a roster and not a data feed. It
carries names, roles and sync times; it must never carry a figure, because a
shop PC that could be asked for a balance over its own loopback socket would be
a read path into the books outside every check the API makes.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from tally_core.protocol import Roster

from tally_backend.db.models import CompanyAccess, Membership, Role, User
from tally_backend.hub.link import ConnectorLink
from tally_backend.services.roster import build_roster

pytestmark = pytest.mark.asyncio


async def roster_for(app, connector_id: str) -> Roster | None:
    async with app.state.session_factory() as session:
        return await build_roster(session, connector_id)


async def test_the_roster_names_the_companies_on_that_pc(app, linked_company) -> None:
    roster = await roster_for(app, linked_company["connector_id"])

    assert roster is not None
    assert [company.tally_name for company in roster.companies] == [
        linked_company["tally_name"]
    ]
    assert roster.organisation
    assert roster.org_status == "active"


async def test_a_roster_carries_no_figures(app, linked_company) -> None:
    """The whole shape of the frame, asserted as a shape.

    Written against the serialised frame rather than the model, because what
    matters is what crosses the socket -- a field added to a nested model would
    reach the shop PC whatever the type annotations said here.
    """
    roster = await roster_for(app, linked_company["connector_id"])
    frame = json.loads(roster.model_dump_json())

    assert set(frame["companies"][0]) == {
        "id",
        "name",
        "tally_name",
        "is_active",
        "last_synced_at",
    }
    assert set(frame["users"][0]) == {"name", "email", "role", "has_access"}


async def test_another_connectors_companies_are_not_in_it(
    app, registered, linked_company, client: AsyncClient
) -> None:
    """A shop with two PCs sees each machine's own books on that machine."""
    second = await client.post(
        "/v1/connectors", json={"name": "Back office"}, headers=registered["headers"]
    )

    roster = await roster_for(app, second.json()["connector_id"])

    assert roster is not None
    assert roster.companies == []


async def test_a_staff_member_without_access_is_listed_as_such(
    app, linked_company
) -> None:
    """"Everyone in the business" and "everyone who can see this" differ.

    A list that ignored the difference would tell an owner their whole team can
    read these books when most of them cannot.
    """
    async with app.state.session_factory() as session:
        colleague = User(email="counter@bhatiastores.in", password_hash="x")
        session.add(colleague)
        await session.flush()
        session.add(
            Membership(
                user_id=colleague.id, org_id=linked_company["org_id"], role=Role.STAFF
            )
        )
        await session.commit()

    roster = await roster_for(app, linked_company["connector_id"])
    entry = next(u for u in roster.users if u.email == "counter@bhatiastores.in")

    assert entry.role == "staff"
    assert entry.has_access is False


async def test_a_staff_member_who_was_granted_the_company_can_see_it(
    app, linked_company
) -> None:
    async with app.state.session_factory() as session:
        colleague = User(email="books@bhatiastores.in", password_hash="x")
        session.add(colleague)
        await session.flush()
        session.add(
            Membership(
                user_id=colleague.id, org_id=linked_company["org_id"], role=Role.STAFF
            )
        )
        session.add(
            CompanyAccess(user_id=colleague.id, company_id=linked_company["company_id"])
        )
        await session.commit()

    roster = await roster_for(app, linked_company["connector_id"])
    entry = next(u for u in roster.users if u.email == "books@bhatiastores.in")

    assert entry.has_access is True


async def test_an_admin_always_has_access(app, linked_company) -> None:
    """Their reach comes from their role, and is never rows in a table."""
    roster = await roster_for(app, linked_company["connector_id"])
    owner = next(u for u in roster.users if u.role == "admin")

    assert owner.has_access is True


async def test_a_connector_that_no_longer_exists_gets_no_roster(app) -> None:
    """Not an empty one: "your books vanished" is the wrong thing to display."""
    assert await roster_for(app, "a-connector-that-was-deleted") is None


# --------------------------------------------------------------------------
# The frame on the wire
# --------------------------------------------------------------------------


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def receive_text(self) -> str:  # pragma: no cover - never called here
        raise AssertionError("unreachable")

    async def close(self, code: int = 1000, reason: str = "") -> None:
        return None


async def test_a_refresh_from_the_shop_pc_is_answered() -> None:
    """The Refresh button on the connector's page, end to end at the link."""
    socket = FakeSocket()
    asked: list[str] = []

    async def on_request(link: ConnectorLink) -> None:
        asked.append(link.connector_id)

    link = ConnectorLink(
        connector_id="conn-1",
        org_id="org-1",
        socket=socket,
        on_roster_request=on_request,
    )

    await link.handle_message({"type": "roster_request", "connector_version": "0.3.0"})

    assert asked == ["conn-1"]


async def test_a_roster_that_cannot_be_built_does_not_drop_the_session() -> None:
    """A diagnostic must never cost the socket a customer's reports come on."""
    socket = FakeSocket()

    async def explode(link: ConnectorLink) -> None:
        raise RuntimeError("database is having a moment")

    link = ConnectorLink(
        connector_id="conn-1", org_id="org-1", socket=socket, on_roster_request=explode
    )

    await link.handle_message({"type": "roster_request"})

    assert not link.is_closed
