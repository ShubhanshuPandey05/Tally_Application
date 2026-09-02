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
from ...db.models import Company, CompanySyncState, Role
from ...services.audit import record
from ...services.entitlements import check_company_limit, require_data
from ..deps import (
    CompanyDep,
    ConnectorDep,
    HubDep,
    PrincipalDep,
    SessionDep,
    SettingsDep,
    SyncDep,
    visible_company_ids,
)
from ..schemas import CompanyResponse, DiscoveredCompany, LinkCompanyRequest

router = APIRouter(tags=["companies"])


@router.get("/connectors/{connector_id}/discover", response_model=list[DiscoveredCompany])
async def discover_companies(
    connector: ConnectorDep, principal: PrincipalDep, session: SessionDep, hub: HubDep
) -> list[DiscoveredCompany]:
    # Discovery is the first half of linking, and it puts a real export request
    # on the shop's Tally. Gating it here as well as at `link_company` keeps a
    # suspended account from using the wizard as a live probe.
    principal.require_changes()
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
    principal.require(Role.ADMIN)
    # Before the lookup below, so re-linking something previously removed is
    # still bounded -- that path also ends with one more active company. The
    # subscription check rides inside it; the two must not be separable.
    await check_company_limit(session, principal.org)

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
    """The companies this caller may open.

    Filtered here as well as in ``deps.get_company``, because the two answer
    different questions. The dependency stops a staff member *reading* a company
    they were not granted; this stops them seeing that it exists at all. Without
    it the company switcher would list every set of books in the business and
    fail only when one was tapped, which tells them the names -- often the most
    sensitive part -- while looking like a bug.
    """
    # Same reasoning one level up: a suspended account must not be told the
    # names of its own companies and then refused on every one of them.
    require_data(principal.org)

    query = (
        select(Company)
        .where(Company.org_id == principal.org_id, Company.is_active.is_(True))
        .order_by(Company.created_at)
    )

    visible = await visible_company_ids(session, principal)
    if visible is not None:
        if not visible:
            # No grants yet. Returning early keeps an `IN ()` out of the query,
            # which some databases reject outright.
            return []
        query = query.where(Company.id.in_(visible))

    rows = (await session.execute(query)).scalars().all()
    if not rows:
        return []

    # One query for every company's books-from date rather than one each. The
    # app needs it to offer financial years, and a switcher that costs N+1
    # round trips is one that gets cached badly later.
    books_from = dict(
        (
            await session.execute(
                select(CompanySyncState.company_id, CompanySyncState.books_from).where(
                    CompanySyncState.company_id.in_([row.id for row in rows])
                )
            )
        ).all()
    )
    return [CompanyResponse.build(row, books_from.get(row.id)) for row in rows]


@router.get("/companies/{company_id}", response_model=CompanyResponse)
async def get_company_detail(company: CompanyDep, session: SessionDep) -> CompanyResponse:
    state = await session.get(CompanySyncState, company.id)
    return CompanyResponse.build(company, state.books_from if state else None)


@router.delete("/companies/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unlink_company(
    company: CompanyDep, principal: PrincipalDep, session: SessionDep, request: Request
) -> None:
    principal.require(Role.ADMIN)
    # Not `require_changes`: a customer whose subscription lapsed is still
    # entitled to remove their own books. The demo is not, because everybody
    # who signs in shares it.
    principal.require_mutable()
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
