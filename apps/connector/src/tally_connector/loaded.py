"""Whether the company a read asks for is actually open in TallyPrime.

Tally does not refuse a read for a company that is not open. It answers
``STATUS 1`` with an **empty collection** and no ``<LINEERROR>`` -- the same
response shape a genuinely empty company produces. Every layer above therefore
treats it as a successful read of nothing, and the dashboard renders zeroes as
real figures: sales blank, stock blank, receivables blank, every section marked
``ok``. There is no error anywhere for a support call to point at, which is what
makes this failure worth a dedicated guard rather than a comment.

Confirmed on a live TallyPrime 2026-08-09 (company "Bhtia Supermarket"):

===============================  ==========================================
read, company open               read, same company closed
===============================  ==========================================
``VoucherType`` -> 24 members    ``VoucherType`` -> 0 members, ``STATUS 1``
``Ledger``      -> 3 members     ``Ledger``      -> 0 members, ``STATUS 1``
``Voucher``     -> 452 members   ``Voucher``     -> **Tally crashes**
===============================  ==========================================

The voucher row is the reason this guard runs *before* the request rather than
inspecting the reply. Asking for vouchers from a company that is not open takes
TallyPrime down with ``c0000005 (Memory Access Violation)`` -- logged in
``tallyerr.log`` with a minidump, reproduced on demand. So the empty-collection
case cannot be detected after the fact: by then the damage is done.

``companies.list`` is the check because Tally only ever returns companies that
are **open** -- that is already the documented contract of that query, and it is
the same list the pairing screen picks from. It costs about 1.5 KB, is cached
for :attr:`ttl_seconds`, and is the one read that is safe to make in this state.

Two ways in, because there are two ways to reach Tally. The service path holds a
:class:`~tally_connector.pipeline.TallyPipeline` and calls
:meth:`LoadedCompanies.ensure` from the job executor. The CLI (``livecheck``)
holds a bare client and no queue, so it wraps that client in
:class:`GuardedClient` instead and gets the same guard on every read it makes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from pydantic import BaseModel
from tally_core.tally import TallyClient, get_query
from tally_core.tally.errors import CompanyNotLoadedError, TallyError
from tally_core.tally.query import TallyQuery

logger = logging.getLogger(__name__)

#: How long an observed set of open companies is trusted. Short, because the
#: operator closing a company is exactly the event this guard exists to catch,
#: and the read it saves is tiny.
DEFAULT_TTL_SECONDS = 30.0

#: Budget for the check itself. It must be well under any job deadline: a guard
#: that expires is worse than no guard, because it converts a read that would
#: have worked into a failure.
DEFAULT_DEADLINE_SECONDS = 15.0


def company_key(name: str) -> str:
    """Companies are matched case- and whitespace-insensitively.

    The name travels from Tally into the backend's database and back out in a
    job's params. Round-tripping it through a JSON payload and a user-editable
    label is enough for the casing to drift, and refusing a read over that would
    be indistinguishable, to the shop, from the bug this guard fixes.

    Public because the CLI matches a hand-typed ``--company`` the same way; a
    guard that accepts a name the caller's own lookup rejected would be worse
    than either rule on its own.
    """
    return " ".join(name.split()).casefold()


class QueryRunner(Protocol):
    """The one capability the guard needs: run a query, within a deadline.

    Narrower than :class:`~tally_connector.pipeline.TallyPipeline` on purpose.
    The service path has a pipeline in front of Tally; the CLI tools do not, and
    tying the guard to the queue would have meant either standing a pipeline up
    for a one-shot command or leaving those paths unguarded.
    """

    async def execute(
        self,
        query: TallyQuery[Any, Any],
        params: BaseModel,
        *,
        deadline_seconds: float,
    ) -> Any: ...

    @property
    def outages(self) -> int:
        """How many times Tally has been seen to stop answering, ever.

        The guard keeps nothing across a change in this number. It does not care
        what the count is, only that it is the same one as when the open set was
        taken -- Tally going away and coming back is how a company stops being
        loaded without anybody having closed it.
        """
        ...


class _DirectRunner:
    """Runs the guard's probe straight at Tally, with no queue in between.

    Safe only where a single task owns the client -- which is exactly the CLI
    case. :class:`~tally_core.tally.TallyClient` still serialises the socket, so
    the worst case is waiting, not a wedged gateway.
    """

    def __init__(self, client: TallyClient) -> None:
        self._client = client

    @property
    def outages(self) -> int:
        # Nothing on this path watches Tally's health between calls, and a CLI
        # command is short enough that there is no long-lived cache to protect.
        return 0

    async def execute(
        self,
        query: TallyQuery[Any, Any],
        params: BaseModel,
        *,
        deadline_seconds: float,
    ) -> Any:
        return await asyncio.wait_for(
            self._client.execute(query, params), timeout=deadline_seconds
        )


class GuardedClient:
    """A :class:`TallyClient` that refuses reads for a company Tally has closed.

    Drop-in for the calls the CLI makes, so guarding a command is one wrap at
    the top rather than a check before every read -- and a read added later is
    guarded by default instead of by remembering.
    """

    def __init__(
        self,
        client: TallyClient,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    ) -> None:
        self._client = client
        self._loaded = LoadedCompanies(
            _DirectRunner(client),
            ttl_seconds=ttl_seconds,
            deadline_seconds=deadline_seconds,
        )

    @property
    def config(self) -> Any:
        return self._client.config

    @property
    def loaded(self) -> LoadedCompanies:
        return self._loaded

    async def is_alive(self) -> bool:
        return await self._client.is_alive()

    async def execute(self, query: TallyQuery[Any, Any], params: BaseModel) -> Any:
        await self._loaded.ensure(getattr(params, "company", ""))
        return await self._client.execute(query, params)

    async def aclose(self) -> None:
        await self._client.aclose()


class LoadedCompanies:
    """Caches which companies TallyPrime currently has open."""

    def __init__(
        self,
        runner: QueryRunner,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    ) -> None:
        self._runner = runner
        self._ttl = ttl_seconds
        self._deadline = deadline_seconds
        self._names: frozenset[str] | None = None
        #: The same companies as Tally spells them. Matching is normalised, but
        #: a support message has to show the operator the name they will see on
        #: their own screen.
        self._display: tuple[str, ...] = ()
        self._fetched_at: float | None = None
        #: The runner's outage count when the set above was taken.
        self._outages_at_fetch = -1
        #: One refresh at a time. A dashboard fires several reads at once and
        #: they would otherwise each queue their own copy of the same probe in
        #: front of the export they are all waiting for.
        self._lock = asyncio.Lock()

    async def ensure(self, company: str) -> None:
        """Raise unless ``company`` is *known* to be open in TallyPrime.

        The rule is positive confirmation: a read reaches Tally only after a
        ``companies.list`` that actually named its company. When the check
        cannot be made at all, the error that stopped it is re-raised -- the
        caller still learns the true reason, but the read is not sent.

        This used to return without an opinion in that case, on the grounds that
        the real query was about to fail with a better description of the same
        problem. That reasoning holds only while the failure lasts, and the gap
        between the check and the read is exactly where an operator starts
        TallyPrime. From a shop's log on 2026-09-01:

        ============  ==========================================================
        ``20:35:14``  ``companies.list`` cannot connect (attempt 1 of 3)
        ``20:35:23``  the guard stands aside, "letting the read through"
        ``20:35:23``  the pipeline pauses 5s for Tally to settle
        ``20:35:28``  the read goes out -- into a TallyPrime the owner has just
                      opened, with **no company loaded**
        ============  ==========================================================

        That last state is the one that takes Tally down with ``c0000005``. An
        unreachable Tally and a Tally that has just become reachable are one
        second apart, so "the read will fail honestly anyway" is not something
        this layer can promise. Refusing costs a retry; guessing costs the shop
        its till.
        """
        if not company:
            # Company discovery is the one read that is not scoped to a company.
            return

        if company_key(company) in await self._open_companies():
            return

        # A cached set can only be wrong in one direction that matters: the
        # operator opened the company since it was taken. Re-ask before
        # refusing, so opening a company recovers on the next request instead
        # of after the TTL.
        if company_key(company) in await self._open_companies(force=True):
            return

        open_now = ", ".join(self._display) if self._display else "none"
        raise CompanyNotLoadedError(
            f"{company!r} is not open in TallyPrime (currently open: {open_now})"
        )

    async def invalidate(self) -> None:
        """Drop the cached set, so the next check re-reads it."""
        async with self._lock:
            self._forget()

    async def _open_companies(self, *, force: bool = False) -> frozenset[str]:
        """The open set, refreshed if stale. Raises if Tally cannot be asked."""
        async with self._lock:
            if not force and (cached := self._cached()) is not None:
                return cached

            try:
                companies = await self._runner.execute(
                    (query := get_query("companies.list")),
                    query.validate_params({}),
                    deadline_seconds=self._deadline,
                )
            except TallyError as exc:
                # Forgotten, not merely left unrefreshed. A set we could not
                # confirm was taken before whatever is now wrong with Tally, and
                # the next caller has to go and look rather than trust it.
                self._forget()
                logger.info(
                    "could not check which companies are open (%s); refusing the "
                    "read rather than sending it to a TallyPrime whose state we "
                    "cannot see",
                    exc,
                )
                raise

            names = [c.name for c in companies if c.name]
            self._display = tuple(sorted(names))
            self._names = frozenset(company_key(name) for name in names)
            self._fetched_at = asyncio.get_running_loop().time()
            self._outages_at_fetch = self._runner.outages
            return self._names

    def _forget(self) -> None:
        self._names = None
        self._display = ()
        self._fetched_at = None

    def _cached(self) -> frozenset[str] | None:
        """The stored set if it can still be trusted, else ``None``."""
        if self._names is None or self._fetched_at is None:
            return None
        if self._runner.outages != self._outages_at_fetch:
            # Tally stopped answering at some point after this set was taken, so
            # it has been reopened since. That is precisely the moment a company
            # is *not* loaded, and it is the one transition a cached "yes" must
            # never survive: on the TTL alone, an answer taken before the outage
            # can wave a voucher read into a freshly opened, empty TallyPrime.
            return None
        if asyncio.get_running_loop().time() - self._fetched_at > self._ttl:
            return None
        return self._names
