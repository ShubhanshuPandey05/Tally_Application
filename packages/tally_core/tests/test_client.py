"""Transport tests: retry policy, error mapping and request serialisation."""

from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pytest

from tally_core.tally import TallyClient, TallyConfig, get_query
from tally_core.tally.errors import (
    TallyResponseError,
    TallyTimeoutError,
    TallyUnreachableError,
)

FAST = TallyConfig(retry_backoff_seconds=0.0, max_attempts=3)

COMPANIES_XML = (
    "<ENVELOPE><BODY><DATA><COLLECTION>"
    "<COMPANY NAME='Acme'><NAME>Acme</NAME></COMPANY>"
    "</COLLECTION></DATA></BODY></ENVELOPE>"
)


def client_with(handler, config: TallyConfig = FAST) -> TallyClient:
    return TallyClient(config, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_execute_maps_a_successful_response():
    async with client_with(lambda req: httpx.Response(200, text=COMPANIES_XML)) as client:
        query = get_query("companies.list")
        companies = await client.execute(query, query.validate_params({}))
    assert [c.name for c in companies] == ["Acme"]


async def test_posts_the_envelope_as_utf8_xml():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=COMPANIES_XML)

    async with client_with(handler) as client:
        query = get_query("companies.list")
        await client.execute(query, query.validate_params({}))

    assert seen[0].method == "POST"
    assert seen[0].url.host == "127.0.0.1"
    assert seen[0].url.port == 9000
    assert b"<TALLYREQUEST>Export</TALLYREQUEST>" in seen[0].content


async def test_retries_connect_errors_then_succeeds():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, text=COMPANIES_XML)

    async with client_with(handler) as client:
        query = get_query("companies.list")
        result = await client.execute(query, query.validate_params({}))

    assert attempts == 3
    assert len(result) == 1


async def test_gives_up_after_max_attempts_with_a_user_message():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(TallyUnreachableError) as exc_info:
        async with client_with(handler) as client:
            query = get_query("companies.list")
            await client.execute(query, query.validate_params({}))

    assert attempts == FAST.max_attempts
    assert "Tally is open" in exc_info.value.user_message
    assert exc_info.value.retryable is True


async def test_timeout_is_retryable_and_typed():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(TallyTimeoutError) as exc_info:
        async with client_with(handler) as client:
            query = get_query("companies.list")
            await client.execute(query, query.validate_params({}))

    assert exc_info.value.retryable is True


async def test_http_error_status_is_not_retried():
    """Tally already rejected the envelope; resending it only delays the error."""
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, text="bad request")

    with pytest.raises(TallyResponseError):
        async with client_with(handler) as client:
            query = get_query("companies.list")
            await client.execute(query, query.validate_params({}))

    assert attempts == 1


async def test_line_error_is_not_retried():
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            text=(
                "<ENVELOPE><BODY><DATA>"
                "<LINEERROR>No such company</LINEERROR>"
                "</DATA></BODY></ENVELOPE>"
            ),
        )

    with pytest.raises(TallyResponseError, match="No such company"):
        async with client_with(handler) as client:
            query = get_query("companies.list")
            await client.execute(query, query.validate_params({}))

    assert attempts == 1


async def test_heavy_queries_get_the_longer_timeout():
    seen: list[httpx.Timeout] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, text="<ENVELOPE><BODY><DATA/></BODY></ENVELOPE>")

    async with client_with(handler) as client:
        heavy = get_query("vouchers.list")
        await client.execute(
            heavy,
            heavy.validate_params(
                {"company": "Acme", "from_date": date(2025, 7, 1), "to_date": date(2025, 7, 31)}
            ),
        )
        light = get_query("companies.list")
        await client.execute(light, light.validate_params({}))

    assert seen[0]["read"] == FAST.heavy_timeout_seconds
    assert seen[1]["read"] == FAST.timeout_seconds


async def test_calls_to_tally_are_serialised():
    """Tally's gateway is single-threaded; overlapping POSTs corrupt responses."""
    concurrent = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.01)
        concurrent -= 1
        return httpx.Response(200, text=COMPANIES_XML)

    async with client_with(handler) as client:
        query = get_query("companies.list")
        await asyncio.gather(
            *(client.execute(query, query.validate_params({})) for _ in range(5))
        )

    assert peak == 1


async def test_is_alive_reports_false_when_tally_is_down():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    async with client_with(handler) as client:
        assert await client.is_alive() is False
