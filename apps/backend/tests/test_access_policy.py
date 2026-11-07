"""What one staff member may see inside a company they were given.

The feature is only as good as the place it is enforced, so most of these go
through the API as the restricted person and check what comes back. Three rules
carry it:

1. **Everything is visible until an admin switches part off** -- a new grant
   changes nothing a person could already see.
2. **Hidden means hidden everywhere** -- from the ledger report, receivables,
   the day book and the dashboard alike, because the filter sits in the read
   service they all share, not in each handler.
3. **A restriction never lapses by accident** -- not when the grant list is
   saved again, and not when a master the filter needs has not been read.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from test_team import make_staff, sign_in

from tally_backend.db.models import Company, CompanyAccess
from tally_backend.services.access_policy import (
    AccessPolicy,
    AccessScope,
    masters_from_payloads,
)
from tally_backend.services.reads import ReadService


def groups() -> list[dict[str, Any]]:
    """A small tree: Sundry Debtors sits under Current Assets, and so on."""
    return [
        {"name": "Current Assets", "parent": None},
        {"name": "Current Liabilities", "parent": None},
        {"name": "Sundry Debtors", "parent": "Current Assets"},
        {"name": "Cash-in-Hand", "parent": "Current Assets"},
        {"name": "Bank Accounts", "parent": "Current Assets"},
        {"name": "Sundry Creditors", "parent": "Current Liabilities"},
    ]


async def seed_masters(app, company_id: str, samples) -> None:
    """Store the snapshots the filter reads, as a sync would have."""
    async with app.state.session_factory() as session:
        company = await session.get(Company, company_id)
        reads = ReadService(session, app.state.hub, app.state.settings)
        await reads.put_snapshot(company, dataset="groups.list", payload=groups())
        await reads.put_snapshot(company, dataset="ledgers.list", payload=samples.ledgers())
        await reads.put_snapshot(company, dataset="stock_items.list", payload=samples.stock())
        await session.commit()


@pytest.fixture
async def cashier(client: AsyncClient, linked_company) -> dict[str, Any]:
    """A staff member granted the linked company, signed in."""
    created = await make_staff(
        client,
        linked_company["headers"],
        email="cashier@bhatiastores.in",
        company_ids=[linked_company["company_id"]],
    )
    headers = await sign_in(client, "cashier@bhatiastores.in", created["temporary_password"])
    return {"id": created["member"]["id"], "headers": headers}


async def set_policy(client, linked_company, cashier, **policy) -> Any:
    return await client.put(
        f"/v1/team/{cashier['id']}/access/{linked_company['company_id']}",
        json=policy,
        headers=linked_company["headers"],
    )


def path(linked_company, rest: str) -> str:
    return f"/v1/companies/{linked_company['company_id']}{rest}"


# --------------------------------------------------------------------------
# The rules, without HTTP
# --------------------------------------------------------------------------


def scope(samples, **policy) -> AccessScope:
    return AccessScope(
        policy=AccessPolicy(**policy),
        masters=masters_from_payloads(
            groups=groups(), ledgers=samples.ledgers(), stock=samples.stock()
        ),
    )


def test_a_hidden_group_hides_every_ledger_beneath_it_at_any_depth(samples) -> None:
    s = scope(samples, denied_ledger_groups=["Current Assets"])

    assert not s.ledger_visible("Reliance Retail")  # Sundry Debtors < Current Assets
    assert not s.ledger_visible("Cash")
    assert s.ledger_visible("SARA DISITIBUTOR")  # under Current Liabilities


def test_names_compare_the_way_tally_does(samples) -> None:
    s = scope(samples, denied_ledgers=["  reliance retail "])
    assert not s.ledger_visible("Reliance Retail")


def test_an_unplaceable_ledger_is_hidden_while_groups_are_restricted(samples) -> None:
    """Created since the last master read: we cannot say it is outside the hidden group."""
    s = scope(samples, denied_ledger_groups=["Sundry Creditors"])
    assert not s.ledger_visible("A Ledger Made This Morning")


def test_with_no_group_restriction_an_unknown_ledger_is_visible(samples) -> None:
    s = scope(samples, denied_ledgers=["Cash"])
    assert s.ledger_visible("A Ledger Made This Morning")


def test_a_voucher_touching_anything_hidden_is_hidden_whole(samples) -> None:
    sale = samples.vouchers()[0]  # Reliance Retail, Sales, Output GST, Rice
    assert not scope(samples, denied_ledgers=["Output GST"]).voucher_visible(sale)
    assert not scope(samples, denied_stock_items=["Rice"]).voucher_visible(sale)
    assert not scope(samples, denied_stock_groups=["Grains"]).voucher_visible(sale)
    assert scope(samples, denied_stock_groups=["Grocery"]).voucher_visible(sale)


def test_switching_a_section_off_hides_its_reports_whatever_they_say(samples) -> None:
    policy = AccessPolicy(prime_reports=False)
    assert not policy.allows_report("top:customers")
    assert policy.allows_report("daybook")
    assert not AccessPolicy(reports=False).allows_report("daybook")


def test_a_report_key_nobody_knows_is_refused_not_stored() -> None:
    with pytest.raises(ValueError):
        AccessPolicy(denied_reports=["day_book_typo"])


def test_a_new_entry_for_a_party_nobody_has_seen_is_not_refused(samples) -> None:
    """Tally refuses it and offers to add the party; we must not get there first."""
    s = scope(samples, denied_ledger_groups=["Sundry Creditors"])
    assert s.entry_refusal({"party_name": "Brand New Customer"}) is None
    assert s.entry_refusal({"party_name": "SARA DISITIBUTOR"}) is not None


# --------------------------------------------------------------------------
# Through the API, as the restricted person
# --------------------------------------------------------------------------


async def test_a_new_grant_restricts_nothing(
    client: AsyncClient, linked_company, cashier, loaded
) -> None:
    mine = await client.get(path(linked_company, "/access"), headers=cashier["headers"])
    assert mine.status_code == 200
    assert mine.json()["restricted"] is False

    ledgers = await client.get(path(linked_company, "/reports/ledgers"), headers=cashier["headers"])
    assert len(ledgers.json()["data"]["ledgers"]) == len(loaded.responses["ledgers.list"])


async def test_a_switched_off_report_is_refused(
    client: AsyncClient, linked_company, cashier, loaded
) -> None:
    saved = await set_policy(client, linked_company, cashier, denied_reports=["daybook"])
    assert saved.status_code == 200, saved.text

    today = "2026-11-04"
    refused = await client.get(
        path(linked_company, f"/reports/daybook?from_date={today}&to_date={today}"),
        headers=cashier["headers"],
    )
    assert refused.status_code == 403
    assert "admin" in refused.json()["error"]["message"].lower()

    # The admin is never restricted.
    admin = await client.get(
        path(linked_company, f"/reports/daybook?from_date={today}&to_date={today}"),
        headers=linked_company["headers"],
    )
    assert admin.status_code == 200


async def test_a_hidden_group_is_missing_from_every_report_that_names_it(
    app, client: AsyncClient, linked_company, cashier, loaded, samples
) -> None:
    await seed_masters(app, linked_company["company_id"], samples)
    saved = await set_policy(
        client, linked_company, cashier, denied_ledger_groups=["Sundry Creditors"]
    )
    assert saved.status_code == 200, saved.text
    h = cashier["headers"]

    ledgers = await client.get(path(linked_company, "/reports/ledgers"), headers=h)
    names = {row["name"] for row in ledgers.json()["data"]["ledgers"]}
    assert "SARA DISITIBUTOR" not in names
    assert "Reliance Retail" in names

    payables = await client.get(
        path(linked_company, "/reports/outstanding?kind=payable"), headers=h
    )
    assert payables.json()["data"]["bills"] == []

    statement = await client.get(
        path(linked_company, "/reports/ledger-statement?ledger=SARA%20DISITIBUTOR"), headers=h
    )
    assert statement.status_code == 404

    dashboard = await client.get(path(linked_company, "/dashboard"), headers=h)
    assert "SARA DISITIBUTOR" not in dashboard.text


async def test_hidden_stock_is_gone_and_the_summary_counts_only_what_is_left(
    app, client: AsyncClient, linked_company, cashier, loaded, samples
) -> None:
    await seed_masters(app, linked_company["company_id"], samples)
    await set_policy(client, linked_company, cashier, denied_stock_groups=["Grocery"])

    stock = await client.get(path(linked_company, "/reports/stock"), headers=cashier["headers"])
    items = [row["name"] for row in stock.json()["data"]["items"]]
    assert items == ["Rice"]

    movement = await client.get(
        path(linked_company, "/reports/stock/movement?item=Oil"), headers=cashier["headers"]
    )
    assert movement.status_code == 404


async def test_saving_the_company_list_again_keeps_the_restrictions(
    client: AsyncClient, linked_company, cashier, loaded
) -> None:
    """Renaming a colleague re-sends their grants; that must not lift anything."""
    await set_policy(client, linked_company, cashier, denied_reports=["daybook"])

    renamed = await client.patch(
        f"/v1/team/{cashier['id']}",
        json={"full_name": "Ravi K", "company_ids": [linked_company["company_id"]]},
        headers=linked_company["headers"],
    )
    assert renamed.status_code == 200
    assert renamed.json()["restricted_company_ids"] == [linked_company["company_id"]]

    mine = await client.get(path(linked_company, "/access"), headers=cashier["headers"])
    assert mine.json()["policy"]["denied_reports"] == ["daybook"]


async def test_the_policy_is_sealed_at_rest(
    app, client: AsyncClient, linked_company, cashier, loaded
) -> None:
    await set_policy(client, linked_company, cashier, denied_ledgers=["Reliance Retail"])

    async with app.state.session_factory() as session:
        stored = await session.scalar(
            select(CompanyAccess.restrictions).where(CompanyAccess.user_id == cashier["id"])
        )
    assert "Reliance" not in stored


async def test_sending_the_default_clears_the_policy(
    app, client: AsyncClient, linked_company, cashier, loaded
) -> None:
    await set_policy(client, linked_company, cashier, denied_reports=["daybook"])
    await set_policy(client, linked_company, cashier)

    async with app.state.session_factory() as session:
        stored = await session.scalar(
            select(CompanyAccess.restrictions).where(CompanyAccess.user_id == cashier["id"])
        )
    assert stored is None


async def test_only_an_admin_sets_a_policy(
    client: AsyncClient, linked_company, cashier, loaded
) -> None:
    response = await client.put(
        f"/v1/team/{cashier['id']}/access/{linked_company['company_id']}",
        json={"reports": False},
        headers=cashier["headers"],
    )
    assert response.status_code == 403

    catalog = await client.get(path(linked_company, "/access/catalog"), headers=cashier["headers"])
    assert catalog.status_code == 403


async def test_a_policy_needs_the_company_granted_first(
    client: AsyncClient, linked_company, loaded
) -> None:
    created = await make_staff(client, linked_company["headers"], email="new@bhatiastores.in")
    response = await client.put(
        f"/v1/team/{created['member']['id']}/access/{linked_company['company_id']}",
        json={"reports": False},
        headers=linked_company["headers"],
    )
    assert response.status_code == 409


async def test_the_catalog_lists_names_and_never_a_figure(
    app, client: AsyncClient, linked_company, loaded, samples
) -> None:
    await seed_masters(app, linked_company["company_id"], samples)
    catalog = await client.get(
        path(linked_company, "/access/catalog"), headers=linked_company["headers"]
    )
    assert catalog.status_code == 200
    body = catalog.json()
    assert body["masters_read"] is True
    assert "daybook" in body["standard_reports"]
    assert "top:customers" in body["prime_reports"]
    assert {g["name"] for g in body["stock_groups"]} == {"Grains", "Grocery"}
    assert "344220" not in catalog.text


async def test_an_entry_against_a_hidden_ledger_is_refused_before_tally(
    client: AsyncClient, linked_company, cashier, loaded
) -> None:
    await set_policy(client, linked_company, cashier, denied_ledgers=["Reliance Retail"])
    response = await client.post(
        path(linked_company, "/vouchers"),
        json={
            "kind": "receipt",
            "date": "2026-11-04",
            "party": "Reliance Retail",
            "amount": "100.00",
            "account": "Cash",
        },
        headers=cashier["headers"],
    )
    assert response.status_code == 403, response.text
    assert loaded.writes == []


# --------------------------------------------------------------------------
# Entries: which kinds of voucher a person may create
# --------------------------------------------------------------------------


def receipt(party: str = "Reliance Retail") -> dict[str, Any]:
    return {
        "kind": "receipt",
        "date": "2026-11-04",
        "party": party,
        "amount": "100.00",
        "account": "Cash",
    }


def test_every_writeable_kind_has_an_entry_switch() -> None:
    # A kind added to WRITEABLE_KINDS without a switch here would be one no
    # admin could take away from anybody.
    from tally_backend.api.v1.vouchers import _KINDS
    from tally_backend.services.access_policy import ENTRY_KINDS

    assert set(ENTRY_KINDS) == set(_KINDS)


def test_an_entry_kind_nobody_knows_is_refused_not_stored() -> None:
    with pytest.raises(ValueError):
        AccessPolicy(denied_entries=["journal"])


def test_the_entries_switch_off_allows_no_kind() -> None:
    policy = AccessPolicy(entries=False, denied_entries=["payment"])
    assert not policy.allows_entry("receipt")
    assert not policy.allows_any_entry
    # Back on, the kinds come back exactly as they were left.
    back = policy.model_copy(update={"entries": True})
    assert back.allows_entry("receipt") and not back.allows_entry("payment")


async def test_a_switched_off_entry_kind_is_refused_before_tally(
    client: AsyncClient, linked_company, cashier, loaded
) -> None:
    saved = await set_policy(client, linked_company, cashier, denied_entries=["receipt"])
    assert saved.status_code == 200, saved.text
    response = await client.post(
        path(linked_company, "/vouchers"), json=receipt(), headers=cashier["headers"]
    )
    assert response.status_code == 403, response.text
    assert "make this kind of entry" in response.text
    assert loaded.writes == []

    # Another kind still goes through, and the admin is never limited.
    payment = {**receipt(), "kind": "payment"}
    allowed = await client.post(
        path(linked_company, "/vouchers"), json=payment, headers=cashier["headers"]
    )
    assert allowed.status_code == 200, allowed.text
    admin = await client.post(
        path(linked_company, "/vouchers"), json=receipt(), headers=linked_company["headers"]
    )
    assert admin.status_code == 200, admin.text


async def test_with_entries_off_no_party_can_be_added_either(
    client: AsyncClient, linked_company, cashier, loaded
) -> None:
    await set_policy(client, linked_company, cashier, entries=False)
    party = await client.post(
        path(linked_company, "/ledgers"),
        json={"name": "Ram Traders", "role": "customer"},
        headers=cashier["headers"],
    )
    assert party.status_code == 403, party.text
    item = await client.post(
        path(linked_company, "/stock-items"),
        json={"name": "Basmati Rice"},
        headers=cashier["headers"],
    )
    assert item.status_code == 403, item.text
    assert loaded.writes == []


async def test_an_entry_restriction_alone_hides_no_report(
    app, client: AsyncClient, linked_company, cashier, loaded, samples
) -> None:
    await seed_masters(app, linked_company["company_id"], samples)
    await set_policy(client, linked_company, cashier, entries=False)
    mine = await client.get(path(linked_company, "/access"), headers=cashier["headers"])
    assert mine.json()["restricted"] is True
    assert mine.json()["policy"]["entries"] is False
    ledgers = await client.get(path(linked_company, "/reports/ledgers"), headers=cashier["headers"])
    assert ledgers.status_code == 200, ledgers.text
    catalog = await client.get(
        path(linked_company, "/access/catalog"), headers=linked_company["headers"]
    )
    assert catalog.json()["entry_kinds"] == [
        "receipt", "payment", "sales", "purchase", "sales_order", "purchase_order"
    ]


async def test_sharing_off_is_stored_and_reported_and_hides_nothing(
    client: AsyncClient, linked_company, cashier, loaded
) -> None:
    saved = await set_policy(client, linked_company, cashier, share=False)
    assert saved.status_code == 200, saved.text
    mine = await client.get(path(linked_company, "/access"), headers=cashier["headers"])
    assert mine.json()["restricted"] is True
    assert mine.json()["policy"]["share"] is False
    # Only the button goes: the reports themselves still open.
    ledgers = await client.get(path(linked_company, "/reports/ledgers"), headers=cashier["headers"])
    assert ledgers.status_code == 200, ledgers.text
    assert AccessPolicy.from_stored({"reports": True}).share is True
