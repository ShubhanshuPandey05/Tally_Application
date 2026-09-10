"""Pairing a shop PC by scanning a code, rather than by typing a secret.

The old flow asked a shop owner to read a connector id and a 43-character
secret off a phone and type both into a Windows machine. It works, it is the
first thing every customer does, and it is where they get stuck -- the two
strings are exactly the shape a person cannot carry across a room.

So the connector draws a QR code on its own local page and the phone scans it.
Three parties, in this order:

1. **The connector** invents a ``code`` and a ``token``, registers the pair
   here, and shows the code. It then polls, holding the token.
2. **The app**, signed in as an admin, scans the code and says which account
   this PC belongs to. That is when a connector row and its secret exist.
3. **The connector** collects the secret with its token, exactly once.

What each half of the credential is for:

``code``   identifies the claim, and is on a screen anybody in the shop can see.
``token``  proves the collector is the machine that drew that screen.

Both are stored only as SHA-256, so this table completes no pairing if it
leaks; the secret waiting to be collected is encrypted with the same box that
protects ``Connector.secret_encrypted``.

The connector still dials out for every step. Nothing listens on the customer's
machine and no port is opened -- a pairing flow where the phone connected
directly to the PC would have been less code and would have put an inbound
socket on a shop's network, which is the one thing this product does not do.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.crypto import SecretBox
from ..core.errors import ConflictError, NotFound, RateLimited
from ..db.models import ConnectorClaim, as_utc, utc_now

logger = logging.getLogger(__name__)

#: How long a code shown on a shop PC stays claimable. Somebody is standing in
#: front of that screen with their phone, so this is generous already; longer
#: would mostly mean a code left up overnight on an unattended machine.
CLAIM_TTL_SECONDS = 15 * 60

#: How long a claimed pairing waits to be collected. Shorter than the claim
#: itself: the connector is polling every few seconds by the time this matters,
#: so a secret still sitting here after two minutes is one nobody is coming for.
COLLECT_TTL_SECONDS = 5 * 60

#: Ceiling on open claims across the whole platform. Registration is
#: unauthenticated by necessity -- a connector has no credentials yet, which is
#: the entire point -- so this is what stops the endpoint being a free row
#: generator. It is deliberately far above any real install rate: a number that
#: could be reached by customers would be an outage.
MAX_OPEN_CLAIMS = 5_000

#: Bytes in each half. 16 keeps the QR small enough to scan from a phone held at
#: arm's length in a shop with bad lighting, which is the real constraint.
CODE_BYTES = 16


def _fingerprint(value: str) -> str:
    """SHA-256 of a claim credential.

    Not a password hash, deliberately. These are 128-bit random strings with a
    fifteen-minute life, so there is nothing to brute force and no reason to pay
    Argon2 on a path a connector polls every few seconds.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class IssuedClaim:
    """What a connector gets back when it registers, and shows as a QR."""

    code: str
    token: str
    expires_at: datetime


@dataclass(frozen=True)
class CollectedPairing:
    """Credentials handed to the machine that drew the code."""

    connector_id: str
    secret: str
    org_id: str


class PairingService:
    """The claim table, and the three transitions it allows."""

    def __init__(self, session: AsyncSession, secret_box: SecretBox) -> None:
        self._session = session
        self._box = secret_box

    # -- 1. the connector registers -------------------------------------

    async def open_claim(
        self, *, hostname: str, os_name: str, connector_version: str
    ) -> IssuedClaim:
        """Create a claim and return the pair the connector will need.

        The code and the token are generated here rather than accepted from the
        connector. A caller-chosen code could collide with a live one -- and a
        collision here is not a duplicate row, it is one shop's PC collecting
        another shop's pairing.
        """
        await self._prune()

        open_claims = await self._session.scalar(
            select(func.count())
            .select_from(ConnectorClaim)
            .where(ConnectorClaim.collected_at.is_(None))
        )
        if (open_claims or 0) >= MAX_OPEN_CLAIMS:
            raise RateLimited(
                f"{open_claims} claims are already open",
                user_message="Pairing is busy right now. Try again in a few minutes.",
            )

        code = secrets.token_urlsafe(CODE_BYTES)
        token = secrets.token_urlsafe(CODE_BYTES)
        claim = ConnectorClaim(
            code_hash=_fingerprint(code),
            token_hash=_fingerprint(token),
            expires_at=utc_now() + timedelta(seconds=CLAIM_TTL_SECONDS),
            # Truncated to the column widths rather than validated: this is
            # unauthenticated input, and a machine with a silly hostname should
            # still be able to pair.
            hostname=hostname[:200],
            os=os_name[:100],
            connector_version=connector_version[:50],
        )
        self._session.add(claim)
        await self._session.flush()
        return IssuedClaim(code=code, token=token, expires_at=claim.expires_at)

    # -- 2. the app claims it -------------------------------------------

    async def claimable(self, code: str) -> ConnectorClaim:
        """The open claim behind a scanned code.

        404 for expired, unknown and already-claimed alike. They are the same
        thing to the person holding the phone -- "that code is no good, look at
        the PC again" -- and distinguishing them would let a caller test codes.
        """
        claim = await self._by_code(code)
        if claim is None or claim.is_expired or claim.claimed_at is not None:
            raise NotFound(
                "no open claim for that code",
                user_message=(
                    "That pairing code is no longer valid. Press Refresh on the "
                    "connector screen and scan the new code."
                ),
            )
        return claim

    async def fulfil(
        self, claim: ConnectorClaim, *, org_id: str, connector_id: str, secret: str
    ) -> None:
        """Attach an issued pairing to a claim, for the PC to collect.

        The window reopens from now rather than running out with the original
        claim: the owner may have taken ten minutes to find the app, and the
        connector's collection is seconds away once they have.
        """
        claim.org_id = org_id
        claim.connector_id = connector_id
        claim.secret_encrypted = self._box.encrypt(secret)
        claim.claimed_at = utc_now()
        claim.expires_at = utc_now() + timedelta(seconds=COLLECT_TTL_SECONDS)

    # -- 3. the connector collects --------------------------------------

    async def collect(self, *, code: str, token: str) -> CollectedPairing | None:
        """Hand over the pairing, once, to whoever holds the token.

        ``None`` means "not claimed yet", which is the normal answer while the
        owner is still walking over to the PC -- the connector polls on it.

        Single-use: the row is marked collected before the value is returned, so
        a captured response cannot be replayed into a second machine that would
        then hold the same credentials as the shop's.
        """
        claim = await self._by_code(code)
        if claim is None or claim.is_expired:
            raise NotFound(
                "unknown or expired claim",
                user_message="This pairing code has expired.",
            )

        # Constant time, and before any other check: an attacker with a
        # photographed code must not be able to learn from the timing whether a
        # claim has been fulfilled yet.
        if not secrets.compare_digest(claim.token_hash, _fingerprint(token)):
            raise NotFound(
                "claim token does not match",
                user_message="This pairing code has expired.",
            )

        if claim.collected_at is not None:
            raise ConflictError(
                "claim was already collected",
                user_message="These pairing details were already collected.",
            )

        if claim.claimed_at is None or claim.connector_id is None or claim.org_id is None:
            return None

        claim.collected_at = utc_now()
        return CollectedPairing(
            connector_id=claim.connector_id,
            secret=self._box.decrypt(claim.secret_encrypted or ""),
            org_id=claim.org_id,
        )

    # -- housekeeping ----------------------------------------------------

    async def _by_code(self, code: str) -> ConnectorClaim | None:
        return await self._session.scalar(
            select(ConnectorClaim).where(ConnectorClaim.code_hash == _fingerprint(code))
        )

    async def _prune(self) -> None:
        """Delete claims nobody can complete any more.

        On the registration path rather than in a background job, because that
        is the only path whose volume tracks the table's growth. A sweeper that
        stops running leaves a table that only grows; this cannot.
        """
        cutoff = utc_now() - timedelta(seconds=CLAIM_TTL_SECONDS)
        await self._session.execute(
            delete(ConnectorClaim).where(ConnectorClaim.expires_at < cutoff)
        )


def seconds_until(when: datetime) -> int:
    """Whole seconds from now until ``when``, floored at zero."""
    remaining = (as_utc(when) - datetime.now(UTC)).total_seconds()
    return max(0, int(remaining))
