"""Connector pairing and fleet management."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Request, status
from sqlalchemy import func, select

from ...core.errors import ConflictError
from ...core.security import generate_secret
from ...db.models import Company, Connector, ConnectorStatus, Role, utc_now
from ...services.audit import record
from ..deps import ConnectorDep, HubDep, PrincipalDep, SessionDep, SettingsDep
from ..schemas import ConnectorPairingResponse, ConnectorResponse, CreateConnectorRequest

router = APIRouter(prefix="/connectors", tags=["connectors"])


def _pairing_code(connector_id: str) -> str:
    """Short human-readable code for the desktop pairing wizard.

    Not a credential -- it only identifies which connector row a wizard is
    completing. The actual secret is far too long to read off a phone screen.
    """
    return "-".join(
        [connector_id[:4].upper(), connector_id[4:8].upper(), secrets.token_hex(2).upper()]
    )


@router.post("", response_model=ConnectorPairingResponse, status_code=status.HTTP_201_CREATED)
async def create_connector(
    payload: CreateConnectorRequest,
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
) -> ConnectorPairingResponse:
    """Issue credentials for a new connector installation.

    The secret is returned here and never again. It is written into
    ``connector.json`` on the shop's PC by the pairing wizard.

    Refused until the account is approved. This is the first thing a new
    customer tries and therefore the place the approval flow is actually felt:
    they can install everything and sign in, and the Tally PC is what waits.
    """
    principal.require(Role.ADMIN)
    principal.require_changes()

    secret = generate_secret()
    connector = Connector(
        org_id=principal.org_id,
        name=payload.name,
        secret_encrypted=request.app.state.secret_box.encrypt(secret),
        status=ConnectorStatus.PENDING,
    )
    session.add(connector)
    await session.flush()

    await record(
        session,
        action="connector.create",
        org_id=principal.org_id,
        user_id=principal.user.id,
        detail={"connector_id": connector.id, "name": connector.name},
        request=request,
    )

    return ConnectorPairingResponse(
        connector_id=connector.id,
        secret=secret,
        name=connector.name,
        pairing_code=_pairing_code(connector.id),
    )



@router.post("/{connector_id}/secret", response_model=ConnectorPairingResponse)
async def rotate_connector_secret(
    connector: ConnectorDep,
    principal: PrincipalDep,
    session: SessionDep,
    hub: HubDep,
    request: Request,
) -> ConnectorPairingResponse:
    """Issue a fresh secret for a PC that is already registered.

    This is the answer to "I lost the secret" or "I reinstalled Windows", and
    it exists to stop that becoming a *new* connector. The secret is shown once
    and stored only as a hash, so it genuinely cannot be recovered -- which
    without this endpoint leaves pairing a second connector as the only way
    back, and that forks the shop's data: companies are unique per
    ``(connector_id, tally_name)``, so re-linking the same books under a new PC
    creates a second company row with the same name, its own snapshots, its own
    history and its own sync state.

    Keeping the row means the companies, their history and their snapshots all
    stay attached. Only the credential changes.

    The live socket is closed because the machine holding the old secret can no
    longer authenticate; leaving it connected would serve reads on a credential
    the owner has just revoked.
    """
    principal.require(Role.ADMIN)
    principal.require_changes()

    if connector.status is ConnectorStatus.REVOKED:
        raise ConflictError(
            f"connector {connector.id} is revoked",
            user_message=(
                "This Tally PC was removed. Add it again from the app to get new "
                "pairing details."
            ),
        )

    secret = generate_secret()
    connector.secret_encrypted = request.app.state.secret_box.encrypt(secret)
    # Back to PENDING until the PC actually handshakes with the new secret, so
    # the list does not claim a connector is paired when nothing can connect.
    # The websocket handshake sets it to ACTIVE again.
    connector.status = ConnectorStatus.PENDING
    connector.last_tally_online = False

    link = hub.local_link(connector.id)
    if link is not None:
        await link.close(code=1008, reason="connector secret rotated")

    await record(
        session,
        action="connector.rotate_secret",
        org_id=principal.org_id,
        user_id=principal.user.id,
        detail={"connector_id": connector.id},
        request=request,
    )

    return ConnectorPairingResponse(
        connector_id=connector.id,
        secret=secret,
        name=connector.name,
        pairing_code=_pairing_code(connector.id),
    )


def _seen_at(link) -> datetime | None:  # noqa: ANN001 - ConnectorLink or None
    """When the live link last heard from the connector.

    Preferred over the stored column, which only moves on connect, disconnect
    and Tally status transitions -- so a healthy connector's "last seen" would
    otherwise creep upward while it is answering every heartbeat.
    """
    return datetime.fromtimestamp(link.last_seen, UTC) if link is not None else None


@router.get("", response_model=list[ConnectorResponse])
async def list_connectors(
    principal: PrincipalDep, session: SessionDep, hub: HubDep
) -> list[ConnectorResponse]:
    rows = (
        (
            await session.execute(
                select(Connector).where(Connector.org_id == principal.org_id)
            )
        )
        .scalars()
        .all()
    )

    counts = dict(
        (
            await session.execute(
                select(Company.connector_id, func.count(Company.id))
                .where(Company.org_id == principal.org_id, Company.is_active.is_(True))
                .group_by(Company.connector_id)
            )
        ).all()
    )

    responses = []
    for connector in rows:
        link = hub.local_link(connector.id)
        online = link is not None or await hub.is_online(connector.id)
        responses.append(
            ConnectorResponse.build(
                connector,
                online=online,
                companies_open=link.companies_open if link else [],
                company_count=counts.get(connector.id, 0),
                seen_at=_seen_at(link),
            )
        )
    return responses


@router.get("/{connector_id}", response_model=ConnectorResponse)
async def get_connector_detail(
    connector: ConnectorDep, session: SessionDep, hub: HubDep
) -> ConnectorResponse:
    link = hub.local_link(connector.id)
    count = await session.scalar(
        select(func.count(Company.id)).where(
            Company.connector_id == connector.id, Company.is_active.is_(True)
        )
    )
    return ConnectorResponse.build(
        connector,
        online=link is not None or await hub.is_online(connector.id),
        companies_open=link.companies_open if link else [],
        company_count=count or 0,
        seen_at=_seen_at(link),
    )


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_connector(
    connector: ConnectorDep,
    principal: PrincipalDep,
    session: SessionDep,
    hub: HubDep,
    request: Request,
) -> None:
    """Revoke a connector's credentials and disconnect it immediately.

    Marking the row revoked stops it reconnecting; closing the live socket stops
    it serving reads right now. Both are needed -- doing only the first would
    leave a compromised machine reading the books until it happens to reconnect.
    """
    principal.require(Role.ADMIN)

    connector.status = ConnectorStatus.REVOKED
    connector.revoked_at = utc_now()

    link = hub.local_link(connector.id)
    if link is not None:
        await link.close(code=1008, reason="connector revoked")

    await record(
        session,
        action="connector.revoke",
        org_id=principal.org_id,
        user_id=principal.user.id,
        detail={"connector_id": connector.id},
        request=request,
    )
