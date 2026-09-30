"""Per-person usage and the portal's view of it.

What is pinned here, in order of how quietly each would fail:

1. **Using the product is counted, per person per day** -- from the audit trail
   as it is written, so it cannot fall behind the trail's two-day pruning.
2. **A partner sees only their own accounts' usage**, exactly as they see only
   their own accounts. The failure looks like a dashboard with slightly bigger
   numbers.
3. **Counts, never contents.** Nothing a customer looked at reaches the portal.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tally_backend.core.security import hash_password
from tally_backend.db.models import (
    PlatformRole,
    PlatformUser,
    RefreshToken,
    User,
    UserActivityDay,
    utc_now,
)
from tally_backend.services import usage
from tally_backend.services.usage import business_day

pytestmark = pytest.mark.asyncio

APP_HEADERS = {"X-App-Version": "0.9.0", "X-App-Platform": "android"}


async def _rows(app) -> list[UserActivityDay]:
    async with app.state.session_factory() as session:
        return list((await session.execute(select(UserActivityDay))).scalars().all())


async def _open_dashboard(client: AsyncClient, company: dict, times: int = 1) -> None:
    for _ in range(times):
        response = await client.get(
            f"/v1/companies/{company['company_id']}/dashboard",
            headers={**company["headers"], **APP_HEADERS},
        )
        assert response.status_code == 200, response.text


async def _partner(app, client: AsyncClient, email: str) -> dict:
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
        "/v1/portal/auth/login", json={"email": email, "password": "partner-password-here"}
    )
    assert response.status_code == 200, response.text
    token = response.json()["access_token"]
    return {"id": user_id, "headers": {"Authorization": f"Bearer {token}"}}


# --------------------------------------------------------------------------
# Counting
# --------------------------------------------------------------------------


async def test_opening_screens_is_counted_once_per_person_per_day(
    app, client: AsyncClient, linked_company, loaded
) -> None:
    await _open_dashboard(client, linked_company, times=3)
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/reports/stock",
        headers={**linked_company["headers"], **APP_HEADERS},
    )
    assert response.status_code == 200, response.text

    rows = await _rows(app)
    # One row for the day, however many screens: a row per request is what the
    # audit trail already is, and it is pruned after two days for that reason.
    assert len(rows) == 1
    row = rows[0]
    assert row.org_id == linked_company["org_id"]
    assert row.dashboard_views == 3
    assert row.report_views == 1
    assert row.events >= 4
    assert row.day == business_day(utc_now())
    # The build, from headers the app already sends -- "who is on the old app?"
    assert (row.app_version, row.platform) == ("0.9.0", "android")


async def test_a_request_without_version_headers_keeps_the_known_build(
    app, client: AsyncClient, linked_company, loaded
) -> None:
    await _open_dashboard(client, linked_company)
    await client.get(
        f"/v1/companies/{linked_company['company_id']}/dashboard",
        headers=linked_company["headers"],
    )

    assert (await _rows(app))[0].app_version == "0.9.0"


async def test_portal_clicks_are_never_credited_to_a_customer(
    app, client: AsyncClient, registered, platform_owner
) -> None:
    """Portal audit rows carry a *portal* user's id -- a different table."""
    before = len(await _rows(app))
    await client.patch(
        f"/v1/portal/accounts/{registered['org_id']}",
        json={"notes": "rang them"},
        headers=platform_owner["headers"],
    )
    assert len(await _rows(app)) == before


async def test_old_days_are_pruned_and_recent_ones_kept(app, registered) -> None:
    today = business_day(utc_now())
    async with app.state.session_factory() as session:
        for age in (0, 10, 400):
            session.add(
                UserActivityDay(
                    user_id="someone",
                    org_id=registered["org_id"],
                    day=today - timedelta(days=age),
                    first_at=utc_now(),
                    last_at=utc_now(),
                    events=1,
                    dashboard_views=0,
                    report_views=0,
                    entries_created=0,
                    logins=0,
                )
            )
        await session.commit()
        await usage.prune(session, keep_days=180)
        await session.commit()

    ages = sorted((today - row.day).days for row in await _rows(app))
    assert 400 not in ages
    assert {0, 10} <= set(ages)


# --------------------------------------------------------------------------
# The portal's view
# --------------------------------------------------------------------------


async def test_the_platform_view_counts_active_people(
    client: AsyncClient, linked_company, loaded, platform_owner
) -> None:
    await _open_dashboard(client, linked_company, times=2)

    response = await client.get("/v1/portal/usage?days=30", headers=platform_owner["headers"])
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["headline"]["active_today"] == 1
    assert body["headline"]["active_30d"] == 1
    assert body["headline"]["people"] >= 1
    # One point per day, gaps filled -- a chart that skips a quiet day draws a
    # line straight across it.
    assert len(body["series"]) == 30
    assert body["series"][-1]["active_users"] == 1
    assert body["series"][-1]["dashboard_views"] == 2
    assert [a["id"] for a in body["top_accounts"]] == [linked_company["org_id"]]
    assert body["versions"] == [{"version": "0.9.0", "platform": "android", "users": 1}]


async def test_an_account_nobody_uses_is_flagged_as_quiet(
    app, client: AsyncClient, registered, platform_owner
) -> None:
    """Approved, paying, and silent: the account to ring before it lapses."""

    async def quiet_ids() -> list[str]:
        body = (await client.get("/v1/portal/usage", headers=platform_owner["headers"])).json()
        return [a["id"] for a in body["quiet_accounts"]]

    # Registering signed the owner in just now, so it is not quiet yet.
    assert registered["org_id"] not in await quiet_ids()

    # Every sign of life -- activity, sign-in, session renewal -- a week and a
    # day ago.
    long_ago = utc_now() - timedelta(days=8)
    async with app.state.session_factory() as session:
        for row in (await session.execute(select(UserActivityDay))).scalars():
            row.last_at = long_ago
        for user in (await session.execute(select(User))).scalars():
            user.last_login_at = long_ago
        for token in (await session.execute(select(RefreshToken))).scalars():
            token.last_used_at = long_ago
        await session.commit()

    assert registered["org_id"] in await quiet_ids()


async def test_the_account_view_goes_person_by_person(
    client: AsyncClient, linked_company, loaded, platform_owner
) -> None:
    await _open_dashboard(client, linked_company, times=2)

    response = await client.get(
        f"/v1/portal/accounts/{linked_company['org_id']}/usage?days=14",
        headers=platform_owner["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["series"]) == 14
    person = body["people"][0]
    assert person["email"] == "owner@bhatiastores.in"
    assert person["active_days"] == 1
    assert person["dashboard_views"] == 2
    assert person["app_version"] == "0.9.0"
    assert len(person["daily"]) == 14 and person["daily"][-1] >= 2
    # Signed in once, so one device -- however many times the token rotated.
    assert len(person["devices"]) == 1
    assert person["last_seen_at"] is not None


async def test_usage_never_carries_a_figure(
    client: AsyncClient, linked_company, loaded, platform_owner
) -> None:
    await _open_dashboard(client, linked_company)
    for path in ("/v1/portal/usage", f"/v1/portal/accounts/{linked_company['org_id']}/usage"):
        text = (await client.get(path, headers=platform_owner["headers"])).text.lower()
        for word in ("amount", "balance", "receivable", "ledger", "bhtia supermarket"):
            assert word not in text, f"{path} mentions {word!r}"


# --------------------------------------------------------------------------
# Scoping
# --------------------------------------------------------------------------


async def test_a_partner_sees_only_their_own_accounts_usage(
    app, client: AsyncClient, linked_company, loaded, platform_owner
) -> None:
    await _open_dashboard(client, linked_company)
    mine = await _partner(app, client, "mine@partner.in")
    theirs = await _partner(app, client, "theirs@partner.in")
    await client.patch(
        f"/v1/portal/accounts/{linked_company['org_id']}",
        json={"partner_id": mine["id"]},
        headers=platform_owner["headers"],
    )

    ours = (await client.get("/v1/portal/usage", headers=mine["headers"])).json()
    others = (await client.get("/v1/portal/usage", headers=theirs["headers"])).json()

    assert ours["headline"]["active_today"] == 1
    assert others["headline"]["active_today"] == 0
    assert others["top_accounts"] == [] and others["versions"] == []

    # And the per-account view is 404, not 403 -- the same enumeration rule as
    # every other account-scoped portal route.
    refused = await client.get(
        f"/v1/portal/accounts/{linked_company['org_id']}/usage", headers=theirs["headers"]
    )
    assert refused.status_code == 404


async def test_a_customer_token_cannot_read_usage(
    client: AsyncClient, linked_company
) -> None:
    response = await client.get("/v1/portal/usage", headers=linked_company["headers"])
    assert response.status_code == 401
