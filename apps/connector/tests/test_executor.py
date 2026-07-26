"""Executor tests: caching, single-flight, deadlines and the offline fallback."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from tally_connector.cache import ResponseCache
from tally_connector.protocol import JobRequest


def job(**overrides) -> JobRequest:
    defaults = {"job_id": "j1", "query": "companies.list", "params": {}}
    return JobRequest(**{**defaults, **overrides})


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


async def test_runs_a_query_and_serialises_the_result(make_executor, ok_handler):
    result = await make_executor(ok_handler).run(job())

    assert result.ok is True
    assert result.from_cache is False
    assert result.data() == [
        {
            "name": "Acme",
            "guid": None,
            "financial_year_from": "2024-04-01",
            "books_from": None,
            "gstin": None,
            "state": None,
            "base_currency": "INR",
        }
    ]


async def test_amounts_serialise_as_strings_not_floats(make_executor):
    """Binary floats would quietly corrupt rupee figures; Decimal must survive."""
    xml = (
        "<ENVELOPE><BODY><DATA><COLLECTION>"
        # Negative is a DEBIT in Tally's convention -- cash on hand.
        "<LEDGER NAME='Cash'><NAME>Cash</NAME><CLOSINGBALANCE>-127450.75</CLOSINGBALANCE></LEDGER>"
        "</COLLECTION></DATA></BODY></ENVELOPE>"
    )
    executor = make_executor(lambda r: httpx.Response(200, text=xml))
    result = await executor.run(job(query="ledgers.list", params={"company": "Acme"}))

    balance = result.data()[0]["closing_balance"]
    assert balance["amount"] == "127450.75"
    assert isinstance(balance["amount"], str)
    assert balance["side"] == "debit"


# --------------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------------


async def test_cache_hit_skips_tally(make_executor, companies_xml):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=companies_xml)

    executor = make_executor(handler)
    first = await executor.run(job(cache_ttl_seconds=60))
    second = await executor.run(job(job_id="j2", cache_ttl_seconds=60))

    assert calls == 1
    assert first.from_cache is False
    assert second.from_cache is True
    assert second.data() == first.data()


async def test_zero_ttl_forces_a_fresh_read(make_executor, companies_xml):
    """Pull-to-refresh must actually reach Tally."""
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=companies_xml)

    executor = make_executor(handler)
    await executor.run(job(cache_ttl_seconds=60))
    result = await executor.run(job(job_id="j2", cache_ttl_seconds=0))

    assert calls == 2
    assert result.from_cache is False


async def test_different_params_do_not_share_a_cache_entry(make_executor):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text="<ENVELOPE><BODY><DATA/></BODY></ENVELOPE>")

    executor = make_executor(handler)
    base = {"query": "ledgers.list", "cache_ttl_seconds": 60}
    await executor.run(job(**base, params={"company": "Acme"}))
    await executor.run(job(job_id="j2", **base, params={"company": "Other Co"}))

    assert calls == 2


async def test_concurrent_identical_jobs_hit_tally_once(make_executor, companies_xml):
    """Tally serves one request at a time; five dashboard tiles must not queue five exports."""
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return httpx.Response(200, text=companies_xml)

    executor = make_executor(handler)
    results = await asyncio.gather(
        *(executor.run(job(job_id=f"j{i}", cache_ttl_seconds=60)) for i in range(5))
    )

    assert calls == 1
    assert all(r.ok for r in results)
    assert all(r.data() == results[0].data() for r in results)


# --------------------------------------------------------------------------
# Failures
# --------------------------------------------------------------------------


async def test_unknown_query_is_reported_as_version_skew(make_executor, ok_handler):
    result = await make_executor(ok_handler).run(job(query="vouchers.create"))

    assert result.ok is False
    assert result.error.code == "unknown_query"
    assert "out of date" in result.error.user_message


async def test_invalid_params_are_rejected_before_touching_tally(make_executor, companies_xml):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=companies_xml)

    executor = make_executor(handler)
    result = await executor.run(job(query="ledgers.list", params={}))  # missing company

    assert calls == 0
    assert result.ok is False
    assert result.error.code == "invalid_params"


async def test_tally_offline_produces_a_retryable_error(make_executor):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = await make_executor(handler).run(job())

    assert result.ok is False
    assert result.error.code == "tally_unreachable"
    assert result.error.retryable is True


async def test_deadline_is_enforced(make_executor, companies_xml):
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, text=companies_xml)

    result = await make_executor(handler).run(job(deadline_seconds=0.1))

    assert result.ok is False
    assert result.error.code == "deadline_exceeded"


async def test_executor_never_raises(make_executor):
    """The session loop depends on this: one bad job must not end the session."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="this is not xml at all")

    result = await make_executor(handler).run(job())
    assert result.ok is False


# --------------------------------------------------------------------------
# Offline fallback
# --------------------------------------------------------------------------


async def test_serves_stale_data_when_tally_goes_offline(make_executor, companies_xml):
    """CLAUDE.md: if Tally is offline, show last synced data rather than failing."""
    online = True

    def handler(request: httpx.Request) -> httpx.Response:
        if not online:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, text=companies_xml)

    cache = ResponseCache()
    executor = make_executor(handler, cache=cache)

    fresh = await executor.run(job(cache_ttl_seconds=0.01))
    await asyncio.sleep(0.05)  # let the entry go stale
    online = False
    stale = await executor.run(job(job_id="j2", cache_ttl_seconds=0.01))

    assert stale.ok is True
    assert stale.from_cache is True
    assert stale.data() == fresh.data()


async def test_no_stale_fallback_for_non_retryable_errors(make_executor, companies_xml):
    """A malformed request would fail identically next time; old data would hide the bug."""
    executor = make_executor(lambda r: httpx.Response(200, text=companies_xml))
    await executor.run(job(cache_ttl_seconds=60))

    result = await executor.run(job(job_id="j2", query="vouchers.create"))
    assert result.ok is False


async def test_stale_fallback_needs_a_prior_success(make_executor):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    result = await make_executor(handler).run(job())
    assert result.ok is False
    assert result.error.code == "tally_unreachable"


@pytest.mark.parametrize("ttl", [0.0, 60.0])
async def test_results_are_always_stored_for_offline_use(make_executor, ok_handler, ttl):
    cache = ResponseCache()
    executor = make_executor(ok_handler, cache=cache)
    await executor.run(job(cache_ttl_seconds=ttl))

    assert (await cache.stats())["entries"] == 1
