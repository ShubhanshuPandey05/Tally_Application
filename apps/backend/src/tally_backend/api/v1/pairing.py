"""The connector's half of QR pairing: unauthenticated, by necessity.

A connector that has never been paired holds no credential, so the two routes it
needs here cannot require one. That makes this the second unauthenticated
surface in the product after ``public``, and it is kept in its own module for
the same reason -- what a caller with no token can reach should be a file, not
the result of a search.

What is actually exposed:

``POST /v1/pairing/claims``
    Ask for a code. Returns two random strings and nothing else. It reads no
    customer data, and cannot: at this point the caller is not associated with
    an account and neither is the row it creates.

``POST /v1/pairing/claims/collect``
    Present a code *and* the token that only the machine which asked for it
    holds, and receive the pairing an admin has since attached to it. Once.

Neither route takes anything an attacker can vary usefully. A caller with no
code learns nothing; a caller who photographed a code off a shop screen still
cannot collect, because the token never appeared on that screen.

The counterpart routes -- the ones that decide *which account* a claim belongs
to -- are in ``connectors.py`` behind an admin token, which is the whole point:
the unauthenticated half can create a waiting machine, and only a signed-in
owner can give it access to any books.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request, status

from ...core.errors import NotFound
from ...db.models import Connector
from ...services.pairing import PairingService, seconds_until
from ..deps import SessionDep
from ..schemas import (
    ClaimCollectRequest,
    ClaimCollectResponse,
    OpenClaimRequest,
    OpenClaimResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pairing", tags=["pairing"])


@router.post("/claims", response_model=OpenClaimResponse, status_code=status.HTTP_201_CREATED)
async def open_claim(
    payload: OpenClaimRequest, session: SessionDep, request: Request
) -> OpenClaimResponse:
    """Register a pairing claim for a connector that has no credentials yet."""
    service = PairingService(session, request.app.state.secret_box)
    claim = await service.open_claim(
        hostname=payload.hostname,
        os_name=payload.os,
        connector_version=payload.connector_version,
    )
    logger.info("pairing claim opened for %r", payload.hostname or "unnamed machine")
    return OpenClaimResponse(
        code=claim.code,
        token=claim.token,
        expires_in_seconds=seconds_until(claim.expires_at),
    )


@router.post("/claims/collect", response_model=ClaimCollectResponse)
async def collect_claim(
    payload: ClaimCollectRequest, session: SessionDep, request: Request
) -> ClaimCollectResponse:
    """Collect the credentials an admin attached to this claim.

    A POST rather than a GET despite reading, because it is not idempotent:
    collection is single-use and consumes the claim. A GET would also put a
    pairing secret in every proxy log between the shop and here.
    """
    service = PairingService(session, request.app.state.secret_box)
    pairing = await service.collect(code=payload.code, token=payload.token)

    if pairing is None:
        # The normal answer while the owner is still walking to the PC with
        # their phone. The connector polls on it, so it must not be an error.
        return ClaimCollectResponse(status="pending")

    connector = await session.get(Connector, pairing.connector_id)
    if connector is None:
        # Claimed, then the connector was deleted before the PC collected.
        # Refusing beats handing over a credential for a row that is gone.
        raise NotFound(
            f"connector {pairing.connector_id} no longer exists",
            user_message="That Tally PC was removed before it finished pairing.",
        )

    logger.info("connector %s collected its pairing", connector.id)
    return ClaimCollectResponse(
        status="ready",
        connector_id=pairing.connector_id,
        secret=pairing.secret,
        name=connector.name,
    )
