"""Onboarding approval, subscription limits, and who may decide them.

Three things carry the feature, and each of them fails silently rather than
loudly if it breaks:

1. **A signup provisions nothing.** If the gate ever came off, every account
   would work perfectly — which is exactly why nobody would notice for months.
2. **Portal authority is a separate axis.** A customer's token must not reach
   the portal, and a portal token must not reach a customer's books.
3. **A partner sees only their own accounts.** The failure mode is one reseller
   reading another's customer list, and it looks like nothing at all from the
   inside.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient

from tally_backend.core.security import hash_password
from tally_backend.db.models import (
    Organisation,
    OrgStatus,
    PlatformRole,
    PlatformUser,
    utc_now,
)

pytestmark = pytest.mark.asyncio


async def make_partner(app, client: AsyncClient, email: str) -> dict:
    async with app.state.session_factory() as session:
        user = PlatformUser(
            email=email,
            password_hash=hash_password("partner-password-here"),
            full_name="A Partner",
            role=PlatformRole.PARTNER,
        )
        session.add(user)
        await session.commit()
        user_id = user.id

    response = await client.post(
        "/v1/portal/auth/login",
        json={"email": email, "password": "partner-password-here"},
    )
    assert response.status_code == 200, response.text
    return {
        "id": user_id,
        "headers": {"Authorization": f"Bearer {response.json()['access_token']}"},
    }


async def set_status(app, org_id: str, status: OrgStatus, **fields) -> None:
    async with app.state.session_factory() as session:
        org = await session.get(Organisation, org_id)
        org.status = status
        for key, value in fields.items():
            setattr(org, key, value)
        await session.commit()


# --------------------------------------------------------------------------
# A new signup is real, and entitled to nothing
# --------------------------------------------------------------------------


async def test_a_new_signup_can_sign_in(client: AsyncClient, signed_up) -> None:
    """Signing in has to work, or the app has nowhere to explain the wait."""
    response = await client.get("/v1/auth/me", headers=signed_up["headers"])
    assert response.status_code == 200
    body = response.json()
    assert body["subscription"]["status"] == "pending"
    assert body["subscription"]["allows_changes"] is False
    # Reads stay open: there is nothing to read yet, and refusing would turn the
    # first launch after signup into an error screen.
    assert body["subscription"]["allows_data"] is True
    assert "waiting to be approved" in body["subscription"]["message"]


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/v1/connectors", {"name": "Shop PC"}),
        ("POST", "/v1/team", {"email": "staff@bhatiastores.in"}),
    ],
)
async def test_a_pending_account_cannot_grow(
    client: AsyncClient, signed_up, method: str, path: str, body: dict
) -> None:
    """The two things the customer will try first, and the ones that wait."""
    response = await client.request(method, path, json=body, headers=signed_up["headers"])
    assert response.status_code == 402, response.text
    assert response.json()["error"]["code"] == "subscription_inactive"


async def test_a_pending_account_starts_with_no_entitlement(
    app, client: AsyncClient, signed_up
) -> None:
    """Zero, not "unlimited pending a decision".

    A forgotten approval has to fail closed. Defaulting the ceilings to
    something generous would make the approval step optional in practice.
    """
    me = await client.get("/v1/auth/me", headers=signed_up["headers"])
    async with app.state.session_factory() as session:
        org = await session.get(Organisation, me.json()["org_id"])
        assert org.status is OrgStatus.PENDING
        assert org.max_users == 0
        assert org.max_companies == 0


# --------------------------------------------------------------------------
# Approving
# --------------------------------------------------------------------------


async def test_approval_unlocks_the_account(
    client: AsyncClient, signed_up, platform_owner
) -> None:
    me = await client.get("/v1/auth/me", headers=signed_up["headers"])
    org_id = me.json()["org_id"]

    approved = await client.post(
        f"/v1/portal/accounts/{org_id}/approve",
        json={"max_users": 3, "max_companies": 2},
        headers=platform_owner["headers"],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "active"

    created = await client.post(
        "/v1/connectors", json={"name": "Shop PC"}, headers=signed_up["headers"]
    )
    assert created.status_code == 201, created.text

    refreshed = await client.get("/v1/auth/me", headers=signed_up["headers"])
    assert refreshed.json()["subscription"]["max_companies"] == 2


async def test_the_approved_ceiling_is_what_bites(
    client: AsyncClient, signed_up, platform_owner
) -> None:
    """The number set in the portal, not a setting, is the one enforced."""
    me = await client.get("/v1/auth/me", headers=signed_up["headers"])
    await client.post(
        f"/v1/portal/accounts/{me.json()['org_id']}/approve",
        json={"max_users": 2, "max_companies": 5},
        headers=platform_owner["headers"],
    )

    first = await client.post(
        "/v1/team", json={"email": "one@bhatiastores.in"}, headers=signed_up["headers"]
    )
    assert first.status_code == 201
    second = await client.post(
        "/v1/team", json={"email": "two@bhatiastores.in"}, headers=signed_up["headers"]
    )
    assert second.status_code == 409
    assert "plan covers 2 people" in second.json()["error"]["message"]


async def test_approving_twice_is_refused(
    client: AsyncClient, registered, platform_owner
) -> None:
    """Silently re-approving would overwrite who made the original decision."""
    response = await client.post(
        f"/v1/portal/accounts/{registered['org_id']}/approve",
        json={"max_users": 9, "max_companies": 9},
        headers=platform_owner["headers"],
    )
    assert response.status_code == 409


async def test_a_lowered_ceiling_never_deletes_anything(
    app, client: AsyncClient, linked_company, registered, platform_owner
) -> None:
    """The customer keeps what they have; they just cannot add more.

    The alternative -- unlinking a shop's books because somebody mistyped a
    number in the portal -- is not a behaviour worth having.
    """
    lowered = await client.patch(
        f"/v1/portal/accounts/{registered['org_id']}",
        json={"max_companies": 1},
        headers=platform_owner["headers"],
    )
    assert lowered.status_code == 200
    assert lowered.json()["companies_used"] == 1

    still_there = await client.get("/v1/companies", headers=registered["headers"])
    assert len(still_there.json()) == 1

    blocked = await client.post(
        f"/v1/connectors/{linked_company['connector_id']}/companies",
        json={"tally_name": "Second Books"},
        headers=registered["headers"],
    )
    assert blocked.status_code == 409


# --------------------------------------------------------------------------
# Suspension and expiry
# --------------------------------------------------------------------------


async def test_suspension_cuts_the_data_off(
    app, client: AsyncClient, linked_company, registered, platform_owner
) -> None:
    suspended = await client.post(
        f"/v1/portal/accounts/{registered['org_id']}/suspend",
        json={"reason": "unpaid"},
        headers=platform_owner["headers"],
    )
    assert suspended.status_code == 200

    company_id = linked_company["company_id"]
    reports = await client.get(
        f"/v1/companies/{company_id}/dashboard", headers=registered["headers"]
    )
    assert reports.status_code == 402
    # And the names too: being told what exists and refused on every one of them
    # tells a suspended customer more than nothing does.
    listing = await client.get("/v1/companies", headers=registered["headers"])
    assert listing.status_code == 402


async def test_the_suspension_reason_is_kept(
    client: AsyncClient, registered, platform_owner
) -> None:
    """Why an account was suspended in March matters in November."""
    await client.post(
        f"/v1/portal/accounts/{registered['org_id']}/suspend",
        json={"reason": "three months unpaid"},
        headers=platform_owner["headers"],
    )
    detail = await client.get(
        f"/v1/portal/accounts/{registered['org_id']}", headers=platform_owner["headers"]
    )
    assert "three months unpaid" in detail.json()["notes"]


async def test_an_expired_term_behaves_like_a_suspension(
    app, client: AsyncClient, linked_company, registered
) -> None:
    """Computed, not swept.

    A midnight job that stops running would otherwise leave every expired
    account quietly live -- the kind of failure nobody notices.
    """
    await set_status(
        app, registered["org_id"], OrgStatus.ACTIVE, expires_at=utc_now() - timedelta(days=1)
    )
    me = await client.get("/v1/auth/me", headers=registered["headers"])
    assert me.json()["subscription"]["is_expired"] is True
    assert me.json()["subscription"]["allows_changes"] is False

    blocked = await client.post(
        "/v1/connectors", json={"name": "Another PC"}, headers=registered["headers"]
    )
    assert blocked.status_code == 402


async def test_reinstating_restates_the_numbers(
    client: AsyncClient, registered, platform_owner
) -> None:
    """A suspended account must not come back on a plan it outgrew."""
    await client.post(
        f"/v1/portal/accounts/{registered['org_id']}/suspend",
        json={"reason": "unpaid"},
        headers=platform_owner["headers"],
    )
    back = await client.post(
        f"/v1/portal/accounts/{registered['org_id']}/approve",
        json={"max_users": 4, "max_companies": 4},
        headers=platform_owner["headers"],
    )
    assert back.status_code == 200
    assert back.json()["status"] == "active"
    assert back.json()["max_companies"] == 4


# --------------------------------------------------------------------------
# The two authority axes never touch
# --------------------------------------------------------------------------


async def test_a_customer_token_cannot_reach_the_portal(
    client: AsyncClient, registered
) -> None:
    """An admin of their own shop is nobody on the platform."""
    for path in ("/v1/portal/accounts", "/v1/portal/stats", "/v1/portal/me"):
        response = await client.get(path, headers=registered["headers"])
        assert response.status_code == 401, path


async def test_a_portal_token_cannot_read_a_customers_books(
    client: AsyncClient, linked_company, platform_owner
) -> None:
    """The portal shows how much of the product is used, never what is in it."""
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/dashboard",
        headers=platform_owner["headers"],
    )
    assert response.status_code == 401


async def test_the_portal_never_returns_a_financial_figure(
    client: AsyncClient, linked_company, registered, platform_owner
) -> None:
    account = await client.get(
        f"/v1/portal/accounts/{registered['org_id']}", headers=platform_owner["headers"]
    )
    body = account.json()
    # Counts of things, never contents of them. The company's *name* is not here
    # either -- how many books a shop keeps is a subscription question; what they
    # are called is the shop's business.
    assert set(body) >= {"companies_used", "users_used", "connectors"}
    assert "companies" not in body
    assert "balance" not in str(body).lower()


# --------------------------------------------------------------------------
# Partner scoping
# --------------------------------------------------------------------------


async def test_a_partner_sees_only_their_own_accounts(
    app, client: AsyncClient, registered, platform_owner
) -> None:
    mine = await make_partner(app, client, "mine@partner.in")
    theirs = await make_partner(app, client, "theirs@partner.in")

    await client.patch(
        f"/v1/portal/accounts/{registered['org_id']}",
        json={"partner_id": mine["id"]},
        headers=platform_owner["headers"],
    )

    ours = await client.get("/v1/portal/accounts", headers=mine["headers"])
    others = await client.get("/v1/portal/accounts", headers=theirs["headers"])
    assert [row["id"] for row in ours.json()] == [registered["org_id"]]
    assert others.json() == []


async def test_another_partners_account_is_404_not_403(
    app, client: AsyncClient, registered, platform_owner
) -> None:
    """403 would confirm the id belongs to somebody, one guess at a time."""
    mine = await make_partner(app, client, "mine@partner.in")
    theirs = await make_partner(app, client, "theirs@partner.in")
    await client.patch(
        f"/v1/portal/accounts/{registered['org_id']}",
        json={"partner_id": mine["id"]},
        headers=platform_owner["headers"],
    )

    response = await client.get(
        f"/v1/portal/accounts/{registered['org_id']}", headers=theirs["headers"]
    )
    assert response.status_code == 404


async def test_a_partner_cannot_hand_an_account_to_someone_else(
    app, client: AsyncClient, signed_up, platform_owner
) -> None:
    """The field that decides a partner's scope is not settable by that partner.

    Otherwise it is not a scope: a partner could park accounts under a colleague
    and quietly read them back by assigning them again.
    """
    mine = await make_partner(app, client, "mine@partner.in")
    theirs = await make_partner(app, client, "theirs@partner.in")

    me = await client.get("/v1/auth/me", headers=signed_up["headers"])
    org_id = me.json()["org_id"]
    await client.patch(
        f"/v1/portal/accounts/{org_id}",
        json={"partner_id": mine["id"]},
        headers=platform_owner["headers"],
    )

    approved = await client.post(
        f"/v1/portal/accounts/{org_id}/approve",
        json={"max_users": 2, "max_companies": 2, "partner_id": theirs["id"]},
        headers=mine["headers"],
    )
    assert approved.status_code == 200
    assert approved.json()["partner_id"] == mine["id"]


async def test_only_an_owner_can_create_portal_accounts(
    app, client: AsyncClient, platform_owner
) -> None:
    partner = await make_partner(app, client, "mine@partner.in")
    refused = await client.post(
        "/v1/portal/partners",
        json={"email": "new@partner.in"},
        headers=partner["headers"],
    )
    assert refused.status_code == 403

    allowed = await client.post(
        "/v1/portal/partners",
        json={"email": "new@partner.in", "full_name": "New Partner"},
        headers=platform_owner["headers"],
    )
    assert allowed.status_code == 201
    # Shown once, and long enough to be safe read down a phone line.
    assert len(allowed.json()["temporary_password"]) >= 10
    assert allowed.json()["partner"]["must_change_password"] is True


async def test_the_last_owner_cannot_be_removed(
    client: AsyncClient, platform_owner
) -> None:
    """There is no way back: portal accounts are never self-service."""
    response = await client.patch(
        f"/v1/portal/partners/{platform_owner['user']['id']}",
        json={"is_active": False},
        headers=platform_owner["headers"],
    )
    assert response.status_code == 409


async def test_a_disabled_portal_account_stops_immediately(
    app, client: AsyncClient, platform_owner
) -> None:
    """Not "when the token expires" -- the row is re-read on every request."""
    partner = await make_partner(app, client, "mine@partner.in")
    assert (await client.get("/v1/portal/me", headers=partner["headers"])).status_code == 200

    await client.patch(
        f"/v1/portal/partners/{partner['id']}",
        json={"is_active": False},
        headers=platform_owner["headers"],
    )
    assert (await client.get("/v1/portal/me", headers=partner["headers"])).status_code == 401


# --------------------------------------------------------------------------
# The queue itself
# --------------------------------------------------------------------------


async def test_the_pending_queue_is_what_the_portal_opens_on(
    client: AsyncClient, signed_up, platform_owner
) -> None:
    pending = await client.get(
        "/v1/portal/accounts?status=pending", headers=platform_owner["headers"]
    )
    assert [row["name"] for row in pending.json()] == ["Bhatia Supermarket"]

    stats = await client.get("/v1/portal/stats", headers=platform_owner["headers"])
    assert stats.json()["pending"] == 1
    assert stats.json()["active"] == 0


async def test_an_expired_account_is_counted_apart_from_an_active_one(
    app, client: AsyncClient, registered, platform_owner
) -> None:
    """A dashboard that files expiry under "active" hides the problem."""
    await set_status(
        app, registered["org_id"], OrgStatus.ACTIVE, expires_at=utc_now() - timedelta(days=2)
    )
    stats = await client.get("/v1/portal/stats", headers=platform_owner["headers"])
    assert stats.json()["expired"] == 1
    assert stats.json()["active"] == 0


async def test_the_account_row_carries_who_to_ring(
    client: AsyncClient, registered, platform_owner
) -> None:
    """A decision that needs a second request is a decision made without one."""
    accounts = await client.get(
        "/v1/portal/accounts?status=active", headers=platform_owner["headers"]
    )
    admins = accounts.json()[0]["admins"]
    assert [admin["email"] for admin in admins] == ["owner@bhatiastores.in"]


async def test_a_suspended_account_stops_costing_its_tally(
    app, linked_company, registered, platform_owner, client: AsyncClient, loaded
) -> None:
    """The background sweep is the one path to a shop's PC with no request behind it.

    `deps.get_company` refuses a suspended account, so every read is already
    blocked -- but the refresher runs on a timer and would keep exporting from
    that shop's machine every fifteen minutes regardless. On a PC that is
    somebody's till, that is not a small thing.
    """
    refresher = app.state.refresher
    assert await refresher.sweep() > 0, "an active account is swept"

    await client.post(
        f"/v1/portal/accounts/{registered['org_id']}/suspend",
        json={"reason": "unpaid"},
        headers=platform_owner["headers"],
    )
    assert await refresher.sweep() == 0
