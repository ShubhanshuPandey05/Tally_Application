"""Company discovery and linking.

Discovery deliberately lists only companies **currently open** in TallyPrime.
That is a security boundary, not a limitation: the operator sitting at the shop's
PC decides what is visible by choosing what to load, so pairing an account cannot
silently expose a set of books nobody meant to share.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from sqlalchemy import select

from ...core.errors import ConflictError, NotFound
from ...db.models import Company, Role
from ...services.audit import record
from ..deps import (
    CompanyDep,
    ConnectorDep,
    HubDep,
    PrincipalDep,
    SessionDep,
    SettingsDep,
    SyncDep,
)
from ..schemas import CompanyResponse, DiscoveredCompany, LinkCompanyRequest

router = APIRouter(tags=["companies"])


@router.get("/connectors/{connector_id}/discover", response_model=list[DiscoveredCompany])
async def discover_companies(
    connector: ConnectorDep, session: SessionDep, hub: HubDep
) -> list[DiscoveredCompany]:
    result = await hub.run(
        connector_id=connector.id,
        query="companies.list",
        params={"company": ""},
        timeout_seconds=30,
    )
    if not result.ok:
        raise NotFound(
            "connector could not list companies",
            user_message=(
                result.error.user_message
                if result.error
                else "Could not reach your Tally PC."
            ),
        )

    linked = {
        row.tally_name: row.id
        for row in (
            await session.execute(
                select(Company).where(Company.connector_id == connector.id)
            )
        )
        .scalars()
        .all()
    }

    discovered = []
    for item in result.data() or []:
        name = item.get("name")
        if not name:
            continue
        discovered.append(
            DiscoveredCompany(
                tally_name=name,
                guid=item.get("guid"),
                linked=name in linked,
                company_id=linked.get(name),
            )
        )
    return discovered


@router.post(
    "/connectors/{connector_id}/companies",
    response_model=CompanyResponse,
    status_code=status.HTTP_201_CREATED,
)
async def link_company(
    payload: LinkCompanyRequest,
    connector: ConnectorDep,
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    sync: SyncDep,
    request: Request,
) -> CompanyResponse:
    principal.require(Role.ACCOUNTANT)

    existing = await session.scalar(
        select(Company).where(
            Company.connector_id == connector.id,
            Company.tally_name == payload.tally_name,
        )
    )
    if existing is not None:
        if existing.is_active:
            raise ConflictError(
                "company already linked",
                user_message="That company is already connected.",
            )
        # Re-linking something previously removed keeps its id, which means its
        # snapshots and audit history survive rather than silently starting over.
        existing.is_active = True
        existing.display_name = payload.display_name or existing.display_name
        await session.flush()
        await _begin_history(session, sync, settings, existing.id)
        return CompanyResponse.build(existing)

    company = Company(
        org_id=principal.org_id,
        connector_id=connector.id,
        tally_name=payload.tally_name,
        display_name=payload.display_name,
    )
    session.add(company)
    await session.flush()

    await record(
        session,
        action="company.link",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        detail={"tally_name": company.tally_name},
        request=request,
    )
    await _begin_history(session, sync, settings, company.id)
    return CompanyResponse.build(company)


async def _begin_history(
    session: SessionDep, sync: SyncDep, settings: SettingsDep, company_id: str
) -> None:
    """Kick off the history backfill for a freshly linked company.

    Here rather than on first dashboard open, because this is the moment the
    owner is watching and expecting something to happen. Deliberately fired and
    forgotten: the backfill takes minutes and the linking request must not.

    The commit is not incidental. The coordinator opens its own session, so a
    company still uncommitted in this one does not exist as far as it is
    concerned -- and the sync would fail with a "no such company" nobody could
    explain.
    """
    if not settings.sync_auto_start:
        return
    await session.commit()
    await sync.ensure_backfill(company_id)


@router.get("/companies", response_model=list[CompanyResponse])
async def list_companies(principal: PrincipalDep, session: SessionDep) -> list[CompanyResponse]:
    rows = (
        (
            await session.execute(
                select(Company)
                .where(Company.org_id == principal.org_id, Company.is_active.is_(True))
                .order_by(Company.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [CompanyResponse.build(row) for row in rows]


@router.get("/companies/{company_id}", response_model=CompanyResponse)
async def get_company_detail(company: CompanyDep) -> CompanyResponse:
    return CompanyResponse.build(company)


@router.delete("/companies/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_company(
    company: CompanyDep, principal: PrincipalDep, session: SessionDep, request: Request
) -> None:
    principal.require(Role.ACCOUNTANT)
    # Soft delete. Hard-deleting would cascade the audit trail away with it, and
    # "who had access to these books last quarter" must remain answerable.
    company.is_active = False
    await record(
        session,
        action="company.unlink",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company.id,
        request=request,
    )
