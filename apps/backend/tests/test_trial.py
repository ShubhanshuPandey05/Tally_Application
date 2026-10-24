"""The free trial, the phone number that unlocks it, and partner codes.

What these pin, in the order a customer meets them:

1. **Email plus phone is a live account.** A signup with a phone number can
   pair a PC and link a company straight away, on the trial's limits.
2. **The clock starts at the first company of their own.** Not at signup --
   that week would be spent looking at the lent demo -- and never again after.
3. **A trial is given once.** Signing in again, re-linking, or changing the
   number does not hand out another week.
4. **A partner code is set once and checked, never guessed.** A wrong code is
   refused rather than dropped, and a code cannot be changed after it is set.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient

from tally_backend.core.security import hash_password
from tally_backend.db.models import (
    Connector,
    ConnectorStatus,
    Organisation,
    OrgStatus,
    PlatformRole,
    PlatformUser,
    User,
    as_utc,
    utc_now,
)
from tally_backend.services.trial import normalise_phone

PASSWORD = "a-sufficiently-long-password"


async def sign_up(client: AsyncClient, email: str = "owner@shop.in", **fields) -> dict:
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "full_name": "Shop Owner",
            "org_name": "Gupta Electricals",
            **fields,
        },
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    me = await client.get("/v1/auth/me", headers=headers)
    assert me.status_code == 200, me.text
    return {"headers": headers, "me": me.json()}


async def seed_partner(
    app, *, email: str = "ravi@partner.in", code: str | None = "RAVI-01", active: bool = True
) -> str:
    async with app.state.session_factory() as session:
        partner = PlatformUser(
            email=email,
            password_hash=hash_password("partner-password-here"),
            full_name="Ravi Computers",
            role=PlatformRole.PARTNER,
            partner_code=code,
            is_active=active,
        )
        session.add(partner)
        await session.commit()
        return partner.id


async def link_first_company(app, client: AsyncClient, headers: dict, name: str = "Gupta") -> dict:
    created = await client.post("/v1/connectors", json={"name": "Shop PC"}, headers=headers)
    assert created.status_code == 201, created.text
    connector_id = created.json()["connector_id"]
    async with app.state.session_factory() as session:
        connector = await session.get(Connector, connector_id)
        connector.status = ConnectorStatus.ACTIVE
        await session.commit()
    return await client.post(
        f"/v1/connectors/{connector_id}/companies",
        json={"tally_name": name},
        headers=headers,
    )


async def org_row(app, org_id: str) -> Organisation:
    async with app.state.session_factory() as session:
        return await session.get(Organisation, org_id)


# --------------------------------------------------------------------------
# Phone numbers
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("9876543210", "+919876543210"),
        ("98765 43210", "+919876543210"),
        ("098765-43210", "+919876543210"),
        ("+91 98765 43210", "+919876543210"),
        ("919876543210", "+919876543210"),
        ("0091 9876543210", "+919876543210"),
        ("+44 7700 900123", "+447700900123"),
    ],
)
def test_one_number_typed_many_ways_is_one_number(typed: str, stored: str) -> None:
    # Stored as typed, the uniqueness check would let one phone onto three
    # accounts by spacing it three ways.
    assert normalise_phone(typed) == stored


@pytest.mark.parametrize("typed", ["12345", "1234567890", "+91 12345 67890", "phone", ""])
def test_an_unreadable_number_is_refused(typed: str) -> None:
    assert normalise_phone(typed) is None


async def test_a_bad_number_refuses_the_signup_with_a_readable_reason(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": "owner@shop.in",
            "password": PASSWORD,
            "org_name": "Gupta Electricals",
            "phone": "12345",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["detail"]["field"] == "phone"
    assert "mobile number" in response.json()["error"]["message"]


async def test_a_number_is_on_one_account_only(client: AsyncClient) -> None:
    await sign_up(client, phone="9876543210")
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": "second@shop.in",
            "password": PASSWORD,
            "org_name": "Second Shop",
            # The same number, spaced differently.
            "phone": "+91 98765 43210",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["detail"]["field"] == "phone"


# --------------------------------------------------------------------------
# The trial
# --------------------------------------------------------------------------


async def test_email_and_phone_is_a_live_trial_with_no_clock_running(
    app, client: AsyncClient, settings
) -> None:
    account = await sign_up(client, phone="9876543210")
    me = account["me"]
    assert me["phone"] == "+919876543210"
    assert me["needs_phone"] is False

    plan = me["subscription"]
    assert plan["status"] == "active"
    assert plan["allows_changes"] is True
    assert plan["is_trial"] is True
    assert plan["max_companies"] == settings.trial_max_companies
    assert plan["max_users"] == settings.trial_max_users
    # Not counting yet: the account holds only the lent demo.
    assert plan["expires_at"] is None
    assert plan["trial_started_at"] is None
    assert plan["trial_days"] == 7


async def test_without_a_phone_a_signup_still_waits_for_approval(client: AsyncClient) -> None:
    """Builds from before the field existed still sign up, as they always did."""
    me = (await sign_up(client))["me"]
    assert me["needs_phone"] is True
    assert me["subscription"]["status"] == "pending"
    assert me["subscription"]["allows_changes"] is False
    assert me["subscription"]["is_trial"] is False


async def test_the_clock_starts_at_the_first_company_of_their_own(
    app, client: AsyncClient
) -> None:
    account = await sign_up(client, phone="9876543210")
    before = utc_now()

    linked = await link_first_company(app, client, account["headers"])
    assert linked.status_code == 201, linked.text

    me = (await client.get("/v1/auth/me", headers=account["headers"])).json()
    plan = me["subscription"]
    assert plan["is_trial"] is True
    org = await org_row(app, me["org_id"])
    started = as_utc(org.trial_started_at)
    assert started >= before
    assert as_utc(org.expires_at) == started + timedelta(days=7)


async def test_the_clock_never_restarts(app, client: AsyncClient) -> None:
    """Unlink and re-link must not buy another week."""
    account = await sign_up(client, phone="9876543210")
    linked = await link_first_company(app, client, account["headers"])
    company_id = linked.json()["id"]
    org_id = account["me"]["org_id"]
    first = await org_row(app, org_id)

    removed = await client.delete(f"/v1/companies/{company_id}", headers=account["headers"])
    assert removed.status_code in (200, 204), removed.text
    relinked = await link_first_company(app, client, account["headers"])
    assert relinked.status_code == 201, relinked.text

    again = await org_row(app, org_id)
    assert as_utc(again.trial_started_at) == as_utc(first.trial_started_at)
    assert as_utc(again.expires_at) == as_utc(first.expires_at)


async def test_an_ended_trial_locks_the_account_and_says_why(app, client: AsyncClient) -> None:
    account = await sign_up(client, phone="9876543210")
    await link_first_company(app, client, account["headers"])
    async with app.state.session_factory() as session:
        org = await session.get(Organisation, account["me"]["org_id"])
        org.expires_at = utc_now() - timedelta(minutes=1)
        await session.commit()

    plan = (await client.get("/v1/auth/me", headers=account["headers"])).json()["subscription"]
    assert plan["is_expired"] is True
    assert plan["allows_changes"] is False
    assert "free trial has ended" in plan["message"]


async def test_the_trial_covers_one_company(app, client: AsyncClient) -> None:
    account = await sign_up(client, phone="9876543210")
    assert (await link_first_company(app, client, account["headers"], "One")).status_code == 201
    second = await link_first_company(app, client, account["headers"], "Two")
    assert second.status_code == 409


async def test_an_older_account_gets_its_trial_when_it_adds_a_phone(
    app, client: AsyncClient
) -> None:
    account = await sign_up(client)
    assert account["me"]["subscription"]["status"] == "pending"

    added = await client.post(
        "/v1/auth/me/phone", json={"phone": "98765 43210"}, headers=account["headers"]
    )
    assert added.status_code == 200, added.text
    body = added.json()
    assert body["phone"] == "+919876543210"
    assert body["needs_phone"] is False
    assert body["subscription"]["is_trial"] is True
    assert body["subscription"]["allows_changes"] is True


async def test_a_pending_account_with_a_phone_gets_its_trial_at_sign_in(
    app, client: AsyncClient
) -> None:
    """The account that signed up with a phone while trials were switched off."""
    account = await sign_up(client)
    async with app.state.session_factory() as session:
        user = await session.get(User, account["me"]["id"])
        user.phone = "+919876543210"
        await session.commit()

    signed_in = await client.post(
        "/v1/auth/login", json={"email": "owner@shop.in", "password": PASSWORD}
    )
    assert signed_in.status_code == 200
    headers = {"Authorization": f"Bearer {signed_in.json()['access_token']}"}
    plan = (await client.get("/v1/auth/me", headers=headers)).json()["subscription"]
    assert plan["is_trial"] is True
    assert plan["status"] == "active"


@pytest.mark.parametrize("decided", [OrgStatus.REJECTED, OrgStatus.SUSPENDED])
async def test_a_decided_account_never_gets_a_trial(
    app, client: AsyncClient, decided: OrgStatus
) -> None:
    """A rejected signup must not come back to life by adding a number."""
    account = await sign_up(client)
    async with app.state.session_factory() as session:
        org = await session.get(Organisation, account["me"]["org_id"])
        org.status = decided
        await session.commit()

    added = await client.post(
        "/v1/auth/me/phone", json={"phone": "9876543210"}, headers=account["headers"]
    )
    assert added.status_code == 200
    assert added.json()["subscription"]["is_trial"] is False
    assert added.json()["subscription"]["status"] == decided.value


async def test_a_trial_is_given_once(app, client: AsyncClient) -> None:
    account = await sign_up(client, phone="9876543210")
    org_id = account["me"]["org_id"]
    granted = as_utc((await org_row(app, org_id)).trial_granted_at)

    # Pushed back to waiting by hand -- the state an older account is in --
    # and a fresh number added: still no second trial.
    async with app.state.session_factory() as session:
        org = await session.get(Organisation, org_id)
        org.status = OrgStatus.PENDING
        await session.commit()
    await client.post(
        "/v1/auth/me/phone", json={"phone": "9123456780"}, headers=account["headers"]
    )
    org = await org_row(app, org_id)
    assert org.status is OrgStatus.PENDING
    assert as_utc(org.trial_granted_at) == granted


async def test_switched_off_every_signup_waits(app, client: AsyncClient, settings) -> None:
    settings.trial_enabled = False
    me = (await sign_up(client, phone="9876543210"))["me"]
    assert me["subscription"]["status"] == "pending"


# --------------------------------------------------------------------------
# Approval out of a trial
# --------------------------------------------------------------------------


async def test_approving_a_trial_ends_it(app, client: AsyncClient, platform_owner) -> None:
    account = await sign_up(client, phone="9876543210")
    await link_first_company(app, client, account["headers"])
    org_id = account["me"]["org_id"]

    listed = await client.get(
        "/v1/portal/accounts?status=trial", headers=platform_owner["headers"]
    )
    assert [row["id"] for row in listed.json()] == [org_id]
    assert listed.json()[0]["is_trial"] is True
    assert listed.json()[0]["admins"][0]["phone"] == "+919876543210"
    stats = (await client.get("/v1/portal/stats", headers=platform_owner["headers"])).json()
    assert stats["trial"] == 1
    assert stats["active"] == 0

    approved = await client.post(
        f"/v1/portal/accounts/{org_id}/approve",
        json={"max_users": 5, "max_companies": 3},
        headers=platform_owner["headers"],
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["is_trial"] is False
    # The trial's end date does not survive into the plan.
    assert approved.json()["expires_at"] is None

    plan = (await client.get("/v1/auth/me", headers=account["headers"])).json()["subscription"]
    assert plan["is_trial"] is False
    assert plan["max_companies"] == 3


# --------------------------------------------------------------------------
# Partner codes
# --------------------------------------------------------------------------


async def test_the_live_check_knows_a_code_in_any_case(app, client: AsyncClient) -> None:
    await seed_partner(app)
    good = await client.post("/v1/auth/partner-code", json={"code": " ravi-01 "})
    assert good.status_code == 200
    assert good.json() == {"valid": True, "partner_name": "Ravi Computers"}

    bad = await client.post("/v1/auth/partner-code", json={"code": "RAVI-02"})
    assert bad.json() == {"valid": False, "partner_name": None}


async def test_a_deactivated_partners_code_is_not_valid(app, client: AsyncClient) -> None:
    await seed_partner(app, active=False)
    response = await client.post("/v1/auth/partner-code", json={"code": "RAVI-01"})
    assert response.json()["valid"] is False


async def test_a_valid_code_hands_the_account_to_that_partner(
    app, client: AsyncClient
) -> None:
    partner_id = await seed_partner(app)
    me = (await sign_up(client, phone="9876543210", partner_code="ravi-01"))["me"]
    assert (await org_row(app, me["org_id"])).partner_id == partner_id


async def test_an_invalid_code_refuses_the_signup_instead_of_dropping_it(
    app, client: AsyncClient
) -> None:
    await seed_partner(app)
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": "owner@shop.in",
            "password": PASSWORD,
            "org_name": "Gupta Electricals",
            "phone": "9876543210",
            "partner_code": "NOPE-99",
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["detail"]["field"] == "partner_code"

    # Cleared, the same form goes through.
    me = (await sign_up(client, phone="9876543210", partner_code="  "))["me"]
    assert (await org_row(app, me["org_id"])).partner_id is None


async def test_an_owner_sets_a_code_when_creating_a_partner(
    client: AsyncClient, platform_owner
) -> None:
    missing = await client.post(
        "/v1/portal/partners",
        json={"email": "new@partner.in"},
        headers=platform_owner["headers"],
    )
    assert missing.status_code == 400
    assert missing.json()["error"]["detail"]["field"] == "partner_code"

    malformed = await client.post(
        "/v1/portal/partners",
        json={"email": "new@partner.in", "partner_code": "a b"},
        headers=platform_owner["headers"],
    )
    assert malformed.status_code == 400

    created = await client.post(
        "/v1/portal/partners",
        json={"email": "new@partner.in", "partner_code": "new-01"},
        headers=platform_owner["headers"],
    )
    assert created.status_code == 201, created.text
    assert created.json()["partner"]["partner_code"] == "NEW-01"

    duplicate = await client.post(
        "/v1/portal/partners",
        json={"email": "other@partner.in", "partner_code": "NEW-01"},
        headers=platform_owner["headers"],
    )
    assert duplicate.status_code == 409


async def test_a_code_can_never_be_changed(app, client: AsyncClient, platform_owner) -> None:
    partner_id = await seed_partner(app)
    changed = await client.patch(
        f"/v1/portal/partners/{partner_id}",
        json={"partner_code": "RAVI-02"},
        headers=platform_owner["headers"],
    )
    assert changed.status_code == 409
    # Not even to the value it already has: a no-op that answers 200 would look
    # like the portal accepted an edit.
    same = await client.patch(
        f"/v1/portal/partners/{partner_id}",
        json={"partner_code": "RAVI-01"},
        headers=platform_owner["headers"],
    )
    assert same.status_code == 409


async def test_a_partner_from_before_codes_can_be_given_one_once(
    app, client: AsyncClient, platform_owner
) -> None:
    partner_id = await seed_partner(app, code=None)
    given = await client.patch(
        f"/v1/portal/partners/{partner_id}",
        json={"partner_code": "late-01"},
        headers=platform_owner["headers"],
    )
    assert given.status_code == 200, given.text
    assert given.json()["partner_code"] == "LATE-01"

    again = await client.patch(
        f"/v1/portal/partners/{partner_id}",
        json={"partner_code": "LATE-02"},
        headers=platform_owner["headers"],
    )
    assert again.status_code == 409


async def test_a_code_typed_with_the_phone_attaches_a_partner_only_once(
    app, client: AsyncClient
) -> None:
    first = await seed_partner(app)
    await seed_partner(app, email="other@partner.in", code="OTHER-01")
    account = await sign_up(client)

    added = await client.post(
        "/v1/auth/me/phone",
        json={"phone": "9876543210", "partner_code": "RAVI-01"},
        headers=account["headers"],
    )
    assert added.status_code == 200, added.text
    assert (await org_row(app, account["me"]["org_id"])).partner_id == first

    # A business that already has a partner keeps it.
    await client.post(
        "/v1/auth/me/phone",
        json={"phone": "9123456780", "partner_code": "OTHER-01"},
        headers=account["headers"],
    )
    assert (await org_row(app, account["me"]["org_id"])).partner_id == first
