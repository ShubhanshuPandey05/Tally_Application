"""Plans, the sales they record, transfers, and the partner sales report.

What matters here fails quietly if it breaks:

1. **Every activation is a sale.** A plan picked in the portal is written
   down with its price and GST, or the report under-counts a partner's work.
2. **Money is whole paise.** ₹3,000 + 18% is ₹3,540 exactly, every time.
3. **A partner sees only their own.** Their accounts, and their line of the
   report. Signups with no partner code belong to the owner until transferred.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from httpx import AsyncClient

from tally_backend.core.security import hash_password
from tally_backend.db.models import Organisation, PlatformRole, PlatformUser, as_utc, utc_now

PASSWORD = "a-sufficiently-long-password"


async def sign_up(client: AsyncClient, email: str, phone: str, code: str | None = None) -> str:
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": PASSWORD,
            "org_name": f"Shop {email}",
            "phone": phone,
            **({"partner_code": code} if code else {}),
        },
    )
    assert response.status_code == 201, response.text
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    return (await client.get("/v1/auth/me", headers=headers)).json()["org_id"]


async def partner(app, client: AsyncClient, email: str, code: str, name: str) -> dict:
    async with app.state.session_factory() as session:
        user = PlatformUser(
            email=email,
            password_hash=hash_password("partner-password-here"),
            full_name=name,
            role=PlatformRole.PARTNER,
            partner_code=code,
        )
        session.add(user)
        await session.commit()
        user_id = user.id
    signed_in = await client.post(
        "/v1/portal/auth/login", json={"email": email, "password": "partner-password-here"}
    )
    return {
        "id": user_id,
        "headers": {"Authorization": f"Bearer {signed_in.json()['access_token']}"},
    }


async def approve(client: AsyncClient, headers: dict, org_id: str, plan: str):
    return await client.post(
        f"/v1/portal/accounts/{org_id}/approve",
        json={"plan": plan, "max_users": 3, "max_companies": 1},
        headers=headers,
    )


async def test_the_catalogue_is_two_plans_with_gst_in_paise(
    client: AsyncClient, platform_owner
) -> None:
    plans = (await client.get("/v1/portal/plans", headers=platform_owner["headers"])).json()
    assert [(p["code"], p["price_paise"], p["gst_paise"], p["total_paise"]) for p in plans] == [
        ("standard", 300000, 54000, 354000),
        ("premium", 500000, 90000, 590000),
    ]


async def test_activating_needs_a_plan_and_runs_a_year(
    app, client: AsyncClient, platform_owner
) -> None:
    org_id = await sign_up(client, "a@shop.in", "9876543210")
    missing = await client.post(
        f"/v1/portal/accounts/{org_id}/approve",
        json={"max_users": 3, "max_companies": 1},
        headers=platform_owner["headers"],
    )
    assert missing.status_code == 422

    approved = await approve(client, platform_owner["headers"], org_id, "premium")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["plan"] == "premium"
    assert body["plan_name"] == "Premium"
    ends = datetime.fromisoformat(body["expires_at"])
    assert timedelta(days=364) < ends - utc_now() <= timedelta(days=365)


async def test_renewing_early_adds_a_year_to_the_current_end(
    app, client: AsyncClient, platform_owner
) -> None:
    org_id = await sign_up(client, "a@shop.in", "9876543210")
    await approve(client, platform_owner["headers"], org_id, "standard")
    async with app.state.session_factory() as session:
        first_end = as_utc((await session.get(Organisation, org_id)).expires_at)

    renewed = await client.post(
        f"/v1/portal/accounts/{org_id}/renew",
        json={"plan": "premium"},
        headers=platform_owner["headers"],
    )
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["plan"] == "premium"
    async with app.state.session_factory() as session:
        second_end = as_utc((await session.get(Organisation, org_id)).expires_at)
    assert second_end == first_end + timedelta(days=365)


async def test_a_trial_is_approved_not_renewed(app, client: AsyncClient, platform_owner) -> None:
    org_id = await sign_up(client, "a@shop.in", "9876543210")
    refused = await client.post(
        f"/v1/portal/accounts/{org_id}/renew",
        json={"plan": "standard"},
        headers=platform_owner["headers"],
    )
    assert refused.status_code == 409


async def test_a_partner_sees_only_signups_with_their_code(
    app, client: AsyncClient, platform_owner
) -> None:
    ravi = await partner(app, client, "ravi@p.in", "RAVI-01", "Ravi")
    await sign_up(client, "mine@shop.in", "9876543210", code="RAVI-01")
    stray = await sign_up(client, "stray@shop.in", "9876543211")

    listed = (await client.get("/v1/portal/accounts", headers=ravi["headers"])).json()
    assert [row["name"] for row in listed] == ["Shop mine@shop.in"]
    hidden = await client.get(f"/v1/portal/accounts/{stray}", headers=ravi["headers"])
    assert hidden.status_code == 404

    unassigned = (
        await client.get(
            "/v1/portal/accounts?partner=unassigned", headers=platform_owner["headers"]
        )
    ).json()
    assert [row["id"] for row in unassigned] == [stray]
    stats = (await client.get("/v1/portal/stats", headers=platform_owner["headers"])).json()
    assert stats["unassigned"] == 1


async def test_an_owner_transfers_an_unassigned_signup(
    app, client: AsyncClient, platform_owner
) -> None:
    ravi = await partner(app, client, "ravi@p.in", "RAVI-01", "Ravi")
    stray = await sign_up(client, "stray@shop.in", "9876543211")

    refused = await client.post(
        f"/v1/portal/accounts/{stray}/transfer",
        json={"partner_id": ravi["id"]},
        headers=ravi["headers"],
    )
    # A partner cannot even see it, let alone take it.
    assert refused.status_code in (403, 404)

    moved = await client.post(
        f"/v1/portal/accounts/{stray}/transfer",
        json={"partner_id": ravi["id"]},
        headers=platform_owner["headers"],
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["partner_id"] == ravi["id"]
    seen = await client.get(f"/v1/portal/accounts/{stray}", headers=ravi["headers"])
    assert seen.status_code == 200


async def test_the_report_credits_each_partner_by_plan(
    app, client: AsyncClient, platform_owner
) -> None:
    ravi = await partner(app, client, "ravi@p.in", "RAVI-01", "Ravi")
    asha = await partner(app, client, "asha@p.in", "ASHA-01", "Asha")
    one = await sign_up(client, "1@shop.in", "9876543201", code="RAVI-01")
    two = await sign_up(client, "2@shop.in", "9876543202", code="RAVI-01")
    three = await sign_up(client, "3@shop.in", "9876543203", code="ASHA-01")
    direct = await sign_up(client, "4@shop.in", "9876543204")

    await approve(client, ravi["headers"], one, "standard")
    await approve(client, ravi["headers"], two, "premium")
    await approve(client, asha["headers"], three, "premium")
    await approve(client, platform_owner["headers"], direct, "standard")
    await client.post(
        f"/v1/portal/accounts/{one}/renew",
        json={"plan": "standard"},
        headers=ravi["headers"],
    )

    report = (
        await client.get("/v1/portal/reports/sales", headers=platform_owner["headers"])
    ).json()
    rows = {row["partner_name"]: row for row in report["partners"]}
    assert rows["Ravi"]["plans"]["standard"]["count"] == 2
    assert rows["Ravi"]["plans"]["standard"]["renewals"] == 1
    assert rows["Ravi"]["plans"]["premium"]["count"] == 1
    assert rows["Ravi"]["total"]["total_paise"] == 354000 * 2 + 590000
    assert rows["Ravi"]["active_accounts"] == 2
    assert rows["Asha"]["total"]["gst_paise"] == 90000
    assert rows["Direct (no partner)"]["total"]["count"] == 1
    assert report["totals"]["total"]["count"] == 5
    assert report["totals"]["total"]["total_paise"] == 354000 * 3 + 590000 * 2
    # Best seller first.
    assert report["partners"][0]["partner_name"] == "Ravi"
    assert sum(sum(month["plans"].values()) for month in report["months"]) == 5

    # A partner's report is their own line only.
    mine = (await client.get("/v1/portal/reports/sales", headers=asha["headers"])).json()
    assert [row["partner_name"] for row in mine["partners"]] == ["Asha"]
    assert mine["totals"]["total"]["total_paise"] == 590000


async def test_a_transfer_does_not_move_sales_already_made(
    app, client: AsyncClient, platform_owner
) -> None:
    ravi = await partner(app, client, "ravi@p.in", "RAVI-01", "Ravi")
    asha = await partner(app, client, "asha@p.in", "ASHA-01", "Asha")
    org = await sign_up(client, "1@shop.in", "9876543201", code="RAVI-01")
    await approve(client, ravi["headers"], org, "premium")
    await client.post(
        f"/v1/portal/accounts/{org}/transfer",
        json={"partner_id": asha["id"]},
        headers=platform_owner["headers"],
    )
    report = (
        await client.get("/v1/portal/reports/sales", headers=platform_owner["headers"])
    ).json()
    rows = {row["partner_name"]: row for row in report["partners"]}
    assert rows["Ravi"]["total"]["count"] == 1
    assert rows["Asha"]["total"]["count"] == 0
    assert rows["Asha"]["active_accounts"] == 1


async def test_the_app_is_told_its_plan(app, client: AsyncClient, platform_owner) -> None:
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": "a@shop.in",
            "password": PASSWORD,
            "org_name": "Shop",
            "phone": "9876543210",
        },
    )
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    org_id = (await client.get("/v1/auth/me", headers=headers)).json()["org_id"]
    await approve(client, platform_owner["headers"], org_id, "premium")
    plan = (await client.get("/v1/auth/me", headers=headers)).json()["subscription"]
    assert plan["plan_name"] == "Premium"
    assert plan["is_trial"] is False


async def test_an_owner_approving_a_partners_signup_keeps_it_with_them(
    app, client: AsyncClient, platform_owner
) -> None:
    """The sale is credited by the account's partner, so approval must not clear it."""
    ravi = await partner(app, client, "ravi@p.in", "RAVI-01", "Ravi")
    org = await sign_up(client, "1@shop.in", "9876543201", code="RAVI-01")
    approved = await approve(client, platform_owner["headers"], org, "premium")
    assert approved.json()["partner_id"] == ravi["id"]
    report = (
        await client.get("/v1/portal/reports/sales", headers=ravi["headers"])
    ).json()
    assert report["totals"]["total"]["count"] == 1


async def test_reinstating_a_customer_who_already_bought_is_not_a_new_sale(
    app, client: AsyncClient, platform_owner
) -> None:
    from sqlalchemy import select

    from tally_backend.db.models import PlanSale

    org_id = await sign_up(client, "a@shop.in", "9876543210")
    await approve(client, platform_owner["headers"], org_id, "standard")
    suspended = await client.post(
        f"/v1/portal/accounts/{org_id}/suspend",
        json={"reason": "unpaid"},
        headers=platform_owner["headers"],
    )
    assert suspended.status_code == 200, suspended.text
    back = await approve(client, platform_owner["headers"], org_id, "standard")
    assert back.status_code == 200, back.text

    async with app.state.session_factory() as session:
        kinds = (
            await session.execute(
                select(PlanSale.kind).where(PlanSale.org_id == org_id).order_by(PlanSale.created_at)
            )
        ).scalars().all()
    assert kinds == ["new", "renewal"]
