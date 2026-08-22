"""The company guard on the CLI paths.

``livecheck`` and ``diagnose`` hold a bare :class:`TallyClient` and no pipeline,
so they cannot reach the guard the job executor uses. They wrap the client in
:class:`GuardedClient` instead. These tests pin that the wrapper refuses the
same reads the executor refuses, and -- the reason it exists at all -- that it
keeps refusing them for the whole length of a run rather than only at the start.
"""

from __future__ import annotations

import httpx
import pytest
from tally_core.tally import TallyClient, TallyConfig, get_query
from tally_core.tally.errors import CompanyNotLoadedError

from tally_connector.livecheck import Report, run
from tally_connector.loaded import GuardedClient, company_key

FAST_TALLY = TallyConfig(retry_backoff_seconds=0.0, max_attempts=1)

LEDGERS_XML = (
    "<ENVELOPE><BODY><DATA><COLLECTION>"
    "<LEDGER NAME='Cash'><NAME>Cash</NAME><CLOSINGBALANCE>-100.00</CLOSINGBALANCE></LEDGER>"
    "</COLLECTION></DATA></BODY></ENVELOPE>"
)


def company_list_xml(*names: str) -> str:
    members = "".join(
        f"<COMPANY NAME='{name}'><NAME>{name}</NAME></COMPANY>" for name in names
    )
    return f"<ENVELOPE><BODY><DATA><COLLECTION>{members}</COLLECTION></DATA></BODY></ENVELOPE>"


def guarded_for(open_companies, **kwargs) -> tuple[GuardedClient, list[str]]:
    """A GuardedClient over a fake Tally. ``open_companies`` may be a callable.

    Passing a callable is how a test closes a company part-way through a run:
    the fake is re-asked on every probe, exactly as Tally would be.
    """
    seen: list[str] = []
    resolve = open_companies if callable(open_companies) else (lambda: open_companies)

    def handler(request: httpx.Request) -> httpx.Response:
        if "TFCompanies" in request.content.decode("utf-8"):
            seen.append("companies.list")
            return httpx.Response(200, text=company_list_xml(*resolve()))
        seen.append("read")
        return httpx.Response(200, text=LEDGERS_XML)

    client = TallyClient(
        FAST_TALLY, client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return GuardedClient(client, **kwargs), seen


async def read_ledgers(tally: GuardedClient, company: str):
    query = get_query("ledgers.list")
    return await tally.execute(query, query.validate_params({"company": company}))


# --------------------------------------------------------------------------
# The wrapper refuses what the executor refuses
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_closed_company_is_refused_on_the_cli_too():
    tally, _ = guarded_for(("Acme",))
    with pytest.raises(CompanyNotLoadedError):
        await read_ledgers(tally, "Ghost Traders")


@pytest.mark.asyncio
async def test_the_read_never_reaches_tally():
    """The whole point: a voucher read for a closed company crashes Tally."""
    tally, seen = guarded_for(("Acme",))
    with pytest.raises(CompanyNotLoadedError):
        await read_ledgers(tally, "Ghost Traders")
    assert "read" not in seen


@pytest.mark.asyncio
async def test_an_open_company_is_read_normally():
    tally, _ = guarded_for(("Acme",))
    ledgers = await read_ledgers(tally, "Acme")
    assert [lg.name for lg in ledgers] == ["Cash"]


@pytest.mark.asyncio
async def test_company_discovery_passes_straight_through():
    """The guard's own probe must not be guarded, or nothing could ever run."""
    tally, _ = guarded_for(())
    query = get_query("companies.list")
    assert await tally.execute(query, query.validate_params({})) == []


# --------------------------------------------------------------------------
# The reason the CLI needed it: a run lasts minutes
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_closing_the_company_mid_run_stops_the_next_read():
    """livecheck's one-off check at the start cannot see this happen.

    Staleness is forced rather than waited out: the TTL is 30s and the clock
    this guard reads has coarse enough granularity on Windows that a zero TTL
    can still measure as fresh. ``invalidate`` takes the same path an expired
    TTL does -- both leave the guard with nothing cached to trust.

    Note what this does *not* claim: inside the TTL window the close is not
    seen, which is the deliberate cost of not probing before every read.
    """
    still_open = {"Acme"}
    tally, seen = guarded_for(lambda: tuple(still_open))

    await read_ledgers(tally, "Acme")

    still_open.clear()  # the operator closes it while the check is running
    await tally.loaded.invalidate()

    with pytest.raises(CompanyNotLoadedError):
        await read_ledgers(tally, "Acme")
    assert seen.count("read") == 1


@pytest.mark.asyncio
async def test_the_open_set_is_cached_between_reads():
    """A livecheck runs ~10 queries; it must not add ~10 probes to Tally's queue."""
    tally, seen = guarded_for(("Acme",))
    for _ in range(4):
        await read_ledgers(tally, "Acme")
    assert seen.count("companies.list") == 1
    assert seen.count("read") == 4


# --------------------------------------------------------------------------
# livecheck's own reporting
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refusal_is_reported_as_a_failed_check_not_an_exception():
    """A livecheck must run every remaining check rather than abort on one."""
    tally, _ = guarded_for(("Acme",))
    report = Report()
    assert await run(tally, "ledgers.list", {"company": "Ghost"}, report) == []
    assert len(report.failed) == 1
    assert "isn't open" in report.failed[0]


@pytest.mark.parametrize(
    "asked", ["Bhtia Supermarket", "bhtia supermarket", "  Bhtia   Supermarket "]
)
def test_the_cli_matches_a_hand_typed_company_the_way_the_guard_does(asked):
    """--company differing only in case or spacing must not be a false refusal."""
    assert company_key(asked) == company_key("Bhtia Supermarket")
