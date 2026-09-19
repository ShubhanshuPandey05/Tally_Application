"""Creating a party or a stock item, and knowing when one is missing.

Two things are being protected here. One is somebody's chart of accounts: a
ledger created on every unrecognised spelling turns it into a list of
near-duplicates, and no report afterwards can say which "Ram Traders" an
outstanding belongs to. The other is their trial balance: nothing created from
a phone carries a figure.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tally_backend.services.masters import CREATABLE, missing_master

TODAY = date.today().isoformat()


async def make_ledger(client: AsyncClient, linked_company, **body: Any):
    return await client.post(
        f"/v1/companies/{linked_company['company_id']}/ledgers",
        json={"name": "Ram & Sons", **body},
        headers=linked_company["headers"],
    )


async def make_item(client: AsyncClient, linked_company, **body: Any):
    return await client.post(
        f"/v1/companies/{linked_company['company_id']}/stock-items",
        json={"name": "Sugar 1kg", **body},
        headers=linked_company["headers"],
    )


def sent(fake) -> dict[str, Any]:
    assert fake.writes, "nothing was sent to the connector"
    return fake.writes[-1][1]["draft"]


# --------------------------------------------------------------------------
# Reading Tally's complaint
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Ledger 'Ram Traders' does not exist!", ("ledger", "Ram Traders")),
        ("Stock Item 'Sugar 1kg' does not exist!", ("stock item", "Sugar 1kg")),
        ("Stock Group 'Grocery' does not exist!", ("stock group", "Grocery")),
        ("Unit 'Nos' does not exist!", ("unit", "Nos")),
    ],
)
def test_tallys_complaint_becomes_something_the_app_can_act_on(message, expected):
    """Verified against live phrasing on 2026-09-17.

    Parsed on the server so the app offers to create exactly the missing thing
    rather than running regular expressions on an error message.
    """
    assert missing_master(message) == expected


@pytest.mark.parametrize(
    "message",
    [
        None,
        "",
        "TallyPrime is taking too long to respond.",
        "Something went wrong on the Tally Connector.",
    ],
)
def test_an_unrecognised_message_offers_nothing(message):
    """The safe direction. An unparsed error is shown as Tally worded it and
    the offer simply does not appear."""
    assert missing_master(message) is None


def test_only_a_party_or_an_item_may_be_created():
    """A stock group or a unit is a decision about how a business classifies
    things, and inventing one from a counter gives a chart of accounts a shape
    nobody chose."""
    assert {"ledger", "stock item"} == CREATABLE


# --------------------------------------------------------------------------
# The hint on a failed entry
# --------------------------------------------------------------------------


async def test_a_missing_party_is_named_on_the_failed_entry(
    client, linked_company, fake_connector
):
    fake_connector.write_fails_with = "tally_error"
    fake_connector.write_error_message = "Ledger 'Ram Traders' does not exist!"

    body = (
        await client.post(
            f"/v1/companies/{linked_company['company_id']}/vouchers",
            json={
                "kind": "receipt",
                "date": TODAY,
                "party": "Ram Traders",
                "amount": "5000.00",
                "account": "Cash",
            },
            headers=linked_company["headers"],
        )
    ).json()

    assert body["ok"] is False
    assert body["missing_kind"] == "ledger"
    assert body["missing_name"] == "Ram Traders"


async def test_a_missing_stock_group_is_not_offered(
    client, linked_company, fake_connector
):
    """It parses, but it is not something the app may create."""
    fake_connector.write_fails_with = "tally_error"
    fake_connector.write_error_message = "Stock Group 'Grocery' does not exist!"

    body = (
        await client.post(
            f"/v1/companies/{linked_company['company_id']}/vouchers",
            json={
                "kind": "receipt",
                "date": TODAY,
                "party": "Ram Traders",
                "amount": "5000.00",
                "account": "Cash",
            },
            headers=linked_company["headers"],
        )
    ).json()

    assert body["missing_kind"] is None
    assert body["missing_name"] is None


# --------------------------------------------------------------------------
# Creating
# --------------------------------------------------------------------------


async def test_a_customer_is_created_under_sundry_debtors(
    client, linked_company, fake_connector
):
    response = await make_ledger(client, linked_company, role="customer")

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    # Which side of the balance sheet a party lands on, decided from a role
    # rather than a group name the client could send as free text.
    assert sent(fake_connector)["role"] == "customer"


async def test_a_supplier_is_created_under_sundry_creditors(
    client, linked_company, fake_connector
):
    await make_ledger(client, linked_company, role="supplier")

    assert sent(fake_connector)["role"] == "supplier"


async def test_a_party_is_created_with_bill_tracking_on(
    client, linked_company, fake_connector
):
    """Without it the ledger cannot carry outstanding bills, which is half of
    what this product reports on."""
    await make_ledger(client, linked_company)

    assert sent(fake_connector)["bill_wise"] is True


async def test_nothing_created_from_a_phone_carries_a_figure(
    client, linked_company, fake_connector
):
    """An opening balance is money and an opening quantity is stock. Inventing
    either would move a trial balance on the strength of a typo."""
    await make_ledger(client, linked_company)
    ledger = sent(fake_connector)
    assert "opening_balance" not in ledger

    await make_item(client, linked_company)
    item = sent(fake_connector)
    assert "opening_quantity" not in item
    assert "rate" not in item


async def test_a_group_cannot_be_chosen_by_the_client(
    client, linked_company, fake_connector
):
    response = await make_ledger(client, linked_company, role="Indirect Expenses")

    assert response.status_code == 422
    assert fake_connector.writes == []


async def test_a_blank_name_is_refused(client, linked_company, fake_connector):
    response = await make_ledger(client, linked_company, name="   ")

    assert response.status_code == 422
    assert fake_connector.writes == []


async def test_a_stock_item_carries_its_unit(client, linked_company, fake_connector):
    await make_item(client, linked_company, unit="Nos")

    assert sent(fake_connector)["unit"] == "Nos"


async def test_a_master_is_never_queued(client, linked_company, fake_connector, app):
    """Unlike a voucher.

    A master exists to let an entry be made *now*. Holding it would leave
    somebody looking at a form they still cannot complete, and by the time the
    PC came back the entry it was for is long gone.
    """
    from tally_backend.db.models import PendingVoucher

    fake_connector.online = False

    response = await make_ledger(client, linked_company)

    assert response.json()["ok"] is False
    async with app.state.session_factory() as session:
        held = (await session.execute(select(PendingVoucher))).scalars().all()
    assert held == []


async def test_creating_a_master_is_audited(client, linked_company, app):
    """A chart of accounts that grew a duplicate needs to say who added it."""
    from tally_backend.db.models import AuditLog

    await make_ledger(client, linked_company)

    async with app.state.session_factory() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "master.create.ledger")
            )
        ).scalars().all()

    assert len(rows) == 1
    assert rows[0].detail["name"] == "Ram & Sons"


async def test_the_demo_account_cannot_add_a_party(client, linked_company, app):
    from tally_backend.db.models import Organisation

    async with app.state.session_factory() as session:
        org = await session.get(Organisation, linked_company["org_id"])
        org.is_demo = True
        await session.commit()

    response = await make_ledger(client, linked_company)

    assert response.status_code == 409


async def test_a_stranger_cannot_add_to_someone_elses_books(
    client, linked_company, fake_connector
):
    other = await client.post(
        "/v1/auth/register",
        json={
            "org_name": "Other Shop",
            "email": "stranger2@example.com",
            "password": "correct-horse-battery",
        },
    )
    token = other.json()["access_token"]

    response = await client.post(
        f"/v1/companies/{linked_company['company_id']}/ledgers",
        json={"name": "Ram & Sons"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code in (402, 404)
    assert fake_connector.writes == []


# --------------------------------------------------------------------------
# The name pickers
# --------------------------------------------------------------------------


async def warm(client, linked_company, report: str) -> None:
    """Put a snapshot in place, the way an ordinary screen would.

    The pickers read stored snapshots and never fall through to Tally -- see
    :func:`test_a_picker_does_not_reach_tally_for_every_keystroke` -- so a test
    has to arrange for one to exist first, exactly as opening any report does.
    """
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/reports/{report}",
        headers=linked_company["headers"],
    )
    assert response.status_code == 200, response.text


async def names(client, linked_company, what: str, q: str | None = None):
    path = f"/v1/companies/{linked_company['company_id']}/masters/{what}"
    if q:
        path += f"?q={q}"
    response = await client.get(path, headers=linked_company["headers"])
    assert response.status_code == 200, response.text
    return response.json()


async def test_the_party_picker_offers_customers_and_suppliers(
    client, linked_company, fake_connector, samples
):
    fake_connector.set("ledgers.list", samples.ledgers())
    await warm(client, linked_company, "ledgers")

    rows = await names(client, linked_company, "parties")

    assert rows, "expected some parties"
    # Every one is in a party group. A picker that offered Duties & Taxes and
    # Indirect Expenses would bury the three names somebody actually wants.
    assert all(
        r["group"].lower() in {"sundry debtors", "sundry creditors"} for r in rows
    )


async def test_the_party_picker_reads_the_domain_field_name(
    client, linked_company, fake_connector
):
    """``parent_group`` on a Ledger, ``parent`` in Tally's own XML.

    Reading only one of them returned an empty list against real books while
    every other test passed, because the fixtures happened to use the other.
    """
    fake_connector.set(
        "ledgers.list",
        [
            {"name": "By domain field", "parent_group": "Sundry Debtors"},
            {"name": "By tally field", "parent": "Sundry Creditors"},
            {"name": "Not a party", "parent_group": "Duties & Taxes"},
        ],
    )
    await warm(client, linked_company, "ledgers")

    found = {r["name"] for r in await names(client, linked_company, "parties")}

    assert found == {"By domain field", "By tally field"}


async def test_a_party_search_narrows_the_list(client, linked_company, fake_connector):
    fake_connector.set(
        "ledgers.list",
        [
            {"name": "Ram Traders", "parent_group": "Sundry Debtors"},
            {"name": "Shyam Stores", "parent_group": "Sundry Debtors"},
        ],
    )
    await warm(client, linked_company, "ledgers")

    found = {r["name"] for r in await names(client, linked_company, "parties", "ram")}

    assert found == {"Ram Traders"}


async def test_the_pickers_never_send_a_balance(
    client, linked_company, fake_connector, samples
):
    """A shop with two thousand ledgers should not send two thousand closing
    balances to fill a dropdown, over a connection whose upload is already the
    bottleneck for report exports."""
    fake_connector.set("ledgers.list", samples.ledgers())
    await warm(client, linked_company, "ledgers")

    rows = await names(client, linked_company, "parties")

    assert rows
    for row in rows:
        assert set(row) == {"name", "group"}


async def test_the_item_picker_offers_stock_items(
    client, linked_company, fake_connector, samples
):
    fake_connector.set("stock_items.list", samples.stock())
    await warm(client, linked_company, "stock")

    rows = await names(client, linked_company, "items")

    assert rows
    assert all("name" in r for r in rows)


async def test_a_company_with_nothing_synced_yet_gets_an_empty_picker(
    client, linked_company
):
    """Not a 503.

    The field a picker feeds is a text box somebody can type into, so no
    suggestions is a working form. An error here would be a shop unable to
    record a sale until a backfill finished.
    """
    assert await names(client, linked_company, "parties") == []
    assert await names(client, linked_company, "items") == []


async def test_a_picker_does_not_reach_tally_for_every_keystroke(
    client, linked_company, fake_connector, samples
):
    """Served from the stored snapshot the refresher already keeps.

    A request per character would be a request per character on a shop's
    broadband, against a TallyPrime that serves one caller at a time.
    """
    fake_connector.set("ledgers.list", samples.ledgers())
    await warm(client, linked_company, "ledgers")
    before = fake_connector.call_count("ledgers.list")

    for typed in ("r", "ra", "ram"):
        await names(client, linked_company, "parties", typed)

    assert fake_connector.call_count("ledgers.list") == before
