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
from tally_core.protocol import PROTOCOL_VERSION, Hello, HelloAck, sign_handshake

from ...core.crypto import SecretBox, SecretDecryptionError
from ...core.security import constant_time_equals
from ...db.models import Connector, ConnectorStatus, utc_now
from ...hub import ConnectorHub, ConnectorLink

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
        await _reject(websocket, "malformed handshake")
        return

    if hello.protocol_version != PROTOCOL_VERSION:
        # Refusing beats guessing. Misparsing a job request is exactly how a
        # read-only product grows an accidental write path.
        await _reject(
            websocket,
            f"backend speaks protocol v{PROTOCOL_VERSION}, connector sent "
            f"v{hello.protocol_version}",
        )
        return

    if abs(int(time.time()) - hello.issued_at) > MAX_HANDSHAKE_AGE_SECONDS:
        await _reject(websocket, "handshake is too old")
        return

    async with session_factory() as session:
        connector = await session.scalar(
            select(Connector).where(Connector.id == hello.connector_id)
        )

        if connector is None or connector.status is ConnectorStatus.REVOKED:
            # Same message either way: a distinct "unknown connector" reply would
            # let anyone enumerate valid connector ids.
            await _reject(websocket, "unknown or revoked connector")
            return

        if not _verify(hello, connector, websocket.app.state.secret_box):
            logger.warning("bad signature from connector %s", hello.connector_id)
            await _reject(websocket, "authentication failed")
            return

        connector.status = ConnectorStatus.ACTIVE
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
    )
    link.capabilities = [c.model_dump() for c in hello.capabilities]
    link.host = hello.host.model_dump()

    await websocket.send_text(
        HelloAck(
            accepted=True,
            session_id=link.session_id,
            heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
        ).model_dump_json()
    )
    await hub.attach(link)
    logger.info(
        "connector %s connected (%s, v%s)",
        hello.connector_id,
        hello.host.hostname,
        hello.host.connector_version,
    )

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
        await _mark_offline(session_factory, hello.connector_id)


def _verify(hello: Hello, connector: Connector, secret_box: SecretBox) -> bool:
    """Recompute the handshake signature and compare in constant time."""
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
        return False

    expected = sign_handshake(
        connector_id=hello.connector_id,
        nonce=hello.nonce,
        issued_at=hello.issued_at,
        secret=secret,
    )
    return constant_time_equals(expected, hello.signature)


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


async def _reject(websocket: WebSocket, reason: str) -> None:
    with contextlib.suppress(Exception):
        await websocket.send_text(
            HelloAck(accepted=False, reason=reason).model_dump_json()
        )
        await websocket.close(code=1008, reason=reason)
