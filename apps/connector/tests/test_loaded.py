"""The company guard.

TallyPrime answers a read for a company it does not have open with an empty
collection and ``STATUS 1`` -- success, no error, no data. Verified against a
live instance on 2026-08-09: with the company closed, ``VoucherType`` and
``Ledger`` both returned zero members, and a ``Voucher`` read crashed Tally with
``c0000005``. These tests pin the guard that turns that into a real error before
the request is sent.
"""

from __future__ import annotations

import httpx
import pytest
from tally_core.tally.errors import CompanyNotLoadedError, TallyUnreachableError

from tally_connector.cache import ResponseCache
from tally_connector.executor import JobExecutor
from tally_connector.loaded import LoadedCompanies
from tally_connector.protocol import JobRequest


def company_list_xml(*names: str) -> str:
    members = "".join(
        f"<COMPANY NAME='{name}'><NAME>{name}</NAME></COMPANY>" for name in names
    )
    return f"<ENVELOPE><BODY><DATA><COLLECTION>{members}</COLLECTION></DATA></BODY></ENVELOPE>"


LEDGERS_XML = (
    "<ENVELOPE><BODY><DATA><COLLECTION>"
    "<LEDGER NAME='Cash'><NAME>Cash</NAME><CLOSINGBALANCE>-100.00</CLOSINGBALANCE></LEDGER>"
    "</COLLECTION></DATA></BODY></ENVELOPE>"
)

#: What Tally really sends for a company that is not open: a well-formed,
#: successful response whose collection is empty.
EMPTY_XML = "<ENVELOPE><BODY><DATA><COLLECTION></COLLECTION></DATA></BODY></ENVELOPE>"


def scripted(open_companies: tuple[str, ...], *, then: str = LEDGERS_XML):
    """A fake Tally that answers the guard honestly and everything else with `then`."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode("utf-8")
        if "TFCompanies" in body:
            seen.append("companies.list")
            return httpx.Response(200, text=company_list_xml(*open_companies))
        seen.append("read")
        return httpx.Response(200, text=then)

    handler.seen = seen  # type: ignore[attr-defined]
    return handler


def executor_for(handler, make_pipeline, **guard_kwargs) -> JobExecutor:
    pipeline = make_pipeline(handler)
    return JobExecutor(pipeline, ResponseCache(), LoadedCompanies(pipeline, **guard_kwargs))


def ledger_job(company: str, job_id: str = "j1") -> JobRequest:
    return JobRequest(job_id=job_id, query="ledgers.list", params={"company": company})


# --------------------------------------------------------------------------
# Refusing
# --------------------------------------------------------------------------


async def test_a_closed_company_is_refused_instead_of_read_as_empty(make_pipeline):
    """The bug this exists for: an unopened company read as a company with no data."""
    handler = scripted(("D.D Enterprises",), then=EMPTY_XML)
    result = await executor_for(handler, make_pipeline).run(ledger_job("Bhtia Supermarket"))

    assert result.ok is False
    assert result.error.code == "company_not_loaded"
    # The whole point: not an empty success.
    assert result.data() is None


async def test_the_query_never_reaches_tally_when_the_company_is_closed(make_pipeline):
    """A voucher read for a closed company crashes TallyPrime, so it must not be sent."""
    handler = scripted(("D.D Enterprises",))
    executor = executor_for(handler, make_pipeline)

    await executor.run(
        JobRequest(
            job_id="j1",
            query="vouchers.list",
            params={
                "company": "Bhtia Supermarket",
                "from_date": "2026-01-01",
                "to_date": "2026-08-09",
            },
        )
    )

    assert handler.seen == ["companies.list", "companies.list"], (
        "expected only the guard's own checks -- the voucher read must not be sent"
    )


async def test_the_message_names_the_company_and_what_is_open(make_pipeline):
    handler = scripted(("D.D Enterprises",), then=EMPTY_XML)
    result = await executor_for(handler, make_pipeline).run(ledger_job("Bhtia Supermarket"))

    assert "Bhtia Supermarket" in result.error.message
    # Spelled as Tally spells it, not as the guard normalises it -- this line
    # gets read aloud on a support call.
    assert "D.D Enterprises" in result.error.message
    # The shop owner is told what to do, not what went wrong internally.
    assert "open it" in result.error.user_message.lower()


async def test_refusal_is_retryable_so_the_phone_still_gets_its_snapshot(make_pipeline):
    """Opening a company fixes it, so this must not be a permanent failure."""
    handler = scripted(("D.D Enterprises",), then=EMPTY_XML)
    result = await executor_for(handler, make_pipeline).run(ledger_job("Bhtia Supermarket"))

    assert result.error.retryable is True


async def test_no_company_open_at_all_is_still_refused(make_pipeline):
    handler = scripted((), then=EMPTY_XML)
    result = await executor_for(handler, make_pipeline).run(ledger_job("Bhtia Supermarket"))

    assert result.ok is False
    assert result.error.code == "company_not_loaded"


# --------------------------------------------------------------------------
# Letting reads through
# --------------------------------------------------------------------------


async def test_an_open_company_is_read_normally(make_pipeline):
    handler = scripted(("Bhtia Supermarket",))
    result = await executor_for(handler, make_pipeline).run(ledger_job("Bhtia Supermarket"))

    assert result.ok is True
    assert result.data()[0]["name"] == "Cash"


@pytest.mark.parametrize(
    "asked",
    ["bhtia supermarket", "BHTIA SUPERMARKET", "  Bhtia Supermarket  ", "Bhtia  Supermarket"],
)
async def test_company_names_match_loosely(make_pipeline, asked):
    """The name round-trips through a database and a JSON payload; casing drifts."""
    handler = scripted(("Bhtia Supermarket",))
    result = await executor_for(handler, make_pipeline).run(ledger_job(asked))

    assert result.ok is True


async def test_company_discovery_is_not_guarded(make_pipeline):
    """`companies.list` is the check itself -- guarding it would recurse."""
    handler = scripted((), then=company_list_xml("Acme"))
    result = await executor_for(handler, make_pipeline).run(
        JobRequest(job_id="j1", query="companies.list", params={})
    )

    assert result.ok is True
    assert handler.seen == ["companies.list"], "the guard must not add a check of its own"


# --------------------------------------------------------------------------
# Cost and recovery
# --------------------------------------------------------------------------


async def test_the_open_set_is_cached_across_reads(make_pipeline):
    """A dashboard fires several reads; they must not each re-ask."""
    handler = scripted(("Bhtia Supermarket",))
    executor = executor_for(handler, make_pipeline)

    for i in range(4):
        assert (await executor.run(ledger_job("Bhtia Supermarket", f"j{i}"))).ok is True

    assert handler.seen.count("companies.list") == 1
    assert handler.seen.count("read") == 4


async def test_opening_a_company_recovers_without_waiting_for_the_ttl(make_pipeline):
    """The operator opens the company; the very next read must work."""
    open_now = {"D.D Enterprises"}

    def handler(request: httpx.Request) -> httpx.Response:
        if "TFCompanies" in request.content.decode("utf-8"):
            return httpx.Response(200, text=company_list_xml(*sorted(open_now)))
        return httpx.Response(200, text=LEDGERS_XML)

    # A long TTL proves recovery does not come from the cache simply expiring.
    executor = executor_for(handler, make_pipeline, ttl_seconds=3600.0)

    first = await executor.run(ledger_job("Bhtia Supermarket", "j1"))
    assert first.ok is False

    open_now.add("Bhtia Supermarket")

    second = await executor.run(ledger_job("Bhtia Supermarket", "j2"))
    assert second.ok is True


async def test_a_stale_yes_is_not_re_checked(make_pipeline):
    """Only a *miss* forces a re-read. A hit must stay free."""
    handler = scripted(("Bhtia Supermarket",))
    executor = executor_for(handler, make_pipeline, ttl_seconds=3600.0)

    await executor.run(ledger_job("Bhtia Supermarket", "j1"))
    await executor.run(ledger_job("Bhtia Supermarket", "j2"))

    assert handler.seen.count("companies.list") == 1


# --------------------------------------------------------------------------
# When the check itself cannot be made
# --------------------------------------------------------------------------


async def test_an_unreachable_tally_reports_itself_not_a_closed_company(make_pipeline):
    """Guessing "not loaded" would send the shop after the wrong problem."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = await executor_for(handler, make_pipeline).run(ledger_job("Bhtia Supermarket"))

    assert result.ok is False
    assert result.error.code == TallyUnreachableError.code


async def test_the_guard_does_not_mask_a_real_read_failure(make_pipeline):
    """Guard passes, read fails: the read's error is what surfaces."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "TFCompanies" in request.content.decode("utf-8"):
            return httpx.Response(200, text=company_list_xml("Bhtia Supermarket"))
        return httpx.Response(500, text="boom")

    result = await executor_for(handler, make_pipeline).run(ledger_job("Bhtia Supermarket"))

    assert result.ok is False
    assert result.error.code != CompanyNotLoadedError.code
