"""Tenant isolation and role enforcement.

The highest-consequence failure this product can have is one customer seeing
another's books, so these are written as attacks rather than as feature checks.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from tally_backend.db.models import Membership, Role


async def _register(client: AsyncClient, email: str, org: str) -> dict[str, Any]:
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "a-sufficiently-long-password",
            "org_name": org,
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_another_org_cannot_read_your_company(
    client: AsyncClient, linked_company
) -> None:
    """The core isolation guarantee, stated as an attack."""
    attacker = await _register(client, "attacker@othershop.in", "Other Shop")
    company_id = linked_company["company_id"]

    for path in (
        f"/v1/companies/{company_id}",
        f"/v1/companies/{company_id}/dashboard",
        f"/v1/companies/{company_id}/reports/daybook?from_date=2026-01-01&to_date=2026-01-31",
        f"/v1/companies/{company_id}/reports/outstanding",
        f"/v1/companies/{company_id}/reports/stock",
        f"/v1/companies/{company_id}/reports/ledgers",
    ):
        response = await client.get(path, headers=attacker)
        # 404 rather than 403: a 403 would confirm the id belongs to someone,
        # which turns the endpoint into an id oracle.
        assert response.status_code == 404, f"{path} leaked to another org"


async def test_another_org_cannot_touch_your_connector(
    client: AsyncClient, linked_company
) -> None:
    attacker = await _register(client, "attacker2@othershop.in", "Other Shop 2")
    connector_id = linked_company["connector_id"]

    assert (
        await client.get(f"/v1/connectors/{connector_id}", headers=attacker)
    ).status_code == 404
    assert (
        await client.delete(f"/v1/connectors/{connector_id}", headers=attacker)
    ).status_code == 404
    assert (
        await client.get(f"/v1/connectors/{connector_id}/discover", headers=attacker)
    ).status_code == 404
    # Rotating someone else's secret would be a takeover, not just a leak: the
    # attacker would hold the only working credential for that shop's PC.
    assert (
        await client.post(f"/v1/connectors/{connector_id}/secret", headers=attacker)
    ).status_code == 404


async def test_company_lists_are_scoped_to_the_caller(
    client: AsyncClient, linked_company
) -> None:
    attacker = await _register(client, "attacker3@othershop.in", "Other Shop 3")

    assert (await client.get("/v1/companies", headers=attacker)).json() == []
    assert (await client.get("/v1/connectors", headers=attacker)).json() == []

    mine = await client.get("/v1/companies", headers=linked_company["headers"])
    assert len(mine.json()) == 1


async def test_unauthenticated_requests_are_refused(
    client: AsyncClient, linked_company
) -> None:
    company_id = linked_company["company_id"]
    assert (await client.get(f"/v1/companies/{company_id}/dashboard")).status_code == 401
    assert (await client.get("/v1/companies")).status_code == 401
    assert (await client.post("/v1/connectors", json={"name": "x"})).status_code == 401


async def test_staff_cannot_create_or_revoke_connectors(
    app, client: AsyncClient, registered
) -> None:
    """Roles are re-read from the database, not trusted from the token."""
    async with app.state.session_factory() as session:
        membership = (await session.execute(_all_memberships())).scalars().first()
        membership.role = Role.STAFF
        await session.commit()

    headers = registered["headers"]
    created = await client.post("/v1/connectors", json={"name": "Sneaky"}, headers=headers)
    assert created.status_code == 403
    assert created.json()["error"]["code"] == "forbidden"


async def test_role_downgrade_takes_effect_without_a_new_token(
    app, client: AsyncClient, linked_company
) -> None:
    """A demoted admin must lose access inside the access token's lifetime.

    The token still says "admin" for up to 15 minutes; the database is the
    authority, which is why the role is re-read on every request.
    """
    headers = linked_company["headers"]
    assert (
        await client.post("/v1/connectors", json={"name": "Second PC"}, headers=headers)
    ).status_code == 201

    async with app.state.session_factory() as session:
        membership = (await session.execute(_all_memberships())).scalars().first()
        membership.role = Role.STAFF
        await session.commit()

    # Same token, no re-login.
    assert (
        await client.post("/v1/connectors", json={"name": "Third PC"}, headers=headers)
    ).status_code == 403


async def test_a_deactivated_user_is_locked_out_immediately(
    app, client: AsyncClient, registered
) -> None:

    async with app.state.session_factory() as session:
        user = (await session.execute(_all_users())).scalars().first()
        user.is_active = False
        await session.commit()

    assert (await client.get("/v1/auth/me", headers=registered["headers"])).status_code == 401


def _all_memberships():
    from sqlalchemy import select

    return select(Membership)


def _all_users():
    from sqlalchemy import select

    from tally_backend.db.models import User

    return select(User)
