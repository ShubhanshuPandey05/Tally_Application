"""History sync: start it, watch it, stop it.

Three endpoints for one long-running job, and the shape is deliberate. Reading
four years of books out of a desktop TallyPrime takes minutes, so it cannot be
an HTTP request that returns when it is done -- the phone would sit on an open
socket through a screen lock. ``POST`` therefore starts work and returns
immediately; ``GET`` is what the progress bar reads.

``GET`` is also polled, once a second or so, while the bar is on screen. It is
built to be cheap enough for that: two indexed primary-key lookups and no
contact with the connector at all.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, status

from ...core.errors import ConflictError
from ...db.models import Role
from ...services.audit import record
from ...services.sync import SyncService
from ..deps import CompanyDep, PrincipalDep, SessionDep, SettingsDep, SyncDep

router = APIRouter(prefix="/companies/{company_id}", tags=["sync"])


@router.get("/sync", response_model=dict)
async def sync_status(
    company: CompanyDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Where the company's history sync has got to.

    Answered from the database alone. A status endpoint that asked the shop's
    PC how it was doing would put a request on the very gateway the sync is
    already using -- and the app polls this every second while the bar is up.
    """
    return await SyncService(session, settings).status(company.id)


@router.post("/sync", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
async def start_sync(
    company: CompanyDep,
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    sync: SyncDep,
    request: Request,
    full: bool = False,
) -> dict[str, Any]:
    """Start (or resume) reading this company's history.

    ``full=false`` resumes: slices already read are kept, which on a sync that
    dropped at year three saves the shop's Tally several multi-minute exports.
    ``full=true`` re-plans from scratch and is the answer to "the numbers look
    wrong", which is worth having but is never the default.
    """
    principal.require(Role.ADMIN)

    service = SyncService(session, settings)
    if await service.active_run(company.id) is not None or sync.is_running(company.id):
        raise ConflictError(
            "sync already running",
            user_message="A sync is already running for this company.",
        )

    await record(
        session,
        action="company.sync",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={"full": full},
        request=request,
    )
    # Committed before handing off: the coordinator opens its own session and
    # would not see an audit row still sitting in this one's transaction.
    await session.commit()

    if full:
        await sync.start_backfill(company.id)
    else:
        await sync.ensure_backfill(company.id)

    return await SyncService(session, settings).status(company.id)


@router.delete("/sync", response_model=dict)
async def cancel_sync(
    company: CompanyDep,
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
) -> dict[str, Any]:
    """Ask the running sync to stop after the slice it is on.

    Not an abort. A read already on the wire has cost TallyPrime its time
    whether or not anyone waits for the answer, so cancelling mid-export would
    throw away work without giving the shop's PC anything back.
    """
    principal.require(Role.ADMIN)
    service = SyncService(session, settings)
    await service.request_cancel(company.id)
    return await service.status(company.id)
