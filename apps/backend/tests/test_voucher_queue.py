"""Entries held for a PC that was not there.

A queue that delivers a voucher twice is worse than no queue at all, so most of
this file is about what must *not* happen: no double claim, no retry after an
ambiguous failure, no silent delivery of something a week stale.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from tally_backend.db.models import PendingVoucher, PendingVoucherState, utc_now
from tally_backend.services.voucher_queue import VoucherQueue

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


async def post(client: AsyncClient, linked_company, body: dict[str, Any] | None = None):
    return await client.post(
        f"/v1/companies/{linked_company['company_id']}/vouchers",
        json=body or receipt(),
        headers=linked_company["headers"],
    )


def forget_attempts(fake) -> None:
    """Drop the record of the live try that failed and caused the queueing.

    The fake logs a write before it checks whether it is online, so an offline
    attempt still lands in ``writes``. Clearing it here keeps every assertion
    below about what the *drain* sent, which is the thing under test.
    """
    fake.writes.clear()


async def rows(app, company_id: str) -> list[PendingVoucher]:
    async with app.state.session_factory() as session:
        return list(
            (
                await session.execute(
                    select(PendingVoucher)
                    .where(PendingVoucher.company_id == company_id)
                    .order_by(PendingVoucher.created_at)
                )
            ).scalars().all()
        )


def queue_for(app) -> VoucherQueue:
    return VoucherQueue(app.state.session_factory, app.state.hub, app.state.settings)


# --------------------------------------------------------------------------
# Accepting
# --------------------------------------------------------------------------


async def test_an_unreachable_pc_puts_the_entry_in_the_queue(
    client, linked_company, fake_connector, app
):
    fake_connector.online = False

    await post(client, linked_company)

    held = await rows(app, linked_company["company_id"])
    assert len(held) == 1
    assert held[0].state == PendingVoucherState.WAITING
    assert held[0].kind == "receipt"
    assert held[0].party_name == "Ram & Sons"


async def test_a_timeout_is_never_queued(client, linked_company, fake_connector, app):
    """The rule the whole design rests on.

    A write that timed out may already be in TallyPrime. Holding it for a
    second attempt is how one receipt becomes two, and no evidence afterwards
    separates that from a write that never arrived.
    """
    fake_connector.write_fails_with = "write_timeout"

    body = (await post(client, linked_company)).json()

    assert body["queued"] is False
    assert await rows(app, linked_company["company_id"]) == []


async def test_a_voucher_tally_refused_is_never_queued(
    client, linked_company, fake_connector, app
):
    """Nothing was written, but the same entry will be refused the same way.
    Queueing it would retry a broken entry until it expired."""
    fake_connector.write_fails_with = "tally_error"

    await post(client, linked_company)

    assert await rows(app, linked_company["company_id"]) == []


async def test_a_successful_write_is_never_queued(
    client, linked_company, fake_connector, app
):
    await post(client, linked_company)

    assert await rows(app, linked_company["company_id"]) == []


# --------------------------------------------------------------------------
# Delivering
# --------------------------------------------------------------------------


async def test_the_queue_drains_when_the_pc_comes_back(
    client, linked_company, fake_connector, app
):
    fake_connector.online = False
    await post(client, linked_company)

    forget_attempts(fake_connector)
    fake_connector.online = True
    delivered = await queue_for(app).drain(linked_company["connector_id"])

    assert delivered == 1
    held = await rows(app, linked_company["company_id"])
    assert held[0].state == PendingVoucherState.SENT
    assert held[0].tally_voucher_id == 4821
    assert held[0].last_error is None


async def test_entries_reach_tally_in_the_order_they_were_made(
    client, linked_company, fake_connector, app
):
    """Oldest first. A shop that took three payments in a row should see them
    in the Day Book the way they happened."""
    fake_connector.online = False
    for party in ("First", "Second", "Third"):
        await post(client, linked_company, receipt(party=party))

    forget_attempts(fake_connector)
    fake_connector.online = True
    await queue_for(app).drain(linked_company["connector_id"])

    sent = [params["draft"]["party_name"] for _, params in fake_connector.writes]
    assert sent == ["First", "Second", "Third"]


async def test_a_still_offline_pc_leaves_the_entry_waiting(
    client, linked_company, fake_connector, app
):
    """Nothing reached Tally, so it keeps its turn rather than counting as a
    failure."""
    fake_connector.online = False
    await post(client, linked_company)

    delivered = await queue_for(app).drain(linked_company["connector_id"])

    assert delivered == 0
    held = await rows(app, linked_company["company_id"])
    assert held[0].state == PendingVoucherState.WAITING
    assert held[0].attempts == 1


async def test_an_ambiguous_failure_settles_rather_than_waiting_again(
    client, linked_company, fake_connector, app
):
    """A timeout *during a drain* must not put the entry back in the queue.

    By then the frame was on the wire. Sending it again on the next sweep is
    exactly the duplicate this whole design is built to avoid.
    """
    fake_connector.online = False
    await post(client, linked_company)

    forget_attempts(fake_connector)
    fake_connector.online = True
    fake_connector.write_fails_with = "write_timeout"
    await queue_for(app).drain(linked_company["connector_id"])

    held = await rows(app, linked_company["company_id"])
    assert held[0].state == PendingVoucherState.FAILED
    assert held[0].settled_at is not None


async def test_a_drain_does_not_send_the_same_entry_twice(
    client, linked_company, fake_connector, app
):
    """Two drains racing. The claim is a compare-and-swap, so exactly one wins.

    ``SELECT ... FOR UPDATE`` would have looked right and is a no-op on SQLite
    -- the kind of concurrency control that passes its own tests.
    """
    fake_connector.online = False
    await post(client, linked_company)
    forget_attempts(fake_connector)
    fake_connector.online = True

    # Two separate queue objects, so the per-instance "already draining" guard
    # cannot be what makes this pass.
    await asyncio.gather(
        queue_for(app).drain(linked_company["connector_id"]),
        queue_for(app).drain(linked_company["connector_id"]),
    )

    assert fake_connector.write_count("voucher.create") == 1


async def test_an_expired_entry_is_never_delivered(
    client, linked_company, fake_connector, app
):
    """A week-old entry must not post silently into a period nobody is looking
    at any more."""
    fake_connector.online = False
    await post(client, linked_company)

    async with app.state.session_factory() as session:
        row = (
            await session.execute(select(PendingVoucher))
        ).scalars().one()
        row.expires_at = utc_now() - timedelta(minutes=1)
        await session.commit()

    forget_attempts(fake_connector)
    fake_connector.online = True
    delivered = await queue_for(app).drain(linked_company["connector_id"])

    assert delivered == 0
    assert fake_connector.writes == []
    held = await rows(app, linked_company["company_id"])
    assert held[0].state == PendingVoucherState.FAILED
    assert "waited too long" in held[0].last_error


async def test_a_drain_is_bounded(client, linked_company, fake_connector, app):
    """TallyPrime is somebody's till and serves one request at a time. A shop
    that queued a long weekend's entries must not have it locked up on boot."""
    fake_connector.online = False
    for i in range(5):
        await post(client, linked_company, receipt(party=f"Customer {i}"))

    forget_attempts(fake_connector)
    fake_connector.online = True
    delivered = await queue_for(app).drain(linked_company["connector_id"], limit=2)

    assert delivered == 2
    assert fake_connector.write_count("voucher.create") == 2


# --------------------------------------------------------------------------
# What the phone sees
# --------------------------------------------------------------------------


async def test_pending_entries_are_listed(client, linked_company, fake_connector):
    fake_connector.online = False
    await post(client, linked_company)

    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/vouchers/pending",
        headers=linked_company["headers"],
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["kind"] == "receipt"
    assert body[0]["party"] == "Ram & Sons"
    assert body[0]["is_open"] is True
    # No amount. This list says *that* an entry is stuck, not what it was worth.
    assert "amount" not in body[0]


async def test_a_settled_entry_still_shows(client, linked_company, fake_connector, app):
    """Somebody who watched an entry go into the queue needs to find out what
    became of it. A list that drops the failures is how a receipt goes
    missing."""
    fake_connector.online = False
    await post(client, linked_company)
    forget_attempts(fake_connector)
    fake_connector.online = True
    await queue_for(app).drain(linked_company["connector_id"])

    body = (
        await client.get(
            f"/v1/companies/{linked_company['company_id']}/vouchers/pending",
            headers=linked_company["headers"],
        )
    ).json()

    assert body[0]["state"] == "sent"
    assert body[0]["is_open"] is False


async def test_a_waiting_entry_can_be_cancelled(
    client, linked_company, fake_connector, app
):
    fake_connector.online = False
    pending_id = (await post(client, linked_company)).json()["pending_id"]

    response = await client.delete(
        f"/v1/companies/{linked_company['company_id']}/vouchers/pending/{pending_id}",
        headers=linked_company["headers"],
    )

    assert response.status_code == 204
    held = await rows(app, linked_company["company_id"])
    assert held[0].state == PendingVoucherState.CANCELLED

    # And a cancelled entry is never delivered.
    forget_attempts(fake_connector)
    fake_connector.online = True
    assert await queue_for(app).drain(linked_company["connector_id"]) == 0
    assert fake_connector.writes == []


async def test_an_entry_already_on_its_way_cannot_be_cancelled(
    client, linked_company, fake_connector, app
):
    """It may be on the wire this second. Reporting it withdrawn while it lands
    in somebody's books is worse than refusing."""
    fake_connector.online = False
    pending_id = (await post(client, linked_company)).json()["pending_id"]

    async with app.state.session_factory() as session:
        row = await session.get(PendingVoucher, pending_id)
        row.state = PendingVoucherState.SENDING
        await session.commit()

    response = await client.delete(
        f"/v1/companies/{linked_company['company_id']}/vouchers/pending/{pending_id}",
        headers=linked_company["headers"],
    )

    assert response.status_code == 400


async def test_another_orgs_pending_entry_is_not_found(
    client, linked_company, fake_connector
):
    fake_connector.online = False
    pending_id = (await post(client, linked_company)).json()["pending_id"]

    other = await client.post(
        "/v1/auth/register",
        json={
            "org_name": "Other Shop",
            "email": "stranger@example.com",
            "password": "correct-horse-battery",
        },
    )
    token = other.json()["access_token"]

    response = await client.delete(
        f"/v1/companies/{linked_company['company_id']}/vouchers/pending/{pending_id}",
        headers={"Authorization": f"Bearer {token}"},
    )

    # 404 or 402 -- never 204, and never a 403 that would confirm the id exists.
    assert response.status_code in (402, 404)


@pytest.mark.parametrize("state", ["sent", "failed", "cancelled"])
async def test_a_settled_entry_is_not_drained_again(
    client, linked_company, fake_connector, app, state
):
    fake_connector.online = False
    await post(client, linked_company)

    async with app.state.session_factory() as session:
        row = (await session.execute(select(PendingVoucher))).scalars().one()
        row.state = PendingVoucherState(state)
        await session.commit()

    forget_attempts(fake_connector)
    fake_connector.online = True
    assert await queue_for(app).drain(linked_company["connector_id"]) == 0
    assert fake_connector.writes == []
