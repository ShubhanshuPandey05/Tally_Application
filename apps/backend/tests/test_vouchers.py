"""Creating a voucher from the app.

The only endpoint in this API that changes a customer's books, so most of what
is asserted here is what it refuses to do and what it refuses to claim.

The double-entry assertions matter more than they look. Tally's sign convention
is inverted from the usual one — **negative is a debit** — and an entry posted
the wrong way round produces no error at all, just a balance moving in the
wrong direction on somebody's real books.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from httpx import AsyncClient

TODAY = date.today().isoformat()


def receipt(**overrides: Any) -> dict[str, Any]:
    body = {
        "kind": "receipt",
        "date": TODAY,
        "party": "Ram & Sons",
        "amount": "5000.00",
        "account": "Cash",
    }
    body.update(overrides)
    return body


async def post(client: AsyncClient, linked_company, body: dict[str, Any]):
    return await client.post(
        f"/v1/companies/{linked_company['company_id']}/vouchers",
        json=body,
        headers=linked_company["headers"],
    )


def sent(fake) -> dict[str, Any]:
    """The draft the backend handed the connector."""
    assert fake.writes, "nothing was sent to the connector"
    return fake.writes[-1][1]["draft"]


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


async def test_a_receipt_is_created_and_waits_for_approval(
    client, linked_company, fake_connector
):
    response = await post(client, linked_company, receipt())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    # The distinction the app leads with. "Saved" and "saved but not counting
    # yet" are different things to tell somebody who just took money.
    assert body["awaiting_approval"] is True
    assert body["voucher_id"] == 4821


async def test_the_entry_is_pinned_to_the_right_company(
    client, linked_company, fake_connector
):
    await post(client, linked_company, receipt())

    # Tally's own name for the books, not our id. An import that does not name
    # one posts into whichever company is open on that PC.
    assert fake_connector.writes[-1][1]["company"] == linked_company["tally_name"]


async def test_a_receipt_debits_cash_and_credits_the_customer(
    client, linked_company, fake_connector
):
    await post(client, linked_company, receipt())

    entries = {e["ledger_name"]: e for e in sent(fake_connector)["ledger_entries"]}
    # Negative is a debit, and the flag has to agree with the sign.
    assert entries["Cash"]["amount"] == "-5000.00"
    assert entries["Cash"]["is_deemed_positive"] is True
    assert entries["Ram & Sons"]["amount"] == "5000.00"
    assert entries["Ram & Sons"]["is_deemed_positive"] is False


async def test_a_payment_goes_the_other_way(client, linked_company, fake_connector):
    await post(
        client,
        linked_company,
        receipt(kind="payment", party="Wholesaler", account="Bank"),
    )

    entries = {e["ledger_name"]: e for e in sent(fake_connector)["ledger_entries"]}
    # The supplier is debited on a payment; cash leaves.
    assert entries["Wholesaler"]["amount"] == "-5000.00"
    assert entries["Wholesaler"]["is_deemed_positive"] is True
    assert entries["Bank"]["amount"] == "5000.00"


async def test_a_receipt_against_a_bill_uses_agst_ref(
    client, linked_company, fake_connector
):
    await post(
        client,
        linked_company,
        receipt(bills=[{"bill": "INV-001", "amount": "5000.00"}]),
    )

    party = next(
        e for e in sent(fake_connector)["ledger_entries"] if e["ledger_name"] == "Ram & Sons"
    )
    assert party["bill_references"][0]["method"] == "Agst Ref"
    assert party["bill_references"][0]["name"] == "INV-001"


async def test_a_sale_creates_the_debt_with_new_ref(
    client, linked_company, fake_connector
):
    """A sale makes the bill; a receipt settles it. Using Agst Ref on a sale
    would try to allocate against an invoice that does not exist yet."""
    await post(
        client,
        linked_company,
        receipt(
            kind="sales",
            account="Sales",
            bills=[{"bill": "INV-002", "amount": "5000.00"}],
        ),
    )

    party = next(
        e for e in sent(fake_connector)["ledger_entries"] if e["ledger_name"] == "Ram & Sons"
    )
    assert party["bill_references"][0]["method"] == "New Ref"


async def test_a_sale_carries_its_stock_lines(client, linked_company, fake_connector):
    await post(
        client,
        linked_company,
        receipt(
            kind="sales",
            account="Sales",
            amount="1180.00",
            lines=[
                {
                    "item": "Sugar 1kg",
                    "quantity": "10",
                    "rate": "118",
                    "amount": "1180.00",
                    "unit": "Nos",
                }
            ],
        ),
    )

    lines = sent(fake_connector)["inventory_entries"]
    assert lines[0]["item_name"] == "Sugar 1kg"
    # The stock line's value has to name the ledger it posts to.
    assert lines[0]["ledger_name"] == "Sales"


async def test_an_order_is_shaped_like_one_entered_in_tally(
    client, linked_company, fake_connector
):
    """Copied from a purchase order entered by hand in TallyPrime and exported
    (live, 2026-09-19): the party at the full value, each line on the purchase
    ledger, and an order number on every line."""
    response = await post(
        client,
        linked_company,
        receipt(
            kind="purchase_order",
            party="Wholesaler",
            account="Gst Purchase",
            amount="1000.00",
            reference="PO-17",
            lines=[{"item": "Sugar 1kg", "quantity": "10", "amount": "1000.00"}],
        ),
    )

    assert response.status_code == 200, response.text
    draft = sent(fake_connector)
    assert [(e["ledger_name"], e["amount"]) for e in draft["ledger_entries"]] == [
        ("Wholesaler", "1000.00")
    ]
    assert draft["inventory_entries"][0]["ledger_name"] == "Gst Purchase"
    # The reference the person typed is the order number, as on Tally's own.
    assert draft["order_number"] == "PO-17"
    assert draft["reference"] == "PO-17"
    assert draft["order_due_date"] == TODAY


async def test_an_order_with_no_reference_gets_a_number(
    client, linked_company, fake_connector
):
    """Tally refuses an order without one, and cannot tell us the voucher
    number until after it has saved the order."""
    await post(
        client,
        linked_company,
        receipt(
            kind="sales_order",
            account="Gst Sales",
            amount="1000.00",
            lines=[{"item": "Sugar 1kg", "quantity": "10", "amount": "1000.00"}],
        ),
    )

    number = sent(fake_connector)["order_number"]
    assert number.startswith("SO-")
    assert sent(fake_connector)["reference"] == number


# --------------------------------------------------------------------------
# What it refuses
# --------------------------------------------------------------------------


async def test_the_phone_always_asks_for_an_entry_that_waits(
    client, linked_company, fake_connector
):
    """Whatever a client sends, the draft leaving here asks to be optional.

    The connector has the final say, but asking for the cautious thing means a
    bug in that layer fails safe rather than posting into somebody's books.
    """
    await post(client, linked_company, receipt())

    assert sent(fake_connector)["optional"] is True


async def test_a_kind_nobody_reviewed_is_refused(client, linked_company, fake_connector):
    response = await post(client, linked_company, receipt(kind="journal"))

    assert response.status_code == 422
    assert fake_connector.writes == []


async def test_bills_that_do_not_add_up_are_refused(
    client, linked_company, fake_connector
):
    response = await post(
        client,
        linked_company,
        receipt(bills=[{"bill": "INV-001", "amount": "3000.00"}]),
    )

    assert response.status_code == 422
    # Refused here, not by Tally: nothing should reach a shop's PC to be
    # rejected when this side can already see the arithmetic is wrong.
    assert fake_connector.writes == []


async def test_stock_lines_that_do_not_add_up_are_refused(
    client, linked_company, fake_connector
):
    response = await post(
        client,
        linked_company,
        receipt(
            kind="sales",
            amount="1180.00",
            lines=[{"item": "Sugar", "quantity": "1", "amount": "999.00"}],
        ),
    )

    assert response.status_code == 422
    assert fake_connector.writes == []


async def test_an_order_with_no_lines_is_refused(client, linked_company, fake_connector):
    response = await post(client, linked_company, receipt(kind="sales_order"))

    assert response.status_code == 422
    assert fake_connector.writes == []


@pytest.mark.parametrize("amount", ["0", "-100"])
async def test_a_nonpositive_amount_is_refused(
    client, linked_company, fake_connector, amount
):
    response = await post(client, linked_company, receipt(amount=amount))

    assert response.status_code == 422
    assert fake_connector.writes == []


async def test_a_connector_too_old_to_write_is_not_sent_one(
    client, linked_company, fake_connector
):
    """An old connector ignores an unknown frame, correctly and silently.

    Without the capability check the phone would wait out the whole deadline
    for a reply that was never coming, so the refusal has to happen here.
    """
    fake_connector.mutations = []

    response = await post(client, linked_company, receipt())

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "too old" in response.json()["message"]


async def test_an_offline_pc_holds_the_entry_rather_than_losing_it(
    client, linked_company, fake_connector
):
    """The PC being off is what the queue is for.

    Nothing reached Tally, which is provable, so the entry is held on the
    server and goes when the machine comes back.
    """
    fake_connector.online = False

    body = (await post(client, linked_company, receipt())).json()

    assert body["queued"] is True
    assert body["pending_id"]
    # Not ok, and not awaiting approval: nothing is in the books yet, and both
    # of those would tell somebody their receipt is recorded when it is not.
    assert body["ok"] is False
    assert body["awaiting_approval"] is False
    assert "as soon as it is back" in body["message"]


async def test_a_refused_entry_is_never_reported_as_awaiting_approval(
    client, linked_company, fake_connector
):
    fake_connector.write_fails_with = "write_timeout"

    body = (await post(client, linked_company, receipt())).json()

    assert body["ok"] is False
    assert body["awaiting_approval"] is False


async def test_tally_creating_nothing_is_not_a_success(
    client, linked_company, fake_connector
):
    """A reply that parsed with nothing created is a failure with a reason."""
    fake_connector.set_write(
        "voucher.create",
        {"created": 0, "errors": 0, "voucher_id": None, "message": "Nothing recorded."},
    )

    body = (await post(client, linked_company, receipt())).json()

    assert body["ok"] is False
    assert body["awaiting_approval"] is False
    assert body["message"] == "Nothing recorded."


async def test_a_stranger_cannot_write_to_someone_elses_books(
    client, linked_company, registered, fake_connector
):
    """404, not 403 -- a 403 would confirm the company id belongs to somebody."""
    other = await client.post(
        "/v1/auth/register",
        json={
            "org_name": "Other Shop",
            "email": "other@example.com",
            "password": "correct-horse-battery",
        },
    )
    assert other.status_code in (200, 201), other.text
    token = other.json()["access_token"]

    response = await client.post(
        f"/v1/companies/{linked_company['company_id']}/vouchers",
        json=receipt(),
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code in (402, 404)
    assert fake_connector.writes == []


async def test_writing_needs_authentication(client, linked_company, fake_connector):
    response = await client.post(
        f"/v1/companies/{linked_company['company_id']}/vouchers", json=receipt()
    )

    assert response.status_code == 401
    assert fake_connector.writes == []


# --------------------------------------------------------------------------
# Never twice
# --------------------------------------------------------------------------


async def test_two_identical_entries_are_two_entries(
    client, linked_company, fake_connector
):
    """The one place coalescing would be actively harmful.

    Two identical reads are one read. Two identical receipts are two receipts --
    a customer paying ₹5,000 twice in a day is ordinary, and folding them
    together would silently lose one of their payments.
    """
    await post(client, linked_company, receipt())
    await post(client, linked_company, receipt())

    assert fake_connector.write_count("voucher.create") == 2


async def test_a_write_is_audited_whatever_happens(client, linked_company, app):
    """Including the failures -- a trail that holds only successes cannot
    answer "who tried?"."""
    from sqlalchemy import select

    from tally_backend.db.models import AuditLog

    await post(client, linked_company, receipt())

    async with app.state.session_factory() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "voucher.create")
            )
        ).scalars().all()

    assert len(rows) == 1
    # The kind and the id, never the amount. This trail is read on support
    # calls, and what somebody's receipt was worth is not a diagnostic.
    assert rows[0].detail["kind"] == "receipt"
    assert "amount" not in rows[0].detail


async def test_an_invoice_with_items_credits_sales_only_through_its_lines(
    client, linked_company, fake_connector
):
    """The bug this test exists to prevent doubles somebody's revenue.

    With stock lines, the sales ledger is credited inside each line's
    accounting allocation. Emitting a ledger entry for it as well credits the
    ledger twice for one invoice -- Tally either refuses the voucher or books
    double the sale, and no figure on screen would say which.
    """
    await post(
        client,
        linked_company,
        receipt(
            kind="sales",
            account="Sales",
            amount="1180.00",
            lines=[
                {
                    "item": "Sugar 1kg",
                    "quantity": "10",
                    "rate": "118",
                    "amount": "1180.00",
                    "unit": "Nos",
                }
            ],
        ),
    )

    draft = sent(fake_connector)
    ledgers = [e["ledger_name"] for e in draft["ledger_entries"]]
    assert ledgers == ["Ram & Sons"]
    assert draft["inventory_entries"][0]["ledger_name"] == "Sales"


async def test_an_invoice_with_no_items_still_names_both_sides(
    client, linked_company, fake_connector
):
    """Without lines there is no allocation to carry the credit, so the sales
    ledger has to be a ledger entry."""
    await post(client, linked_company, receipt(kind="sales", account="Sales"))

    ledgers = {e["ledger_name"] for e in sent(fake_connector)["ledger_entries"]}
    assert ledgers == {"Ram & Sons", "Sales"}


async def test_the_demo_account_cannot_save_entries(
    client, linked_company, app, fake_connector
):
    """There is no PC behind the demo books and the rows are regenerated daily.

    An entry would vanish overnight and look like a bug in the product a
    prospect is evaluating, so it is refused here with a reason rather than
    sent to a connector that does not exist.
    """
    from tally_backend.db.models import Organisation

    async with app.state.session_factory() as session:
        org = await session.get(Organisation, linked_company["org_id"])
        org.is_demo = True
        await session.commit()

    response = await post(client, linked_company, receipt())

    assert response.status_code == 409
    assert "demo" in response.json()["error"]["message"].lower()
    # Refused before anything left this building.
    assert fake_connector.writes == []


# --------------------------------------------------------------------------
# "Can I press it again?" -- the question a failed write has to answer
# --------------------------------------------------------------------------


async def test_a_queued_entry_offers_no_retry(client, linked_company, fake_connector):
    """Because it is already held.

    Offering "Try again" beside a queued entry would invite somebody to create
    a second copy of one that is going to be delivered anyway -- the exact
    duplicate the queue exists to avoid.
    """
    fake_connector.online = False

    body = (await post(client, linked_company, receipt())).json()

    assert body["queued"] is True
    assert body["can_retry"] is False


async def test_a_timed_out_write_may_never_be_tried_again(
    client, linked_company, fake_connector
):
    """Tally can finish an import and lose the reply. Offering a retry here is
    how one receipt becomes two."""
    fake_connector.write_fails_with = "write_timeout"

    body = (await post(client, linked_company, receipt())).json()

    assert body["ok"] is False
    assert body["can_retry"] is False


async def test_a_successful_write_offers_no_retry(client, linked_company, fake_connector):
    body = (await post(client, linked_company, receipt())).json()

    assert body["ok"] is True
    assert body["can_retry"] is False


# --------------------------------------------------------------------------
# Entry order and taxes
# --------------------------------------------------------------------------


async def test_a_receipt_starts_with_its_credit(client, linked_company, fake_connector):
    """A TallyPrime receipt is entered Cr first; a payment Dr first."""
    await post(client, linked_company, receipt())
    names = [e["ledger_name"] for e in sent(fake_connector)["ledger_entries"]]
    assert names == ["Ram & Sons", "Cash"]

    await post(client, linked_company, receipt(kind="payment", party="Wholesaler"))
    names = [e["ledger_name"] for e in sent(fake_connector)["ledger_entries"]]
    assert names == ["Wholesaler", "Cash"]


GST = [
    {"ledger": "Output CGST 9%", "amount": "450.00"},
    {"ledger": "Output SGST 9%", "amount": "450.00"},
]


async def test_a_sale_charges_the_customer_the_tax_and_credits_the_duty_ledgers(
    client, linked_company, fake_connector
):
    response = await post(
        client, linked_company, receipt(kind="sales", account="Sales", taxes=GST)
    )
    assert response.status_code == 200, response.text

    entries = {e["ledger_name"]: e for e in sent(fake_connector)["ledger_entries"]}
    assert entries["Ram & Sons"]["amount"] == "-5900.00"
    # The tax is owed onward, so it never lands in Sales.
    assert entries["Sales"]["amount"] == "5000.00"
    assert entries["Output CGST 9%"]["amount"] == "450.00"
    assert entries["Output CGST 9%"]["is_deemed_positive"] is False
    assert entries["Output SGST 9%"]["amount"] == "450.00"


async def test_an_invoice_with_items_and_tax_still_balances(
    client, linked_company, fake_connector
):
    response = await post(
        client,
        linked_company,
        receipt(
            kind="sales",
            account="Sales",
            lines=[{"item": "Rice", "quantity": "50", "rate": "100", "amount": "5000.00"}],
            taxes=GST,
        ),
    )
    assert response.status_code == 200, response.text

    draft = sent(fake_connector)
    entries = {e["ledger_name"]: e["amount"] for e in draft["ledger_entries"]}
    assert entries == {
        "Ram & Sons": "-5900.00",
        "Output CGST 9%": "450.00",
        "Output SGST 9%": "450.00",
    }


async def test_a_purchase_order_takes_the_tax_on_the_other_side(
    client, linked_company, fake_connector
):
    response = await post(
        client,
        linked_company,
        receipt(
            kind="purchase_order",
            party="Wholesaler",
            account=None,
            lines=[{"item": "Rice", "quantity": "50", "rate": "100", "amount": "5000.00"}],
            taxes=[{"ledger": "Input IGST 18%", "amount": "900.00"}],
        ),
    )
    assert response.status_code == 200, response.text

    entries = {e["ledger_name"]: e for e in sent(fake_connector)["ledger_entries"]}
    assert entries["Wholesaler"]["amount"] == "5900.00"
    assert entries["Input IGST 18%"]["amount"] == "-900.00"
    assert entries["Input IGST 18%"]["is_deemed_positive"] is True


async def test_taxes_are_refused_on_a_receipt(client, linked_company, fake_connector):
    response = await post(client, linked_company, receipt(taxes=GST))

    assert response.status_code == 422
    assert fake_connector.writes == []


async def test_a_missing_tax_ledger_is_not_offered_as_a_new_customer(
    client, linked_company, fake_connector
):
    """Tally names whichever ledger it could not find. Only the party may be
    created from the app; offering "Output CGST 9%" would file a tax ledger
    under Sundry Debtors."""
    fake_connector.write_fails_with = "tally_error"
    fake_connector.write_error_message = "Ledger 'Output CGST 9%' does not exist!"

    body = (
        await post(client, linked_company, receipt(kind="sales", account="Sales", taxes=GST))
    ).json()

    assert body["ok"] is False
    assert body["missing_kind"] is None
    assert "Output CGST 9%" in body["message"]
