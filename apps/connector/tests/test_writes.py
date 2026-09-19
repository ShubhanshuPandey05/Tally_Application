"""The connector's write path.

Most of these tests are about what the connector refuses to do. A read that
goes wrong costs a stale figure; a write that goes wrong puts a voucher in
somebody's books twice, or puts one there that nobody approved.
"""

from __future__ import annotations

import asyncio
from datetime import date

import httpx
import pytest
from tally_core.domain.masters import VoucherTypeKind
from tally_core.domain.writes import DraftLedgerEntry, VoucherDraft

from tally_connector.protocol import JobRequest, MutationRequest

IMPORT_OK = (
    "<ENVELOPE><BODY><DATA><IMPORTRESULT>"
    "<CREATED>1</CREATED><ALTERED>0</ALTERED><IGNORED>0</IGNORED>"
    "<LASTVCHID>4821</LASTVCHID><ERRORS>0</ERRORS><EXCEPTIONS>0</EXCEPTIONS>"
    "</IMPORTRESULT></DATA></BODY></ENVELOPE>"
)


def draft(**overrides) -> VoucherDraft:
    defaults = {
        "kind": VoucherTypeKind.RECEIPT,
        "date": date(2026, 9, 17),
        "party_name": "Ram & Sons",
        "ledger_entries": [
            DraftLedgerEntry(
                ledger_name="Cash", amount="-5000", is_deemed_positive=True
            ),
            DraftLedgerEntry(
                ledger_name="Ram & Sons", amount="5000", is_deemed_positive=False
            ),
        ],
    }
    return VoucherDraft(**{**defaults, **overrides})


def write(**overrides) -> MutationRequest:
    defaults = {
        "job_id": "w1",
        "mutation": "voucher.create",
        "params": {
            "company": "Acme",
            "draft": draft().model_dump(mode="json"),
        },
    }
    return MutationRequest(**{**defaults, **overrides})


def ok(_: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=IMPORT_OK)


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


async def test_a_voucher_is_created_and_its_id_comes_back(make_executor):
    result = await make_executor(ok).write(write())

    assert result.ok is True
    assert result.data()["created"] == 1
    assert result.data()["voucher_id"] == 4821


async def test_the_machines_entry_mode_decides_what_tally_is_sent(make_executor):
    """The setting is applied by the connector, not carried by the request."""
    sent: list[str] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(request.content.decode())
        return httpx.Response(200, text=IMPORT_OK)

    optional = make_executor(capture, voucher_entry_mode="optional")
    await optional.write(write())
    assert "<ISOPTIONAL>Yes</ISOPTIONAL>" in sent[-1]

    regular = make_executor(capture, voucher_entry_mode="regular")
    await regular.write(
        write(params={"company": "Acme", "draft": draft(optional=False).model_dump(mode="json")})
    )
    assert "<ISOPTIONAL>No</ISOPTIONAL>" in sent[-1]


async def test_a_phone_cannot_post_straight_into_the_books_on_a_cautious_machine(
    make_executor,
):
    """The asymmetry, at the layer that enforces it.

    Whatever the request says, a machine set to optional produces an optional
    entry. This is what makes the connector window's choice a policy rather
    than a default a client can talk its way past.
    """
    sent: list[str] = []

    def capture(request: httpx.Request) -> httpx.Response:
        sent.append(request.content.decode())
        return httpx.Response(200, text=IMPORT_OK)

    executor = make_executor(capture, voucher_entry_mode="optional")
    await executor.write(
        write(
            params={
                "company": "Acme",
                # The phone asking, loudly, for a regular entry.
                "draft": draft(optional=False).model_dump(mode="json"),
                "force_optional": False,
            }
        )
    )

    assert "<ISOPTIONAL>Yes</ISOPTIONAL>" in sent[-1]


async def test_the_mode_can_be_changed_without_a_restart(make_executor):
    executor = make_executor(ok, voucher_entry_mode="optional")
    assert executor.voucher_entry_mode == "optional"
    executor.set_voucher_entry_mode("regular")
    assert executor.voucher_entry_mode == "regular"


# --------------------------------------------------------------------------
# The refusals that matter
# --------------------------------------------------------------------------


async def test_a_write_is_never_cached(make_executor):
    """Two identical receipts are two receipts.

    A cache hit, or single-flight collapsing, would silently drop a real second
    payment from the same customer for the same amount on the same day.
    """
    calls = 0

    def counting(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=IMPORT_OK)

    executor = make_executor(counting)
    await executor.write(write())
    await executor.write(write())

    assert calls == 2


async def test_two_identical_writes_are_not_collapsed(make_executor):
    calls = 0

    async def slow(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)
        return httpx.Response(200, text=IMPORT_OK)

    executor = make_executor(slow)
    await asyncio.gather(
        executor.write(write(job_id="a")), executor.write(write(job_id="b"))
    )

    assert calls == 2


async def test_a_timed_out_write_is_never_reported_as_retryable(make_executor):
    """The single most dangerous thing about write-back.

    Tally can finish an import and lose the reply. A retry books the voucher
    twice and nothing afterwards can tell which attempt landed, so the person
    is told to go and look rather than told to try again.
    """

    async def hangs(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, text=IMPORT_OK)

    result = await make_executor(hangs).write(write(deadline_seconds=0.1))

    assert result.ok is False
    assert result.error is not None
    assert result.error.retryable is False
    assert result.error.code == "write_deadline_exceeded"
    assert "may still have been saved" in result.error.user_message


async def test_an_unbalanced_entry_is_refused_before_tally_sees_it(make_executor):
    calls = 0

    def counting(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=IMPORT_OK)

    lopsided = draft(
        ledger_entries=[
            DraftLedgerEntry(ledger_name="Cash", amount="-5000", is_deemed_positive=True)
        ]
    )
    result = await make_executor(counting).write(
        write(params={"company": "Acme", "draft": lopsided.model_dump(mode="json")})
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "unbalanced_voucher"
    assert calls == 0


async def test_tallys_own_words_reach_the_person_who_tried(make_executor):
    refused = (
        "<ENVELOPE><BODY><DATA>"
        "<LINEERROR>Ledger 'Ram Traders' does not exist!</LINEERROR>"
        "</DATA></BODY></ENVELOPE>"
    )
    result = await make_executor(
        lambda _: httpx.Response(200, text=refused)
    ).write(write())

    assert result.ok is False
    assert result.error is not None
    assert "Ram Traders" in result.error.message
    # Never retryable, whatever the underlying error class says about reads.
    assert result.error.retryable is False


async def test_an_unknown_mutation_is_refused_not_guessed_at(make_executor):
    result = await make_executor(ok).write(write(mutation="ledger.delete"))

    assert result.ok is False
    assert result.error is not None
    assert result.error.retryable is False


async def test_a_read_job_cannot_reach_the_writer(make_executor):
    """Naming a mutation in a read job must not post a voucher."""
    calls = 0

    def counting(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=IMPORT_OK)

    result = await make_executor(counting).run(
        JobRequest(job_id="j1", query="voucher.create", params={"company": "Acme"})
    )

    assert result.ok is False
    assert calls == 0


@pytest.mark.parametrize("field", ["company", "draft"])
async def test_a_write_missing_a_required_field_is_refused(make_executor, field):
    params = {"company": "Acme", "draft": draft().model_dump(mode="json")}
    params.pop(field)

    result = await make_executor(ok).write(write(params=params))

    assert result.ok is False
    assert result.error is not None
    assert result.error.code == "invalid_params"


# --------------------------------------------------------------------------
# "Nothing was sent" is the only safe reason to offer a retry
# --------------------------------------------------------------------------


async def test_tally_being_closed_is_safe_to_try_again(make_executor):
    """TallyPrime shut, PC on. The connection is refused, so no bytes left the
    machine -- opening Tally and pressing the button again cannot duplicate
    anything."""

    def refused(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    result = await make_executor(refused).write(write())

    assert result.ok is False
    assert result.error is not None
    assert result.error.retryable is True
    assert "Tally" in result.error.user_message


async def test_the_company_being_closed_is_safe_to_try_again(make_executor):
    """The loaded-company guard refuses before anything is built into a
    request, so the entry definitely does not exist."""
    from tally_core.tally.errors import CompanyNotLoadedError

    class Refuses:
        async def ensure(self, company: str) -> None:
            raise CompanyNotLoadedError(f"{company!r} is not open in TallyPrime")

        async def invalidate(self) -> None:
            return None

    result = await make_executor(ok, loaded=Refuses()).write(write())

    assert result.ok is False
    assert result.error is not None
    assert result.error.retryable is True
    assert "open it and try again" in result.error.user_message


async def test_a_timeout_is_never_safe_to_try_again(make_executor):
    """The request was on the wire. Tally can finish an import and lose the
    reply, and nothing afterwards separates that from a write that never
    arrived -- so there is no retry offered, at any layer."""

    async def hangs(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, text=IMPORT_OK)

    result = await make_executor(hangs).write(write(deadline_seconds=0.1))

    assert result.error is not None
    assert result.error.retryable is False


async def test_a_voucher_tally_refused_is_not_offered_as_a_retry(make_executor):
    """Nothing was written, but the same envelope will be refused the same way.
    Inviting a retry would send somebody round a loop."""
    refused = (
        "<ENVELOPE><BODY><DATA>"
        "<LINEERROR>Ledger 'Ram Traders' does not exist!</LINEERROR>"
        "</DATA></BODY></ENVELOPE>"
    )
    result = await make_executor(lambda _: httpx.Response(200, text=refused)).write(write())

    assert result.error is not None
    assert result.error.retryable is False
