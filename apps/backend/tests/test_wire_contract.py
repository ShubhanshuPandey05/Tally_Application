"""The contract between the backend and the Flutter app.

Every response the app decodes is captured here from the *real* app, written to
``apps/mobile/test/fixtures``, and compared against what is checked in. The
mobile test suite then parses those same files.

This is the only thing that catches wire drift. Both halves are independently
well tested, and both would stay green if the backend renamed ``change_pct``
tomorrow -- the app's own tests would keep passing against the app's own idea of
the shape, and the failure would surface as a blank tile on a shopkeeper's
phone. The same lesson as the connector work: mocks on both sides of a boundary
agree with each other and with nothing else.

Regenerate deliberately after an intended change::

    UPDATE_WIRE_FIXTURES=1 pytest apps/backend/tests/test_wire_contract.py
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

from tally_backend.db.models import Connector, utc_now

FIXTURES = (
    Path(__file__).resolve().parents[3] / "apps" / "mobile" / "test" / "fixtures"
)

#: Pinned so fixtures do not churn daily. The sample vouchers are generated
#: relative to this date and every endpoint is asked for it explicitly.
AS_OF = date(2026, 3, 15)

#: Freshness stamps are wall-clock by nature; the app's parsing of them is
#: tested separately. Only the *shape* is pinned here.
_PLACEHOLDER_TIME = "2026-03-15T00:00:00+00:00"

#: What a *naive* timestamp normalises to. Deliberately distinct, so a
#: regression back to offset-less timestamps fails the contract diff loudly
#: instead of being papered over.
_NAIVE_TIME = "2026-03-15T00:00:00-NAIVE-NO-OFFSET"


def _is_aware(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def normalise(value: Any) -> Any:
    """Strip the parts that legitimately change between runs."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"refreshed_at", "last_seen_at"} and item is not None:
                # The instant is replaced but *whether it carries a UTC offset*
                # is not: that is part of the contract, not run-to-run noise.
                # Dart reads an offset-less string as local time, so a naive
                # timestamp reaches the phone wrong by the device's offset --
                # and this normaliser used to stamp an offset onto naive values,
                # which is precisely why the fixtures never caught it.
                result[key] = _PLACEHOLDER_TIME if _is_aware(item) else _NAIVE_TIME
            elif key == "age_seconds":
                result[key] = 0.0
            elif key == "expires_in_seconds":
                # A countdown, so it lands on 900 or 899 depending on which side
                # of a second the run started. Pinning the value would make this
                # file fail roughly half the time and teach everyone to
                # regenerate it without reading the diff.
                result[key] = 0
            elif key in {"id", "company_id", "connector_id", "org_id"} and isinstance(
                item, str
            ):
                result[key] = f"<{key}>"
            else:
                result[key] = normalise(item)
        return result
    if isinstance(value, list):
        return [normalise(item) for item in value]
    return value


def check(name: str, payload: Any) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    path = FIXTURES / f"{name}.json"
    rendered = json.dumps(normalise(payload), indent=2, sort_keys=True) + "\n"

    if os.environ.get("UPDATE_WIRE_FIXTURES") or not path.exists():
        # newline="\n" explicitly. Without it, regenerating on Windows rewrites
        # every line of every fixture with CRLF and the diff is a thousand lines
        # of nothing -- which is exactly the diff nobody reads, on the one file
        # set whose whole job is to be read when it changes.
        path.write_text(rendered, encoding="utf-8", newline="\n")
        return

    existing = path.read_text(encoding="utf-8")
    assert existing == rendered, (
        f"The wire shape of '{name}' changed.\n"
        f"The Flutter app decodes this file ({path}).\n"
        f"If the change is intended, regenerate with "
        f"UPDATE_WIRE_FIXTURES=1 and update the app's parsers to match."
    )


@pytest.fixture
def dated(fake_connector, samples):
    """Sample data anchored to [AS_OF] rather than to today."""
    samples.load(fake_connector, AS_OF)
    return fake_connector


async def test_dashboard_shape(client: AsyncClient, linked_company, dated) -> None:
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/dashboard"
        f"?as_of={AS_OF.isoformat()}",
        headers=linked_company["headers"],
    )
    assert response.status_code == 200, response.text
    check("dashboard", response.json())


async def test_dashboard_period_shape(client: AsyncClient, linked_company, dated) -> None:
    """A dashboard scoped to an explicit from/to period."""
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/dashboard"
        f"?as_of={AS_OF.isoformat()}"
        f"&from_date={(AS_OF - timedelta(days=29)).isoformat()}"
        f"&to_date={AS_OF.isoformat()}",
        headers=linked_company["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["period"]["days"] == 30
    assert body["period"]["from_date"] == (AS_OF - timedelta(days=29)).isoformat()
    assert body["period"]["to_date"] == AS_OF.isoformat()
    # The window was widened far enough to actually read the baseline, which is
    # what separates "nothing sold then" from "we did not look".
    assert body["period"]["has_baseline"] is True

    period = body["sections"]["sales"]["data"]["period"]
    assert period["total"] is not None
    assert period["previous_total"] is not None
    # The sample books hold nothing in the baseline span, so the change is
    # genuinely unanswerable and must be null rather than a 100% swing.
    assert period["change_pct"] is None

    # The trend now spans the period rather than a fixed 30 days back from
    # today -- same length here, but anchored to what was asked for.
    trend = body["sections"]["sales"]["data"]["trend"]
    assert trend[0]["date"] == (AS_OF - timedelta(days=29)).isoformat()
    assert trend[-1]["date"] == AS_OF.isoformat()

    check("dashboard_period", body)


async def test_default_dashboard_is_unchanged_by_the_period_feature(
    client: AsyncClient, linked_company, dated
) -> None:
    """No period asked for, no period fields, no widened voucher window.

    The ordinary dashboard's request params are a snapshot's identity. If
    asking for nothing started sending a period, every dashboard would miss the
    row the background refresher warms and go to the shop's Tally instead --
    a regression whose only symptom is "the app got slower".
    """
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/dashboard"
        f"?as_of={AS_OF.isoformat()}",
        headers=linked_company["headers"],
    )
    assert response.status_code == 200
    body = response.json()

    assert "period" not in body
    assert "period" not in body["sections"]["sales"]["data"]


async def test_degraded_dashboard_shape(
    client: AsyncClient, linked_company, dated
) -> None:
    """One dataset missing must still produce a renderable dashboard.

    Section-level degradation is a promise the app relies on: it draws the
    sections that worked and marks the rest unavailable. If this ever collapses
    into a whole-response error the app would show a failure page over three
    perfectly good sections.
    """
    dated.responses.pop("outstanding.bills")

    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/dashboard"
        f"?as_of={AS_OF.isoformat()}",
        headers=linked_company["headers"],
    )
    assert response.status_code == 200
    body = response.json()
    assert body["sections"]["sales"]["ok"] is True
    assert body["sections"]["receivables"]["ok"] is False
    check("dashboard_degraded", body)


async def test_daybook_shape(client: AsyncClient, linked_company, dated) -> None:
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/reports/daybook"
        f"?from_date=2026-03-01&to_date={AS_OF.isoformat()}",
        headers=linked_company["headers"],
    )
    assert response.status_code == 200, response.text
    check("daybook", response.json())


@pytest.mark.parametrize("kind", ["receivable", "payable"])
async def test_outstanding_shape(
    client: AsyncClient, linked_company, dated, kind: str
) -> None:
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/reports/outstanding"
        f"?kind={kind}&as_of={AS_OF.isoformat()}",
        headers=linked_company["headers"],
    )
    assert response.status_code == 200, response.text
    check(f"outstanding_{kind}", response.json())


@pytest.mark.parametrize("kind", ["receivable", "payable"])
async def test_outstanding_group_shape(
    client: AsyncClient, linked_company, dated, kind: str
) -> None:
    """The group report's own shape.

    Nested parties rather than a flat bill list, so it cannot share the flat
    report's fixture -- and the app decodes it with a separate parser.
    """
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/reports/outstanding/group"
        f"?kind={kind}&as_of={AS_OF.isoformat()}",
        headers=linked_company["headers"],
    )
    assert response.status_code == 200, response.text
    check(f"outstanding_group_{kind}", response.json())


async def test_stock_shape(client: AsyncClient, linked_company, dated) -> None:
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/reports/stock",
        headers=linked_company["headers"],
    )
    assert response.status_code == 200, response.text
    check("stock", response.json())


async def test_ledgers_shape(client: AsyncClient, linked_company, dated) -> None:
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/reports/ledgers",
        headers=linked_company["headers"],
    )
    assert response.status_code == 200, response.text
    check("ledgers", response.json())


async def test_slow_moving_shape(client: AsyncClient, linked_company, dated) -> None:
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/insights/slow-moving?days=90",
        headers=linked_company["headers"],
    )
    assert response.status_code == 200, response.text
    check("slow_moving", response.json())


async def test_company_and_connector_shapes(
    client: AsyncClient, linked_company, dated
) -> None:
    companies = await client.get("/v1/companies", headers=linked_company["headers"])
    assert companies.status_code == 200
    check("companies", companies.json())

    connectors = await client.get("/v1/connectors", headers=linked_company["headers"])
    assert connectors.status_code == 200
    check("connectors", connectors.json())

    me = await client.get("/v1/auth/me", headers=linked_company["headers"])
    assert me.status_code == 200
    check("me", me.json())


async def test_claim_preview_shape(client: AsyncClient, linked_company) -> None:
    """What the app shows between reading a code and granting a PC access.

    Pinned like every other decoded response, and worth pinning despite being
    four fields: it is the last screen before somebody hands a machine the
    ability to read their books, and a field that silently decoded to empty
    would turn a named computer into "That computer".
    """
    opened = await client.post(
        "/v1/pairing/claims",
        json={"hostname": "SHOP-PC", "os": "Windows 11", "connector_version": "0.3.0"},
    )
    assert opened.status_code == 201, opened.text

    preview = await client.get(
        f"/v1/connectors/claims/{opened.json()['code']}",
        headers=linked_company["headers"],
    )
    assert preview.status_code == 200, preview.text
    check("claim_preview", preview.json())


async def test_sync_status_shapes(
    app, client: AsyncClient, linked_company, dated, settings
) -> None:
    """Both states the progress screen has to render.

    Idle-with-no-history is the first-run screen; running-mid-backfill is the
    determinate bar. They are pinned together because the app switches between
    them on fields (``running``, ``history.has_history``) that a careless rename
    would leave decoding to ``false`` rather than failing.
    """
    company_id = linked_company["company_id"]

    idle = await client.get(f"/v1/companies/{company_id}/sync", headers=linked_company["headers"])
    assert idle.status_code == 200, idle.text
    check("sync_idle", idle.json())

    from tally_backend.db.models import SyncRun, SyncState, utc_now
    from tally_backend.services.sync import SyncService

    async with app.state.session_factory() as session:
        run = SyncRun(
            company_id=company_id,
            state=SyncState.RUNNING,
            total_chunks=8,
            completed_chunks=3,
            current_label="Oct 2024 - Mar 2025",
            vouchers_ingested=14_820,
            started_at=utc_now(),
            heartbeat_at=utc_now(),
        )
        session.add(run)
        state = await SyncService(session, settings).state_for(company_id)
        state.books_from = date(2022, 4, 1)
        state.backfilled_from = date(2024, 10, 1)
        state.backfilled_to = AS_OF
        state.supports_incremental = True
        await session.commit()

    running = await client.get(
        f"/v1/companies/{company_id}/sync", headers=linked_company["headers"]
    )
    assert running.status_code == 200
    body = running.json()
    assert body["running"] is True
    # Elapsed and ETA are wall-clock; only their presence and type are pinned.
    body["elapsed_seconds"] = 0.0
    body["eta_seconds"] = 0.0
    body["started_at"] = _PLACEHOLDER_TIME
    check("sync_running", body)


async def test_error_envelope_shape(client: AsyncClient, registered) -> None:
    """The one error shape the app parses.

    Pinned because every failure path in the app -- offline banners, first-run
    empty states, forced sign-out -- keys off `error.code`.
    """
    missing = await client.get(
        "/v1/companies/does-not-exist/dashboard", headers=registered["headers"]
    )
    assert missing.status_code == 404
    check("error_not_found", missing.json())

    unauthenticated = await client.get("/v1/companies")
    assert unauthenticated.status_code == 401
    check("error_unauthenticated", unauthenticated.json())

    invalid = await client.post(
        "/v1/auth/login", json={"email": "not-an-email", "password": ""}
    )
    assert invalid.status_code == 422
    check("error_invalid_request", invalid.json())


async def test_every_timestamp_reaches_the_app_with_a_utc_offset(
    app, client: AsyncClient, linked_company, dated
) -> None:
    """A naive timestamp is not a neutral one -- it is a wrong one.

    Dart's ``DateTime.parse`` treats an offset-less string as *local* time, so
    ``.toLocal()`` does nothing and the value lands wrong by exactly the
    device's UTC offset. On an IST phone this rendered a connector last seen one
    second ago as "seen 5 hours ago", and stamped every dashboard as refreshed
    five and a half hours before it was.

    SQLite returns naive datetimes even from ``DateTime(timezone=True)``
    columns, so this is one missing ``as_utc`` away at all times.
    """
    # Stamped the way a real handshake stamps it: aware in Python, stored into
    # a column SQLite will hand back naive.
    async with app.state.session_factory() as session:
        connector = await session.get(Connector, linked_company["connector_id"])
        connector.last_seen_at = utc_now()
        await session.commit()

    connectors = await client.get("/v1/connectors", headers=linked_company["headers"])
    assert connectors.status_code == 200
    seen = connectors.json()[0]["last_seen_at"]
    assert seen is not None
    assert datetime.fromisoformat(seen).tzinfo is not None, (
        f"last_seen_at must carry a UTC offset, got {seen!r}"
    )

    dashboard = await client.get(
        f"/v1/companies/{linked_company['company_id']}/dashboard"
        f"?as_of={AS_OF.isoformat()}",
        headers=linked_company["headers"],
    )
    assert dashboard.status_code == 200
    body = dashboard.json()

    stamps = [body["freshness"]["refreshed_at"]]
    stamps += [
        section["meta"]["refreshed_at"]
        for section in body["sections"].values()
        if section["ok"] and section["meta"].get("refreshed_at")
    ]
    assert stamps, "expected at least one freshness stamp to check"
    for stamp in stamps:
        assert datetime.fromisoformat(stamp).tzinfo is not None, (
            f"refreshed_at must carry a UTC offset, got {stamp!r}"
        )


# --------------------------------------------------------------------------
# Drill-down
# --------------------------------------------------------------------------
#
# These four are served from stored history, so their fixtures are captured
# after a real backfill rather than from a fresh snapshot. That is not just
# setup: it is the path production uses, and capturing them any other way would
# pin a shape the app never actually receives.


@pytest.fixture
async def backfilled(app, settings, linked_company, dated):
    """A company whose history has been read, anchored to [AS_OF]."""
    from tally_backend.services.sync import SyncCoordinator

    coordinator = SyncCoordinator(app.state.session_factory, app.state.hub, settings)
    await _run_backfill(coordinator, linked_company["company_id"])
    return linked_company


async def _run_backfill(coordinator, company_id: str) -> None:
    """Start a backfill and await it, so these tests are not timing-dependent."""
    await coordinator.start_backfill(company_id, today=AS_OF)
    task = coordinator._tasks.get(company_id)
    if task is not None:
        await task


async def _first_voucher_key(client: AsyncClient, linked_company) -> tuple[str, str]:
    """A key and date straight from the day book, the way the app gets them."""
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/reports/daybook"
        f"?from_date=2026-03-01&to_date={AS_OF.isoformat()}",
        headers=linked_company["headers"],
    )
    assert response.status_code == 200, response.text
    rows = response.json()["data"]["vouchers"]
    assert rows, "expected the day book to list something to drill into"
    # Pinned here as well as in the fixture: the whole drill-down depends on
    # every listed row carrying an identity, and a row without one is a tap the
    # app has to withhold.
    assert all(row.get("key") for row in rows)
    return rows[0]["key"], rows[0]["date"]


async def test_voucher_detail_shape(
    client: AsyncClient, backfilled, dated
) -> None:
    key, on = await _first_voucher_key(client, backfilled)

    # The ledger list first, the way the app reaches this screen: from a report
    # or a dashboard that has already read the masters. The party's address and
    # GSTIN ride on this response so a voucher can be shared as a document, and
    # they are served from that snapshot -- capturing the fixture cold would pin
    # the shape with the party block missing, as though that were the normal one.
    warm = await client.get(
        f"/v1/companies/{backfilled['company_id']}/reports/ledgers",
        headers=backfilled["headers"],
    )
    assert warm.status_code == 200, warm.text

    response = await client.get(
        f"/v1/companies/{backfilled['company_id']}/reports/voucher"
        f"?key={key}&on={on}",
        headers=backfilled["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # The point of the screen: the lines, with their sides intact.
    assert body["data"]["ledger_entries"], "a voucher detail without its lines is a stub"
    assert {e["amount"]["side"] for e in body["data"]["ledger_entries"]} <= {
        "debit",
        "credit",
    }

    # The party's own details, off the ledger master rather than off the
    # voucher. This is what puts an address on a shared invoice, and it costs
    # the shop's Tally nothing -- it comes from a snapshot already held.
    party = body["data"]["party_details"]
    assert party is not None, "the ledger snapshot was warm, so the party is known"
    assert party["name"] == body["data"]["party"]
    assert "gstin" in party and "address" in party
    # Contact only. A receipt an owner forwards to a customer must not carry
    # that customer's balance or credit limit.
    assert "closing_balance" not in party and "credit_limit" not in party

    check("voucher", body)


async def test_a_voucher_opens_while_the_backfill_is_still_running(
    client: AsyncClient, linked_company, dated, app, settings
) -> None:
    """Rows are on screen before a backfill finishes, and they must open.

    The window rule that guards a *report* -- refuse rather than under-report a
    half-read range -- would refuse these too, and the app would look broken at
    exactly the moment it is working. One voucher is either stored or it is not.
    """
    from tally_backend.services.sync import SyncCoordinator
    from tally_backend.services.voucher_store import VoucherStore

    key, on = await _first_voucher_key(client, linked_company)

    # History that holds the voucher but covers no window at all.
    coordinator = SyncCoordinator(app.state.session_factory, app.state.hub, settings)
    await _run_backfill(coordinator, linked_company["company_id"])

    async with app.state.session_factory() as session:
        from tally_backend.db.models import CompanySyncState

        state = await session.get(CompanySyncState, linked_company["company_id"])
        assert state is not None
        stored = await VoucherStore(session).find(
            linked_company["company_id"], key=key, on=date.fromisoformat(on)
        )
        assert stored is not None, "the backfill should have stored this voucher"
        # Wind the coverage back so no window is covered, leaving only the rows.
        state.backfilled_from = None
        state.backfilled_to = None
        await session.commit()

    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/reports/voucher"
        f"?key={key}&on={on}",
        headers=linked_company["headers"],
    )
    assert response.status_code == 200, response.text


async def test_a_voucher_that_is_gone_says_why(
    client: AsyncClient, backfilled, dated
) -> None:
    """Deleted or edited in Tally since the list was drawn. The user is looking
    at the row, so "not found" on its own would read as a bug in the app."""
    _, on = await _first_voucher_key(client, backfilled)

    response = await client.get(
        f"/v1/companies/{backfilled['company_id']}/reports/voucher"
        f"?key=no-such-voucher&on={on}",
        headers=backfilled["headers"],
    )
    assert response.status_code == 404, response.text
    assert "Tally" in response.json()["error"]["message"]


async def test_ledger_statement_shape(client: AsyncClient, backfilled, dated) -> None:
    # The ledger list first, exactly as the app reaches this screen: from the
    # balances report, or from a voucher line on a company whose dashboard has
    # already read the masters. The statement serves "balance today" from that
    # snapshot and never triggers a read of its own, so a fixture captured
    # without it would pin the degraded shape as though it were the normal one.
    warm = await client.get(
        f"/v1/companies/{backfilled['company_id']}/reports/ledgers",
        headers=backfilled["headers"],
    )
    assert warm.status_code == 200, warm.text

    response = await client.get(
        # A party rather than an income ledger: parties are what the app links
        # to from a bill, from a voucher line and from the balances report, and
        # they are the ledgers that actually have a master record behind them.
        f"/v1/companies/{backfilled['company_id']}/reports/ledger-statement"
        f"?ledger=Reliance%20Retail&from_date=2026-01-01&to_date={AS_OF.isoformat()}",
        headers=backfilled["headers"],
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Movement and balance are different figures and the app labels them
    # differently. Both keys must be present, or the screen quietly renders one
    # of them as the other.
    assert "net_movement" in body["data"]
    assert body["data"]["closing_balance"] is not None
    check("ledger_statement", body)


@pytest.mark.parametrize("kind", ["sales", "purchase"])
async def test_register_shape(
    client: AsyncClient, backfilled, dated, kind: str
) -> None:
    response = await client.get(
        f"/v1/companies/{backfilled['company_id']}/reports/register"
        f"?kind={kind}&from_date=2026-01-01&to_date={AS_OF.isoformat()}",
        headers=backfilled["headers"],
    )
    assert response.status_code == 200, response.text
    check(f"register_{kind}", response.json())


async def test_stock_movement_shape(client: AsyncClient, backfilled, dated) -> None:
    response = await client.get(
        f"/v1/companies/{backfilled['company_id']}/reports/stock/movement"
        f"?item=Rice&from_date=2026-01-01&to_date={AS_OF.isoformat()}",
        headers=backfilled["headers"],
    )
    assert response.status_code == 200, response.text
    check("stock_movement", response.json())
