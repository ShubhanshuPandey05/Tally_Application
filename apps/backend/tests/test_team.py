"""Team management and per-company access.

Two rules carry the whole feature, and both fail silently if they break:

1. **Staff see only what they were granted.** Not "see everything and get an
   error on tap" -- the company *names* are often the sensitive part, and a shop
   owner adding a cashier assumes the cashier cannot browse the other business.
2. **An organisation can never end up with no admin.** There is no self-service
   way back: nobody could add a PC, link a company, or restore anyone's access.

The rest of the file is about the boundary between the two roles, and about not
trusting ids that arrive in a request body just because an admin sent them.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tally_backend.db.models import Company, Membership, Organisation


async def make_staff(
    client: AsyncClient, headers: dict, *, email: str = "staff@bhatiastores.in", **kw
) -> dict[str, Any]:
    response = await client.post(
        "/v1/team",
        json={"email": email, "full_name": kw.pop("full_name", "Ravi"), **kw},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def sign_in(client: AsyncClient, email: str, password: str) -> dict:
    response = await client.post(
        "/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


# --------------------------------------------------------------------------
# Creating people
# --------------------------------------------------------------------------


async def test_an_admin_adds_a_colleague_without_them_signing_up(
    client: AsyncClient, registered
) -> None:
    """The whole point: two staff do not create their own accounts and PCs."""
    created = await make_staff(client, registered["headers"])

    assert created["member"]["role"] == "staff"
    assert created["member"]["must_change_password"] is True
    # Shown exactly once, like the connector secret.
    assert len(created["temporary_password"]) >= 10


async def test_the_new_colleague_can_sign_in_with_the_temporary_password(
    client: AsyncClient, registered
) -> None:
    created = await make_staff(client, registered["headers"])

    headers = await sign_in(
        client, "staff@bhatiastores.in", created["temporary_password"]
    )
    me = await client.get("/v1/auth/me", headers=headers)

    assert me.status_code == 200
    assert me.json()["role"] == "staff"
    assert me.json()["must_change_password"] is True


async def test_a_temporary_password_is_replaced_without_knowing_the_old_one(
    client: AsyncClient, registered
) -> None:
    """They were handed it by someone else; retyping it proves nothing."""
    created = await make_staff(client, registered["headers"])
    headers = await sign_in(client, "staff@bhatiastores.in", created["temporary_password"])

    changed = await client.post(
        "/v1/team/me/password",
        json={"new_password": "a-much-better-password"},
        headers=headers,
    )
    assert changed.status_code == 204

    fresh = await sign_in(client, "staff@bhatiastores.in", "a-much-better-password")
    me = await client.get("/v1/auth/me", headers=fresh)
    assert me.json()["must_change_password"] is False


async def test_an_established_password_needs_the_current_one_to_change(
    client: AsyncClient, registered
) -> None:
    """Otherwise a borrowed unlocked phone is a permanent account takeover."""
    response = await client.post(
        "/v1/team/me/password",
        json={"new_password": "brand-new-password"},
        headers=registered["headers"],
    )
    assert response.status_code == 403


async def test_staff_cannot_add_people(client: AsyncClient, registered) -> None:
    created = await make_staff(client, registered["headers"])
    headers = await sign_in(client, "staff@bhatiastores.in", created["temporary_password"])

    response = await client.post(
        "/v1/team", json={"email": "another@x.in"}, headers=headers
    )
    assert response.status_code == 403


async def test_staff_cannot_list_the_team(client: AsyncClient, registered) -> None:
    """The list says who the admins are, which is a map of who to target."""
    created = await make_staff(client, registered["headers"])
    headers = await sign_in(client, "staff@bhatiastores.in", created["temporary_password"])

    assert (await client.get("/v1/team", headers=headers)).status_code == 403


async def test_adding_the_same_person_twice_is_refused(
    client: AsyncClient, registered
) -> None:
    await make_staff(client, registered["headers"])
    response = await client.post(
        "/v1/team", json={"email": "staff@bhatiastores.in"}, headers=registered["headers"]
    )
    assert response.status_code == 409


# --------------------------------------------------------------------------
# Per-company access
# --------------------------------------------------------------------------


async def test_staff_see_only_the_companies_they_were_granted(
    app, client: AsyncClient, linked_company
) -> None:
    headers = linked_company["headers"]
    granted = linked_company["company_id"]

    # A second set of books the staff member is deliberately not given.
    async with app.state.session_factory() as session:
        session.add(
            Company(
                org_id=(await session.scalar(select(Membership))).org_id,
                connector_id=linked_company["connector_id"],
                tally_name="Private Family Trust",
            )
        )
        await session.commit()

    created = await make_staff(client, headers, company_ids=[granted])
    staff = await sign_in(client, "staff@bhatiastores.in", created["temporary_password"])

    listed = await client.get("/v1/companies", headers=staff)
    assert listed.status_code == 200
    ids = [row["id"] for row in listed.json()]

    assert ids == [granted]
    # The name of the other company never reaches them at all.
    assert "Private Family Trust" not in listed.text


async def test_an_ungranted_company_is_404_not_403(
    app, client: AsyncClient, linked_company
) -> None:
    """403 would confirm the id exists, which is an enumeration oracle."""
    headers = linked_company["headers"]
    async with app.state.session_factory() as session:
        org_id = (await session.scalar(select(Membership))).org_id
        other = Company(
            org_id=org_id,
            connector_id=linked_company["connector_id"],
            tally_name="Private Family Trust",
        )
        session.add(other)
        await session.commit()
        other_id = other.id

    created = await make_staff(client, headers, company_ids=[])
    staff = await sign_in(client, "staff@bhatiastores.in", created["temporary_password"])

    response = await client.get(f"/v1/companies/{other_id}", headers=staff)
    assert response.status_code == 404


async def test_a_staff_member_with_no_grants_sees_nothing(
    client: AsyncClient, linked_company
) -> None:
    """Deny by default. Forgetting to grant must fail closed, and visibly."""
    created = await make_staff(client, linked_company["headers"], company_ids=[])
    staff = await sign_in(client, "staff@bhatiastores.in", created["temporary_password"])

    listed = await client.get("/v1/companies", headers=staff)
    assert listed.status_code == 200
    assert listed.json() == []


async def test_reports_are_refused_for_an_ungranted_company(
    app, client: AsyncClient, linked_company
) -> None:
    """The list filter is not the security boundary; get_company is."""
    created = await make_staff(client, linked_company["headers"], company_ids=[])
    staff = await sign_in(client, "staff@bhatiastores.in", created["temporary_password"])

    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/dashboard", headers=staff
    )
    assert response.status_code == 404


async def test_an_admin_sees_every_company_without_any_grants(
    client: AsyncClient, linked_company
) -> None:
    """An admin's reach comes from the role, never from rows."""
    listed = await client.get("/v1/companies", headers=linked_company["headers"])
    assert listed.status_code == 200
    assert len(listed.json()) >= 1

    team = await client.get("/v1/team", headers=linked_company["headers"])
    admin = next(m for m in team.json() if m["role"] == "admin")
    # Reported as their real reach so the app never has to infer the rule.
    assert linked_company["company_id"] in admin["company_ids"]


async def test_access_can_be_revoked_by_sending_the_remaining_boxes(
    client: AsyncClient, linked_company
) -> None:
    """The screen sends every ticked box, so an absent id is an un-tick."""
    granted = linked_company["company_id"]
    created = await make_staff(client, linked_company["headers"], company_ids=[granted])
    member_id = created["member"]["id"]
    staff = await sign_in(client, "staff@bhatiastores.in", created["temporary_password"])

    assert len((await client.get("/v1/companies", headers=staff)).json()) == 1

    updated = await client.patch(
        f"/v1/team/{member_id}",
        json={"company_ids": []},
        headers=linked_company["headers"],
    )
    assert updated.status_code == 200
    assert (await client.get("/v1/companies", headers=staff)).json() == []


async def test_a_company_from_another_org_cannot_be_granted(
    app, client: AsyncClient, linked_company, registered
) -> None:
    """A request body is not a trusted source of ids just because an admin sent it."""
    async with app.state.session_factory() as session:
        foreign = Company(
            org_id="some-other-org",
            connector_id=linked_company["connector_id"],
            tally_name="Someone Else's Books",
        )
        session.add(foreign)
        await session.commit()
        foreign_id = foreign.id

    response = await client.post(
        "/v1/team",
        json={"email": "staff@bhatiastores.in", "company_ids": [foreign_id]},
        headers=linked_company["headers"],
    )
    assert response.status_code == 404


async def test_promoting_to_admin_clears_the_old_grants(
    app, client: AsyncClient, linked_company
) -> None:
    """Stale rows would silently return if they were ever demoted again."""
    granted = linked_company["company_id"]
    created = await make_staff(client, linked_company["headers"], company_ids=[granted])
    member_id = created["member"]["id"]

    await client.patch(
        f"/v1/team/{member_id}", json={"role": "admin"}, headers=linked_company["headers"]
    )
    demoted = await client.patch(
        f"/v1/team/{member_id}", json={"role": "staff"}, headers=linked_company["headers"]
    )

    assert demoted.status_code == 200
    assert demoted.json()["company_ids"] == []


# --------------------------------------------------------------------------
# Not locking the organisation out
# --------------------------------------------------------------------------


async def test_the_last_admin_cannot_demote_themselves(
    client: AsyncClient, registered
) -> None:
    me = await client.get("/v1/auth/me", headers=registered["headers"])
    response = await client.patch(
        f"/v1/team/{me.json()['id']}",
        json={"role": "staff"},
        headers=registered["headers"],
    )
    assert response.status_code == 409


async def test_the_last_admin_cannot_be_deactivated(
    client: AsyncClient, registered
) -> None:
    me = await client.get("/v1/auth/me", headers=registered["headers"])
    response = await client.patch(
        f"/v1/team/{me.json()['id']}",
        json={"is_active": False},
        headers=registered["headers"],
    )
    assert response.status_code == 409


async def test_an_admin_may_step_down_once_another_exists(
    client: AsyncClient, registered
) -> None:
    created = await make_staff(client, registered["headers"], role="admin")
    me = await client.get("/v1/auth/me", headers=registered["headers"])

    await client.patch(
        f"/v1/team/{created['member']['id']}",
        json={"role": "admin"},
        headers=registered["headers"],
    )
    response = await client.patch(
        f"/v1/team/{me.json()['id']}", json={"role": "staff"}, headers=registered["headers"]
    )
    assert response.status_code == 200


async def test_nobody_can_remove_themselves(client: AsyncClient, registered) -> None:
    me = await client.get("/v1/auth/me", headers=registered["headers"])
    response = await client.delete(
        f"/v1/team/{me.json()['id']}", headers=registered["headers"]
    )
    assert response.status_code == 409


# --------------------------------------------------------------------------
# Limits, ahead of subscriptions
# --------------------------------------------------------------------------


async def set_user_limit(app, org_id: str, limit: int) -> None:
    """The ceiling now lives on the organisation, set by a portal approval."""
    async with app.state.session_factory() as session:
        org = await session.get(Organisation, org_id)
        org.max_users = limit
        await session.commit()


@pytest.mark.parametrize("limit", [1, 2])
async def test_the_user_limit_is_enforced_where_people_are_created(
    app, client: AsyncClient, registered, limit: int
) -> None:
    """The number comes from the subscription; this is where it bites."""
    await set_user_limit(app, registered["org_id"], limit)

    last = None
    for index in range(limit + 2):
        last = await client.post(
            "/v1/team",
            json={"email": f"person{index}@bhatiastores.in"},
            headers=registered["headers"],
        )
        if last.status_code != 201:
            break

    assert last is not None
    assert last.status_code == 409
    assert "plan covers" in last.json()["error"]["message"]


async def test_removing_someone_frees_their_slot(
    app, client: AsyncClient, registered
) -> None:
    await set_user_limit(app, registered["org_id"], 2)

    created = await make_staff(client, registered["headers"])
    blocked = await client.post(
        "/v1/team", json={"email": "third@bhatiastores.in"}, headers=registered["headers"]
    )
    assert blocked.status_code == 409

    removed = await client.delete(
        f"/v1/team/{created['member']['id']}", headers=registered["headers"]
    )
    assert removed.status_code == 204

    allowed = await client.post(
        "/v1/team", json={"email": "third@bhatiastores.in"}, headers=registered["headers"]
    )
    assert allowed.status_code == 201
