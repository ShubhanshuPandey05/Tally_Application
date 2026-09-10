"""The connector WebSocket endpoint.

This is the server half of the handshake in ``tally_core.protocol``. The
connector dials out to here; nothing ever dials in to the customer's machine.

Authentication is HMAC over ``connector_id|nonce|issued_at``, so the pairing
secret itself never crosses the wire -- only a proof of possession, bound to a
nonce and a timestamp so a captured frame cannot be replayed.

Verifying that proof requires the same secret that produced it, which is why
secrets are encrypted at rest rather than hashed. See ``core.crypto`` for the
reasoning; the short version is that a password hash is the correct tool for
comparing a user's password and the wrong tool for keying an HMAC.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from tally_core.protocol import PROTOCOL_VERSION, Hello, HelloAck, LogBatch, sign_handshake

from ...core.crypto import SecretBox, SecretDecryptionError
from ...core.security import constant_time_equals
from ...db.models import Connector, ConnectorStatus, utc_now
from ...hub import ConnectorHub, ConnectorLink
from ...services.releases import ConnectorReleaseView
from ...services.roster import build_roster

logger = logging.getLogger(__name__)

router = APIRouter()

#: A connector that opens a socket and then says nothing is either broken or
#: hostile; either way it must not hold a slot open indefinitely.
HANDSHAKE_TIMEOUT_SECONDS = 20.0

#: Rejects replays of a captured Hello frame.
MAX_HANDSHAKE_AGE_SECONDS = 120


@router.websocket("/connector")
async def connector_socket(websocket: WebSocket) -> None:
    settings = websocket.app.state.settings
    hub: ConnectorHub = websocket.app.state.hub
    session_factory = websocket.app.state.session_factory

    await websocket.accept()

    try:
        raw = await asyncio.wait_for(
            websocket.receive_text(), timeout=HANDSHAKE_TIMEOUT_SECONDS
        )
        hello = Hello.model_validate_json(raw)
    except (TimeoutError, WebSocketDisconnect):
        with contextlib.suppress(Exception):
            await websocket.close(code=1008, reason="handshake timeout")
        return
    except Exception as exc:  # noqa: BLE001 - any malformed hello is a rejection
        logger.info("malformed handshake: %s", exc)
        await _reject(websocket, "malformed handshake", code="malformed")
        return

    if hello.protocol_version != PROTOCOL_VERSION:
        # Refusing beats guessing. Misparsing a job request is exactly how a
        # read-only product grows an accidental write path.
        await _reject(
            websocket,
            f"backend speaks protocol v{PROTOCOL_VERSION}, connector sent "
            f"v{hello.protocol_version}",
            code="protocol",
        )
        return

    if abs(int(time.time()) - hello.issued_at) > MAX_HANDSHAKE_AGE_SECONDS:
        await _reject(websocket, "handshake is too old", code="malformed")
        return

    async with session_factory() as session:
        connector = await session.scalar(
            select(Connector).where(Connector.id == hello.connector_id)
        )

        if connector is None or connector.status is ConnectorStatus.REVOKED:
            # Same message either way: a distinct "unknown connector" reply would
            # let anyone enumerate valid connector ids.
            #
            # ``revoked`` is the one code that makes a connector give up on its
            # stored pairing and ask to be paired again, so it is answered only
            # here -- where the credential really is dead for good.
            await _reject(websocket, "unknown or revoked connector", code="revoked")
            return

        verdict = _verify(hello, connector, websocket.app.state.secret_box)
        if verdict is not True:
            # A signature that does not check out means one of two very
            # different things, and the row is the only place the difference is
            # recorded. An admin who asked for this PC to be paired again
            # replaced its secret on purpose, and the machine should say so and
            # show a code; anything else is a fault, and a fault must never put
            # a fleet on a pairing screen -- least of all a backend that cannot
            # decrypt its own secrets, which fails this way for every customer
            # at once.
            if connector.repair_requested_at is not None:
                logger.info(
                    "connector %s is waiting to be paired again", hello.connector_id
                )
                await _reject(websocket, "this computer is being paired again",
                              code="repairing")
                return
            logger.warning("bad signature from connector %s", hello.connector_id)
            await _reject(websocket, "authentication failed", code=verdict)
            return

        connector.status = ConnectorStatus.ACTIVE
        # Cleared by the thing it was waiting for. Not cleared when the new
        # secret is *issued*: between an admin scanning and the PC collecting,
        # the machine still holds the old credential and still needs to be told
        # to show a code.
        connector.repair_requested_at = None
        connector.last_seen_at = utc_now()
        connector.hostname = hello.host.hostname
        connector.os = hello.host.os
        connector.connector_version = hello.host.connector_version
        connector.capabilities = [c.model_dump() for c in hello.capabilities]
        org_id = connector.org_id
        await session.commit()

    link = ConnectorLink(
        connector_id=hello.connector_id,
        org_id=org_id,
        socket=websocket,
        max_concurrent_jobs=settings.max_jobs_per_connector,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
        heartbeat_grace_seconds=settings.heartbeat_grace_seconds,
        on_status_change=_status_persister(session_factory),
        # Logs are handed to the ingest buffer, not written here. A database
        # round trip on this receive loop would sit between two frames on the
        # socket a customer's reports come back on.
        on_logs=_log_receiver(getattr(websocket.app.state, "connector_logs", None)),
        # Answers the Refresh button on the connector's local page. It reads the
        # database, so it is a callback rather than something the link could
        # compute -- the link is transport and knows nothing about an account.
        on_roster_request=_roster_sender(session_factory),
        # The link stamps the published version onto every frame it sends and
        # checks the version on every frame it receives, so an outdated connector
        # is told to update within one heartbeat instead of at its next poll.
        releases=ConnectorReleaseView(websocket.app.state.releases),
        push_updates=settings.push_connector_updates,
    )
    link.capabilities = [c.model_dump() for c in hello.capabilities]
    link.host = hello.host.model_dump()

    # Through `link.send`, not the raw socket, so the ack carries the published
    # version like every other server frame. That makes the handshake itself the
    # first opportunity for a connector to notice it is out of date.
    await link.send(
        HelloAck(
            accepted=True,
            session_id=link.session_id,
            heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
        )
    )
    await hub.attach(link)
    logger.info(
        "connector %s connected (%s, v%s)",
        hello.connector_id,
        hello.host.hostname,
        hello.host.connector_version,
    )
    # Evaluated after attach so the socket is fully live before an update
    # command can be sent on it.
    await link.review_version(hello.host.connector_version)
    # Pushed unasked, straight after the handshake. The local page on the shop
    # PC is often opened *because* something is wrong, and a page that had to
    # ask for its contents would be blank exactly when the socket is flapping.
    await _send_roster(session_factory, link)

    heartbeat = asyncio.create_task(link.heartbeat_loop())
    try:
        while True:
            message = json.loads(await websocket.receive_text())
            await link.handle_message(message)

    except WebSocketDisconnect:
        logger.info("connector %s disconnected", hello.connector_id)
    except (TypeError, ValueError):
        logger.warning("connector %s sent a non-JSON frame", hello.connector_id)
    except Exception:  # noqa: BLE001 - never let one socket take the server down
        logger.exception("connector %s session failed", hello.connector_id)
    finally:
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
        await hub.detach(link)
        # Guarded for the same reason `detach` guards itself: a reconnect may
        # already have installed a newer link. Marking offline unconditionally
        # let a *replaced* session, unwinding seconds later, stamp the live
        # connector as "Tally not responding" -- which the app renders as
        # "Open TallyPrime and load the company" over a perfectly healthy PC.
        if hub.local_link(hello.connector_id) is None:
            await _mark_offline(session_factory, hello.connector_id)


def _verify(hello: Hello, connector: Connector, secret_box: SecretBox) -> bool | str:
    """Recompute the handshake signature and compare in constant time.

    Returns ``True``, or the rejection code explaining the failure. The two
    failures are worth telling apart on the wire and not only in a log: a wrong
    signature is one connector's problem, and a secret this backend cannot
    decrypt is every connector's problem at once.
    """
    try:
        secret = secret_box.decrypt(connector.secret_encrypted)
    except SecretDecryptionError:
        # The encryption key changed without the rows being migrated. The
        # connector is not at fault, so say so in the log -- otherwise this
        # presents as a fleet-wide authentication failure with no explanation.
        logger.error(
            "cannot decrypt secret for connector %s; check TALLYFLOW_SECRET_KEYS",
            connector.id,
        )
        return "server_key"

    expected = sign_handshake(
        connector_id=hello.connector_id,
        nonce=hello.nonce,
        issued_at=hello.issued_at,
        secret=secret,
    )
    return True if constant_time_equals(expected, hello.signature) else "auth_failed"


def _log_receiver(ingest):  # noqa: ANN001, ANN202 - closure over app state
    """Route a connector's pushed log lines into the ingest buffer.

    ``None`` when remote logging is switched off for this deployment, in which
    case the frames are simply not handled -- the connector keeps sending them
    and the link ignores them, which costs one discarded frame per interval and
    needs no negotiation to turn off.
    """
    if ingest is None:
        return None

    def receive(link: ConnectorLink, batch: LogBatch) -> None:
        ingest.submit(
            org_id=link.org_id,
            connector_id=link.connector_id,
            session_id=link.session_id,
            entries=batch.entries,
            dropped=batch.dropped,
        )

    return receive


async def _send_roster(session_factory, link: ConnectorLink) -> None:
    """Build this connector's roster and push it.

    Never fatal. A connector with no roster shows an honest "we could not reach
    the server for this" on its page and goes on serving reports, which is the
    only ordering that makes sense: the roster is the diagnostic, and the
    reports are the product.
    """
    with contextlib.suppress(Exception):
        async with session_factory() as session:
            roster = await build_roster(session, link.connector_id)
        if roster is not None:
            await link.send(roster)


def _roster_sender(session_factory):  # noqa: ANN202 - closure over app state
    async def send(link: ConnectorLink) -> None:
        await _send_roster(session_factory, link)

    return send


def _status_persister(session_factory):  # noqa: ANN202 - closure over app state
    """Persist Tally reachability when it changes.

    Only on transitions, not on every heartbeat: writing a row every 30 seconds
    per connector would be the single busiest query in the system and would tell
    us nothing a transition does not.
    """

    async def persist(link: ConnectorLink) -> None:
        async with session_factory() as session:
            connector = await session.get(Connector, link.connector_id)
            if connector is None:
                return
            connector.last_tally_online = link.tally_online
            connector.last_seen_at = utc_now()
            await session.commit()

    return persist


async def _mark_offline(session_factory, connector_id: str) -> None:
    with contextlib.suppress(Exception):
        async with session_factory() as session:
            connector = await session.get(Connector, connector_id)
            if connector is not None:
                connector.last_tally_online = False
                connector.last_seen_at = utc_now()
                await session.commit()


async def _reject(websocket: WebSocket, reason: str, *, code: str = "") -> None:
    with contextlib.suppress(Exception):
        await websocket.send_text(
            HelloAck(accepted=False, reason=reason, reason_code=code).model_dump_json()
        )
        await websocket.close(code=1008, reason=reason)
