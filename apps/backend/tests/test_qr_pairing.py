"""Pairing a Tally PC by scanning a code instead of typing a secret.

Three parties, and the tests below follow them in order: the connector opens a
claim and shows the code, an admin scans it and adopts the machine, the
connector collects the credentials exactly once.

What is really being pinned down here is who may do what. The connector's half
has to work with no credentials at all -- that is the entire point of it -- and
everything that decides which account a machine joins has to be impossible
without an admin token. Those two facts pull in opposite directions, and the
failure they meet at would be somebody photographing a screen and receiving
another business's connector secret.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from fastapi import WebSocketDisconnect
from httpx import AsyncClient
from sqlalchemy import select
from tally_core.protocol import Hello, HostInfo

from tally_backend.api.v1.connector_ws import connector_socket
from tally_backend.db.models import (
    Connector,
    ConnectorClaim,
    ConnectorStatus,
    Organisation,
    OrgStatus,
    utc_now,
)
from tally_backend.services.pairing import _fingerprint

pytestmark = pytest.mark.asyncio


class FakeWebSocket:
    """Just enough socket for the handshake half of ``connector_socket``.

    The transport is the only thing faked. Everything the test is about -- the
    signature check, the branch that decides *why* a connector was refused, and
    the row it writes on success -- is the real handler, because that decision
    is the one a mock would happily get wrong in agreement with itself.
    """

    def __init__(self, app, frames: list[str]) -> None:
        self.app = app
        self._incoming = list(frames)
        self.sent: list[dict] = []
        self.close_code: int | None = None

    async def accept(self) -> None:
        return None

    async def receive_text(self) -> str:
        if not self._incoming:
            # What a connector that has said its piece and gone away looks like,
            # which is how the accepted path leaves the handler.
            raise WebSocketDisconnect(1000)
        return self._incoming.pop(0)

    async def send_text(self, data: str) -> None:
        self.sent.append(json.loads(data))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_code = code


async def handshake(client: AsyncClient, connector_id: str, secret: str) -> dict:
    """Dial the real connector endpoint as a machine holding ``secret``."""
    app = client._transport.app  # noqa: SLF001 - the fixture's own app
    hello = Hello.signed(
        connector_id=connector_id,
        secret=secret,
        host=HostInfo(
            hostname="SHOP-PC",
            os="Windows 11",
            connector_version="0.3.0",
            python_version="3.11.0",
        ),
        capabilities=[],
    )
    socket = FakeWebSocket(app, [hello.model_dump_json()])
    await connector_socket(socket)
    return socket.sent[0]


async def open_claim(client: AsyncClient, *, hostname: str = "SHOP-PC") -> dict:
    """What an unpaired connector does on its own, holding nothing."""
    response = await client.post(
        "/v1/pairing/claims",
        json={"hostname": hostname, "os": "Windows 11", "connector_version": "0.3.0"},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_a_connector_can_ask_for_a_code_with_no_credentials(client: AsyncClient) -> None:
    claim = await open_claim(client)

    assert claim["code"] and claim["token"]
    # The two halves must never be the same string: the code is on a screen and
    # the token is the proof that you are the screen.
    assert claim["code"] != claim["token"]
    assert claim["expires_in_seconds"] > 0


async def test_neither_half_of_the_claim_is_stored_as_issued(
    app, client: AsyncClient
) -> None:
    """A dump of this table must complete no pairing."""
    claim = await open_claim(client)

    async with app.state.session_factory() as session:
        row = await session.scalar(select(ConnectorClaim))

    assert row is not None
    assert claim["code"] not in (row.code_hash, row.token_hash)
    assert claim["token"] not in (row.code_hash, row.token_hash)
    assert row.code_hash == _fingerprint(claim["code"])


async def test_collecting_before_anybody_scans_is_pending_not_an_error(
    client: AsyncClient,
) -> None:
    """The connector polls on this, so the waiting state cannot be a failure."""
    claim = await open_claim(client)

    response = await client.post(
        "/v1/pairing/claims/collect",
        json={"code": claim["code"], "token": claim["token"]},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "pending"
    assert response.json()["secret"] == ""


async def test_a_scan_creates_the_connector_and_the_pc_collects_it(
    app, registered, client: AsyncClient
) -> None:
    claim = await open_claim(client)

    adopted = await client.post(
        "/v1/connectors/claims",
        json={"code": claim["code"], "name": "Counter PC"},
        headers=registered["headers"],
    )
    assert adopted.status_code == 201, adopted.text
    body = adopted.json()
    assert body["name"] == "Counter PC"
    # Taken from what the machine said about itself, so the fleet list can name
    # the PC before it has ever connected.
    assert body["hostname"] == "SHOP-PC"
    # The secret is never in this response. Nobody is meant to read it.
    assert "secret" not in body

    collected = await client.post(
        "/v1/pairing/claims/collect",
        json={"code": claim["code"], "token": claim["token"]},
    )
    assert collected.status_code == 200, collected.text
    assert collected.json()["status"] == "ready"
    assert collected.json()["connector_id"] == body["id"]
    assert len(collected.json()["secret"]) > 20


async def test_the_secret_can_only_be_collected_once(
    registered, client: AsyncClient
) -> None:
    """A captured reply must not pair a second machine as the shop's PC."""
    claim = await open_claim(client)
    await client.post(
        "/v1/connectors/claims",
        json={"code": claim["code"], "name": "Counter PC"},
        headers=registered["headers"],
    )

    first = await client.post(
        "/v1/pairing/claims/collect",
        json={"code": claim["code"], "token": claim["token"]},
    )
    second = await client.post(
        "/v1/pairing/claims/collect",
        json={"code": claim["code"], "token": claim["token"]},
    )

    assert first.json()["status"] == "ready"
    assert second.status_code == 409


async def test_the_code_alone_does_not_collect_the_secret(
    registered, client: AsyncClient
) -> None:
    """Photographing the screen is not enough; the token was never on it."""
    claim = await open_claim(client)
    await client.post(
        "/v1/connectors/claims",
        json={"code": claim["code"], "name": "Counter PC"},
        headers=registered["headers"],
    )

    response = await client.post(
        "/v1/pairing/claims/collect",
        json={"code": claim["code"], "token": "not-the-real-token"},
    )

    assert response.status_code == 404
    # And the real machine can still collect: a wrong guess must not consume
    # the claim, or anyone could deny a shop its pairing by spamming tokens.
    assert (
        await client.post(
            "/v1/pairing/claims/collect",
            json={"code": claim["code"], "token": claim["token"]},
        )
    ).json()["status"] == "ready"


async def test_adopting_a_pc_needs_a_signed_in_admin(client: AsyncClient) -> None:
    claim = await open_claim(client)

    response = await client.post(
        "/v1/connectors/claims", json={"code": claim["code"], "name": "Counter PC"}
    )

    assert response.status_code == 401


async def test_a_pending_account_cannot_adopt_a_pc(
    signed_up, client: AsyncClient
) -> None:
    """The approval gate is felt here like everywhere else that grows an account."""
    claim = await open_claim(client)

    response = await client.post(
        "/v1/connectors/claims",
        json={"code": claim["code"], "name": "Counter PC"},
        headers=signed_up["headers"],
    )

    assert response.status_code == 402


async def test_a_claim_can_only_be_adopted_once(
    registered, client: AsyncClient
) -> None:
    """Otherwise one code would quietly create a connector per scan."""
    claim = await open_claim(client)
    first = await client.post(
        "/v1/connectors/claims",
        json={"code": claim["code"], "name": "Counter PC"},
        headers=registered["headers"],
    )
    second = await client.post(
        "/v1/connectors/claims",
        json={"code": claim["code"], "name": "Back office"},
        headers=registered["headers"],
    )

    assert first.status_code == 201
    assert second.status_code == 404


async def test_an_expired_claim_is_refused(
    app, registered, client: AsyncClient
) -> None:
    claim = await open_claim(client)
    async with app.state.session_factory() as session:
        row = await session.scalar(select(ConnectorClaim))
        row.expires_at = utc_now() - timedelta(seconds=1)
        await session.commit()

    response = await client.post(
        "/v1/connectors/claims",
        json={"code": claim["code"], "name": "Counter PC"},
        headers=registered["headers"],
    )

    assert response.status_code == 404


async def test_the_preview_names_the_machine_before_anyone_adopts_it(
    registered, client: AsyncClient
) -> None:
    claim = await open_claim(client, hostname="BACK-OFFICE")

    response = await client.get(
        f"/v1/connectors/claims/{claim['code']}", headers=registered["headers"]
    )

    assert response.status_code == 200, response.text
    assert response.json()["hostname"] == "BACK-OFFICE"
    assert response.json()["os"] == "Windows 11"


# --------------------------------------------------------------------------
# Re-pairing an existing PC
# --------------------------------------------------------------------------


async def test_repair_kills_the_credential_immediately(
    app, registered, client: AsyncClient
) -> None:
    """"Re-pair" means "stop trusting that machine now", not "when I get there"."""
    created = await client.post(
        "/v1/connectors", json={"name": "Shop PC"}, headers=registered["headers"]
    )
    connector_id = created.json()["connector_id"]
    async with app.state.session_factory() as session:
        before = (await session.get(Connector, connector_id)).secret_encrypted

    response = await client.post(
        f"/v1/connectors/{connector_id}/repair", headers=registered["headers"]
    )

    assert response.status_code == 204
    async with app.state.session_factory() as session:
        connector = await session.get(Connector, connector_id)
        assert connector.secret_encrypted != before
        assert connector.status is ConnectorStatus.PENDING
        # The row survives, which is the whole difference from revoking: its
        # companies and their synced history stay attached to it.
        assert connector.revoked_at is None


async def test_a_repaired_pc_is_told_to_show_a_code_not_that_it_is_broken(
    app, registered, client: AsyncClient
) -> None:
    """The bug this column exists to fix.

    Re-pairing replaces the secret, so the PC's next handshake fails its
    signature check -- which on its own is indistinguishable from a genuine
    authentication failure. Answered as ``auth_failed``, the connector correctly
    refuses to unpair itself and exits, and the machine sits there dead instead
    of showing the code somebody is standing in front of it waiting for.
    """
    created = await client.post(
        "/v1/connectors", json={"name": "Shop PC"}, headers=registered["headers"]
    )
    connector_id = created.json()["connector_id"]
    stale_secret = created.json()["secret"]

    await client.post(
        f"/v1/connectors/{connector_id}/repair", headers=registered["headers"]
    )

    ack = await handshake(client, connector_id, stale_secret)

    assert ack["accepted"] is False
    assert ack["reason_code"] == "repairing"


async def test_a_genuinely_wrong_secret_is_never_reported_as_a_re_pair(
    registered, client: AsyncClient
) -> None:
    """The other half, and the more important one.

    A connector that treated any refusal as "ask to be paired again" would be
    one bad deploy from a fleet on pairing screens, each machine needing a
    visit. Nothing but a deliberate decision may produce that answer.
    """
    created = await client.post(
        "/v1/connectors", json={"name": "Shop PC"}, headers=registered["headers"]
    )

    ack = await handshake(client, created.json()["connector_id"], "not-the-secret")

    assert ack["accepted"] is False
    assert ack["reason_code"] == "auth_failed"


async def test_pairing_again_clears_the_repair_flag(
    app, registered, client: AsyncClient
) -> None:
    """Cleared by the handshake it was waiting for, not by issuing the secret.

    Between an admin scanning and the PC collecting, the machine still holds the
    old credential and still needs to be told to show a code.
    """
    created = await client.post(
        "/v1/connectors", json={"name": "Shop PC"}, headers=registered["headers"]
    )
    connector_id = created.json()["connector_id"]
    await client.post(
        f"/v1/connectors/{connector_id}/repair", headers=registered["headers"]
    )

    claim = await open_claim(client)
    await client.post(
        f"/v1/connectors/{connector_id}/claims",
        json={"code": claim["code"]},
        headers=registered["headers"],
    )
    async with app.state.session_factory() as session:
        assert (await session.get(Connector, connector_id)).repair_requested_at is not None

    collected = await client.post(
        "/v1/pairing/claims/collect",
        json={"code": claim["code"], "token": claim["token"]},
    )
    ack = await handshake(client, connector_id, collected.json()["secret"])

    assert ack["accepted"] is True
    async with app.state.session_factory() as session:
        assert (await session.get(Connector, connector_id)).repair_requested_at is None


async def test_rescanning_keeps_the_same_connector_row(
    app, registered, client: AsyncClient
) -> None:
    """Adding the PC again instead would fork the shop's companies in two."""
    created = await client.post(
        "/v1/connectors", json={"name": "Shop PC"}, headers=registered["headers"]
    )
    connector_id = created.json()["connector_id"]
    await client.post(
        f"/v1/connectors/{connector_id}/repair", headers=registered["headers"]
    )

    claim = await open_claim(client)
    response = await client.post(
        f"/v1/connectors/{connector_id}/claims",
        json={"code": claim["code"], "name": "Shop PC"},
        headers=registered["headers"],
    )
    assert response.status_code == 200, response.text

    collected = await client.post(
        "/v1/pairing/claims/collect",
        json={"code": claim["code"], "token": claim["token"]},
    )
    assert collected.json()["connector_id"] == connector_id

    async with app.state.session_factory() as session:
        rows = (await session.execute(select(Connector))).scalars().all()
    assert len(rows) == 1


async def test_a_revoked_pc_cannot_be_brought_back_by_a_scan(
    registered, client: AsyncClient
) -> None:
    """It was removed on purpose, and a camera is not the place to undo that."""
    created = await client.post(
        "/v1/connectors", json={"name": "Shop PC"}, headers=registered["headers"]
    )
    connector_id = created.json()["connector_id"]
    await client.delete(f"/v1/connectors/{connector_id}", headers=registered["headers"])

    claim = await open_claim(client)
    response = await client.post(
        f"/v1/connectors/{connector_id}/claims",
        json={"code": claim["code"], "name": "Shop PC"},
        headers=registered["headers"],
    )

    assert response.status_code == 409


async def test_one_business_cannot_claim_another_businesses_connector(
    app, registered, client: AsyncClient
) -> None:
    """The connector id in the path is scoped to the caller's own account."""
    created = await client.post(
        "/v1/connectors", json={"name": "Shop PC"}, headers=registered["headers"]
    )
    connector_id = created.json()["connector_id"]

    outsider = await client.post(
        "/v1/auth/register",
        json={
            "email": "someone@elsewhere.in",
            "password": "a-sufficiently-long-password",
            "org_name": "Elsewhere Traders",
        },
    )
    headers = {"Authorization": f"Bearer {outsider.json()['access_token']}"}
    me = await client.get("/v1/auth/me", headers=headers)
    # Approved directly rather than through the conftest helper: `conftest` is
    # not an importable package, so a bare `from conftest import ...` resolves
    # to whichever tests directory pytest collected first -- which passes when
    # this file runs alone and fails when the whole suite does.
    async with app.state.session_factory() as session:
        org = await session.get(Organisation, me.json()["org_id"])
        org.status = OrgStatus.ACTIVE
        org.max_users = 5
        org.max_companies = 5
        await session.commit()

    claim = await open_claim(client)
    response = await client.post(
        f"/v1/connectors/{connector_id}/claims",
        json={"code": claim["code"], "name": "Stolen PC"},
        headers=headers,
    )

    # 404, not 403: confirming the id exists would be an enumeration oracle.
    assert response.status_code == 404
