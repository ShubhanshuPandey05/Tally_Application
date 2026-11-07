"""Per-person access inside a company: reports, ledger groups, stock groups.

Three kinds of route:

* ``GET /companies/{id}/access`` -- what *I* may see here. The app uses it to
  leave switched-off reports out of its menus. Any role; an admin's answer is
  always "everything".
* ``GET /companies/{id}/access/catalog`` -- the names an admin chooses from:
  the report keys, the group tree with its ledgers, the stock groups with their
  items. Admin-only, names only, never a figure.
* ``GET``/``PUT /team/{member}/access/{company}`` -- read and set one staff
  member's policy for one company.

Enforcement is not here. It is in ``deps.get_read_service`` and the report
gates in ``data.py``; this module only writes what they enforce. See
``services.access_policy`` for the rules.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import select

from ...core.errors import ConflictError, NotFound
from ...db.models import Company, CompanyAccess, Role
from ...services.access_policy import (
    STANDARD_REPORTS,
    AccessPolicy,
    prime_report_keys,
    seal_policy,
    stored_policy,
)
from ...services.audit import record
from ..deps import AccessScopeDep, CompanyDep, PrincipalDep, ReadServiceDep, SessionDep
from .team import _member_in_org

router = APIRouter(tags=["access"])


class MyAccessResponse(BaseModel):
    company_id: str
    #: False for an admin and for anyone with nothing switched off -- the app
    #: then shows every menu as it always has.
    restricted: bool
    policy: AccessPolicy


class MemberAccessResponse(BaseModel):
    member_id: str
    company_id: str
    policy: AccessPolicy


class CatalogGroup(BaseModel):
    name: str
    parent: str | None = None


class CatalogMember(BaseModel):
    name: str
    group: str | None = None


class AccessCatalogResponse(BaseModel):
    standard_reports: list[str]
    prime_reports: list[str]
    #: False until the company's masters have been read. The trees are then
    #: empty because they are unknown, not because the company has none.
    masters_read: bool
    ledger_groups: list[CatalogGroup]
    ledgers: list[CatalogMember]
    stock_groups: list[CatalogGroup]
    stock_items: list[CatalogMember]


@router.get("/companies/{company_id}/access", response_model=MyAccessResponse)
async def my_access(company: CompanyDep, scope: AccessScopeDep) -> MyAccessResponse:
    policy = scope.policy if scope is not None else AccessPolicy()
    return MyAccessResponse(
        company_id=company.id, restricted=not policy.is_unrestricted, policy=policy
    )


@router.get("/companies/{company_id}/access/catalog", response_model=AccessCatalogResponse)
async def access_catalog(
    company: CompanyDep, principal: PrincipalDep, reads: ReadServiceDep
) -> AccessCatalogResponse:
    """Everything an admin can switch off, by name.

    Straight from stored snapshots -- opening the access screen must not queue
    an export on the shop's PC -- and names only: the screen decides who sees a
    ledger, not what it holds.
    """
    principal.require(Role.ADMIN)

    def rows(payload: Any) -> list[dict[str, Any]]:
        return [r for r in payload or [] if isinstance(r, dict) and r.get("name")]

    groups = await reads.snapshot_payload(company, dataset="groups.list")
    ledgers = await reads.snapshot_payload(company, dataset="ledgers.list")
    stock = await reads.snapshot_payload(company, dataset="stock_items.list")

    items = rows(stock)
    stock_groups = sorted(
        {str(r.get("parent_group") or "").strip() for r in items} - {""}, key=str.casefold
    )
    return AccessCatalogResponse(
        standard_reports=list(STANDARD_REPORTS),
        prime_reports=list(prime_report_keys()),
        masters_read=groups is not None or ledgers is not None or stock is not None,
        ledger_groups=[
            CatalogGroup(name=r["name"], parent=r.get("parent") or None) for r in rows(groups)
        ],
        ledgers=[
            CatalogMember(name=r["name"], group=r.get("parent_group") or None)
            for r in rows(ledgers)
        ],
        stock_groups=[CatalogGroup(name=name) for name in stock_groups],
        stock_items=[
            CatalogMember(name=r["name"], group=r.get("parent_group") or None) for r in items
        ],
    )


async def _grant(session, principal, member_id: str, company_id: str) -> CompanyAccess:
    """The grant row a policy lives on, or the reason there is none."""
    principal.require(Role.ADMIN)
    _, membership = await _member_in_org(session, principal.org_id, member_id)
    company = await session.get(Company, company_id)
    if company is None or company.org_id != principal.org_id:
        raise NotFound(f"company {company_id}", user_message="That company was not found.")
    if membership.role.allows(Role.ADMIN):
        raise ConflictError(
            "admins see every company in full",
            user_message="Admins see everything in every company. Make them staff first.",
        )
    access = await session.scalar(
        select(CompanyAccess).where(
            CompanyAccess.user_id == member_id, CompanyAccess.company_id == company_id
        )
    )
    if access is None:
        raise ConflictError(
            f"user {member_id} has no grant for {company_id}",
            user_message="Turn this company on for them and save first.",
        )
    return access


@router.get("/team/{member_id}/access/{company_id}", response_model=MemberAccessResponse)
async def member_access(
    member_id: str, company_id: str, principal: PrincipalDep, session: SessionDep
) -> MemberAccessResponse:
    access = await _grant(session, principal, member_id, company_id)
    return MemberAccessResponse(
        member_id=member_id,
        company_id=company_id,
        policy=await stored_policy(session, access),
    )


@router.put("/team/{member_id}/access/{company_id}", response_model=MemberAccessResponse)
async def set_member_access(
    member_id: str,
    company_id: str,
    policy: AccessPolicy,
    principal: PrincipalDep,
    session: SessionDep,
    request: Request,
) -> MemberAccessResponse:
    """Replace one person's policy for one company. Sending the default clears it."""
    principal.require_mutable()
    access = await _grant(session, principal, member_id, company_id)
    access.restrictions = await seal_policy(session, company_id, policy)
    await session.flush()

    # Counts only. The audit trail is not sealed, and the names of what was
    # hidden are names out of somebody's books.
    await record(
        session,
        action="team.set_access",
        org_id=principal.org_id,
        user_id=principal.user.id,
        company_id=company_id,
        detail={
            "member_id": member_id,
            "restricted": not policy.is_unrestricted,
            "reports": policy.reports,
            "ledgers": policy.ledgers,
            "stock": policy.stock,
            "denied_reports": len(policy.denied_reports),
            "denied_ledger_groups": len(policy.denied_ledger_groups),
            "denied_ledgers": len(policy.denied_ledgers),
            "denied_stock_groups": len(policy.denied_stock_groups),
            "denied_stock_items": len(policy.denied_stock_items),
        },
        request=request,
    )
    return MemberAccessResponse(member_id=member_id, company_id=company_id, policy=policy)
