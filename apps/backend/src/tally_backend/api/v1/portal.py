"""The management portal's API: onboarding decisions and subscription state.

This is the other side of the product. A shop owner installs the app, registers,
and signs in to an organisation that is ``PENDING`` — real, theirs, and entitled
to nothing. Somebody here then decides how many people and how many companies
that business gets, and the account comes to life.

Three rules shape every handler below.

**Portal authority is a separate axis.** Nothing in this module takes a
:class:`~tally_backend.api.deps.Principal`. A customer's access token cannot
reach these routes at all — it fails the ``typ`` check in ``decode_token``
before any handler runs — so there is no ordering of checks that could let a
tenant's role escalate into portal access.

**A partner sees only their own accounts.** Enforced through one helper,
:func:`_account_or_404`, for the same reason ``deps.get_company`` is the only
place tenant isolation lives: a rule applied in each handler is a rule the next
handler ships without. 404 rather than 403, again for the same reason — a
partner probing ids must not learn which businesses exist on the platform.

**No financial figures, ever.** The portal shows how much of the product an
account uses. It does not show, and must never grow the ability to show, what is
in anybody's books. Counting companies is a subscription question; opening one
is not, and a partner has no route to a customer's data anywhere in this file.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.errors import ConflictError, NotFound
from ...core.security import (
    generate_temporary_password,
    hash_password,
    verify_password,
)
from ...db.models import (
    Company,
    Connector,
    ConnectorStatus,
    Membership,
    Organisation,
    OrgStatus,
    PlatformRole,
    PlatformUser,
    Role,
    User,
    utc_now,
)
from ...services.audit import record
from ...services.entitlements import Entitlement
from ...services.portal import PortalAuthService
from ..deps import HubDep, PlatformPrincipal, PlatformPrincipalDep, SessionDep, SettingsDep
from ..portal_schemas import (
    AccountAdmin,
    AccountResponse,
    ApproveAccountRequest,
    CreatePartnerRequest,
    PartnerCreatedResponse,
    PortalChangePasswordRequest,
    PortalLoginRequest,
    PortalSessionResponse,
    PortalStats,
    PortalUserResponse,
    StatusChangeRequest,
    UpdateAccountRequest,
    UpdatePartnerRequest,
)

router = APIRouter(prefix="/portal", tags=["portal"])


# --------------------------------------------------------------------------
# Sign-in
# --------------------------------------------------------------------------


@router.post("/auth/login", response_model=PortalSessionResponse)
async def portal_login(
    payload: PortalLoginRequest,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
) -> PortalSessionResponse:
    service = PortalAuthService(session, settings)
    user = await service.authenticate(email=payload.email, password=payload.password)
    issued = service.issue_session(user)

    await record(
        session,
        action="portal.login",
        user_id=user.id,
        detail={"email": user.email, "role": user.role.value},
        request=request,
    )
    return PortalSessionResponse(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        user=await _portal_user_response(session, user),
    )


@router.get("/me", response_model=PortalUserResponse)
async def portal_me(
    principal: PlatformPrincipalDep, session: SessionDep
) -> PortalUserResponse:
    return await _portal_user_response(session, principal.user)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_portal_password(
    payload: PortalChangePasswordRequest,
    principal: PlatformPrincipalDep,
    session: SessionDep,
    request: Request,
) -> None:
    user = principal.user
    proven = payload.current_password is not None and verify_password(
        payload.current_password or "", user.password_hash
    )
    if not user.must_change_password and not proven:
        raise ConflictError(
            "current portal password does not match",
            user_message="That is not your current password.",
        )

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    await session.flush()

    await record(
        session, action="portal.change_password", user_id=user.id, request=request
    )


# --------------------------------------------------------------------------
# Customer accounts
# --------------------------------------------------------------------------


@router.get("/stats", response_model=PortalStats)
async def portal_stats(
    principal: PlatformPrincipalDep, session: SessionDep, hub: HubDep
) -> PortalStats:
    """The counters across the top of the portal, scoped to what the caller owns."""
    scope = principal.partner_scope
    orgs = (await session.execute(_scoped(select(Organisation), scope))).scalars().all()

    stats = PortalStats()
    for org in orgs:
        entitlement = Entitlement.of(org)
        # Expiry is counted as its own bucket rather than folded into "active".
        # An account whose term ran out is not working for the customer, and a
        # dashboard that files it under active is one that hides the problem.
        if entitlement.is_expired and org.status is OrgStatus.ACTIVE:
            stats.expired += 1
        else:
            setattr(stats, org.status.value, getattr(stats, org.status.value) + 1)

    org_ids = [org.id for org in orgs]
    if org_ids:
        stats.companies = (
            await session.scalar(
                select(func.count())
                .select_from(Company)
                .where(Company.org_id.in_(org_ids), Company.is_active.is_(True))
            )
        ) or 0
        stats.connectors_online = len(await _online_ids(session, hub, org_ids))
    return stats


@router.get("/accounts", response_model=list[AccountResponse])
async def list_accounts(
    principal: PlatformPrincipalDep,
    session: SessionDep,
    hub: HubDep,
    # A plain string rather than the enum, so an empty `?status=` reads as "all"
    # instead of 422. The filter tabs send exactly that for their All tab, and a
    # validation error on the way to seeing everything is a poor answer.
    status_filter: Annotated[str, Query(alias="status", max_length=20)] = "",
    search: Annotated[str, Query(max_length=200)] = "",
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[AccountResponse]:
    """Customer accounts, newest first.

    Newest first because the thing somebody opens this screen to do is act on a
    signup that just arrived. Oldest-first would bury today's work under every
    account ever approved.
    """
    query = _scoped(select(Organisation), principal.partner_scope)
    if status_filter.strip():
        try:
            query = query.where(Organisation.status == OrgStatus(status_filter.strip()))
        except ValueError as exc:
            raise NotFound(
                f"unknown status {status_filter!r}",
                user_message="That is not a status.",
            ) from exc
    if search.strip():
        query = query.where(Organisation.name.ilike(f"%{search.strip()}%"))

    orgs = (
        (await session.execute(query.order_by(Organisation.created_at.desc()).limit(limit)))
        .scalars()
        .all()
    )
    return await _account_responses(session, hub, list(orgs))


@router.get("/accounts/{org_id}", response_model=AccountResponse)
async def get_account(
    org_id: str, principal: PlatformPrincipalDep, session: SessionDep, hub: HubDep
) -> AccountResponse:
    org = await _account_or_404(session, principal, org_id)
    return (await _account_responses(session, hub, [org]))[0]


@router.post("/accounts/{org_id}/approve", response_model=AccountResponse)
async def approve_account(
    org_id: str,
    payload: ApproveAccountRequest,
    principal: PlatformPrincipalDep,
    session: SessionDep,
    hub: HubDep,
    request: Request,
) -> AccountResponse:
    """Activate a business and set what it is entitled to.

    Also the way back from ``SUSPENDED`` and ``REJECTED``, deliberately: a
    reinstatement is the same decision as the original one and has to restate
    the numbers rather than silently resurrect whatever they used to be. An
    account suspended for outgrowing its plan must not come back on that plan by
    accident.
    """
    org = await _account_or_404(session, principal, org_id)

    # Not an error, but not a no-op either -- silently re-approving would reset
    # `approved_at` and lose who actually made the original decision.
    if org.status is OrgStatus.ACTIVE:
        raise ConflictError(
            f"org {org.id} is already active",
            user_message="That account is already active. Edit its limits instead.",
        )

    previous = org.status
    org.status = OrgStatus.ACTIVE
    org.max_users = payload.max_users
    org.max_companies = payload.max_companies
    org.expires_at = payload.expires_at
    org.approved_at = utc_now()
    org.approved_by = principal.user.id
    org.partner_id = _resolve_partner(principal, payload.partner_id)
    if payload.notes is not None:
        org.notes = payload.notes
    await session.flush()

    await record(
        session,
        action="portal.approve_account",
        org_id=org.id,
        user_id=principal.user.id,
        detail={
            "from": previous.value,
            "max_users": org.max_users,
            "max_companies": org.max_companies,
            "expires_at": org.expires_at.isoformat() if org.expires_at else None,
            "partner_id": org.partner_id,
        },
        request=request,
    )
    return (await _account_responses(session, hub, [org]))[0]


@router.patch("/accounts/{org_id}", response_model=AccountResponse)
async def update_account(
    org_id: str,
    payload: UpdateAccountRequest,
    principal: PlatformPrincipalDep,
    session: SessionDep,
    hub: HubDep,
    request: Request,
) -> AccountResponse:
    """Change limits, the term, the owning partner, or the notes.

    Lowering a ceiling below what is already in use is allowed and does not
    delete anything. The customer keeps what they have and simply cannot add
    more, which is the only behaviour that is safe: the alternative is a portal
    that unlinks a shop's books because somebody mistyped a number.
    """
    org = await _account_or_404(session, principal, org_id)

    if payload.max_users is not None:
        org.max_users = payload.max_users
    if payload.max_companies is not None:
        org.max_companies = payload.max_companies
    if payload.clear_expiry:
        org.expires_at = None
    elif payload.expires_at is not None:
        org.expires_at = payload.expires_at
    if payload.partner_id is not None:
        org.partner_id = _resolve_partner(principal, payload.partner_id)
    if payload.notes is not None:
        org.notes = payload.notes
    await session.flush()

    await record(
        session,
        action="portal.update_account",
        org_id=org.id,
        user_id=principal.user.id,
        detail={
            "max_users": org.max_users,
            "max_companies": org.max_companies,
            "expires_at": org.expires_at.isoformat() if org.expires_at else None,
            "partner_id": org.partner_id,
        },
        request=request,
    )
    return (await _account_responses(session, hub, [org]))[0]


@router.post("/accounts/{org_id}/suspend", response_model=AccountResponse)
async def suspend_account(
    org_id: str,
    payload: StatusChangeRequest,
    principal: PlatformPrincipalDep,
    session: SessionDep,
    hub: HubDep,
    request: Request,
) -> AccountResponse:
    """Switch an account off without deleting anything.

    Reversible by design. Every connector, company, snapshot and grant stays
    exactly where it is, so reinstating is one click rather than a fresh
    onboarding — and the customer's history is not the leverage.
    """
    org = await _account_or_404(session, principal, org_id)
    return await _set_status(
        session, hub, principal, org, OrgStatus.SUSPENDED, payload.reason, request
    )


@router.post("/accounts/{org_id}/reject", response_model=AccountResponse)
async def reject_account(
    org_id: str,
    payload: StatusChangeRequest,
    principal: PlatformPrincipalDep,
    session: SessionDep,
    hub: HubDep,
    request: Request,
) -> AccountResponse:
    """Turn down a signup that should not have one.

    Distinct from suspension so the pending queue can be cleared without
    pretending a duplicate or a test account was ever a customer.
    """
    org = await _account_or_404(session, principal, org_id)
    if org.status is OrgStatus.ACTIVE:
        raise ConflictError(
            f"org {org.id} is active",
            user_message="Suspend an active account rather than rejecting it.",
        )
    return await _set_status(
        session, hub, principal, org, OrgStatus.REJECTED, payload.reason, request
    )


# --------------------------------------------------------------------------
# Partners
# --------------------------------------------------------------------------


@router.get("/partners", response_model=list[PortalUserResponse])
async def list_partners(
    principal: PlatformPrincipalDep, session: SessionDep
) -> list[PortalUserResponse]:
    """Everyone with portal access.

    Readable by partners as well as owners, because the approval form has to
    offer a partner to assign an account to — and a dropdown that only an owner
    can populate is a dropdown that is empty for the people who use it most.
    """
    rows = (
        (await session.execute(select(PlatformUser).order_by(PlatformUser.created_at)))
        .scalars()
        .all()
    )
    return [await _portal_user_response(session, row) for row in rows]


@router.post(
    "/partners", response_model=PartnerCreatedResponse, status_code=status.HTTP_201_CREATED
)
async def create_partner(
    payload: CreatePartnerRequest,
    principal: PlatformPrincipalDep,
    session: SessionDep,
    request: Request,
) -> PartnerCreatedResponse:
    """Add someone to the portal. Owner-only, and there is no other way in."""
    principal.require(PlatformRole.OWNER)

    email = payload.email.strip().lower()
    if await session.scalar(select(PlatformUser).where(PlatformUser.email == email)):
        raise ConflictError(
            f"portal user {email} already exists",
            user_message="Someone already uses that email in the portal.",
        )

    password = generate_temporary_password()
    partner = PlatformUser(
        email=email,
        password_hash=hash_password(password),
        full_name=payload.full_name,
        role=payload.role,
        must_change_password=True,
    )
    session.add(partner)
    await session.flush()

    await record(
        session,
        action="portal.create_partner",
        user_id=principal.user.id,
        detail={"partner_id": partner.id, "email": email, "role": partner.role.value},
        request=request,
    )
    return PartnerCreatedResponse(
        partner=await _portal_user_response(session, partner),
        temporary_password=password,
    )


@router.patch("/partners/{partner_id}", response_model=PortalUserResponse)
async def update_partner(
    partner_id: str,
    payload: UpdatePartnerRequest,
    principal: PlatformPrincipalDep,
    session: SessionDep,
    request: Request,
) -> PortalUserResponse:
    principal.require(PlatformRole.OWNER)
    partner = await session.get(PlatformUser, partner_id)
    if partner is None:
        raise NotFound(f"portal user {partner_id}", user_message="That person was not found.")

    role = payload.role or partner.role
    # The same lockout guard the tenant side has, for the same reason and with
    # worse consequences: nobody can create a portal owner from inside the
    # portal once the last one is gone, and the only way back is a database.
    losing_owner = partner.role.allows(PlatformRole.OWNER) and (
        not role.allows(PlatformRole.OWNER) or payload.is_active is False
    )
    if losing_owner and await _owner_count(session) <= 1:
        raise ConflictError(
            "portal would have no owner",
            user_message=(
                "This is the only owner. Make someone else an owner first, "
                "otherwise nobody could add portal accounts."
            ),
        )

    if payload.full_name is not None:
        partner.full_name = payload.full_name
    if payload.is_active is not None:
        partner.is_active = payload.is_active
    partner.role = role
    await session.flush()

    await record(
        session,
        action="portal.update_partner",
        user_id=principal.user.id,
        detail={
            "partner_id": partner.id,
            "role": partner.role.value,
            "is_active": partner.is_active,
        },
        request=request,
    )
    return await _portal_user_response(session, partner)


# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------


def _scoped(query, scope: str | None):  # noqa: ANN001, ANN202 - SQLAlchemy Select
    """Narrow a query to one partner's accounts, or leave it alone for an owner."""
    return query if scope is None else query.where(Organisation.partner_id == scope)


async def _account_or_404(
    session: AsyncSession, principal: PlatformPrincipal, org_id: str
) -> Organisation:
    """The single choke point every account-scoped portal route passes through.

    404 for "does not exist" and "not yours" alike. A partner who could tell the
    two apart could enumerate the platform's customer list one id at a time.
    """
    org = await session.get(Organisation, org_id)
    scope = principal.partner_scope
    if org is None or (scope is not None and org.partner_id != scope):
        raise NotFound(
            f"org {org_id} not visible to {principal.user.id}",
            user_message="That account was not found.",
        )
    return org


def _resolve_partner(principal: PlatformPrincipal, requested: str | None) -> str | None:
    """Who ends up owning the account.

    A partner may only ever assign accounts to themselves. Without this, the one
    field that decides what a partner can see would be settable by that same
    partner — which is not a scope at all.
    """
    if principal.partner_scope is not None:
        return principal.user.id
    return requested


async def _set_status(
    session: AsyncSession,
    hub,  # noqa: ANN001 - ConnectorHub
    principal: PlatformPrincipal,
    org: Organisation,
    new_status: OrgStatus,
    reason: str | None,
    request: Request,
) -> AccountResponse:
    previous = org.status
    org.status = new_status
    if reason:
        # Appended rather than replacing: why an account was suspended in March
        # still matters when it is suspended again in November.
        stamp = utc_now().date().isoformat()
        org.notes = f"{org.notes}\n\n[{stamp}] {new_status.value}: {reason}".strip() if org.notes \
            else f"[{stamp}] {new_status.value}: {reason}"
    await session.flush()

    await record(
        session,
        action=f"portal.{new_status.value}_account",
        org_id=org.id,
        user_id=principal.user.id,
        detail={"from": previous.value, "reason": reason},
        request=request,
    )
    return (await _account_responses(session, hub, [org]))[0]


async def _online_ids(session: AsyncSession, hub, org_ids: list[str]) -> set[str]:  # noqa: ANN001
    """Which of these organisations have at least one connector connected.

    Asked of the hub rather than read from ``Connector.last_seen_at``, which
    only advances on connect, disconnect and Tally status transitions -- a
    healthy connector's stored timestamp freezes at connect time, so using it
    here would report a fleet that goes dark a few minutes after it comes up.
    """
    rows = await session.execute(
        select(Connector.org_id, Connector.id).where(
            Connector.org_id.in_(org_ids),
            Connector.status != ConnectorStatus.REVOKED,
        )
    )
    online: set[str] = set()
    for org_id, connector_id in rows.all():
        if org_id in online:
            continue
        if hub.local_link(connector_id) is not None or await hub.is_online(connector_id):
            online.add(org_id)
    return online


async def _account_responses(
    session: AsyncSession, hub, orgs: list[Organisation]  # noqa: ANN001
) -> list[AccountResponse]:
    """Build the account rows, counting usage in three grouped queries.

    Grouped rather than per-row: the list screen shows a hundred accounts, and
    three counts each would be three hundred round trips to render one page.
    """
    if not orgs:
        return []
    org_ids = [org.id for org in orgs]

    companies = dict(
        (
            await session.execute(
                select(Company.org_id, func.count(Company.id))
                .where(Company.org_id.in_(org_ids), Company.is_active.is_(True))
                .group_by(Company.org_id)
            )
        ).all()
    )
    members = dict(
        (
            await session.execute(
                select(Membership.org_id, func.count(Membership.id))
                .where(Membership.org_id.in_(org_ids))
                .group_by(Membership.org_id)
            )
        ).all()
    )
    connectors = dict(
        (
            await session.execute(
                select(Connector.org_id, func.count(Connector.id))
                .where(
                    Connector.org_id.in_(org_ids),
                    Connector.status != ConnectorStatus.REVOKED,
                )
                .group_by(Connector.org_id)
            )
        ).all()
    )
    online = await _online_ids(session, hub, org_ids)

    admin_rows = (
        await session.execute(
            select(Membership.org_id, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id.in_(org_ids), Membership.role == Role.ADMIN)
            .order_by(User.created_at)
        )
    ).all()
    admins: dict[str, list[AccountAdmin]] = {}
    for org_id, user in admin_rows:
        admins.setdefault(org_id, []).append(
            AccountAdmin(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                last_login_at=user.last_login_at,
            )
        )

    # Resolved in one pass so the list does not fetch the same partner's name
    # once per account they own.
    people_ids = {org.partner_id for org in orgs} | {org.approved_by for org in orgs}
    people_ids.discard(None)
    names: dict[str, str] = {}
    if people_ids:
        rows = await session.execute(
            select(PlatformUser.id, PlatformUser.full_name, PlatformUser.email).where(
                PlatformUser.id.in_(people_ids)
            )
        )
        names = {row_id: (full_name or email) for row_id, full_name, email in rows.all()}

    return [
        AccountResponse(
            id=org.id,
            name=org.name,
            status=org.status,
            is_expired=Entitlement.of(org).is_expired,
            max_users=org.max_users,
            max_companies=org.max_companies,
            users_used=members.get(org.id, 0),
            companies_used=companies.get(org.id, 0),
            connectors=connectors.get(org.id, 0),
            connectors_online=1 if org.id in online else 0,
            expires_at=org.expires_at,
            created_at=org.created_at,
            approved_at=org.approved_at,
            approved_by=org.approved_by,
            approved_by_name=names.get(org.approved_by or ""),
            partner_id=org.partner_id,
            partner_name=names.get(org.partner_id or ""),
            notes=org.notes,
            admins=admins.get(org.id, []),
        )
        for org in orgs
    ]


async def _portal_user_response(
    session: AsyncSession, user: PlatformUser
) -> PortalUserResponse:
    accounts = (
        await session.scalar(
            select(func.count())
            .select_from(Organisation)
            .where(Organisation.partner_id == user.id)
        )
    ) or 0
    return PortalUserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        is_active=user.is_active,
        must_change_password=user.must_change_password,
        last_login_at=user.last_login_at,
        accounts=accounts,
    )


async def _owner_count(session: AsyncSession) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(PlatformUser)
            .where(
                PlatformUser.role == PlatformRole.OWNER,
                PlatformUser.is_active.is_(True),
            )
        )
    ) or 0
