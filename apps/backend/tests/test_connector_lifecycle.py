"""Re-pairing a PC that is already registered.

The secret is shown once and stored only as ciphertext the owner never sees
again, so "I lost it" and "I reinstalled Windows" have no recovery path of
their own. Without one, the only way back is to pair a *second* connector --
and that quietly forks the shop's data, because companies are unique per
``(connector_id, tally_name)``. Re-linking the same books under a new PC
produces a second company row with the same name, its own snapshots, its own
backfill and its own sync state, and two identical entries in the app.

Rotating the secret in place is the fix: same row, same companies, same
history, new credential.
"""

from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select

from tally_backend.db.models import (
    Company,
    Connector,
    ConnectorStatus,
    Membership,
    Role,
)


async def rotate(client: AsyncClient, linked_company) -> tuple[int, dict]:
    response = await client.post(
        f"/v1/connectors/{linked_company['connector_id']}/secret",
        headers=linked_company["headers"],
    )
    return response.status_code, response.json()


# --------------------------------------------------------------------------
# The point of the endpoint
# --------------------------------------------------------------------------


async def test_rotating_keeps_the_same_connector(client: AsyncClient, linked_company) -> None:
    status, body = await rotate(client, linked_company)

    assert status == 200
    assert body["connector_id"] == linked_company["connector_id"], (
        "a new id would make this a new PC, which is exactly what it must avoid"
    )
    assert body["secret"]


async def test_the_new_secret_replaces_the_old_one(
    app, client: AsyncClient, linked_company
) -> None:
    async with app.state.session_factory() as session:
        connector = await session.get(Connector, linked_company["connector_id"])
        before = connector.secret_encrypted

    _, body = await rotate(client, linked_company)

    async with app.state.session_factory() as session:
        connector = await session.get(Connector, linked_company["connector_id"])
        assert connector.secret_encrypted != before
        # What is stored must be what the owner was just shown, or the PC they
        # are about to re-pair will never authenticate.
        assert app.state.secret_box.decrypt(connector.secret_encrypted) == body["secret"]


async def test_two_rotations_never_return_the_same_secret(
    client: AsyncClient, linked_company
) -> None:
    _, first = await rotate(client, linked_company)
    _, second = await rotate(client, linked_company)

    assert first["secret"] != second["secret"]


async def test_the_companies_and_their_history_stay_attached(
    app, client: AsyncClient, linked_company
) -> None:
    """The whole reason this exists instead of "just pair a new PC"."""
    await rotate(client, linked_company)

    async with app.state.session_factory() as session:
        companies = (
            (
                await session.execute(
                    select(Company).where(
                        Company.connector_id == linked_company["connector_id"]
                    )
                )
            )
            .scalars()
            .all()
        )

    assert [c.id for c in companies] == [linked_company["company_id"]]
    assert companies[0].tally_name == "Bhtia Supermarket"
    assert companies[0].is_active is True


async def test_the_app_still_lists_one_pc_and_one_company(
    client: AsyncClient, linked_company
) -> None:
    await rotate(client, linked_company)

    connectors = await client.get("/v1/connectors", headers=linked_company["headers"])
    companies = await client.get("/v1/companies", headers=linked_company["headers"])

    assert len(connectors.json()) == 1, "rotating must not add a second Tally PC"
    assert len(companies.json()) == 1, "nor duplicate the company under a new one"


async def test_the_connector_goes_back_to_pending_until_it_reconnects(
    app, client: AsyncClient, linked_company
) -> None:
    """Claiming "active" would describe a PC that cannot currently authenticate."""
    await rotate(client, linked_company)

    async with app.state.session_factory() as session:
        connector = await session.get(Connector, linked_company["connector_id"])
        assert connector.status is ConnectorStatus.PENDING
        assert connector.last_tally_online is False


# --------------------------------------------------------------------------
# Who may do it, and when
# --------------------------------------------------------------------------


async def test_only_an_admin_may_rotate(app, client: AsyncClient, linked_company) -> None:
    """It hands out a working credential for the shop's whole books.

    Staff are refused even though they can read those same books through the
    app: a credential that pairs a new PC is a different thing from a report,
    and only an admin changes the shape of the account.
    """
    async with app.state.session_factory() as session:
        membership = (await session.execute(select(Membership))).scalars().first()
        membership.role = Role.STAFF
        await session.commit()

    status, body = await rotate(client, linked_company)

    assert status == 403
    assert body["error"]["code"] == "forbidden"


async def test_a_revoked_pc_cannot_be_rotated(
    app, client: AsyncClient, linked_company
) -> None:
    """Revoking is a decision to remove the machine, not to re-issue to it."""
    await client.delete(
        f"/v1/connectors/{linked_company['connector_id']}",
        headers=linked_company["headers"],
    )

    status, body = await rotate(client, linked_company)

    assert status == 409
    assert "removed" in body["error"]["message"].lower()


async def test_rotating_an_unknown_connector_is_a_404(
    client: AsyncClient, linked_company
) -> None:
    response = await client.post(
        "/v1/connectors/00000000000000000000000000000000/secret",
        headers=linked_company["headers"],
    )
    assert response.status_code == 404
