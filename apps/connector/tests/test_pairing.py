"""Pairing this machine by showing a code, and what happens when it is revoked.

Two things are being pinned down. The first is the polling contract with the
backend: waiting is the normal state and must not look like an error, and an
internet blip while somebody walks across a shop must not invalidate the code
they are about to scan.

The second is the loop above it. When the account says this computer is no
longer trusted, the connector has to stop offering a dead credential and put a
fresh code on its own screen -- but it must do that *only* when the backend has
said so unambiguously. A connector that unpairs itself on any refusal is one bad
deploy away from a fleet that has to be re-paired by hand, machine by machine.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tally_connector.config import ConnectorSettings, load_settings
from tally_connector.pairing import PairingClient, PairingError, PendingClaim
from tally_connector.runner import EXIT_REJECTED, ConnectorRunner
from tally_connector.session import AuthenticationRejected

BASE = "https://api.example.com"


def client_for(handler) -> PairingClient:
    return PairingClient(
        api_base_url=BASE,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


# --------------------------------------------------------------------------
# Asking for a code
# --------------------------------------------------------------------------


async def test_opening_a_claim_describes_this_machine() -> None:
    """So the app can say "SHOP-PC, Windows 11" before anybody grants access."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            201, json={"code": "the-code", "token": "the-token", "expires_in_seconds": 900}
        )

    claim = await client_for(handler).open_claim(
        hostname="SHOP-PC", os_name="Windows 11", connector_version="0.3.0"
    )

    assert seen == {"hostname": "SHOP-PC", "os": "Windows 11", "connector_version": "0.3.0"}
    assert claim.code == "the-code"
    assert claim.seconds_left > 0


async def test_the_qr_payload_carries_the_code_and_the_server_but_not_the_token() -> None:
    """The token is the proof of being this screen; putting it on the screen
    would make photographing it enough to collect somebody's pairing."""
    claim = PendingClaim(
        code="the-code",
        token="the-token",
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        api_host="api.example.com",
    )

    payload = json.loads(claim.payload())

    assert payload == {"v": 1, "c": "the-code", "h": "api.example.com"}
    assert "the-token" not in claim.payload()


async def test_the_payload_stays_small_enough_to_scan_from_a_step_back() -> None:
    """Every character is another module, read off a monitor by a phone."""
    claim = PendingClaim(
        code="x" * 22,
        token="y" * 22,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
        api_host="api-tallyflow.jsrprimesolution.com",
    )

    assert len(claim.payload()) < 120


async def test_an_unreachable_backend_is_reported_not_swallowed() -> None:
    """"Waiting to be scanned" and "that server is unreachable" look identical
    from a spinner, and they need opposite things done about them."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    with pytest.raises(PairingError, match="could not reach"):
        await client_for(handler).open_claim(
            hostname="SHOP-PC", os_name="Windows 11", connector_version="0.3.0"
        )


# --------------------------------------------------------------------------
# Collecting
# --------------------------------------------------------------------------


def collecting(*responses: httpx.Response):
    """A handler that plays a scripted sequence of collect replies."""
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/collect"):
            return remaining.pop(0) if remaining else responses[-1]
        return httpx.Response(
            201, json={"code": "the-code", "token": "the-token", "expires_in_seconds": 900}
        )

    return handler


async def a_claim(client: PairingClient) -> PendingClaim:
    return await client.open_claim(
        hostname="SHOP-PC", os_name="Windows 11", connector_version="0.3.0"
    )


async def test_waiting_to_be_scanned_is_not_an_error() -> None:
    client = client_for(collecting(httpx.Response(200, json={"status": "pending"})))

    assert await client.collect(await a_claim(client)) is None


async def test_a_collected_pairing_comes_back_whole() -> None:
    client = client_for(
        collecting(
            httpx.Response(
                200,
                json={
                    "status": "ready",
                    "connector_id": "con_abc",
                    "secret": "a-real-secret",
                    "name": "Counter PC",
                },
            )
        )
    )

    pairing = await client.collect(await a_claim(client))

    assert pairing is not None
    assert (pairing.connector_id, pairing.secret, pairing.name) == (
        "con_abc",
        "a-real-secret",
        "Counter PC",
    )


async def test_an_expired_code_ends_the_wait_so_a_new_one_is_drawn() -> None:
    client = client_for(collecting(httpx.Response(404, json={"error": {"message": "gone"}})))
    claim = await a_claim(client)

    assert await client.wait_for_pairing(claim, stop=asyncio.Event()) is None


async def test_a_blip_mid_wait_does_not_invalidate_the_code_on_screen() -> None:
    """Somebody is walking across a shop with a phone. The code has to survive
    the shop's internet doing what a shop's internet does."""
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/collect"):
            return httpx.Response(
                201,
                json={"code": "the-code", "token": "the-token", "expires_in_seconds": 900},
            )
        attempts.append(1)
        if len(attempts) < 3:
            raise httpx.ConnectError("the wifi blinked")
        return httpx.Response(
            200,
            json={
                "status": "ready",
                "connector_id": "con_abc",
                "secret": "a-real-secret",
                "name": "Shop PC",
            },
        )

    client = client_for(handler)
    claim = await a_claim(client)

    pairing = await client.wait_for_pairing(claim, stop=asyncio.Event())

    assert pairing is not None
    assert len(attempts) == 3


async def test_a_shutdown_ends_the_wait_promptly() -> None:
    client = client_for(collecting(httpx.Response(200, json={"status": "pending"})))
    claim = await a_claim(client)
    stop = asyncio.Event()
    stop.set()

    assert await client.wait_for_pairing(claim, stop=stop) is None


# --------------------------------------------------------------------------
# What a rejection means
# --------------------------------------------------------------------------


def runner(tmp_path) -> ConnectorRunner:
    return ConnectorRunner(config_path=tmp_path / "connector.json", version="0.3.0")


@pytest.mark.parametrize("code", ["revoked", "repairing"])
async def test_being_disowned_puts_this_machine_back_on_the_pairing_screen(
    tmp_path, code: str
) -> None:
    """The two rejections somebody decided on, rather than something failing.

    ``revoked`` is the PC being removed; ``repairing`` is the app's "re-pair
    this computer", which replaces the secret and therefore reaches the PC as a
    signature that no longer checks out. Without the second the machine would
    read a deliberate re-pair as a fault and exit instead of showing a code --
    which is exactly what it did before this test existed.
    """
    supervisor = runner(tmp_path)

    outcome = supervisor._explain(AuthenticationRejected("disowned", reason_code=code))

    # None means "go round the loop again", which lands in pairing mode.
    assert outcome is None
    assert supervisor._needs_pairing is True


@pytest.mark.parametrize("code", ["", "auth_failed", "server_key", "protocol"])
async def test_any_other_refusal_leaves_the_pairing_alone(tmp_path, code: str) -> None:
    """A backend that cannot decrypt its own secrets, or a signature check that
    broke for a reason nobody has diagnosed, must not be able to unpair a fleet
    by answering the same way a deliberate revocation does."""
    supervisor = runner(tmp_path)

    outcome = supervisor._explain(AuthenticationRejected("nope", reason_code=code))

    assert outcome == EXIT_REJECTED
    assert supervisor._needs_pairing is False


async def test_a_revoked_connector_keeps_its_credentials_on_disk(tmp_path) -> None:
    """Nothing is gained by deleting them, and a connector that erases its own
    credentials on a server answer is a connector one bad deploy from an
    outage that has to be fixed machine by machine."""
    config = tmp_path / "connector.json"
    config.write_text(
        json.dumps({"connector_id": "con_abc", "connector_secret": "the-secret"}),
        encoding="utf-8",
    )
    supervisor = runner(tmp_path)

    supervisor._explain(AuthenticationRejected("revoked", reason_code="revoked"))

    assert load_settings(config).connector_secret == "the-secret"


# --------------------------------------------------------------------------
# The port button
# --------------------------------------------------------------------------


async def test_changing_the_port_writes_it_before_restarting(tmp_path) -> None:
    """The restart re-reads the file, so the order is the mechanism."""
    config = tmp_path / "connector.json"
    supervisor = ConnectorRunner(config_path=config, version="0.3.0")

    result = await supervisor._action_port({"port": 9002})

    assert result["ok"] is True
    assert load_settings(config).tally_port == 9002
    assert supervisor._restart.is_set()


@pytest.mark.parametrize("value", [0, 70000, "nine thousand", None])
async def test_a_nonsense_port_is_refused_without_touching_the_file(
    tmp_path, value
) -> None:
    config = tmp_path / "connector.json"
    supervisor = ConnectorRunner(config_path=config, version="0.3.0")

    result = await supervisor._action_port({"port": value})

    assert result["ok"] is False
    assert not config.exists()


# --------------------------------------------------------------------------
# Settings
# --------------------------------------------------------------------------


def test_the_pairing_host_is_derived_from_the_socket_address() -> None:
    """One address to configure on a shop PC, not two to keep in step."""
    settings = ConnectorSettings(backend_url="wss://api.example.com/v1/connector")

    assert settings.api_base_url == "https://api.example.com"


def test_a_plaintext_development_backend_derives_a_plaintext_api() -> None:
    settings = ConnectorSettings(backend_url="ws://127.0.0.1:8000/v1/connector")

    assert settings.api_base_url == "http://127.0.0.1:8000"
