"""Phones registering to receive push notifications.

The app calls this after every sign-in and whenever FCM rotates its token, and
removes its token before signing out. What is *sent* lives in
``services.push``; this is only who can be reached.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, status
from sqlalchemy import delete, select

from ...db.models import PushDevice, utc_now
from ..deps import PrincipalDep, SessionDep
from ..schemas import PushDeviceRemoval, PushDeviceRequest

router = APIRouter(prefix="/push", tags=["push"])


@router.post("/devices", status_code=status.HTTP_204_NO_CONTENT)
async def register_device(
    payload: PushDeviceRequest,
    principal: PrincipalDep,
    session: SessionDep,
    x_app_version: str | None = Header(default=None),
) -> None:
    """Point this installation's token at the signed-in person.

    Any role and any subscription state: being told that a queued receipt
    failed is not a feature of a plan. A token already on file for somebody
    else moves to this person -- the token names the installation, and the
    installation is now theirs.
    """
    device = await session.scalar(select(PushDevice).where(PushDevice.token == payload.token))
    if device is None:
        session.add(
            PushDevice(
                user_id=principal.user.id,
                token=payload.token,
                platform=payload.platform,
                app_version=(x_app_version or "")[:40] or None,
            )
        )
    else:
        device.user_id = principal.user.id
        device.platform = payload.platform
        device.app_version = (x_app_version or "")[:40] or device.app_version
        device.last_seen_at = utc_now()
    await session.flush()


@router.post("/devices/remove", status_code=status.HTTP_204_NO_CONTENT)
async def remove_device(
    payload: PushDeviceRemoval, principal: PrincipalDep, session: SessionDep
) -> None:
    """Stop notifying this installation, as it signs out.

    Scoped to the caller's own rows, so a token learned some other way cannot
    be used to switch off somebody else's alerts. A POST rather than a DELETE
    with a body, which some proxies drop.
    """
    await session.execute(
        delete(PushDevice).where(
            PushDevice.token == payload.token, PushDevice.user_id == principal.user.id
        )
    )
