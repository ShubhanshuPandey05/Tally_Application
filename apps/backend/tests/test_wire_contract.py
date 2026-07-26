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
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

FIXTURES = (
    Path(__file__).resolve().parents[3] / "apps" / "mobile" / "test" / "fixtures"
)

#: Pinned so fixtures do not churn daily. The sample vouchers are generated
#: relative to this date and every endpoint is asked for it explicitly.
AS_OF = date(2026, 3, 15)

#: Freshness stamps are wall-clock by nature; the app's parsing of them is
#: tested separately. Only the *shape* is pinned here.
_PLACEHOLDER_TIME = "2026-03-15T00:00:00+00:00"


def normalise(value: Any) -> Any:
    """Strip the parts that legitimately change between runs."""
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if key in {"refreshed_at", "last_seen_at"} and item is not None:
                result[key] = _PLACEHOLDER_TIME
            elif key == "age_seconds":
                result[key] = 0.0
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
        path.write_text(rendered, encoding="utf-8")
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
