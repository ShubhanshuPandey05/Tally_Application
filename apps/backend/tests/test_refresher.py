"""Background snapshot warming."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from tally_backend.db.models import Snapshot, utc_now
from tally_backend.services.dashboard import voucher_params
from tally_backend.services.refresher import SnapshotRefresher, _params_for


@pytest.fixture
def refresher(app, settings):
    return SnapshotRefresher(app.state.session_factory, app.state.hub, settings)


async def test_a_never_fetched_company_is_warmed(
    app, refresher, linked_company, loaded, fake_connector
) -> None:
    """The first user of the morning should not pay for a cold read."""
    assert fake_connector.calls == []

    refreshed = await refresher.sweep()
    assert refreshed > 0

    async with app.state.session_factory() as session:
        datasets = {
            row.dataset for row in (await session.execute(select(Snapshot))).scalars().all()
        }
    assert {"ledgers.list", "vouchers.list", "outstanding.bills", "stock_items.list"} <= datasets


async def test_a_fresh_company_is_left_alone(
    app, refresher, linked_company, loaded, fake_connector
) -> None:
    """A sweep must not re-read what was just read."""
    await refresher.sweep()
    after_first = len(fake_connector.calls)

    await refresher.sweep()
    assert len(fake_connector.calls) == after_first


async def test_an_aged_company_is_refreshed_again(
    app, refresher, linked_company, loaded, fake_connector
) -> None:
    await refresher.sweep()
    after_first = len(fake_connector.calls)

    async with app.state.session_factory() as session:
        for snapshot in (await session.execute(select(Snapshot))).scalars().all():
            snapshot.refreshed_at = utc_now() - timedelta(hours=12)
        await session.commit()

    await refresher.sweep()
    assert len(fake_connector.calls) > after_first


async def test_an_offline_connector_is_skipped_not_retried(
    app, refresher, linked_company, loaded, fake_connector
) -> None:
    """An overnight sweep must not spend its budget on switched-off PCs."""
    fake_connector.online = False

    class OfflineHub:
        async def is_online(self, connector_id: str) -> bool:
            return False

    refresher._hub = OfflineHub()  # type: ignore[assignment]

    assert await refresher.sweep() == 0
    assert fake_connector.calls == []


async def test_the_batch_size_bounds_one_sweep(
    app, settings, refresher, linked_company, loaded
) -> None:
    """A restart must not stampede the whole fleet at once."""
    from tally_backend.db.models import Company

    async with app.state.session_factory() as session:
        connector_id = linked_company["connector_id"]
        company = await session.get(Company, linked_company["company_id"])
        for i in range(10):
            session.add(
                Company(
                    org_id=company.org_id,
                    connector_id=connector_id,
                    tally_name=f"Extra Company {i}",
                )
            )
        await session.commit()

    settings.refresh_batch_size = 3
    due = await refresher._due_companies()
    assert len(due) == 3


def test_warmed_params_match_what_the_dashboard_asks_for() -> None:
    """The refresher and the dashboard must agree exactly.

    ``params_key`` is part of a snapshot's identity, so warming a different
    voucher window writes rows the dashboard will never read -- and the only
    symptom would be that warming silently achieves nothing.
    """
    from datetime import date

    today = date(2026, 7, 23)
    assert _params_for("vouchers.list", today) == voucher_params(today)


async def test_warming_makes_the_dashboard_a_pure_snapshot_read(
    app, refresher, client, linked_company, loaded, fake_connector
) -> None:
    """The payoff: after a sweep, opening the app costs zero Tally reads."""
    await refresher.sweep()
    after_warm = len(fake_connector.calls)

    response = await client.get(
        f"/v1/companies/{linked_company['company_id']}/dashboard",
        headers=linked_company["headers"],
    )
    assert response.status_code == 200
    assert response.json()["sections"]["sales"]["ok"] is True
    assert len(fake_connector.calls) == after_warm, (
        "a warmed dashboard must not touch the connector at all"
    )
