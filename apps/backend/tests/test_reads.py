"""Snapshot behaviour: freshness, staleness, and protecting the shop's Tally.

These assert on *how often the connector was called*, not just on response
bodies. The load characteristic is the entire justification for the snapshot
design, and it is invisible from the payload alone -- a proxy-everything backend
would pass every body assertion here while melting a customer's PC.
"""

from __future__ import annotations

import asyncio
from datetime import date

from httpx import AsyncClient

from tally_backend.db.models import Snapshot, utc_now


async def test_first_read_hits_tally_and_later_reads_do_not(
    client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    """Ten staff opening the dashboard must cost one trip to Tally, not ten."""
    headers = linked_company["headers"]
    url = f"/v1/companies/{linked_company['company_id']}/dashboard"

    first = await client.get(url, headers=headers)
    assert first.status_code == 200
    after_first = fake_connector.call_count("vouchers.list")
    assert after_first == 1

    for _ in range(9):
        assert (await client.get(url, headers=headers)).status_code == 200

    assert fake_connector.call_count("vouchers.list") == 1, (
        "repeat dashboard opens must be served from the snapshot"
    )


async def test_every_response_states_its_own_freshness(
    client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/dashboard",
        headers=linked_company["headers"],
    )
    body = response.json()

    assert "refreshed_at" in body["freshness"]
    for name, section in body["sections"].items():
        if section["ok"]:
            assert "refreshed_at" in section["meta"], f"{name} has no freshness stamp"


async def test_stale_data_is_served_when_tally_goes_offline(
    client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    """Old-but-real numbers beat an error screen -- as long as they are labelled."""
    headers = linked_company["headers"]
    url = f"/v1/companies/{linked_company['company_id']}/dashboard"

    assert (await client.get(url, headers=headers)).status_code == 200

    fake_connector.online = False
    response = await client.get(f"{url}?mode=live", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["sections"]["sales"]["ok"] is True
    assert body["freshness"]["is_stale"] is True
    assert body["freshness"]["connector_online"] is False


async def test_a_throttled_refresh_still_reports_the_connector_honestly(
    app, settings, client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    """Freshness metadata must never be assumed, only observed.

    A live refresh that gets throttled returns the snapshot without contacting
    the connector. Reporting ``connector_online: true`` there would be a guess
    -- and it is the exact field that decides whether the app shows an offline
    banner, so a wrong answer tells an owner their numbers are current while
    their PC is switched off. Found by the end-to-end smoke run, not by any
    single-component test.
    """
    settings.min_refresh_interval_seconds = 3600
    headers = linked_company["headers"]
    url = f"/v1/companies/{linked_company['company_id']}/reports/stock"

    assert (await client.get(url, headers=headers)).status_code == 200
    calls_before = fake_connector.call_count("stock_items.list")

    fake_connector.online = False
    response = await client.get(f"{url}?mode=live", headers=headers)

    assert response.status_code == 200
    # The throttle held, so Tally really was not contacted...
    assert fake_connector.call_count("stock_items.list") == calls_before
    # ...and the response says so rather than assuming the best.
    assert response.json()["meta"]["connector_online"] is False


async def test_no_snapshot_and_no_connector_is_an_honest_error(
    client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    """A first run against an offline PC must not fabricate zeroes.

    Rendering an empty dashboard would read as "you sold nothing today", which
    is a materially different claim from "we could not reach your Tally".
    """
    fake_connector.online = False
    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/dashboard",
        headers=linked_company["headers"],
    )

    assert response.status_code == 200
    body = response.json()
    assert body["freshness"]["available"] is False
    assert all(not section["ok"] for section in body["sections"].values())


async def test_cached_mode_never_touches_the_connector(
    client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    headers = linked_company["headers"]
    url = f"/v1/companies/{linked_company['company_id']}/reports/stock"

    await client.get(url, headers=headers)
    baseline = fake_connector.call_count("stock_items.list")

    await client.get(f"{url}?mode=cached", headers=headers)
    assert fake_connector.call_count("stock_items.list") == baseline


async def test_live_mode_refreshes_when_the_snapshot_has_aged(
    app, client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    headers = linked_company["headers"]
    url = f"/v1/companies/{linked_company['company_id']}/reports/stock"

    await client.get(url, headers=headers)
    baseline = fake_connector.call_count("stock_items.list")

    await client.get(f"{url}?mode=live", headers=headers)
    assert fake_connector.call_count("stock_items.list") == baseline + 1


async def test_refresh_throttling_protects_tally_from_pull_to_refresh(
    app, settings, client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    """Holding pull-to-refresh must not become a one-tap DoS on the shop's PC."""
    settings.min_refresh_interval_seconds = 300

    headers = linked_company["headers"]
    url = f"/v1/companies/{linked_company['company_id']}/reports/stock?mode=live"

    await client.get(url, headers=headers)
    after_first = fake_connector.call_count("stock_items.list")

    for _ in range(5):
        assert (await client.get(url, headers=headers)).status_code == 200

    assert fake_connector.call_count("stock_items.list") == after_first


async def test_stale_snapshots_are_refreshed_automatically(
    app, settings, client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    headers = linked_company["headers"]
    url = f"/v1/companies/{linked_company['company_id']}/reports/stock"

    await client.get(url, headers=headers)
    baseline = fake_connector.call_count("stock_items.list")

    # Age every snapshot past the staleness threshold.
    from datetime import timedelta

    from sqlalchemy import select

    async with app.state.session_factory() as session:
        for snapshot in (await session.execute(select(Snapshot))).scalars().all():
            snapshot.refreshed_at = utc_now() - timedelta(hours=6)
        await session.commit()

    await client.get(url, headers=headers)
    assert fake_connector.call_count("stock_items.list") == baseline + 1


async def test_different_params_do_not_share_a_snapshot(
    client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    """A January report must never be answered with February's data."""
    headers = linked_company["headers"]
    base = f"/v1/companies/{linked_company['company_id']}/reports/daybook"

    await client.get(f"{base}?from_date=2026-01-01&to_date=2026-01-31", headers=headers)
    await client.get(f"{base}?from_date=2026-02-01&to_date=2026-02-28", headers=headers)

    windows = {
        (params.get("from_date"), params.get("to_date"))
        for query, params in fake_connector.calls
        if query == "vouchers.list"
    }
    assert ("2026-01-01", "2026-01-31") in windows
    assert ("2026-02-01", "2026-02-28") in windows


async def test_concurrent_dashboard_opens_collapse_into_one_read(
    app, linked_company, fake_connector, settings
) -> None:
    """Five people opening the app at once is one export, not five.

    Exercised at the hub rather than over HTTP because each HTTP request gets
    its own session and would serialise on SQLite anyway -- which would hide
    whether coalescing is doing anything.
    """
    from tally_backend.hub import ConnectorHub

    hub = ConnectorHub(settings)
    calls = {"n": 0}

    async def slow_dispatch(connector_id, query, params, timeout, cache_ttl):
        calls["n"] += 1
        await asyncio.sleep(0.05)
        from tally_core.protocol import JobResult

        return JobResult.success("job", [{"name": "Cash"}])

    hub._dispatch = slow_dispatch  # type: ignore[method-assign]

    results = await asyncio.gather(
        *[
            hub.run(connector_id="c1", query="ledgers.list", params={"company": "X"})
            for _ in range(5)
        ]
    )

    assert all(r.ok for r in results)
    assert calls["n"] == 1, "identical concurrent reads must share one round trip"


async def test_daybook_window_is_clamped(
    client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    """A ten-year day book would block the shop's Tally for minutes."""
    await client.get(
        f"/v1/companies/{linked_company['company_id']}/reports/daybook"
        "?from_date=2016-01-01&to_date=2026-01-01",
        headers=linked_company["headers"],
    )

    window = next(
        params for query, params in fake_connector.calls if query == "vouchers.list"
    )
    span = date.fromisoformat(window["to_date"]) - date.fromisoformat(window["from_date"])
    assert span.days <= 400


# --------------------------------------------------------------------------
# Voucher classification, end to end
# --------------------------------------------------------------------------


def renamed_sales_payload(day: date) -> list[dict]:
    """One sale, under the name a real shop gives its sales voucher type.

    This is what the connector genuinely sends: the voucher names its type and
    nothing more. Live TallyPrime puts no <PARENT> on a voucher row, so `kind`
    arrives as "other" and only the voucher-type master can correct it.
    """
    return [
        {
            "voucher_number": "1",
            "voucher_type": "Tax Invoice",
            "kind": "other",
            "date": day.isoformat(),
            "party_name": "Reliance Retail",
            "amount": {"amount": "11800.00", "side": "credit"},
            "ledger_entries": [
                {
                    "ledger_name": "Reliance Retail",
                    "amount": {"amount": "11800.00", "side": "debit"},
                }
            ],
            "inventory_entries": [],
        }
    ]


async def test_a_renamed_sales_type_still_reaches_todays_sales(
    client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    """The reported bug: a shop sells all day and the dashboard shows nothing.

    Every default-named voucher type classified fine, which is why this survived
    the fixtures and the live check -- the test company used Tally's own names.
    """
    today = date.today()
    fake_connector.set("vouchers.list", renamed_sales_payload(today))

    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/dashboard",
        headers=linked_company["headers"],
    )

    assert response.status_code == 200, response.text
    sales = response.json()["sections"]["sales"]
    assert sales["ok"] is True
    assert sales["data"]["today"]["amount"] == "11800.00"


async def test_the_daybook_kind_filter_finds_a_renamed_sale(
    client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    today = date.today()
    fake_connector.set("vouchers.list", renamed_sales_payload(today))

    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/reports/daybook"
        f"?from_date={today.isoformat()}&to_date={today.isoformat()}&kind=sales",
        headers=linked_company["headers"],
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["voucher_count"] == 1


async def test_the_dashboard_survives_an_unavailable_voucher_type_read(
    client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    """Classification is a best-effort improvement, never a new hard dependency.

    If the voucher-type read fails, sales must still be reported using the
    name-based kinds -- degraded, not blank.
    """
    fake_connector.responses.pop("voucher_types.list")

    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/dashboard",
        headers=linked_company["headers"],
    )

    assert response.status_code == 200, response.text
    sales = response.json()["sections"]["sales"]
    assert sales["ok"] is True
    assert sales["data"]["today"]["amount"] == "11800.00"


async def test_classifying_does_not_cost_an_extra_trip_to_tally(
    client: AsyncClient, linked_company, fake_connector, loaded
) -> None:
    """The voucher-type map is a snapshot like everything else.

    Reading it per dashboard open would put a second export on the shop's PC for
    data that changes about once a year.
    """
    url = f"/v1/companies/{linked_company['company_id']}/dashboard"
    for _ in range(5):
        assert (await client.get(url, headers=linked_company["headers"])).status_code == 200

    assert fake_connector.call_count("voucher_types.list") == 1
