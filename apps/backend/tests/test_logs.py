"""The support view: backend logs, connector logs, and the activity trail.

The properties worth testing here are all about a diagnostic channel being
unable to hurt the thing it observes, and about a partner never seeing an
account that is not theirs -- not about log lines rendering nicely.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from tally_core.protocol import LogBatch, LogEntry

from tally_backend.core.security import hash_password
from tally_backend.db.models import (
    Connector,
    ConnectorLog,
    ConnectorStatus,
    Organisation,
    PlatformRole,
    PlatformUser,
    ServerLog,
    utc_now,
)
from tally_backend.services.connector_logs import ConnectorLogIngest
from tally_backend.services.logs import (
    LogRecordView,
    ServerLogStore,
    install_capture,
    level_at_least,
    normalise_level,
)

pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------
# The ring
# --------------------------------------------------------------------------


def make_view(level: str = "INFO", logger: str = "app", message: str = "hello"):
    return LogRecordView(
        seq=0, created_at=utc_now(), level=level, logger=logger, message=message
    )


async def test_levels_collapse_onto_the_four_the_ui_offers():
    assert normalise_level("CRITICAL") == "ERROR"
    assert normalise_level("WARN") == "WARNING"
    assert normalise_level("NOTSET") == "DEBUG"
    # An unknown level must not vanish from the filter. INFO is the level that
    # is always shown, so an unrecognised one lands where it will be seen.
    assert normalise_level("bananas") == "INFO"

    assert level_at_least("ERROR", "WARNING")
    assert not level_at_least("INFO", "WARNING")


async def test_ring_is_bounded_and_returns_newest_first():
    store = ServerLogStore(capacity=3)
    for index in range(5):
        store.capture(make_view(message=f"line-{index}"))

    lines = store.recent()
    assert [line.message for line in lines] == ["line-4", "line-3", "line-2"]
    # Sequence numbers keep counting past what the ring holds, so a live tail
    # resuming from one can tell "nothing new" from "you missed some".
    assert store.last_seq == 5


async def test_only_warnings_and_above_are_queued_for_the_database():
    store = ServerLogStore(persist_from="WARNING")
    store.capture(make_view(level="INFO"))
    store.capture(make_view(level="WARNING"))
    store.capture(make_view(level="ERROR"))

    assert [view.level for view in store.take_pending()] == ["WARNING", "ERROR"]
    # Taken means taken: a second flush must not write the same rows again.
    assert store.take_pending() == []


async def test_capture_never_raises_from_a_logging_call():
    """A logging handler that can fail turns a logged warning into a crash."""

    class Exploding(ServerLogStore):
        def capture(self, view):  # noqa: ANN001, ANN201
            raise RuntimeError("boom")

    handler = install_capture(Exploding())
    try:
        # Would propagate out of `logger.warning` at the call site if `emit`
        # did not swallow it -- which is the last place a caller is prepared
        # to handle an error.
        logging.getLogger("test.explode").warning("this must not raise")
    finally:
        logging.getLogger().removeHandler(handler)


async def test_subscribers_get_lines_and_a_full_queue_is_not_fatal():
    store = ServerLogStore()
    store.bind_loop(asyncio.get_running_loop())

    with store.subscribe(max_backlog=2) as queue:
        for index in range(5):
            store.capture(make_view(message=f"line-{index}"))
        await asyncio.sleep(0)  # let call_soon_threadsafe run

        # A reader that stopped reading loses its backlog rather than holding
        # records in memory for as long as the tab stays open.
        assert queue.qsize() == 2


# --------------------------------------------------------------------------
# Backend log endpoints
# --------------------------------------------------------------------------


async def test_backend_logs_are_owner_only(client: AsyncClient, app, platform_owner):
    partner = await make_partner(app, client)

    refused = await client.get("/v1/portal/logs/backend", headers=partner["headers"])
    assert refused.status_code == 403

    allowed = await client.get("/v1/portal/logs/backend", headers=platform_owner["headers"])
    assert allowed.status_code == 200


async def test_backend_logs_read_the_live_ring(client: AsyncClient, app, platform_owner):
    logging.getLogger("tally_backend.test").warning("a distinctive marker line")

    response = await client.get(
        "/v1/portal/logs/backend?source=live&q=distinctive marker",
        headers=platform_owner["headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "live"
    assert any("distinctive marker" in line["message"] for line in body["lines"])


async def test_stored_logs_survive_the_ring(client: AsyncClient, app, platform_owner):
    """The half that outlives a redeploy is read from the table, not the ring."""
    async with app.state.session_factory() as session:
        session.add(
            ServerLog(
                level="ERROR",
                logger="tally_backend.sync",
                message="chunk failed after two attempts",
                instance_id="abc123",
            )
        )
        await session.commit()

    response = await client.get(
        "/v1/portal/logs/backend?source=stored&level=WARNING",
        headers=platform_owner["headers"],
    )
    assert response.status_code == 200
    lines = response.json()["lines"]
    assert [line["message"] for line in lines] == ["chunk failed after two attempts"]


async def test_stored_level_filter_means_this_level_and_above(
    client: AsyncClient, app, platform_owner
):
    async with app.state.session_factory() as session:
        session.add_all(
            [
                ServerLog(level="WARNING", logger="a", message="warned"),
                ServerLog(level="ERROR", logger="b", message="errored"),
            ]
        )
        await session.commit()

    response = await client.get(
        "/v1/portal/logs/backend?source=stored&level=WARNING",
        headers=platform_owner["headers"],
    )
    messages = {line["message"] for line in response.json()["lines"]}
    # Picking WARNING and being shown only warnings would hide the errors,
    # which are the thing being looked for.
    assert messages == {"warned", "errored"}


# --------------------------------------------------------------------------
# Connector log ingestion
# --------------------------------------------------------------------------


async def test_ingest_writes_a_batch(app):
    ingest = ConnectorLogIngest(app.state.session_factory)
    ingest.submit(
        org_id="org1",
        connector_id="conn1",
        session_id="sess1",
        entries=[
            LogEntry(logged_at=utc_now(), level="INFO", logger="session", message="up"),
            LogEntry(logged_at=utc_now(), level="ERROR", logger="pipeline", message="down"),
        ],
    )
    await ingest.flush()

    async with app.state.session_factory() as session:
        from sqlalchemy import select

        rows = (await session.execute(select(ConnectorLog))).scalars().all()

    assert {row.message for row in rows} == {"up", "down"}
    assert {row.org_id for row in rows} == {"org1"}


async def test_a_full_buffer_refuses_the_newest_rather_than_evicting(app):
    """One flooding connector must not blank out every other customer's lines."""
    ingest = ConnectorLogIngest(app.state.session_factory, capacity=2)
    ingest.submit(
        org_id="org1",
        connector_id="conn1",
        session_id="s",
        entries=[
            LogEntry(logged_at=utc_now(), message=f"line-{index}") for index in range(5)
        ],
    )

    assert ingest.buffered == 2
    assert ingest.refused == 3
    await ingest.flush()

    async with app.state.session_factory() as session:
        from sqlalchemy import select

        kept = (await session.execute(select(ConnectorLog.message))).scalars().all()
    assert set(kept) == {"line-0", "line-1"}


async def test_a_drop_only_batch_still_records_the_gap(app):
    """A gap that reads as a quiet period sends support down the wrong path."""
    ingest = ConnectorLogIngest(app.state.session_factory)
    ingest.submit(org_id="org1", connector_id="c", session_id="s", entries=[], dropped=1842)
    await ingest.flush()

    async with app.state.session_factory() as session:
        from sqlalchemy import select

        row = (await session.execute(select(ConnectorLog))).scalars().one()
    assert row.dropped_before == 1842
    assert "1842" in row.message
    assert row.level == "WARNING"


async def test_the_link_never_lets_a_bad_batch_kill_the_socket():
    """A diagnostic side-channel must not cost a customer their reports."""
    from tally_backend.hub.link import ConnectorLink

    class Socket:
        async def send_text(self, data: str) -> None: ...
        async def receive_text(self) -> str: ...
        async def close(self, code: int = 1000, reason: str = "") -> None: ...

    def explode(link, batch):  # noqa: ANN001, ANN202
        raise RuntimeError("ingest is on fire")

    link = ConnectorLink(
        connector_id="c", org_id="o", socket=Socket(), on_logs=explode
    )

    # Malformed, and then well-formed but with a receiver that throws. Neither
    # may propagate: the caller is the socket's receive loop.
    await link.handle_message({"type": "log_batch", "entries": "not a list"})
    batch = LogBatch(entries=[LogEntry(logged_at=utc_now())])
    await link.handle_message(batch.model_dump(mode="json"))


# --------------------------------------------------------------------------
# Connector log endpoints and scoping
# --------------------------------------------------------------------------


@pytest_asyncio.fixture
async def two_accounts(app, client: AsyncClient, platform_owner):
    """Two customer businesses with logs, owned by different partners."""
    partner = await make_partner(app, client)

    async with app.state.session_factory() as session:
        mine = Organisation(name="Mine Ltd", partner_id=partner["id"])
        theirs = Organisation(name="Theirs Ltd")
        session.add_all([mine, theirs])
        await session.flush()

        session.add_all(
            [
                ConnectorLog(
                    org_id=mine.id,
                    connector_id="conn-mine",
                    level="ERROR",
                    logger="pipeline",
                    message="tally refused the export",
                    logged_at=utc_now(),
                ),
                ConnectorLog(
                    org_id=theirs.id,
                    connector_id="conn-theirs",
                    level="INFO",
                    logger="session",
                    message="secret to another partner",
                    logged_at=utc_now(),
                ),
            ]
        )
        await session.commit()
        ids = {"mine": mine.id, "theirs": theirs.id}

    return {"partner": partner, **ids}


async def test_a_partner_sees_only_their_own_accounts_logs(
    client: AsyncClient, two_accounts
):
    response = await client.get(
        "/v1/portal/logs/connector", headers=two_accounts["partner"]["headers"]
    )
    assert response.status_code == 200
    messages = {line["message"] for line in response.json()["lines"]}
    assert messages == {"tally refused the export"}


async def test_asking_for_another_partners_account_is_404_not_empty(
    client: AsyncClient, two_accounts
):
    """404, so a partner probing ids cannot learn which businesses exist."""
    response = await client.get(
        f"/v1/portal/logs/connector?org_id={two_accounts['theirs']}",
        headers=two_accounts["partner"]["headers"],
    )
    assert response.status_code == 404


async def test_an_owner_sees_every_account(client: AsyncClient, platform_owner, two_accounts):
    response = await client.get(
        "/v1/portal/logs/connector", headers=platform_owner["headers"]
    )
    messages = {line["message"] for line in response.json()["lines"]}
    assert messages == {"tally refused the export", "secret to another partner"}


async def test_connector_logs_page_backwards_with_before(
    client: AsyncClient, app, platform_owner
):
    base = datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    async with app.state.session_factory() as session:
        org = Organisation(name="Paging Ltd")
        session.add(org)
        await session.flush()
        session.add_all(
            [
                ConnectorLog(
                    org_id=org.id,
                    connector_id="c",
                    level="INFO",
                    message=f"line-{index}",
                    created_at=base + timedelta(minutes=index),
                    logged_at=base + timedelta(minutes=index),
                )
                for index in range(4)
            ]
        )
        await session.commit()

    first = await client.get(
        "/v1/portal/logs/connector?limit=2", headers=platform_owner["headers"]
    )
    lines = first.json()["lines"]
    assert [line["message"] for line in lines] == ["line-3", "line-2"]

    next_page = await client.get(
        f"/v1/portal/logs/connector?limit=2&before={lines[-1]['created_at']}",
        headers=platform_owner["headers"],
    )
    assert [line["message"] for line in next_page.json()["lines"]] == ["line-1", "line-0"]


async def test_connector_picker_reports_log_activity(
    client: AsyncClient, app, platform_owner, linked_company, registered
):
    org_id = registered["org_id"]
    connector_id = linked_company["connector_id"]

    async with app.state.session_factory() as session:
        session.add_all(
            [
                ConnectorLog(
                    org_id=org_id,
                    connector_id=connector_id,
                    level="ERROR",
                    message="one",
                    logged_at=utc_now(),
                ),
                ConnectorLog(
                    org_id=org_id,
                    connector_id=connector_id,
                    level="ERROR",
                    message="two",
                    logged_at=utc_now(),
                ),
            ]
        )
        await session.commit()

    response = await client.get(
        f"/v1/portal/accounts/{org_id}/connectors", headers=platform_owner["headers"]
    )
    assert response.status_code == 200
    row = next(item for item in response.json() if item["id"] == connector_id)
    assert row["error_count"] == 2
    assert row["last_log_at"] is not None
    assert row["label"] == "Shop PC"


async def test_tally_state_is_not_reported_for_a_machine_that_is_offline():
    """A connector that went offline leaves its last known Tally state behind.

    Rendering that as "Tally is reachable" over a PC that is switched off sends
    support to the wrong end of the problem, so the two are coupled in the one
    place the summary is built.
    """
    from tally_backend.api.v1.portal_logs import _connector_summary

    connector = Connector(id="c", org_id="o", name="Shop PC", secret_encrypted="x")
    # Column defaults are applied on flush, and this row is never flushed.
    connector.status = ConnectorStatus.ACTIVE
    connector.last_tally_online = True
    org = Organisation(id="o", name="Shop Ltd")

    offline = _connector_summary(connector, org, False, {})
    assert offline.online is False
    assert offline.tally_online is False

    online = _connector_summary(connector, org, True, {})
    assert online.tally_online is True


async def test_a_connector_that_never_logged_says_so_rather_than_nothing(
    client: AsyncClient, platform_owner, linked_company, registered
):
    response = await client.get(
        f"/v1/portal/accounts/{registered['org_id']}/connectors",
        headers=platform_owner["headers"],
    )
    row = response.json()[0]
    assert row["last_log_at"] is None
    assert row["error_count"] == 0


# --------------------------------------------------------------------------
# Activity
# --------------------------------------------------------------------------


async def test_audit_trail_is_partner_scoped(client: AsyncClient, two_accounts, app):
    from tally_backend.services.audit import record

    async with app.state.session_factory() as session:
        await record(session, action="data.read", org_id=two_accounts["mine"])
        await record(session, action="data.read", org_id=two_accounts["theirs"])
        await session.commit()

    response = await client.get("/v1/portal/audit", headers=two_accounts["partner"]["headers"])
    assert response.status_code == 200
    orgs = {entry["org_id"] for entry in response.json()}
    assert orgs == {two_accounts["mine"]}


async def test_audit_trail_names_the_actor_across_both_identity_tables(
    client: AsyncClient, app, platform_owner, registered
):
    """Portal and tenant actions share one column and no discriminator."""
    response = await client.get("/v1/portal/audit?action=login", headers=platform_owner["headers"])
    assert response.status_code == 200
    actors = [entry["actor"] for entry in response.json() if entry["actor"]]
    assert "Platform Owner" in actors


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


async def make_partner(app, client: AsyncClient) -> dict:
    password = "partner-password-long-enough"
    async with app.state.session_factory() as session:
        partner = PlatformUser(
            email="partner@channel.in",
            password_hash=hash_password(password),
            full_name="Channel Partner",
            role=PlatformRole.PARTNER,
        )
        session.add(partner)
        await session.commit()
        partner_id = partner.id

    response = await client.post(
        "/v1/portal/auth/login",
        json={"email": "partner@channel.in", "password": password},
    )
    assert response.status_code == 200, response.text
    return {
        "id": partner_id,
        "headers": {"Authorization": f"Bearer {response.json()['access_token']}"},
    }


async def test_connector_rows_are_never_a_foreign_key_to_the_log(app):
    """Deleting a connector must not delete the lines explaining why."""
    from sqlalchemy import select

    async with app.state.session_factory() as session:
        org = Organisation(name="Gone Ltd")
        session.add(org)
        await session.flush()
        connector = Connector(org_id=org.id, name="Old PC", secret_encrypted="x")
        session.add(connector)
        await session.flush()
        session.add(
            ConnectorLog(
                org_id=org.id,
                connector_id=connector.id,
                level="ERROR",
                message="why this PC was removed",
                logged_at=utc_now(),
            )
        )
        await session.commit()
        connector_id = connector.id

    async with app.state.session_factory() as session:
        await session.delete(await session.get(Connector, connector_id))
        await session.commit()

    async with app.state.session_factory() as session:
        rows = (await session.execute(select(ConnectorLog))).scalars().all()
    assert [row.message for row in rows] == ["why this PC was removed"]
