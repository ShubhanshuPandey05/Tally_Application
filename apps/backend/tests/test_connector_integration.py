"""End-to-end: the real connector talking to the real backend.

Everything else in this suite fakes the connector. This module does not -- it
runs the actual ``ConnectorSession`` from ``tally_connector`` against a real
uvicorn server running the real app, over a real WebSocket. Only TallyPrime
itself is stubbed.

That boundary is the point. The handshake, the HMAC, the protocol version check,
job correlation, compression and the heartbeat are all *contracts between two
separately-tested components*, and contracts are exactly what unit tests with
mocks on both sides cannot verify. A signature computed over a different
canonical string passes both halves' own tests and fails only here.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
from typing import Any

import pytest
import pytest_asyncio
import uvicorn
from httpx import ASGITransport, AsyncClient
from tally_connector.config import ConnectorSettings
from tally_connector.session import AuthenticationRejected, ConnectorSession

from tally_backend.config import Settings
from tally_backend.main import create_app


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class StubTally:
    """Stands in for TallyPrime behind the connector."""

    def __init__(self) -> None:
        self.alive = True
        self.calls: list[str] = []

    async def is_alive(self) -> bool:
        return self.alive

    async def execute(self, query: Any, params: Any) -> Any:
        self.calls.append(query.name)
        if query.name == "ledgers.list":
            from decimal import Decimal

            from tally_core.domain.masters import Ledger
            from tally_core.domain.money import Money, Side

            return [
                Ledger(
                    name="Cash",
                    parent_group="Cash-in-Hand",
                    closing_balance=Money(amount=Decimal("344220.00"), side=Side.DEBIT),
                )
            ]
        if query.name == "companies.list":
            from tally_core.domain.masters import Company as TallyCompany

            return [TallyCompany(name="Bhtia Supermarket")]
        return []

    async def aclose(self) -> None:
        return None


class RunningServer:
    """A real uvicorn server on a real port, for the duration of a test."""

    def __init__(self, app, port: int) -> None:
        self._config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self._server = uvicorn.Server(self._config)
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> RunningServer:
        self._task = asyncio.create_task(self._server.serve())
        for _ in range(200):
            if self._server.started:
                return self
            await asyncio.sleep(0.02)
        raise RuntimeError("server did not start")

    async def __aexit__(self, *exc: object) -> None:
        self._server.should_exit = True
        if self._task is not None:
            with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._task, timeout=5)


@pytest_asyncio.fixture
async def live_backend(tmp_path):
    """A backend on a real port, with an org and connector credentials issued."""
    settings = Settings(
        environment="dev",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'live.db'}",
        jwt_secret="integration-secret-long-enough-for-validation",
        secret_keys=["integration-encryption-key"],
        refresh_worker_enabled=False,
        rate_limit_per_minute=10_000,
        auth_rate_limit_per_minute=10_000,
        heartbeat_interval_seconds=0.5,
        heartbeat_grace_multiplier=20.0,
    )
    app = create_app(settings)
    port = free_port()

    async with RunningServer(app, port), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        registered = await http.post(
            "/v1/auth/register",
            json={
                "email": "integration@bhatiastores.in",
                "password": "a-sufficiently-long-password",
                "org_name": "Integration Shop",
            },
        )
        assert registered.status_code == 201, registered.text
        headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

        created = await http.post(
            "/v1/connectors", json={"name": "Integration PC"}, headers=headers
        )
        assert created.status_code == 201, created.text
        pairing = created.json()

        yield {
            "app": app,
            "port": port,
            "headers": headers,
            "http": http,
            "connector_id": pairing["connector_id"],
            "secret": pairing["secret"],
            "settings": settings,
        }


def connector_settings(live_backend, **overrides) -> ConnectorSettings:
    return ConnectorSettings(
        connector_id=live_backend["connector_id"],
        connector_secret=live_backend["secret"],
        backend_url=f"ws://127.0.0.1:{live_backend['port']}/v1/connector",
        heartbeat_timeout_seconds=30.0,
        **overrides,
    )


async def wait_for_attachment(app, connector_id: str, timeout: float = 5.0) -> None:
    for _ in range(int(timeout / 0.05)):
        if app.state.hub.local_link(connector_id) is not None:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("connector never attached to the hub")


@pytest.mark.asyncio
async def test_a_real_connector_handshakes_and_serves_a_job(live_backend) -> None:
    """The whole contract, end to end, with only Tally stubbed."""
    app = live_backend["app"]
    tally = StubTally()
    session = ConnectorSession(
        connector_settings(live_backend), version="0.1.0", tally=tally
    )

    runner = asyncio.create_task(session.run_forever())
    try:
        await wait_for_attachment(app, live_backend["connector_id"])

        link = app.state.hub.local_link(live_backend["connector_id"])
        assert link is not None
        # The capability manifest crossed the wire, so the backend knows what
        # this build can serve.
        assert any(c["name"] == "ledgers.list" for c in link.capabilities)

        result = await app.state.hub.run(
            connector_id=live_backend["connector_id"],
            query="ledgers.list",
            params={"company": "Bhtia Supermarket"},
            timeout_seconds=10,
        )

        assert result.ok, result.error
        payload = result.data()
        assert payload[0]["name"] == "Cash"
        # The live-verified convention survives the whole round trip.
        assert payload[0]["closing_balance"]["side"] == "debit"
        assert payload[0]["closing_balance"]["amount"] == "344220.00"
        # The connector checks the company is actually open before reading it:
        # Tally answers a read for a closed company with an empty collection and
        # no error, which the dashboard would render as a shop with no data.
        assert tally.calls == ["companies.list", "ledgers.list"]
    finally:
        await session.stop()
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner


@pytest.mark.asyncio
async def test_a_wrong_secret_is_rejected(live_backend) -> None:
    """A connector that cannot prove possession of the secret gets nowhere."""
    settings = connector_settings(live_backend)
    settings.connector_secret = "not-the-real-secret"
    session = ConnectorSession(settings, version="0.1.0", tally=StubTally())

    with pytest.raises(AuthenticationRejected):
        await asyncio.wait_for(session.run_forever(), timeout=10)


@pytest.mark.asyncio
async def test_an_unknown_connector_id_is_rejected(live_backend) -> None:
    settings = connector_settings(live_backend)
    settings.connector_id = "0" * 32
    session = ConnectorSession(settings, version="0.1.0", tally=StubTally())

    with pytest.raises(AuthenticationRejected):
        await asyncio.wait_for(session.run_forever(), timeout=10)


@pytest.mark.asyncio
async def test_a_revoked_connector_cannot_reconnect(live_backend) -> None:
    """Revocation must survive the connector trying again."""
    revoked = await live_backend["http"].delete(
        f"/v1/connectors/{live_backend['connector_id']}",
        headers=live_backend["headers"],
    )
    assert revoked.status_code == 204

    session = ConnectorSession(
        connector_settings(live_backend), version="0.1.0", tally=StubTally()
    )
    with pytest.raises(AuthenticationRejected):
        await asyncio.wait_for(session.run_forever(), timeout=10)


@pytest.mark.asyncio
async def test_tally_status_reaches_the_backend_over_the_heartbeat(live_backend) -> None:
    """The app can say "Tally is closed" without waiting for a report to fail."""
    app = live_backend["app"]
    tally = StubTally()
    tally.alive = False

    session = ConnectorSession(
        connector_settings(live_backend), version="0.1.0", tally=tally
    )
    runner = asyncio.create_task(session.run_forever())
    try:
        await wait_for_attachment(app, live_backend["connector_id"])
        link = app.state.hub.local_link(live_backend["connector_id"])

        for _ in range(100):
            if link.status_known:
                break
            await asyncio.sleep(0.05)

        assert link.status_known, "no heartbeat reply arrived"
        assert link.tally_online is False
    finally:
        await session.stop()
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner


@pytest.mark.asyncio
async def test_a_dead_connector_fails_the_job_instead_of_hanging(live_backend) -> None:
    """No socket means a fast, specific error -- not a request that hangs."""
    result = await live_backend["app"].state.hub.run(
        connector_id=live_backend["connector_id"],
        query="ledgers.list",
        params={"company": "X"},
        timeout_seconds=5,
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "connector_offline"
    assert result.error.retryable is True


@pytest.mark.asyncio
async def test_company_discovery_works_through_the_live_connector(live_backend) -> None:
    app = live_backend["app"]
    session = ConnectorSession(
        connector_settings(live_backend), version="0.1.0", tally=StubTally()
    )
    runner = asyncio.create_task(session.run_forever())
    try:
        await wait_for_attachment(app, live_backend["connector_id"])

        response = await live_backend["http"].get(
            f"/v1/connectors/{live_backend['connector_id']}/discover",
            headers=live_backend["headers"],
        )
        assert response.status_code == 200, response.text
        assert response.json()[0]["tally_name"] == "Bhtia Supermarket"
        assert response.json()[0]["linked"] is False
    finally:
        await session.stop()
        runner.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runner
